"""Turn model contributions into plain-language reason codes."""
from __future__ import annotations

import math

from .features import BEHAVIOUR, FWD_CAP


def inr(x: float) -> str:
    """Indian digit grouping: 1234567 -> Rs 12,34,567."""
    n = int(round(x))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + "₹" + s


def _pct(v: float) -> str:
    return f"{min(v, 1.0) * 100:.0f}%"


def _n(v: float, one: str, many: str | None = None) -> str:
    v = int(round(v))
    return f"{v} {one if v == 1 else (many or one + 's')}"


def _dur(minutes: float) -> str:
    if minutes < 90:
        return _n(minutes, "minute")
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.0f} days"


def _age(d: float) -> str:
    if d < 90:
        return f"Account was opened {_n(d, 'day')} ago."
    if d < 730:
        return f"Account has been open for {d / 30.4:.0f} months."
    return f"Account has been open for {d / 365:.0f} years."


def _fwd(v: float) -> str:
    if v >= FWD_CAP:
        return "Money it receives is not moved out again within a week."
    return f"Typically {_dur(v)} pass between receiving money and its next outgoing payment."


def _nbr(v: float, who: str) -> str:
    if v is None or math.isnan(v):
        return f"No risk information is available on the accounts {who}."
    return f"The accounts {who} have an average risk score of {v * 100:.0f} out of 100."


TEMPLATES = {
    "acct_age_days": ("Account age", _age),
    "n_in": ("Incoming payments", lambda v: f"Received {_n(v, 'incoming payment')} in 30 days."),
    "n_out": ("Outgoing payments", lambda v: f"Made {_n(v, 'outgoing payment')} in 30 days."),
    "amt_in": ("Money received", lambda v: f"Received {inr(v)} in total over 30 days."),
    "amt_out": ("Money sent", lambda v: f"Sent or withdrew {inr(v)} in total over 30 days."),
    "uniq_senders": ("Number of senders", lambda v: f"Received money from {_n(v, 'different account')}."),
    "uniq_receivers": ("Number of beneficiaries", lambda v: f"Sent money to {_n(v, 'different account')}."),
    "peak_senders_24h": ("Senders in 24 hours",
                         lambda v: f"Received money from {_n(v, 'different account')} within a single 24-hour period."),
    "pass_through": ("Pass-through", lambda v: f"Moved out {_pct(v)} of the money it received."),
    "fwd_fast_share": ("Rapid forwarding",
                       lambda v: f"{_pct(v)} of incoming money was followed by an outgoing payment within 60 minutes."),
    "median_fwd_min": ("Time to forward", _fwd),
    "burst_share": ("Burst of credits", lambda v: f"{_pct(v)} of all incoming money arrived within one 48-hour window."),
    "one_time_sender_share": ("One-off senders", lambda v: f"{_pct(v)} of its senders paid this account only once."),
    "night_share": ("Night-time activity", lambda v: f"{_pct(v)} of its transactions happened between 11 pm and 5 am."),
    "avg_in_amt": ("Average credit size", lambda v: f"Its average incoming payment is {inr(v)}."),
    "atm_share_out": ("Cash withdrawal", lambda v: f"{_pct(v)} of outgoing money was withdrawn as cash."),
    "top_dst_share": ("Single beneficiary", lambda v: f"{_pct(v)} of outgoing money went to a single beneficiary."),
    "active_days": ("Active days", lambda v: f"Active on {_n(v, 'day')} out of the last 30."),
    "nbr_out_risk": ("Risk of beneficiaries", lambda v: _nbr(v, "it pays")),
    "nbr_in_risk": ("Risk of senders", lambda v: _nbr(v, "that pay it")),
    "risky_nbrs": ("High-risk counterparties",
                   lambda v: f"Transacts directly with {_n(v, 'account')} rated high-risk."),
    "upstream_funnel": ("Funnel upstream",
                        lambda v: f"{_n(v, 'account')} feed money into the high-risk accounts that pay this one."),
    "dev_shared": ("Shared device", lambda v: f"Its device is also used by {_n(v, 'other account')}."),
    "dev_banks": ("Device across banks", lambda v: f"Accounts using its device are spread across {_n(v, 'bank')}."),
}


def source_of(feature: str) -> str:
    return "own" if feature in BEHAVIOUR else "consortium"


def reasons(names: list[str], values, contribs, min_impact: float = 0.12) -> dict:
    """Split contributions into factors that raise and lower the risk score."""
    items = []
    for name, v, c in zip(names, values, contribs):
        if abs(c) < min_impact:
            continue
        label, fn = TEMPLATES[name]
        items.append({"feature": name, "label": label, "text": fn(float(v)), "impact": round(float(c), 3),
                      "source": source_of(name)})
    items.sort(key=lambda r: -abs(r["impact"]))
    return {"raises": [r for r in items if r["impact"] > 0][:6],
            "lowers": [r for r in items if r["impact"] < 0][:4]}
