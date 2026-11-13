"""Feature engineering in plain NumPy.

Behavioural features describe one account using only that account's own
transactions, so a bank can compute them without anyone else's data.
Network features describe an account through its counterparties and devices;
how much of the network is visible is what separates a single bank from the
consortium.
"""
from __future__ import annotations

import numpy as np

DAY = 1440.0
FWD_CAP = 7 * DAY

BEHAVIOUR = [
    "acct_age_days", "n_in", "n_out", "amt_in", "amt_out", "uniq_senders", "uniq_receivers",
    "peak_senders_24h", "pass_through", "fwd_fast_share", "median_fwd_min", "burst_share",
    "one_time_sender_share", "night_share", "avg_in_amt", "atm_share_out", "top_dst_share", "active_days",
]
NETWORK = ["nbr_out_risk", "nbr_in_risk", "risky_nbrs", "upstream_funnel", "dev_shared"]
CONSORTIUM_ONLY = ["dev_banks"]


def _div(a, b, fill=0.0):
    out = np.full(len(a), fill, dtype=float)
    np.divide(a, b, out=out, where=b > 0)
    return out


def edge_table(n: int, src, dst, amt):
    """Collapse transactions into one row per (sender, receiver) pair."""
    p = dst >= 0
    key = src[p].astype(np.int64) * n + dst[p]
    u, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    return u // n, u % n, np.bincount(inv, amt[p], minlength=len(u)), cnt


def _peak_per_bucket(n, acct, other, ts, width, weights=None):
    """Max over time buckets of (unique `other` count) or (sum of weights), per account."""
    best = np.zeros(n)
    if len(acct) == 0:
        return best
    for offset in (0.0, width / 2):  # two staggered grids soften bucket-boundary effects
        b = np.floor((ts + offset) / width).astype(np.int64)
        nb = int(b.max()) + 1
        if weights is None:
            u = np.unique((acct * nb + b) * n + other)
            g, c = np.unique(u // n, return_counts=True)
            np.maximum.at(best, g // nb, c)
        else:
            g, inv = np.unique(acct * nb + b, return_inverse=True)
            np.maximum.at(best, g // nb, np.bincount(inv, weights))
    return best


def behaviour_features(n: int, src, dst, amt, ts, age_days) -> np.ndarray:
    p = dst >= 0
    s_in, d_in, a_in, t_in = src[p], dst[p], amt[p], ts[p]

    n_out = np.bincount(src, minlength=n).astype(float)
    amt_out = np.bincount(src, amt, minlength=n)
    n_in = np.bincount(d_in, minlength=n).astype(float)
    amt_in = np.bincount(d_in, a_in, minlength=n)

    es, ed, ea, ec = edge_table(n, src, dst, amt)
    uniq_senders = np.bincount(ed, minlength=n).astype(float)
    uniq_receivers = np.bincount(es, minlength=n).astype(float)
    one_time = _div(np.bincount(ed, ec == 1, minlength=n), uniq_senders)
    top_dst = np.zeros(n)
    np.maximum.at(top_dst, es, ea)

    peak_senders = _peak_per_bucket(n, d_in, s_in, t_in, DAY)
    burst = _div(_peak_per_bucket(n, d_in, s_in, t_in, 2 * DAY, weights=a_in), amt_in)

    # Time from each credit to the account's next debit.
    order = np.lexsort((ts, src))
    so, to = src[order], ts[order]
    big = 1e7
    pos = np.searchsorted(so * big + to, d_in * big + t_in, side="right")
    posc = np.minimum(pos, len(so) - 1)
    hit = (pos < len(so)) & (so[posc] == d_in)
    delta = np.where(hit, np.minimum(to[posc] - t_in, FWD_CAP), FWD_CAP)
    fwd_fast = _div(np.bincount(d_in, a_in * (delta <= 60), minlength=n), amt_in)
    o2 = np.lexsort((delta, d_in))
    start = np.cumsum(n_in) - n_in
    mid = np.minimum((start + (n_in - 1) // 2).astype(np.int64), max(len(delta) - 1, 0))
    median_fwd = np.where(n_in > 0, delta[o2][mid] if len(delta) else FWD_CAP, FWD_CAP)

    mod = ts % DAY
    night = (mod >= 23 * 60) | (mod < 5 * 60)
    night_share = _div(np.bincount(src, night, minlength=n) + np.bincount(d_in, night[p], minlength=n),
                       n_in + n_out)

    atm = np.bincount(src[~p], amt[~p], minlength=n)
    n_days = int(np.floor(ts.max() / DAY)) + 1 if len(ts) else 1
    day = np.floor(ts / DAY).astype(np.int64)
    active = np.unique(np.concatenate([src * n_days + day, d_in * n_days + day[p]]))
    active_days = np.bincount(active // n_days, minlength=n).astype(float)

    cols = {
        "acct_age_days": age_days, "n_in": n_in, "n_out": n_out, "amt_in": amt_in, "amt_out": amt_out,
        "uniq_senders": uniq_senders, "uniq_receivers": uniq_receivers, "peak_senders_24h": peak_senders,
        "pass_through": np.minimum(_div(amt_out, amt_in), 1.5), "fwd_fast_share": fwd_fast,
        "median_fwd_min": median_fwd, "burst_share": burst, "one_time_sender_share": one_time,
        "night_share": night_share, "avg_in_amt": _div(amt_in, n_in), "atm_share_out": _div(atm, amt_out),
        "top_dst_share": _div(top_dst, amt_out), "active_days": active_days,
    }
    return np.column_stack([cols[k] for k in BEHAVIOUR])


def network_features(n: int, es, ed, ea, risk, uniq_senders, dev_acct, dev_id, bank,
                     consortium: bool) -> np.ndarray:
    """Counterparty and device features.

    consortium=False: a bank sees the risk of a counterparty, and that
    counterparty's own senders, only when the counterparty is its own customer;
    devices are matched within the bank only.
    consortium=True: everything is visible through tokens.
    """
    vis = np.ones(len(es), bool) if consortium else bank[es] == bank[ed]
    s, d, a = es[vis], ed[vis], ea[vis]
    hot_s, hot_d = risk[s] >= 0.5, risk[d] >= 0.5
    out_w = np.bincount(s, a, minlength=n)
    in_w = np.bincount(d, a, minlength=n)
    nbr_out = _div(np.bincount(s, a * risk[d], minlength=n), out_w, np.nan)
    nbr_in = _div(np.bincount(d, a * risk[s], minlength=n), in_w, np.nan)
    risky = np.bincount(s, hot_d, minlength=n) + np.bincount(d, hot_s, minlength=n)
    funnel = np.bincount(d, hot_s * uniq_senders[s], minlength=n)

    n_banks = int(bank.max()) + 1
    key = dev_id if consortium else dev_id * n_banks + bank[dev_acct]
    _, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    shared = np.bincount(dev_acct, cnt[inv] - 1, minlength=n).astype(float)
    cols = [nbr_out, nbr_in, risky.astype(float), funnel.astype(float), shared]

    if consortium:
        pairs = np.unique(dev_id * n_banks + bank[dev_acct])
        _, per_dev = np.unique(pairs // n_banks, return_counts=True)
        dev_index = np.searchsorted(np.unique(dev_id), dev_id)
        dev_banks = np.ones(n)
        np.maximum.at(dev_banks, dev_acct, per_dev[dev_index])
        cols += [dev_banks]
    return np.column_stack(cols)
