# MuleRing Shield

Explainable, privacy-preserving detection of mule-account networks across banks,
running on a live transaction stream. Built for Track 1 (Digital Payments Fraud
Detection and Prevention).

Fraud money moves through chains of "mule" accounts spread over several banks.
Each bank sees only its own slice, so the ring stays invisible. MuleRing Shield
lets banks pool **tokens and numbers, never customer records**, so a hub can
join the slices, score the whole network as transactions arrive, and push each
bank an alert with plain-language reasons within seconds.

> All customers, accounts and transactions in this project are synthetic.

## What the demo shows

| Screen | What it proves |
|---|---|
| **Sign in** | Four roles: the consortium hub and three bank analysts. The server decides what each one may see. |
| **Overview** | Live counters, a live activity feed, and the bank's own ledger filling as transactions are booked. Rules vs. one bank alone vs. the consortium, measured on unseen data. |
| **Alerts** | A ranked queue that updates by itself. Each alert shows the bank's own score next to the shared score. |
| **Account** | Reason codes in plain language, each tagged "own-bank data" or "consortium intelligence", the money trail, and hold / confirm / clear actions. The score updates live. |
| **Cases** | One case file per account under review. Create, read, update and delete cases and their notes. Case contents stay with the bank. |
| **Rings** | Whole rings pieced together across banks, growing on screen as members are flagged. |
| **Privacy** | One record shown both ways: what stays in the bank and what leaves it. How the system is protected. |
| **Audit trail** | Every decision and system event, hash-chained so tampering is detected. |
| **Launch attack** | Starts a new ring whose payments arrive over the next minute or two. Alerts appear as evidence builds. |

Nothing on the page polls. The server pushes events over one connection
(server-sent events) and the page updates in place.

The layout adapts to the screen. On a phone, wide tables turn into a list of
cards, the tab bar scrolls sideways, forms stack into one column and buttons
grow to finger size. It was checked at 375, 768, 1400 and 1920 pixels wide with
no sideways scrolling on any page.

## How it works

```
Bank A ─┐  tokenise + summarise           ┌─ scores + reasons for Bank A's accounts
Bank B ─┼─ share package (no identities) ─► Hub: join tokens, score network, find rings ─┼─ ... Bank B
Bank C ─┘                                  └─ ... Bank C
```

1. **Tokenise.** Each bank turns account and device identifiers into tokens with
   HMAC-SHA256 and a secret shared among member banks. The hub never holds it.
2. **Summarise.** Each bank computes 18 behaviour features per account (for
   example, senders in 24 hours, share forwarded within 60 minutes) and its own
   risk score.
3. **Share.** Tokens, features, scores, per-counterparty totals and device
   tokens go to the hub. No names, account numbers, UPI IDs, phone numbers or
   individual transactions.
4. **Score.** The hub adds network features (risk of counterparties, funnels
   upstream, devices shared across banks) and scores every account.
5. **Explain and act.** The bank gets reasons for its own accounts. An analyst
   places a hold, confirms or clears. Nothing is blocked automatically, and a
   confirmed mule immediately raises the risk of linked accounts at other banks.

### The live stream

A simulation clock runs 60 times faster than real time. Every 2 seconds the
engine adds the transactions that happened in those 2 simulated minutes,
rescores all accounts (about 75 ms for 2,740 accounts and 55,000 transactions),
compares the result with the previous one, and publishes the differences: new
alerts, rings that appeared or grew, cleared accounts. The 30-day window slides,
so memory stays flat. The stream pauses when nobody is connected.

A flagged account stays flagged until its score falls well below the threshold,
so alerts do not flicker. A ring keeps its ID while it grows.

### The model

Gradient-boosted decision stumps trained with Newton steps on log-loss (the
XGBoost recipe at depth 1), written in NumPy in [app/model.py](app/model.py).
Depth-1 trees make the model **additive**: the score is a sum of one curve per
feature, so each feature's contribution to a decision is exact. For an additive
model those contributions are the Shapley values, with no sampling or
approximation. Reason codes are generated from them in [app/explain.py](app/explain.py).

### How the accuracy figures are produced

- Train on one synthetic world, choose thresholds on a second, report on five
  more that the models never saw. Nothing is hard-coded: the figures on the
  Overview page are computed at start-up.
- With the default seed: rules catch 25% of mule accounts, one bank alone 87%,
  the consortium 91% with a third of the false alarms (9 vs 28).
- In the first live attack with the default seed, the first alert fires 12
  simulated minutes after the attack starts, when 21% of the victims' money has
  been paid in, and all 9 mule accounts are flagged before the attack ends.
- The synthetic data includes deliberate look-alikes: self-help-group and chit
  collectors, households that pass salary straight on, casual sellers, shift
  workers, and mule accounts rented from real customers.
- Synthetic data is cleaner than real banking data. Read the figures as a
  comparison between approaches, not a forecast of production accuracy.

## Security

Roles are enforced on the server from a signed session cookie. Writes need an
anti-forgery header and a same-origin check. There is a strict content security
policy, per-client rate limiting, input validation on every route, and a
hash-chained audit trail. The full threat table, and what is not done, is in
[docs/SECURITY.md](docs/SECURITY.md).

## Run it locally

Python 3.10 or newer.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

```bash
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000 and pick a role. On macOS or Linux use `.venv/bin/python`.

Run the tests (28 of them):

```bash
.venv\Scripts\python -m pytest -q
```

Measure route latency and check the event stream against a running server:

```bash
.venv\Scripts\python docs/latency_check.py http://localhost:8000
```

Measured locally: every read route answers in 1 to 4 ms, an analyst decision in
about 90 ms (it rescores everything), and live events arrive every 2 seconds.

## Deploy on Railway (no Docker)

The repo is ready for Railway's default builder: it reads `requirements.txt`
and `.python-version`, and `railway.json` supplies the start command and health
check. `Procfile` is a fallback with the same command.

1. Push this folder to a GitHub repository.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repository.
3. In the service's **Variables**, add `CONSORTIUM_HMAC_KEY`, `SESSION_SECRET`
   and, if you want a gate at sign-in, `ACCESS_CODE`.
4. In **Settings → Networking**, click **Generate Domain**.

Or with the Railway CLI from this folder:

```bash
railway login
```

```bash
railway init
```

```bash
railway up
```

```bash
railway domain
```

Keep it at **one replica**. Scores, rate limits and the event bus live in
memory, so two instances would disagree. The service starts in a few seconds
and used about 150 MB of memory locally.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CONSORTIUM_HMAC_KEY` | a demo key | Secret used to tokenise identifiers. Set it in any shared deployment. |
| `SESSION_SECRET` | random at start-up | Signs session cookies. Set it so sessions survive a restart. |
| `ACCESS_CODE` | empty | If set, sign-in asks for this code. |
| `DEMO_CONTROLS` | `1` | `0` removes Launch attack, Pause, Reset and the answer key. |
| `STREAM_SPEED` | `60` | Simulated minutes per real minute. |
| `STREAM_TICK_SECONDS` | `2` | Seconds between live updates. |
| `MULERING_SEED` | `7` | Seed for the synthetic data. The same seed gives the same demo. |
| `ACCOUNTS_PER_BANK` | `900` | Size of each simulated bank. |
| `DATA_DIR` | `data` | Folder for the SQLite audit database. |
| `PORT` | set by Railway | Port the server listens on. |

The audit trail and case decisions live in SQLite. Railway's disk is reset on
each deploy; attach a volume and point `DATA_DIR` at it to keep them.

## API

All routes except `/api/health` and `/api/session` need a session.

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Health check |
| `GET` / `POST` / `DELETE /api/session` | Who am I, sign in or switch role, sign out |
| `GET /api/overview` | Counters, benchmark, fairness table, model summary |
| `GET /api/alerts` | Flagged accounts visible to the role |
| `GET /api/accounts/{token}` | Reasons, money trail, counterparties, ledger (own bank only) |
| `POST /api/accounts/{token}/action` | Hold, confirm, clear or reopen (own bank only). Opens a case if there is none. |
| `GET /api/cases` | List case files (hub sees a redacted list) |
| `POST /api/cases` | Create a case on one of the bank's accounts. `201` with a `Location` header, `409` if one exists. |
| `GET /api/cases/{id}` | Read a case with its notes |
| `PATCH /api/cases/{id}` | Update summary, priority, assignee or state. Needs the current `version`; `409` if someone else changed it first. |
| `DELETE /api/cases/{id}` | Delete the case, its notes and the decision on the account. `204`. |
| `POST /api/cases/{id}/notes` | Add a note. `201`. |
| `PATCH /api/cases/{id}/notes/{note}` | Edit a note |
| `DELETE /api/cases/{id}/notes/{note}` | Delete a note. `204`. |
| `GET /api/rings`, `GET /api/rings/{id}` | Rings visible to the role |
| `GET /api/privacy` | One record as held by the bank and as shared |
| `GET /api/audit` | Audit entries for the role, plus chain verification |
| `GET /api/activity` | Recent events and current live state |
| `GET /api/events` | Server-sent event stream |
| `POST /api/attack`, `/api/stream`, `/api/reset` | Demo controls |

## Project layout

```
app/
  synth.py        synthetic banks, customers, look-alikes, mule rings, live activity
  tokenise.py     HMAC tokens and masking
  features.py     behaviour and network features (NumPy)
  model.py        glass-box gradient boosting and metrics
  rules.py        rules-only baseline
  consortium.py   the share package and the hub
  pipeline.py     training, scoring, live clock, ring detection, benchmark
  explain.py      reason codes
  presenter.py    what each role is allowed to see, including live events
  realtime.py     event bus and the live stream timer
  security.py     sessions, rate limits, request guards, response headers
  store.py        SQLite: audit trail (hash-chained), decisions, case files and notes
  main.py         FastAPI routes
  static/         dashboard (plain HTML, CSS and JavaScript), responsive from phone to wide desktop
tests/            28 tests: privacy boundary, access rules, security guards, case CRUD, live stream
docs/
  DEMO_SCRIPT.md  a five-minute walkthrough for judges
  SECURITY.md     threats, controls and open items
  SUBMISSION.md   answers for the hackathon form
  latency_check.py
```

## Honest limits

- **Synthetic data.** No real bank data was available. Patterns are modelled on
  published descriptions of mule activity, and real data will be messier.
- **Simulated stream.** Transactions come from a generator on a fast clock, not
  from a payment switch. Each update rescores the whole population, which takes
  about 75 ms at this size; a production system would score incrementally.
- **Pseudonymous, not anonymous.** The hub sees which tokens pay which, so the
  shared data is still personal data under the DPDP Act, 2023.
- **Key custody.** Any party holding the HMAC secret can test guesses against a
  token. In production the secret belongs in an HSM at each bank.
- **One process.** Banks and hub run in one process here. The boundary is
  enforced in code and covered by tests, not by separate infrastructure.
- **Sign-in is a role choice** with an optional shared code. Production needs
  each bank's single sign-on with multi-factor authentication.
- **Closed world.** Every account belongs to a member bank. Real flows also
  leave to banks outside the consortium.
- **Railway deployment was prepared but not run from the development machine.**
