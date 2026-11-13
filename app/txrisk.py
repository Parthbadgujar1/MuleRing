"""Transaction-level risk: should this payment be reviewed before it is released?

Account scores say which accounts look like mules. This module looks at a
single payment as it arrives and asks whether a customer may be paying a
fraudster. Every feature uses only what was known before the payment, so it
can be computed in real time.

Sender-side features belong to the payer's bank. Receiver-side features belong
to the payee's bank and reach the payer's bank only through the consortium.
"""
from __future__ import annotations

import numpy as np

from .explain import inr
from .synth import DAY, World

SENDER = ["amount", "amt_vs_usual", "new_payee", "sender_out_60m", "night", "sender_age_days", "sender_n_out"]
RECEIVER = ["payee_age_days", "payee_in_24h", "payee_amt_24h"]
NAMES = SENDER + RECEIVER
BIG = 1e8


def _count_before(group, ts, weights, window: float):
    """For every row: how many earlier rows in the same group fall within `window` minutes, and their weight."""
    order = np.lexsort((ts, group))
    key = group[order] * BIG + ts[order]
    csum = np.concatenate([[0.0], np.cumsum(weights[order])])
    mine = group * BIG + ts
    lo = np.searchsorted(key, mine - window, side="left")
    hi = np.searchsorted(key, mine, side="left")
    return (hi - lo).astype(float), csum[hi] - csum[lo]


def features(w: World) -> tuple[np.ndarray, np.ndarray]:
    """Features for every account-to-account payment. Returns (row indices into the world, matrix)."""
    p = np.flatnonzero(w.dst >= 0)
    src, dst, amt, ts = w.src[p], w.dst[p], w.amt[p], w.ts[p]
    n = w.n

    # The sender's usual payment size: the median of everything they have sent.
    order = np.lexsort((amt, src))
    cnt = np.bincount(src, minlength=n)
    start = np.cumsum(cnt) - cnt
    median = np.where(cnt > 0, amt[order][np.minimum(start + (cnt - 1) // 2, len(amt) - 1)], np.nan)
    usual = np.where(cnt[src] >= 3, amt / median[src], np.nan)

    # First time this sender pays this account?
    by_time = np.argsort(ts, kind="stable")
    _, first = np.unique((src * n + dst)[by_time], return_index=True)
    new_payee = np.zeros(len(p))
    new_payee[by_time[first]] = 1.0

    out_60m, _ = _count_before(w.src, w.ts, np.ones(len(w.src)), 60.0)    # includes cash withdrawals
    mod = ts % DAY
    night = ((mod >= 23 * 60) | (mod < 5 * 60)).astype(float)
    in_24h, amt_24h = _count_before(dst, ts, amt, DAY)

    X = np.column_stack([amt, usual, new_payee, out_60m[p], night, w.age_days[src],
                         np.bincount(w.src, minlength=n)[src].astype(float),
                         w.age_days[dst], in_24h, amt_24h])
    return p, X


def hide_other_banks(X: np.ndarray, w: World, rows: np.ndarray) -> np.ndarray:
    """What the payer's bank knows on its own: nothing about a payee held elsewhere."""
    X = X.copy()
    other = w.bank[w.src[rows]] != w.bank[w.dst[rows]]
    X[np.ix_(other, [NAMES.index(k) for k in RECEIVER])] = np.nan
    return X


def labels(w: World, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ground truth for a labelled world.

    A payment is fraud when an ordinary customer pays a mule after that mule's
    ring became active. Payments sent by mules are money being moved on, which
    the account alerts cover, so they are left out of training and scoring.
    """
    src, dst = w.src[rows], w.dst[rows]
    started = np.full(w.n, -np.inf)   # without ring timing, every payment into a confirmed mule counts
    if len(w.ring_start):
        m = w.ring >= 0
        started[m] = w.ring_start[w.ring[m]]
    y = w.is_mule[dst] & ~w.is_mule[src] & (w.ts[rows] >= started[dst] - 1.0)
    return y, ~w.is_mule[src]


def combine(pattern: np.ndarray, payee_score: np.ndarray) -> np.ndarray:
    """Two independent reasons to worry: the payment looks wrong, or the payee is known to be risky."""
    return 1.0 - (1.0 - pattern) * (1.0 - payee_score)


def _n(v: float, one: str, many: str | None = None) -> str:
    v = int(round(v))
    return f"{v} {one if v == 1 else (many or one + 's')}"


TEMPLATES = {
    "amount": ("Amount", lambda v: f"The amount is {inr(v)}."),
    "amt_vs_usual": ("Unusual amount", lambda v: "The sender has too little history to judge the amount." if np.isnan(v)
                     else f"This is {v:.1f} times the sender's usual payment."),
    "new_payee": ("New payee", lambda v: "The sender has never paid this account before." if v else
                  "The sender has paid this account before."),
    "sender_out_60m": ("Sender velocity", lambda v: f"The sender made {_n(v, 'other payment')} in the previous hour."),
    "night": ("Time of day", lambda v: "It was sent between 11 pm and 5 am." if v else "It was sent during the day."),
    "sender_age_days": ("Sender account age", lambda v: f"The sender's account is {_n(v, 'day')} old."),
    "sender_n_out": ("Sender activity", lambda v: f"The sender made {_n(v, 'payment')} in the last 30 days."),
    "payee_age_days": ("Payee account age", lambda v: "Nothing is known about the receiving account's age." if np.isnan(v)
                       else f"The receiving account was opened {_n(v, 'day')} ago."),
    "payee_in_24h": ("Payee inflow", lambda v: "Nothing is known about other payments to the receiving account." if np.isnan(v)
                     else f"The receiving account got {_n(v, 'other payment')} in the previous 24 hours."),
    "payee_amt_24h": ("Payee inflow value", lambda v: "Nothing is known about money received by the payee." if np.isnan(v)
                      else f"The receiving account took in {inr(v)} in the previous 24 hours."),
}


def reasons(values, contribs, payee_score: float, payee_flagged: bool, cross_bank: bool, limit: int = 4) -> list[dict]:
    out = []
    if payee_flagged:
        out.append({"feature": "payee_score", "label": "Flagged payee",
                    "text": f"The receiving account is flagged as a likely mule (risk {round(payee_score * 100)} out of 100).",
                    "source": "consortium" if cross_bank else "own", "impact": 9.0})
    for name, v, c in zip(NAMES, values, contribs):
        if c > 0.15:
            label, fn = TEMPLATES[name]
            out.append({"feature": name, "label": label, "text": fn(float(v)), "impact": round(float(c), 3),
                        "source": "consortium" if (name in RECEIVER and cross_bank) else "own"})
    out.sort(key=lambda r: -r["impact"])
    return out[:limit]
