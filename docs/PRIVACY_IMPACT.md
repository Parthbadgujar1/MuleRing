# Privacy impact assessment

A short assessment of what personal data this system touches, the risks, and
what limits them. It describes the design and states plainly where the
prototype falls short of it.

**In one sentence:** privacy-preserving fraud intelligence across institutions,
using pseudonymous identifiers and minimised data sharing. It is not anonymous
data sharing, and should never be described that way.

## What is processed, and where

| Data | Inside the bank | Sent to the hub |
|---|---|---|
| Name, account number, UPI ID, phone, address, KYC documents | Yes | **Never** |
| Individual transactions | Yes | **Never** |
| Device identifiers | Yes | As a keyed token only |
| Account identifier | Yes | As a keyed token only |
| Behaviour summaries (18 numbers per account) | Yes | Yes |
| Total paid to each counterparty (token to token) | Yes | Yes |
| The bank's own risk score | Yes | Yes |
| Analyst decisions | Yes | The decision, not the note |
| Case files and notes | Yes | Only that a case exists, its state and priority |

## Purpose and minimisation

- **Purpose:** detecting and preventing payment fraud and money muling. The
  shared data is not used for marketing, credit decisions or anything else.
- **Minimisation:** the hub gets the least that lets it link accounts and
  score a network. It does not get single transactions.
- **Retention:** transactions older than 30 days leave the scoring window
  automatically. A production deployment needs a written retention period for
  the hub's copy and for the audit trail.

## Risks and what limits them

| Risk | Limit in place | Residual risk |
|---|---|---|
| The hub identifies a customer | It holds no names, no account numbers and no key to recompute tokens. | The hub can see which tokens pay which and how much. A party with outside knowledge of one relationship could recognise a token. |
| A bank reverses another bank's tokens | A bank only ever receives a token and a risk band for accounts it already deals with. | Every member holds the same key. A member that obtained another's tokens in bulk could test guesses. |
| The key leaks | Key version is recorded with every model, so affected results can be traced. | In the prototype the key is an environment variable. Rotation means re-tokenising; that workflow is designed, not built. |
| A wrong score harms a customer | No automatic blocking. A named analyst decides. False alarms are measured per customer group. Clearing is one click. | A hold still inconveniences an innocent customer. The recommended wording is "hold and review". |
| Insider misuse | Role checks on every request; every decision audited in a tamper-evident chain; notes visible only to their bank. | Sign-in is by role, not by person. Production needs named users and MFA. |
| Function creep | Purpose stated; shared fields fixed in code (`SharePackage`) and checked by a test. | Governance, not code, must stop new fields being added without review. |

## Rights of the people in the data

- **Explanation:** every alert has reasons in plain language that an analyst
  can read to the customer.
- **Correction:** a cleared account is removed from the queue at once, and the
  clearance lowers the risk of the accounts linked to it.
- **Erasure and retention:** deleting a case removes its notes. The audit
  entry that it existed is kept, which is the purpose of an audit trail and
  needs to be reflected in the retention policy.

## Not done in the prototype

- Banks and hub run in one process. The separation is enforced in code and by
  tests, not by infrastructure.
- No hardware key storage, no mutual TLS between institutions, no live key
  rotation.
- No private set intersection or secure enclave. Either would reduce what the
  hub can learn about who pays whom.
- No legal review of the basis for sharing under the Digital Personal Data
  Protection Act, 2023.
