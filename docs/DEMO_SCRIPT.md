# Five-minute demo script

Before you start: sign in as *Consortium hub operator*, open **More → Reset demo
data**, and leave **Show answer key** off. The names below are for the default
seed (`7`) straight after a reset. Run through the script once on the machine
you will present from to confirm they match.

The page never needs refreshing. The pill at the top shows the simulation
clock, which runs 60 times faster than real time. Click it to pause or resume.

The story to tell: money enters at one bank, moves through a second and is
cashed out at a third. No bank can see it alone. Together they can, without
any of them handing over a customer's name.

## 1. The problem and the headline (45 seconds) — Overview, as Consortium hub operator

> "Fraud money moves through mule accounts at several banks. Each bank sees
> only its slice."

Read the bars: rules catch 25% of mule accounts, one bank alone 87%, the
consortium 91%, with false alarms cut from 28 to 9. Point at the last callout:
on average the best-placed single bank holds about half of a ring's accounts.

Say at once that this is synthetic data and that the Model page shows where
the model is weak. Judges trust a team that says this first.

## 2. One bank's view (60 seconds) — switch "Signed in as" to *Bank A analyst*

Open **Alerts**, click **Found only through sharing**, then open **Aditya Salian**.

- Bank A's own model scores this account 4. With consortium intelligence it is 91.
- Read two reason codes aloud. The top ones are tagged *Consortium intelligence*.
- Show the money trail. The red arrows lead to an account at Bank C that
  appears only as a token.
- Scroll to **Decision and case**. Click **Hold and review**: a case file opens
  by itself. Nothing was blocked automatically.

## 3. Stopping a payment before it leaves (60 seconds) — Payments tab, still Bank A

Under **Send a test payment**, click **A payment to a flagged account at
another bank**. This posts an event to the ingestion API exactly as the bank's
own systems would.

About two seconds later the payment appears at the top of the list at risk
100. Bank A's own data put it at about 22. The reasons: the payee is flagged
by another bank, and the amount is many times what this customer usually
sends. The advice is to hold the payment and call the customer.

Click **Send the same event again**: it is reported as a duplicate. Click **A
replayed event from two hours ago**: it is refused.

Then, under **Check a UPI QR code**, click the "refund" sample. The check
finds a flagged payee, a name that does not match the account, and a message
that promises a refund. Bank A never learns who the payee is.

## 4. The privacy boundary (30 seconds) — Privacy tab, still Bank A

Left: what stays in the bank. Right: what leaves. Say the sentence exactly:
*pseudonymous identifiers and minimised data sharing, not anonymous data.*

Switch to *Consortium hub operator*. The left panel reads "not available to
the hub". Open **Payments**: the hub sees no payments at all. Open **Audit
trail**: the hub can see that Bank A placed a hold, and the chain is verified.

## 5. A live attack (60 seconds) — as Consortium hub operator, on Overview

Click **Launch attack**. A panel at the bottom left tracks it. Within about 15
seconds the first alert appears in **Live activity**, then a new ring, then
more alerts as the ring grows.

Read the panel: the first alert came 12 simulated minutes in, when about a
fifth of the victims' money had been paid in. "Victim payments flagged on
arrival" counts payments caught with no hindsight.

Click **Open RING-…**. Outer circle paid in, inner circle is the ring, cash
leaves through the square in the middle. Three bank colours in one ring.

## 6. Sharing a decision (30 seconds)

Switch to *Bank C analyst*, open **Alerts**, open **Rohan Nayak** (22 alone,
76 shared). Click **Confirm as mule**. The green box reports that an account
at Bank A, shown to Bank C only as a token, moved from Watch to High risk.

## 7. Evidence, including the weak spot (30 seconds) — Model tab

Show **What each layer adds**: rules, behaviour, a bank's own network, then
cross-bank. Scroll to **Does it still work when conditions change?** and read
the warning aloud: on a fraud pattern it was never trained on, recall falls to
about 29%. Then say what you do about it: every confirmed or cleared case is a
reviewed label, exported for retraining.

Scroll to **Tested on real-world data** and read the three findings. They are
mixed, and showing them is what makes the rest credible.

## Questions to be ready for

- **Is it trained on real data?** The live demonstration is trained on
  synthetic data, because no public dataset of labelled mule accounts across
  Indian banks exists. The same code trains on a bank's own records from three
  CSV files (`TRAIN_DATA_DIR`). The same model was also trained and tested on
  the public Elliptic data set of about 200,000 real Bitcoin transactions: on
  the periods right after training it caught 86% of illicit transactions at
  72% precision.
- **Does sharing help on real data?** Not on that data set: network features
  left the threshold-free score unchanged at 0.759. Say this yourself before a
  judge finds it. The sharing benefit is measured on synthetic data and needs a
  bank pilot to confirm. The real data also confirmed the main weakness: after
  a market closed and behaviour changed, the model caught none of 169 cases.
- **Is it really real time?** Events arrive through an ingestion API or from a
  simulator on a fast clock. Each batch is scored in under a fifth of a second
  and alerts are pushed to the browser over a live connection.
- **Why not XGBoost and SHAP?** The model is gradient boosting with depth-1
  trees, which makes it additive. Its per-feature contributions are exact
  Shapley values, so explanations need no approximation.
- **Can the hub identify a customer?** It holds no names, account numbers or
  key. It can see the pattern of flows between tokens, which is why the data
  is pseudonymous and still treated as personal data.
- **What stops one bank's analyst seeing another bank's customers?** The role
  is in a signed session cookie and checked on the server for every request.
  There are tests for it.
- **What about false positives?** Nothing is blocked automatically. False
  alarms are measured per 1,000 customers and by customer group, and clearing
  an account is one click.
- **How does this relate to MuleHunter.AI?** That works inside one bank on its
  own data. This links what individual banks find into cross-bank rings.
- **What is not built?** KYC and onboarding fraud, account takeover, deepfake
  detection, single sign-on, separate deployments for each bank, and a
  durable message broker. `docs/ARCHITECTURE.md` lists each gap.
