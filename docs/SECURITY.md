# Security and privacy

What can go wrong, what the code does about it, and what is still open. Every
control in the table is implemented. The automated tests in
`tests/test_app.py` cover the ones that can be checked from outside a browser;
escaping of on-screen text was checked by hand in the browser.

## Who can see what

| Viewer | Sees | Never sees |
|---|---|---|
| Consortium hub operator | Tokens, scores, reasons, rings and aggregated flows for all banks | Names, account numbers, UPI IDs, phone numbers, individual transactions, analysts' notes |
| Bank analyst | Own customers in full, own ledger, own audit entries | Other banks' customers (token and risk band only), other banks' audit entries |

This is decided in one file, [app/presenter.py](../app/presenter.py). The role
comes from the signed session; request parameters cannot change it. Live events
are rendered once per role before they are sent, so a subscriber only receives
what its role may see.

## Threats and controls

| Threat | Control | Where |
|---|---|---|
| Viewing another bank's customers by changing a parameter | Role is read from the session cookie only. Every route checks it on the server. | `main.current_role`, `presenter` |
| Forged or edited session | Cookie is `role.expiry.nonce` signed with HMAC-SHA256 and compared in constant time. Expires after 8 hours. | `security.Sessions` |
| Session theft by script | Cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` over HTTPS. | `main.sign_in` |
| Cross-site request forgery | Every write needs the header `X-Requested-With: MuleRingShield`, and a present `Origin` must match the host. No CORS headers are sent. | `security.SecurityMiddleware` |
| Cross-site scripting | Content-Security-Policy allows scripts from this origin only, no inline scripts. All dynamic text is escaped in `app.js`. Notes are stored as plain text. | `security.CSP`, `app.js esc()` |
| Clickjacking | `frame-ancestors 'none'` and `X-Frame-Options: DENY`. | `security.BASE_HEADERS` |
| Guessing the access code | Sign-in is limited to 20 attempts per 5 minutes per client. Failed attempts are written to the audit trail. | `security.RateLimiter` |
| Flooding | 600 reads and 60 writes per minute per client, at most 60 live connections, request bodies over 8 KB refused. | `security`, `realtime.Bus` |
| Malformed input | Tokens and ring IDs must match a strict pattern. Request bodies are validated and unknown fields rejected. SQL uses bound parameters only. | `main`, `store` |
| Tampering with the audit trail | Each entry stores a SHA-256 hash of its content and the previous entry's hash. `GET /api/audit` re-verifies the chain and reports the first broken entry. | `store.Store.verify` |
| Forged or replayed payment events | Bank adapters authenticate with a per-bank key compared in constant time. A bank may only report its own customers' payments. An event ID is accepted once, and events more than five minutes old or dated in the future are refused. | `ingest.Ingestor`, `main.ingest_role` |
| Flooding the ingestion queue | Batches are capped at 200 events and 256 KB, rate-limited per client, and a full queue refuses new events instead of dropping old ones. Refused events are kept with the reason. | `security`, `ingest` |
| A payment's details reaching the wrong party | Only the payer's bank is shown a payment under review. The hub never receives single payments; the QR check returns a risk band and a yes-or-no name match, never an identity. | `presenter.payment_view`, `qr.check` |
| Analyst notes leaking | Case summaries, assignees and notes are returned only to the bank that owns the case. The hub sees that a case exists, its state and priority. Another bank gets "not found". | `presenter.case_view`, `main._case_or_404` |
| Two analysts overwriting each other | Every case update carries the version it was based on. A stale update is refused with a conflict instead of silently winning. | `store.case_update` |
| Deleting evidence quietly | Deleting a case or a note is allowed, but each deletion is written to the hash-chained audit trail with who did it and when. | `main.delete_case`, `main.delete_note` |
| Wrongly freezing a customer | The system never blocks an account. It recommends a hold, a named analyst decides, and clearing is one click. False positives are tracked by customer group. | `main.act`, Overview page |
| Re-identifying customers from shared data | Banks share HMAC tokens, numeric summaries and totals. The hub does not hold the key. | `consortium`, `tokenise` |
| Sensitive data in caches | API responses carry `Cache-Control: no-store`. | `security.SecurityMiddleware` |
| Information leaks | API docs are disabled. Errors return a short message with no stack trace. HSTS is sent over HTTPS. | `main`, `security` |

## Settings for a shared deployment

| Variable | Set it to |
|---|---|
| `CONSORTIUM_HMAC_KEY` | A long random value. The app warns at start-up and on the Privacy page while the demo key is in use. |
| `ACCESS_CODE` | A code you give to judges or teammates. Without it anyone with the link can sign in. |
| `SESSION_SECRET` | A long random value, so sessions survive a restart. |
| `DEMO_CONTROLS` | `0` to remove Launch attack, Pause, Reset and the answer key. |
| `INGEST_KEYS` | One long random key per bank adapter, as `A:key,B:key,C:key`. Without it only a signed-in analyst can post test payments. |

Run a single instance. Scores, sessions' rate limits and the event bus live in
memory, so two replicas would disagree with each other.

## Not done

- **Real identity.** Sign-in is a role choice plus an optional shared code. A
  production system needs each bank's single sign-on with multi-factor
  authentication, and per-user accounts rather than per-role ones.
- **Key custody.** The HMAC key is an environment variable. In production it
  belongs in a hardware security module at each bank, and the hub must never
  hold it. In this demo, banks and hub run in one process, so the separation is
  in code, not infrastructure.
- **Pseudonymous, not anonymous.** The hub can see which tokens pay which. The
  shared data is still personal data under the DPDP Act, 2023. Private set
  intersection or a secure enclave would reduce what the hub learns.
- **Rate limits are per instance and in memory.** They reset on restart and do
  not coordinate across replicas.
- **Audit storage.** The hash chain shows tampering but cannot prevent someone
  with database access from deleting the whole file. Production would write to
  append-only storage and publish the chain head elsewhere.
- **No penetration test or dependency audit** has been run. The dependency list
  is short: FastAPI (with Starlette), Uvicorn, NumPy and NetworkX.
