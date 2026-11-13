"""Rules-only baseline: the kind of static thresholds a bank runs today."""
import numpy as np

from .features import BEHAVIOUR

RULES = [
    "Collects from 8+ accounts in 24 hours, forwards 80%+ of it, typically within 2 hours",
    "Account under 60 days old receives over Rs 2,00,000 and forwards 90%+ of it",
    "Account under 90 days old receives over Rs 1,50,000 and withdraws 70%+ of outflow as cash",
]


def rules_flag(B: np.ndarray) -> np.ndarray:
    c = {name: B[:, j] for j, name in enumerate(BEHAVIOUR)}
    r1 = (c["peak_senders_24h"] >= 8) & (c["pass_through"] >= 0.8) & (c["median_fwd_min"] <= 120)
    r2 = (c["acct_age_days"] < 60) & (c["amt_in"] >= 200000) & (c["pass_through"] >= 0.9)
    r3 = (c["acct_age_days"] < 90) & (c["amt_in"] >= 150000) & (c["atm_share_out"] >= 0.7)
    return r1 | r2 | r3
