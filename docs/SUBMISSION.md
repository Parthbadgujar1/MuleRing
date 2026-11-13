# Submission form answers

These match the working prototype. Rewrite them in your own words where you can.

## Problem Statement (up to 100 words)

Mule accounts receive and rapidly forward money obtained through UPI and digital-lending fraud, and each bank sees only its own slice of the network. Criminals exploit this gap by spreading funds across institutions within minutes. Existing rules are slow, and they flag individual transactions rather than the ring structure behind them. Banks cannot easily share customer data to connect the dots, because of privacy and consent constraints. As a result, mule rings persist, victims lose money, and frontline analysts receive alerts they cannot explain.

## Proposed solution and AI/ML approach (up to 200 words)

MuleRing Shield is an explainable, privacy-preserving system that detects mule-account networks across institutions. Each bank keeps its raw data in-house. It shares only keyed tokens (HMAC over account and device identifiers, using a secret held by member banks), numeric behaviour summaries and its own risk score, so the same account can be matched across banks without being revealed.

Behaviour features include senders within 24 hours, pass-through ratio, time-to-forward, bursts of credits, night-time activity and cash-out share. A consortium hub joins the tokens into a payment graph and adds network features: risk of counterparties, upstream funnels and devices shared across banks. Accounts are scored by gradient-boosted decision stumps, an additive glass-box model, so every feature's contribution is exact (its Shapley value) and becomes a plain-language reason code. Linked flagged accounts are grouped into rings, using Louvain community detection for large clusters.

The system recommends "hold and review", never automatic blocking. An analyst decides, every action is audited, and a confirmed mule immediately raises the risk of linked accounts at other banks. A working prototype scores a live stream from three simulated banks and pushes alerts to analysts within seconds.

## Impact and originality (up to 100 words)

Impact: money trails are interrupted earlier, so more funds can be frozen, and analysts get fewer, better alerts they can defend to auditors. On unseen synthetic data, rules caught 25% of mule accounts, one bank alone 87%, and the consortium 91% with a third of the false alarms. Real data will be messier.

Originality: most mule detection stays inside one bank and scores single transactions. MuleRing Shield combines ring-level detection, cross-bank matching without sharing identities, and exact per-alert explanations with human review, in one workflow designed around the DPDP Act.

## Current Stage of Development

Prototype

## Concept Note Upload

`MuleRing_Shield_Concept_Note.pdf` in the project folder. Fill in the team name first:
edit the "Team" line in `docs/make_concept_note.py`, then run
`python docs/make_concept_note.py MuleRing_Shield_Concept_Note.pdf` (needs the `reportlab` package).

## Declaration of Originality

For your team to confirm. Check that every member is registered in this team only and can
attend the boot camp on 4-6 December 2026.
