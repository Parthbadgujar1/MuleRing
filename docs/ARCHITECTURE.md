# Architecture: what is built, and what production would need

This prototype is one Python service. That is a deliberate choice for a
hackathon: everything can be run, tested and demonstrated on one machine. The
internal boundaries are the same ones a production system would have, so each
piece below can be moved out into its own service without redesigning it.

## What runs today

```
  Bank systems (or the built-in simulator)
        │  payment events (JSON)                      app/ingest.py
        ▼
  Ingestion: validate → de-duplicate → refuse replays → queue
        │                                             app/pipeline.py  advance()
        ▼
  Bank side: behaviour features, own risk score, tokenise          features.py, tokenise.py
        │  share package: tokens + numbers, no identities          consortium.py
        ▼
  Hub: join tokens across banks → network features → account risk → rings
        │                                             pipeline.py, model.py
        ├── payee signals back to the payer's bank → payment risk   txrisk.py
        ▼
  Role filter: who may see what                       presenter.py
        │
        ├── REST API (FastAPI)                         main.py
        ├── live events (server-sent)                  realtime.py
        └── audit trail, cases, notes (SQLite)         store.py
```

One risk engine, four sources of evidence:

| Evidence | Where | Used for |
|---|---|---|
| Rules | `rules.py` | Baseline for comparison |
| Account behaviour (18 features) | `features.py` | Each bank's own risk score |
| Network (risk of counterparties, funnels, shared devices) | `features.py`, `consortium.py` | Cross-bank account risk and rings |
| Payment pattern (10 features, known before the payment) | `txrisk.py` | Review recommendation on a single payment |

A payment's risk combines two independent reasons to worry: the payment looks
wrong, or the payee is already known to be risky. Either alone can raise it.

## Status against the review's gaps

| Gap | Status in this prototype | What production still needs |
|---|---|---|
| Real transaction ingestion | **Built.** `POST /api/ingest/transactions`: schema validation, idempotency by event ID, replay window, event-time ordering, pending / failed / reversed handling, bounded queue with back-pressure, dead letters, per-bank keys. Covered by tests. | A durable broker (Kafka or similar) in place of the in-process queue; real adapters to core banking and the UPI switch. |
| Fraud coverage beyond mule accounts | **Partly built.** Payment-level anomaly scoring and a UPI QR check, both feeding the same engine. | KYC and onboarding fraud, account takeover and deepfake detection are not built. They need data this prototype does not have (documents, login telemetry, media). |
| Privacy architecture | **Partly built.** Tokens, minimised share packages, role filter, key version recorded with every model. | Banks and hub as separate deployments; keys in an HSM or KMS per bank; mutual TLS; live key rotation; private set intersection or an enclave. See `PRIVACY_IMPACT.md`. |
| Risk decision and response | **Built.** Account and payment risk, hold / confirm / clear / reopen, case files with assignee, priority, notes and closure, optimistic locking, reviewed-label export. | Approval chains (maker-checker), SLA timers, and retraining triggered by reviewed labels. |
| Production reliability | **Partly built.** Liveness and readiness checks, graceful shutdown, metrics, online backup with verification, bad ticks isolated. | Shared state (Redis) and a managed database (PostgreSQL) so more than one instance can run; retries; alerting. |
| Business and regulatory readiness | **Documented.** `BUSINESS.md`, `PRIVACY_IMPACT.md`. | A pilot with a bank, and legal review of the sharing basis. |
| ML validation | **Built.** Ablation, confusion matrix, PR-AUC, false alarms per 1,000, threshold curve and calibration, robustness tests, time-split test, drift monitor, model record. Real-data test on a public dataset is scripted. | Validation on a bank's own labelled mule cases. |

## Honest account of "real time"

Transactions arrive from the simulator or the ingestion API. Every two seconds
the engine applies what arrived, rescores all accounts (about 75 ms), scores
the new payments (about 50 ms) and pushes the differences to open browsers.
Rescoring everything on each tick is simple and correct at this size. It would
not scale to a national payment volume: production needs incremental feature
updates per account and scoring only what changed.

## Target architecture

| Layer | Prototype | Production |
|---|---|---|
| Ingestion | In-process queue | Kafka topics per bank, schema registry, dead-letter topic |
| Bank-side processing | Module inside the service | Service inside each bank's network; raw data never leaves |
| Key management | Environment variable, version label | HSM or cloud KMS per bank, scheduled rotation |
| Bank-to-hub transport | Function call | Mutual TLS, signed packages |
| Feature store | Recomputed each tick | Incremental per-account state (Redis or similar) |
| Graph | NetworkX in memory | Graph database once the network outgrows memory |
| Models | Glass-box boosting in NumPy | Same class; a model registry with approval before release |
| Storage | SQLite | PostgreSQL; append-only audit store with the chain head published |
| Identity | Role choice with optional code | Each bank's single sign-on (OIDC) with MFA, per-user accounts |
| Monitoring | `/api/metrics`, `/api/ready` | Prometheus and Grafana, drift alerts, on-call |
| Scale | One instance | Stateless API instances behind a load balancer |

The front end stays plain HTML and JavaScript on purpose. It has no build
step, loads in one request and works on a phone. A framework would add nothing
a judge or an analyst would notice.
