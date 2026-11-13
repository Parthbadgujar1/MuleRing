# MuleRing Shield

Privacy-preserving fraud intelligence across banks: it finds mule-account
networks, scores each payment as it arrives, and explains every decision in
plain language. Built for Track 1 (Digital Payments Fraud Detection and
Prevention).

Fraud money moves through chains of "mule" accounts spread over several banks.
Each bank sees only its own slice, so the ring stays invisible. MuleRing Shield
lets banks pool **pseudonymous tokens and numbers, never customer records**, so
a hub can join the slices, score the whole network as transactions arrive, and
give each bank an alert with reasons within seconds.

> The live demonstration runs on synthetic data, and says so on every page.
> The same code trains on a bank's own records when they are supplied, and can
> be tested on public real-world data. See "Training data" below.

## What the demo shows

| Screen | What it proves |
|---|---|
| **Sign in** | Four roles: the consortium hub and three bank analysts. The server decides what each one may see. |
| **Overview** | Live counters, a live activity feed, and the bank's own ledger filling as transactions are booked. |
| **Alerts** | A ranked queue of risky accounts that updates by itself, with the bank's own score next to the shared score. |
| **Account** | Reason codes in plain language, each tagged "own-bank data" or "consortium intelligence", the money trail, and hold / confirm / clear actions. |
| **Payments** | Single payments recommended for review the moment they arrive. A test payment can be posted through the ingestion API, and a UPI QR code can be checked before a customer pays. |
| **Cases** | One case file per account under review, with full create, read, update and delete for cases and notes. |
| **Rings** | Whole rings pieced together across banks, growing on screen as members are flagged. |
| **Model** | The model record, what each layer adds, the threshold trade-off, robustness and time-split tests, drift, and where the model is weak. |
| **Privacy** | One record shown both ways: what stays in the bank and what leaves it. How the system is protected. |
| **Audit trail** | Every decision and system event, hash-chained so tampering is detected, exportable as CSV. |
| **Launch attack** | Starts a new ring whose payments arrive over the next minute or two. Alerts appear as evidence builds. |

Nothing on the page polls. The server pushes events over one connection and
the page updates in place. The layout adapts from a phone to a wide desktop.

## How it works

```
Bank systems ─► ingestion API ─► bank side: features, own score, tokens
                                        │ share package (no identities)
                                        ▼
                 Hub: join tokens ─► network features ─► account risk ─► rings
                                        │ payee signals
                                        ▼
                 Payer's bank: payment risk ─► review recommendation
```

1. **Ingest.** Banks post payment events. Each is validated, de-duplicated,
   checked for replay and queued. Reversals remove the payment they refer to.
2. **Tokenise and summarise.** Each bank turns identifiers into HMAC-SHA256
   tokens and computes 18 behaviour features and its own score per account.
3. **Share.** Tokens, features, scores and per-counterparty totals go to the
   hub. No names, account numbers, UPI IDs, phone numbers or single transactions.
4. **Score the network.** The hub adds network features (risk of
   counterparties, funnels upstream, devices shared across banks), scores every
   account and groups flagged accounts into rings.
5. **Score the payment.** The payer's bank combines its own view of the payment
   with what the consortium knows about the payee.
6. **Explain and act.** An analyst places a hold, confirms or clears. Nothing
   is blocked automatically. A confirmed mule immediately raises the risk of
   linked accounts at other banks.

More detail, and what production would need: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### The model

Gradient-boosted decision stumps trained with Newton steps on log-loss (the
XGBoost recipe at depth 1), written in NumPy in [app/model.py](app/model.py).
Depth-1 trees make the model **additive**: the score is a sum of one curve per
feature, so each feature's contribution to a decision is exact. For an additive
model those contributions are the Shapley values, with no sampling or
approximation.

## Evidence, and its limits

All figures are computed by the running system and shown on the Model page.
With the default seed, on synthetic test data the models never saw:

| Layer | Mules caught | Precision | False alarms per 1,000 customers |
|---|---|---|---|
| Rules only | 25% | 92% | 0.68 |
| Behaviour only, no network | 82% | 91% | 2.48 |
| Single bank, with its own network | 87% | 93% | 2.11 |
| Consortium | 91% | 98% | 0.68 |

- **Payments:** the payer's bank alone catches about 43% of fraud money; with
  consortium signals about 92%. That uses end-of-period knowledge. In a live
  attack, judged on arrival with no hindsight, roughly half of victim payments
  are flagged.
- **Messy or missing data, smaller banks, fewer mules, a later month:** recall
  stays between 83% and 90%.
- **A fraud pattern never seen in training** (many small payments, slow, no
  shared devices): recall falls to about 29%. The model recognises patterns
  like those it was trained on. This is the main weakness and it is shown on
  the Model page.

These figures show that the model learned the patterns the generator makes and
that sharing adds measurable value on that data. They are **not** a measure of
accuracy on real bank data.

### On real data

The same model was trained and tested on the Elliptic data set: 203,769 real
Bitcoin transactions, 234,355 real payment flows and 4,545 transactions
labelled illicit. It was trained on time steps 1 to 29 and tested on steps 35
to 49, which it never saw.

| What the model may use | Precision | Recall | F1 | PR-AUC |
|---|---|---|---|---|
| A transaction's own features | 57% | 72% | 0.640 | 0.759 |
| Plus network risk, one institution's view | 50% | 73% | 0.598 | 0.759 |
| Plus network risk, shared across institutions | 39% | 75% | 0.512 | 0.759 |

What this shows, stated plainly:

- **The model works on real data while conditions hold.** On steps 35 to 42 it
  caught 86% of illicit transactions at 72% precision (PR-AUC 0.89).
- **The sharing benefit is not reproduced here.** Network-risk features left
  PR-AUC unchanged at 0.759. They raised recall slightly and cost precision.
  The gain measured on synthetic data should be treated as unproven until it
  is tested on real bank data.
- **The model fails when the pattern changes.** After a dark market closed at
  step 43, it caught 0 of 169 illicit transactions. This is the same weakness
  the synthetic "unseen pattern" test shows.

The features in this data set are anonymised and it is Bitcoin rather than
UPI, so it tests the method, not the model used in the live demonstration.

## Training data

No public dataset of labelled mule accounts across Indian banks exists. There
are three ways to run this project.

**1. Synthetic (default).** Nothing to set up. The generator includes
deliberate look-alikes: self-help-group and chit collectors, households that
pass salary straight on, casual sellers, shift workers, and mule accounts
rented from real customers.

**2. A bank's own records.** Put three CSV files in a folder and set
`TRAIN_DATA_DIR`. The models train on them at start-up, thresholds come from a
30% holdout, and the model record shows the data fingerprint.

```
accounts.csv      account_id, bank, opened_days_ago, is_mule[, name]
transactions.csv  sender_id, receiver_id, amount, timestamp, channel
devices.csv       account_id, device_id                       (optional)
```

```bash
.venv\Scripts\python -m app.dataio export sample_data
```

```bash
.venv\Scripts\python -m app.dataio check sample_data
```

The first command writes the synthetic training set in this format as a
template; the second validates any folder. Set `LIVE_DATA_DIR` as well to start
from real history, in which case new activity arrives only through the
ingestion API and the simulator is off.

**3. Public real-world data.** The same model can be trained and tested on the
Elliptic data set: about 200,000 real Bitcoin transactions, 234,000 real
payment flows and 46,000 labels, split strictly by time.

```bash
.venv\Scripts\python -m app.realdata download --yes
```

```bash
.venv\Scripts\python -m app.realdata run
```

This downloads about 146 MB (licensed CC BY-NC-ND 4.0, kept out of the
repository) and writes `app/reports/elliptic.json`, which the Model page
displays. The run takes under a minute after the first read. The results from
this run are in "On real data" above. It is Bitcoin, not UPI, and its features
are anonymised, so it cannot drive the live demonstration.

## Security

Roles are enforced on the server from a signed session cookie. Writes need an
anti-forgery header and a same-origin check. There is a strict content security
policy, per-client rate limiting, input validation on every route, and a
hash-chained audit trail. See [docs/SECURITY.md](docs/SECURITY.md) and
[docs/PRIVACY_IMPACT.md](docs/PRIVACY_IMPACT.md).

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

Run the 36 tests:

```bash
.venv\Scripts\python -m pytest -q
```

Measure route latency and check the event stream against a running server:

```bash
.venv\Scripts\python docs/latency_check.py http://localhost:8000
```

Back up the audit database and verify the copy:

```bash
.venv\Scripts\python -m app.backup backups/mulering.db
```

```bash
.venv\Scripts\python -m app.backup --verify backups/mulering.db
```

To hand the project over as a ZIP without the virtual environment:

```bash
git archive --format=zip -o MuleRing.zip HEAD
```

## Deploy on Railway (no Docker)

The repo is ready for Railway's default builder: it reads `requirements.txt`
and `.python-version`, and `railway.json` supplies the start command and health
check. `Procfile` is a fallback with the same command.

1. Push this folder to a GitHub repository.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repository.
3. In the service's **Variables**, add `CONSORTIUM_HMAC_KEY`, `SESSION_SECRET`
   and `ACCESS_CODE`.
4. In **Settings → Networking**, click **Generate Domain**.

Keep it at **one replica**: scores, rate limits and the event bus live in
memory. The service starts in about six seconds and used about 150 MB locally.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CONSORTIUM_HMAC_KEY` | a demo key | Secret used to tokenise identifiers. Set it in any shared deployment. |
| `CONSORTIUM_KEY_VERSION` | `v1` | Label of the key in use, recorded with every model. |
| `SESSION_SECRET` | random at start-up | Signs session cookies. Set it so sessions survive a restart. |
| `ACCESS_CODE` | empty | If set, sign-in asks for this code. |
| `DEMO_CONTROLS` | `1` | `0` removes Launch attack, Pause, Reset and the answer key. |
| `INGEST_KEYS` | empty | Keys for bank adapters, as `A:key,B:key,C:key`. |
| `TRAIN_DATA_DIR` | empty | Folder of CSV files to train on instead of the generator. |
| `LIVE_DATA_DIR` | empty | Folder of CSV files to start from instead of a simulated history. |
| `STREAM_SPEED` | `60` | Simulated minutes per real minute. |
| `STREAM_TICK_SECONDS` | `2` | Seconds between live updates. |
| `MULERING_SEED` | `7` | Seed for the synthetic data. The same seed gives the same demo. |
| `ACCOUNTS_PER_BANK` | `900` | Size of each simulated bank. |
| `DATA_DIR` | `data` | Folder for the SQLite database. |
| `PORT` | set by Railway | Port the server listens on. |

## API

All routes except `/api/health`, `/api/ready` and `/api/session` need a session.

| Method and path | Purpose |
|---|---|
| `GET /api/health`, `GET /api/ready` | Liveness; readiness of models, stream and database |
| `GET` / `POST` / `DELETE /api/session` | Who am I, sign in or switch role, sign out |
| `GET /api/overview` | Counters, benchmark, fairness table |
| `GET /api/alerts`, `GET /api/accounts/{token}` | Flagged accounts; reasons, money trail, ledger |
| `POST /api/accounts/{token}/action` | Hold, confirm, clear or reopen (own bank only) |
| `GET /api/payments` | Payments recommended for review (payer's bank only) |
| `POST /api/ingest/transactions` | Submit up to 200 payment events. `202` with a result per event. Session or `X-Ingest-Key`. |
| `GET /api/ingest/status`, `GET /api/ingest/samples` | Counters and dead letters; ready-made test events |
| `POST /api/qr/check`, `GET /api/qr/samples` | Assess a UPI QR code or payment link |
| `GET` / `POST /api/cases`, `GET` / `PATCH` / `DELETE /api/cases/{id}` | Case files |
| `POST /api/cases/{id}/notes`, `PATCH` / `DELETE /api/cases/{id}/notes/{note}` | Notes |
| `GET /api/rings`, `GET /api/rings/{id}` | Rings visible to the role |
| `GET /api/model`, `GET /api/metrics` | Model record and evaluation; operating figures |
| `GET /api/audit`, `GET /api/audit/export` | Audit entries with chain verification; CSV export |
| `GET /api/feedback/export` | Analyst decisions as reviewed labels (own bank only) |
| `GET /api/privacy`, `GET /api/activity`, `GET /api/events` | Privacy view; recent events; live event stream |
| `POST /api/attack`, `/api/stream`, `/api/reset` | Demo controls |

Sending a payment event from a bank's own system:

```bash
curl -X POST http://localhost:8000/api/ingest/transactions -H "X-Ingest-Key: YOUR_KEY" -H "Content-Type: application/json" -d "{\"events\":[{\"event_id\":\"evt-20261113-000001\",\"sender_bank\":\"A\",\"sender_account\":\"123456789012\",\"receiver_bank\":\"B\",\"receiver_account\":\"210987654321\",\"amount\":2400,\"timestamp\":\"2026-11-13T10:15:00+05:30\",\"channel\":\"UPI\",\"status\":\"success\"}]}"
```

## Project layout

```
app/
  synth.py        synthetic banks, customers, look-alikes, mule rings, live activity
  dataio.py       import and export of real records as CSV
  realdata.py     train and test on the public Elliptic data set
  tokenise.py     HMAC tokens, UPI IDs and masking
  features.py     behaviour and network features (NumPy)
  txrisk.py       payment-level features, labels and reasons
  model.py        glass-box gradient boosting and metrics
  rules.py        rules-only baseline
  consortium.py   the share package and the hub
  ingest.py       ingestion queue: validation, idempotency, replay, reversals
  qr.py           UPI QR and payment-link checks
  pipeline.py     training, scoring, live clock, ring detection, benchmark
  evaluation.py   threshold curve, robustness, time split, payment evaluation
  explain.py      reason codes
  presenter.py    what each role is allowed to see, including live events
  realtime.py     event bus and the live stream timer
  security.py     sessions, rate limits, request guards, response headers
  store.py        SQLite: audit trail (hash-chained), decisions, cases, notes, backup
  backup.py       back up and verify the database
  main.py         FastAPI routes
  static/         dashboard (plain HTML, CSS and JavaScript)
tests/            36 tests
docs/
  ARCHITECTURE.md    what is built and what production needs, gap by gap
  BUSINESS.md        users, deployment, value, rules to check
  PRIVACY_IMPACT.md  what is processed, risks and limits
  SECURITY.md        threats, controls and open items
  DEMO_SCRIPT.md     a five-minute walkthrough for judges
  SUBMISSION.md      answers for the hackathon form
```

## Honest limits

- **Synthetic data in the live demonstration.** The headline figures compare
  approaches on generated data. Validation on a bank's own confirmed mule
  cases has not been done.
- **The benefit of sharing is shown on synthetic data only.** On the one real
  data set tested, network features did not improve the model.
- **Unseen fraud patterns are caught poorly** (about 29% recall in the test
  above) until the model is retrained on reviewed cases.
- **Not covered:** KYC and onboarding fraud, account takeover and deepfakes.
- **Simulated stream.** Transactions come from a generator on a fast clock or
  from the ingestion API, not from a payment switch. Each update rescores
  everything, which would not scale to national volume.
- **Pseudonymous, not anonymous.** The hub sees which tokens pay which.
- **One process, in-memory state, SQLite.** Banks and hub are separated in
  code and by tests, not by infrastructure. One instance only.
- **Sign-in is a role choice** with an optional shared code.
- **Railway deployment was prepared but not run from the development machine.**
