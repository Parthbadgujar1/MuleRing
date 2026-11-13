# Five-minute demo script

Before you start: sign in as *Consortium hub operator*, open **More → Reset demo
data**, and leave **Show answer key** off. The names below are for the default
seed (`7`) straight after a reset. Run through the script once on the machine
you will present from to confirm they match.

The page never needs refreshing. The pill at the top shows the simulation
clock, which runs 60 times faster than real time. Click it to pause or resume.

## 1. The problem (30 seconds) — Overview, as Consortium hub operator

> "Fraud money moves through mule accounts at several banks. Each bank sees
> only its slice."

Point at the last callout under the bars: on average the best-placed single
bank holds about half of a ring's accounts.

## 2. The result (45 seconds) — still on Overview

Read the three bars: rules catch 25% of mule accounts, one bank alone 87%, the
consortium 91%. Then the callouts: 25 mules caught only through sharing, and
false alarms cut from 28 to 9.

Say how it was measured: five synthetic datasets the models never saw. Say that
synthetic data is cleaner than real data. Judges trust a team that states this
first.

Point at "Who gets wrongly flagged?": collection agents such as self-help-group
organisers look like mules to rules (9 flagged), and the consortium model flags 1.

## 3. One bank's view (75 seconds) — switch "Signed in as" to *Bank A analyst*

Point at **Live ledger**: Bank A's own transactions arriving as they are booked.

Open **Alerts**, click the filter **Found only through sharing**, then open
**Aditya Salian**.

- Bank A's own model scores this account 4. With consortium intelligence it is 91.
- Read two reason codes aloud. The top ones are tagged *Consortium intelligence*:
  the accounts it pays are high-risk, and its device is used by accounts at 3 banks.
- Show the money trail. The red arrows lead to an account at Bank C that appears
  only as a token.
- Scroll to **Decision and case**: hold and review, never an automatic block.
  Click **Hold and review**. A case file opens by itself. Set a priority and an
  assignee, click **Save changes**, and add a note. Notes can be edited and
  deleted, and the **Cases** tab lists every case file.

## 4. The privacy boundary (40 seconds) — Privacy tab, still Bank A

Left: what stays in the bank. Right: what leaves. Point at the token, the
numeric summaries, and the "0" in the Identities column. Scroll to "How this
system is protected".

Switch to *Consortium hub operator*. The left panel now reads "not available to
the hub". Open **Audit trail**: the hub can see that Bank A placed a hold and
added a note, but not what the note says. Open **Cases**: the hub sees that the
case exists, with no summary, assignee or notes. The green banner on the audit
page shows the chain is intact.

## 5. A live attack (75 seconds) — as Consortium hub operator, on Overview

Click **Launch attack**. A panel at the bottom left tracks it. Within about 15
seconds the first alert appears in **Live activity**, then a new ring, then more
alerts as the ring grows. Nothing is refreshed by hand.

Read the panel: the first alert came 12 simulated minutes into the attack, when
about a fifth of the victims' money had been paid in. Everything after that
arrived at accounts that were already flagged.

Click **Open RING-…** in the panel. Outer circle paid in, inner circle is the
ring, cash leaves through the square in the middle. The page keeps updating as
the ring grows.

## 6. Intelligence sharing in action (40 seconds)

Switch to *Bank C analyst*, open **Alerts**, open **Rohan Nayak** (found only
through sharing: 22 alone, 76 shared). Click **Confirm as mule**.

The green box reports that one linked account was newly flagged: an account at
Bank A, shown to Bank C only as a token, moved from Watch to High risk.

Switch to *Bank A analyst* and open Alerts: the account is **Ananya Pai**, now
scored about 90 (it was 37). Bank C never saw her name, and Bank A never saw
Rohan Nayak's.

## Questions to be ready for

- **Is it really real time?** Transactions come from a simulator on a fast
  clock, because no payment switch feed is available. Everything after that is
  real: each batch is scored in about 75 ms and alerts are pushed to the
  browser over a live connection.
- **Why not XGBoost and SHAP?** The model is gradient boosting with depth-1
  trees, which makes it additive. Its per-feature contributions are exact
  Shapley values, so explanations need no approximation. A regulator can read
  the whole model.
- **Is the data real?** No, and the app says so on every page. Thresholds were
  chosen on separate data and results are reported on unseen data.
- **Can the hub identify a customer?** Not from what it holds: it has no key and
  no identifiers. It can see the pattern of flows between tokens, which is why
  the data is still treated as personal data.
- **What stops one bank's analyst seeing another bank's customers?** The role
  is in a signed session cookie and checked on the server for every request.
  There is a test for it. See `docs/SECURITY.md` for the full list.
- **What about false positives?** Nothing is blocked automatically. The first
  action is a temporary hold with human review, and clearing an account is one
  click and is audited.
- **What is next?** Single sign-on with multi-factor authentication, private
  set intersection, incremental scoring on a real feed, and a pilot on
  anonymised bank data.
