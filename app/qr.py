"""UPI QR and payment-link checks, run before a customer pays.

A UPI QR code is a short text such as

    upi://pay?pa=shop123456@banka&pn=Shetty%20Stores&am=450&tn=Groceries

The check reads that text and combines what it says with what the consortium
knows about the receiving account. The payer's bank never learns who the
payee is: it gets a risk band, and a yes or no on whether the name printed in
the code matches the name on the account (compared as keyed tokens).
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

from . import config
from .tokenise import name_token

HANDLES = [b[3] for b in config.BANKS]
PRESSURE_WORDS = ("refund", "cashback", "prize", "lottery", "kyc", "reward", "winner", "electricity", "bill pending",
                  "urgent", "blocked", "verify", "receive", "claim")


def _distance(a: str, b: str) -> int:
    """Edit distance, enough to spot look-alike handles."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def parse(payload: str) -> dict | None:
    payload = payload.strip()
    parts = urlsplit(payload)
    if parts.scheme.lower() != "upi" or parts.netloc.lower() != "pay":
        return None
    q = {k.lower(): v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    amount = None
    try:
        amount = float(q["am"]) if q.get("am") else None
    except ValueError:
        amount = -1.0
    return {"vpa": unquote(q.get("pa", "")).strip().lower(), "name": unquote(q.get("pn", "")).strip(),
            "amount": amount, "note": unquote(q.get("tn", "")).strip(), "extra": sorted(set(q) - {"pa", "pn", "am", "tn", "cu", "mc", "tr", "mode"})}


def check(payload: str, lookup, key: bytes) -> dict:
    """Assess a QR payload. `lookup(vpa)` returns what the consortium knows about that UPI ID, or None."""
    findings: list[dict] = []
    add = lambda level, text, source="code": findings.append({"level": level, "text": text, "source": source})
    q = parse(payload)
    if q is None:
        return {"level": "high", "parsed": None, "payee": None,
                "findings": [{"level": "high", "source": "code",
                              "text": "This is not a UPI payment code. Do not open it in a payment app."}]}
    if not q["vpa"] or q["vpa"].count("@") != 1:
        add("high", "The code does not contain a valid UPI ID.")
        return {"level": "high", "parsed": q, "payee": None, "findings": findings}
    user, handle = q["vpa"].split("@")
    payee = None
    if handle not in HANDLES:
        near = [h for h in HANDLES if 0 < _distance(handle, h) <= 2]
        if near:
            add("high", f"The bank handle \"@{handle}\" is a look-alike of \"@{near[0]}\". Fraudsters use near-copies to pass as a known bank.")
        else:
            add("medium", f"The bank handle \"@{handle}\" does not belong to a member bank, so nothing is known about the payee.")
    else:
        payee = lookup(q["vpa"])
        if payee is None:
            add("medium", "No account with this UPI ID is registered at that bank.")
        else:
            if payee["flagged"]:
                add("high", f"The receiving account is flagged as a likely mule account{' and belongs to a suspected ring' if payee['ring'] else ''}.",
                    "consortium")
            elif payee["band"] == "medium":
                add("medium", "The receiving account is on the watch list.", "consortium")
            if q["name"]:
                if name_token(key, q["name"]) != payee["name_token"]:
                    add("high", f"The name shown in the code (\"{q['name']}\") does not match the name on the account.", "consortium")
                else:
                    add("low", "The name shown in the code matches the name on the account.", "consortium")
            if payee["age_days"] < 30:
                add("medium", f"The receiving account was opened {int(payee['age_days'])} days ago.", "consortium")
    if q["amount"] is not None:
        if q["amount"] <= 0:
            add("high", "The amount in the code is not a valid number.")
        elif q["amount"] >= 20000:
            add("medium", f"The code pre-fills a large amount (₹{q['amount']:,.0f}). Check it before approving.")
    note = q["note"].lower()
    hits = [w for w in PRESSURE_WORDS if w in note]
    if hits:
        add("high", f"The message mentions \"{hits[0]}\". Scanning a code and entering a PIN always sends money; "
                    f"nobody needs it to give you a refund, prize or bill credit.")
    if q["extra"]:
        add("medium", f"The code carries unusual extra fields ({', '.join(q['extra'][:3])}).")
    if not findings:
        add("low", "Nothing unusual was found in this code.")
    level = "high" if any(f["level"] == "high" for f in findings) else \
            "medium" if any(f["level"] == "medium" for f in findings) else "low"
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order[f["level"]])
    public_payee = None if payee is None else {k: payee[k] for k in ("bank_name", "band", "flagged", "ring")}
    return {"level": level, "parsed": {k: q[k] for k in ("vpa", "name", "amount", "note")}, "payee": public_payee,
            "findings": findings}
