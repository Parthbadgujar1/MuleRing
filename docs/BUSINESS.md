# Business and regulatory notes

These notes are for the jury discussion. Figures measured by the prototype are
marked as such. Nothing here is a forecast for a real bank: where a number
would need a pilot to establish, the formula is given instead of a guess.

## Who uses it

| Person | Their problem today | What they get |
|---|---|---|
| Fraud analyst at a bank | A queue of alerts with a score and no reason. Cannot see where the money went after it left the bank. | Reasons in plain words, the money trail, the ring the account belongs to, and a case file to work in. |
| Fraud operations head | Too many false alarms; cannot show the regulator why an account was held. | Fewer, better-ranked alerts; false alarms tracked by customer group; an audit trail that shows tampering. |
| Branch or call-centre staff | A customer is about to pay a scammer and nobody knows. | A warning on the payment itself, before it is released, when the payee is flagged at another bank. |
| Compliance and audit | Evidence scattered across systems. | Every decision recorded with who, when and why; export to CSV; model version on record. |
| Consortium operator (for example an industry utility) | No way to connect cases across banks without pooling customer data. | A hub that links accounts by token and never holds a name or account number. |

## Why banks would join

- **A bank alone cannot see a ring.** In the test data the best-placed single
  bank holds about half of a ring's accounts on average (measured).
- **Sharing catches what one bank misses.** Measured on synthetic test data:
  account-level recall rises from 87% to 91% with a third of the false alarms,
  and the share of fraud money caught at the payment stage rises from about
  43% to about 92% (the second figure uses end-of-period knowledge; the live
  attack measure is lower and is shown on the Model page).
- **Nothing sensitive is handed over.** The share package contains tokens and
  numbers. This lowers the legal and reputational cost of taking part.

One caution to state openly: the benefit of sharing is measured on synthetic
data. On the one real data set tested (public Bitcoin transactions), network
features did not improve the model. A pilot on real bank data is what would
settle whether the gain holds.

## How it would be deployed

1. **Bank-side component** inside each bank's network. It reads the bank's own
   transactions, computes features and its own score, tokenises, and sends the
   share package. It also receives scores and reasons back.
2. **Hub** run by a neutral party. It joins tokens, scores the network and
   returns results. It holds no key and no identities.
3. **Analyst workbench** (this dashboard) in each bank, behind the bank's own
   sign-on.

A sensible first step is a pilot with two or three banks on historical data,
comparing the alerts raised against cases those banks have already confirmed.

## What it would cost and save

Cost drivers, in rough order: integration with core banking and the payment
switch; key management hardware; hub hosting; analyst training. The prototype
itself needs very little compute: it scores 2,740 accounts and 55,000
transactions in about 75 ms on a laptop.

Value, as formulas a pilot would fill in:

- **Money protected** = (fraud value flagged before cash-out) × (share an
  analyst acts on in time).
- **Analyst time saved** = (alerts per day) × (minutes saved per alert by
  having reasons and the money trail) + (false alarms avoided) × (minutes per
  false alarm).
- **Customer harm avoided** = (wrongful holds avoided) × (cost of a complaint
  and of a customer who cannot reach their money).

## Rules and obligations to check

This section lists what a legal and compliance review would need to cover. It
is a starting point, not legal advice, and each item should be verified against
the current text before it is quoted.

| Area | Why it matters here |
|---|---|
| Digital Personal Data Protection Act, 2023 and its Rules | Tokens are pseudonymous personal data. The basis for sharing between banks, purpose limitation, retention, and security safeguards all apply. The Act treats processing for the prevention and detection of offences differently from ordinary processing; whether inter-bank sharing fits that needs legal review. |
| RBI Master Directions on Fraud Risk Management (2024) | Sets expectations for early warning, governance and reporting of fraud in banks. The audit trail and the human-in-the-loop design are aimed at these. |
| RBI guidance on mule accounts, and MuleHunter.AI from the Reserve Bank Innovation Hub | MuleHunter.AI works inside a bank on that bank's data. This project is complementary: it links what individual banks find into cross-bank rings. |
| NPCI rules for UPI | Any use of UPI transaction data, and any pre-payment warning shown to a customer, must fit the scheme's rules. |
| Reporting to law enforcement (National Cybercrime Reporting Portal, 1930 helpline) | A confirmed mule ring should flow into existing reporting channels. The prototype does not do this. |
| Model risk and fairness | A model that affects customers needs documented validation, monitoring and a route to challenge a decision. The Model page and case files are the start of that record. |

## Who owns what

| Data | Owner | Where it lives |
|---|---|---|
| Customer identity, accounts, transactions | The customer's bank | Inside that bank only |
| Tokens, behaviour summaries, flow totals | The contributing bank, shared under the consortium agreement | Hub, for the retention period |
| Scores, reasons, ring membership | Produced by the hub, returned to each bank for its own accounts | Hub and bank |
| Decisions, case files, notes | The bank that made them | That bank only; the hub sees that a decision was made |

## What makes it different

Most fraud tools score one transaction or one account inside one bank. This
one finds the network across banks, says why in words an analyst can repeat to
a customer or a regulator, and does it without any bank handing over its
customer records.
