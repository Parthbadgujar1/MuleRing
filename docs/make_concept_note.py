import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)

out = sys.argv[1]
NAVY = colors.HexColor("#1F3A5F")
ACCENT = colors.HexColor("#2E7D6B")
LIGHT = colors.HexColor("#EEF3F8")

ss = getSampleStyleSheet()
title = ParagraphStyle("t", parent=ss["Title"], textColor=NAVY, fontSize=22, leading=26, spaceAfter=4)
sub = ParagraphStyle("s", parent=ss["Normal"], textColor=ACCENT, fontSize=11.5, leading=15, alignment=1)
meta = ParagraphStyle("m", parent=ss["Normal"], fontSize=9, textColor=colors.HexColor("#555555"), alignment=1)
h1 = ParagraphStyle("h1", parent=ss["Heading2"], textColor=NAVY, fontSize=13, spaceBefore=10, spaceAfter=4)
body = ParagraphStyle("b", parent=ss["Normal"], fontSize=10, leading=14, spaceAfter=4)
bullet = ParagraphStyle("bl", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=2)
cell = ParagraphStyle("c", parent=body, fontSize=9, leading=12, spaceAfter=0)
cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold", textColor=colors.white, alignment=1)
box = ParagraphStyle("bx", parent=cell, alignment=1)


def P(t, s=body):
    return Paragraph(t, s)


def bullets(items):
    return [Paragraph(i, bullet, bulletText="•") for i in items]


def table(rows, widths, header=True):
    data = [[P(c, cellb if (header and r == 0) else cell) for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D0")),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
        for r in range(2, len(rows), 2):
            st.append(("BACKGROUND", (0, r), (-1, r), LIGHT))
    t.setStyle(TableStyle(st))
    return t


def flow():
    steps = ["<b>Member banks</b><br/>local transaction data stays in-house",
             "<b>Tokenise</b><br/>HMAC tokens + aggregate behaviour features",
             "<b>Graph build</b><br/>accounts = nodes, transfers = edges",
             "<b>Score</b><br/>network features + glass-box boosting; ring detection",
             "<b>Explain &amp; act</b><br/>reason codes, hold-and-review, audit log"]
    row = []
    for i, s in enumerate(steps):
        row.append(P(s, box))
        if i < len(steps) - 1:
            row.append(P("<b>&rarr;</b>", box))
    w = 31 * mm
    t = Table([row], colWidths=[w, 5 * mm] * 4 + [w])
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for c in range(0, len(row), 2):
        st += [("BOX", (c, 0), (c, 0), 0.8, ACCENT), ("BACKGROUND", (c, 0), (c, 0), LIGHT)]
    t.setStyle(TableStyle(st))
    return t


story = [
    P("MuleRing Shield", title),
    P("Explainable, privacy-preserving detection of mule-account networks across institutions", sub),
    Spacer(1, 4),
    P("Concept Note &nbsp;|&nbsp; Track 1: Digital Payments Fraud Detection and Prevention &nbsp;|&nbsp; "
      "Stage: Prototype", meta),
    Spacer(1, 8),

    P("1. Problem", h1),
    P("Mule accounts receive and rapidly forward money obtained through UPI and digital-lending fraud. "
      "Each bank sees only its own slice of the money trail, and criminals exploit this by moving funds "
      "across several institutions within minutes. Current controls are largely rule-based and look at "
      "single transactions, not at the ring structure behind them. Banks also cannot freely pool customer "
      "data to connect the dots, because of privacy, consent and data-protection obligations. The result: "
      "mule rings persist, victims lose money before funds can be frozen, and analysts receive alerts they "
      "cannot explain or defend."),

    P("2. Proposed solution", h1),
    P("MuleRing Shield is a detection layer that finds <b>networks</b> of mule accounts, not just suspicious "
      "transactions, and does so across institutions without any bank sharing raw customer data. Every "
      "alert is explained in plain language, and every action is reviewed by a human and logged."),
    Spacer(1, 4),
    flow(),
    Spacer(1, 6),

    P("3. AI/ML approach", h1),
    table([
        ["Component", "Approach"],
        ["Graph construction", "Accounts are nodes; transfers are directed, time-stamped edges with amount and channel (UPI, IMPS, QR)."],
        ["Features", "Fan-in from many new senders; pass-through ratio (share forwarded out); time-to-forward; "
                     "burst of credits; night-time activity; cash-out share; account age. Network features: risk of "
                     "counterparties, funnels upstream, and devices shared across banks."],
        ["Account risk model", "Gradient-boosted decision stumps trained with Newton steps on log-loss: an additive, "
                               "glass-box model in the style of Explainable Boosting Machines, with fast, cheap inference. "
                               "Pairwise interaction terms and graph neural network embeddings are planned extensions."],
        ["Ring detection", "Flagged accounts linked by money flow or a shared device are grouped into rings; Louvain "
                           "community detection splits unusually large clusters. Each member is labelled collector, "
                           "mover or exit point."],
        ["Explainability", "Because the model is additive, each feature's contribution to a score is exact (it equals "
                           "the Shapley value, with no approximation). Contributions become reason codes, e.g. "
                           "\"received from 14 accounts within 24 hours\", each tagged as own-bank data or consortium "
                           "intelligence, plus the money-trail graph for the analyst."],
        ["Training data", "Synthetic UPI-style transactions with injected mule rings and deliberate look-alikes "
                          "(self-help-group collectors, salary pass-through households, casual sellers). Trained on one "
                          "synthetic world, thresholds set on a second, results reported on five unseen ones."],
    ], [38 * mm, 132 * mm]),

    P("4. Privacy-preserving intelligence sharing", h1),
    *bullets([
        "Raw identifiers (account numbers, phone numbers, VPAs) never leave the bank.",
        "Each bank converts identifiers to keyed tokens (HMAC with a consortium-held secret) so the same "
        "account matches across banks without being revealed. Plain or salted hashes are avoided because "
        "phone numbers and account numbers can be recovered by brute force.",
        "Only tokens, risk scores and aggregate behavioural features are shared. Private set intersection is "
        "the planned upgrade for stronger guarantees.",
        "Data minimisation and purpose limitation, in line with the Digital Personal Data Protection Act, 2023.",
    ]),

    P("5. Responsible use and human oversight", h1),
    *bullets([
        "Recommended action is <b>hold and review</b>, not automatic account blocking, to limit harm from false positives.",
        "An analyst queue shows score, reason codes and evidence graph; the analyst makes the final decision.",
        "Every decision is logged for audit and for feedback into model retraining.",
        "Fairness checks so that new-to-bank, rural or low-income customers are not over-flagged simply for having thin histories.",
    ]),

    P("6. Working prototype", h1),
    *bullets([
        "Live demo: https://mulering-production.up.railway.app/",
        "Three simulated banks (about 2,700 accounts, 55,000 transactions) with mule rings spanning all of them.",
        "Analyst dashboard: ranked alerts, plain-language reason codes, money-trail graph, and ring view.",
        "A \"viewing as\" switch shows the boundary: a bank sees its own customers in full and others only as tokens; "
        "the hub sees no identities at all.",
        "Confirming a mule at one bank immediately raises the risk of linked accounts at other banks.",
        "Live stream: transactions arrive continuously, every account is rescored in about 75 ms, and alerts are "
        "pushed to the analyst's screen without a refresh. In a simulated attack the first alert fired when about "
        "a fifth of the victims' money had been paid in.",
        "Payments: each payment is scored on arrival through an ingestion API with duplicate and replay "
        "protection. A customer about to pay an account flagged at another bank triggers a hold-and-call "
        "recommendation. A UPI QR check flags scam codes without revealing the payee.",
        "Evidence: an evaluation page reports what each layer adds, false alarms per 1,000 customers, "
        "robustness and time-split tests, and the model's main weakness: recall falls to about 29% on a fraud "
        "pattern it was never trained on.",
        "Security: server-enforced roles from signed sessions, anti-forgery checks, strict content security "
        "policy, rate limiting, and a hash-chained audit trail that detects tampering.",
        "<b>Results on unseen synthetic data:</b> rules catch 25% of mule accounts, a single bank working alone 87%, "
        "the consortium 91%, with false alarms cut from 28 to 9. Synthetic data is cleaner than real data, so these "
        "figures compare approaches rather than forecast production accuracy.",
    ]),
    P("Stack: Python, NumPy, NetworkX, FastAPI, SQLite, and a plain HTML / JavaScript dashboard with Cytoscape.js. "
      "Deploys on Railway without Docker; covered by automated tests."),

    P("7. Impact", h1),
    *bullets([
        "<b>Victims:</b> earlier interruption of the money trail, improving the chance that funds can be frozen.",
        "<b>Banks:</b> fewer, higher-quality alerts that analysts can act on and defend to auditors and regulators.",
        "<b>Ecosystem:</b> a practical model for cross-institution fraud intelligence that respects privacy.",
    ]),

    P("8. Originality", h1),
    P("Most mule detection stays inside a single bank and scores individual accounts or transactions. "
      "MuleRing Shield combines three things in one workflow: ring-level graph detection, privacy-preserving "
      "matching across institutions, and per-alert explanations with human review. It is designed for how "
      "banks actually work, including the need to justify every action taken on a customer's account."),

    P("9. Limitations and next steps", h1),
    *bullets([
        "Real bank data is not available for the hackathon, so results on synthetic data are indicative only.",
        "Keyed tokens require a trusted key-holder; private set intersection or secure enclaves would reduce that trust.",
        "Next steps: pairwise interaction terms, streaming scoring on a live transaction feed, and a pilot with a "
        "partner bank on anonymised data.",
    ]),
]

doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                        topMargin=16 * mm, bottomMargin=16 * mm,
                        title="MuleRing Shield - Concept Note", author="")


def footer(c, d):
    c.saveState()
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawRightString(A4[0] - 20 * mm, 10 * mm, f"MuleRing Shield - Concept Note  |  Page {d.page}")
    c.restoreState()


doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("ok")
