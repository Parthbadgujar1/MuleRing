"""Synthetic multi-bank payments world.

Everything here is made up: names, account numbers and transactions are random.
The generator creates ordinary customers (including "hard negatives" that look
like mules on the surface) and then injects mule rings that span several banks.

Mule ring shape:  victims -> layer-1 collectors -> layer-2 movers -> cash-out
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

DAY = 1440.0  # minutes
CH_UPI, CH_NEFT, CH_ATM = 0, 1, 2
CHANNELS = ["UPI", "NEFT", "ATM"]

FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Rohan", "Karthik", "Imran", "Manoj", "Suresh",
         "Ramesh", "Vikram", "Naveen", "Faizal", "Joseph", "Deepak", "Girish", "Harish", "Pradeep", "Santosh",
         "Ananya", "Diya", "Priya", "Kavya", "Sneha", "Lakshmi", "Fathima", "Meera", "Pooja", "Divya",
         "Shreya", "Nandini", "Rekha", "Sunita", "Asha", "Geetha", "Mary", "Zainab", "Bhavana", "Roopa"]
LAST = ["Shetty", "Rao", "Nayak", "Pai", "Kamath", "Bhat", "Hegde", "Gowda", "Reddy", "Naik",
        "Sharma", "Verma", "Patel", "Singh", "Kumar", "Das", "Iyer", "Menon", "Pinto", "D'Souza",
        "Khan", "Sheikh", "Joshi", "Kulkarni", "Desai", "Mehta", "Poojary", "Acharya", "Fernandes", "Salian"]
SHOP = ["Stores", "Traders", "Enterprises", "Provisions", "Medicals", "Textiles", "Electronics", "Bakery"]


@dataclass
class World:
    seed: int
    n_days: int
    n_banks: int
    bank: np.ndarray       # int, index into config.BANKS
    kind: np.ndarray       # individual | merchant | business | collector | passthrough | mule
    is_mule: np.ndarray    # ground truth (synthetic label)
    role: np.ndarray       # "", L1, L2, CASHOUT
    ring: np.ndarray       # ground-truth ring id, -1 if none
    age_days: np.ndarray
    name: np.ndarray
    acct_no: np.ndarray
    phone: np.ndarray
    src: np.ndarray        # sender account index
    dst: np.ndarray        # receiver account index, -1 for ATM cash
    amt: np.ndarray
    ts: np.ndarray         # minutes since window start
    ch: np.ndarray
    dev_acct: np.ndarray   # (account, device) pairs
    dev_id: np.ndarray
    next_dev: int
    next_ring: int
    ring_start: np.ndarray = field(default_factory=lambda: np.zeros(0))   # start time of each ring, by ring id

    @property
    def n(self) -> int:
        return len(self.bank)


class Builder:
    def __init__(self, rng: np.random.Generator, n_days: int, n_banks: int):
        self.rng, self.n_days, self.n_banks = rng, n_days, n_banks
        self.acc = {k: [] for k in ("bank", "kind", "is_mule", "role", "ring", "age_days", "name", "acct_no", "phone")}
        self.tx: list[tuple] = []
        self.dev: list[tuple[int, int]] = []
        self.next_dev = 0
        self.next_ring = 0
        self.ring_start: list[float] = []

    # ---- accounts -------------------------------------------------------
    def add_account(self, bank: int, kind: str, age: float, own_device: bool = True) -> int:
        rng = self.rng
        i = len(self.acc["bank"])
        first, last = FIRST[rng.integers(len(FIRST))], LAST[rng.integers(len(LAST))]
        if kind in ("merchant", "business"):
            name = f"{last} {SHOP[rng.integers(len(SHOP))]}"
        else:
            name = f"{first} {last}"
        self.acc["bank"].append(int(bank))
        self.acc["kind"].append(kind)
        self.acc["is_mule"].append(False)
        self.acc["role"].append("")
        self.acc["ring"].append(-1)
        self.acc["age_days"].append(float(age))
        self.acc["name"].append(name)
        self.acc["acct_no"].append(f"{rng.integers(10**11, 10**12)}")
        self.acc["phone"].append(f"+91 9XXXX X{rng.integers(1000, 10000)}")
        if own_device:
            self.new_device(i)
        return i

    def new_device(self, *accounts: int) -> int:
        d = self.next_dev
        self.next_dev += 1
        for a in accounts:
            self.dev.append((int(a), d))
        return d

    def idx(self, kind: str) -> np.ndarray:
        return np.flatnonzero(np.array(self.acc["kind"]) == kind)

    # ---- transactions ---------------------------------------------------
    def add_tx(self, src, dst, amt, ts, ch=CH_UPI):
        src = np.atleast_1d(np.asarray(src, dtype=np.int64))
        n = len(src)
        if n == 0:
            return
        dst = np.broadcast_to(np.asarray(dst, dtype=np.int64), (n,))
        amt = np.broadcast_to(np.asarray(amt, dtype=float), (n,))
        ts = np.broadcast_to(np.asarray(ts, dtype=float), (n,))
        ch = np.broadcast_to(np.asarray(ch, dtype=np.int8), (n,))
        self.tx.append((src, dst, np.round(amt), ts, ch))

    def day_times(self, n: int, lo_day: int = 0) -> np.ndarray:
        """Daytime-skewed timestamps, the way ordinary customers transact."""
        rng = self.rng
        day = rng.integers(lo_day, self.n_days, n)
        mod = np.clip(rng.normal(13.5 * 60, 3.6 * 60, n), 6 * 60, 23.4 * 60)
        night = rng.random(n) < 0.03
        mod[night] = rng.uniform(0, DAY, int(night.sum()))
        return day * DAY + mod

    # ---- output ---------------------------------------------------------
    def finalize(self, seed: int, cut: bool = True) -> World:
        end = self.n_days * DAY if cut else np.inf
        cols = [np.concatenate([t[k] for t in self.tx]) for k in range(5)]
        keep = (cols[3] >= 0) & (cols[3] < end) & (cols[2] >= 1) & (cols[0] != cols[1])
        order = np.argsort(cols[3][keep], kind="stable")
        src, dst, amt, ts, ch = (c[keep][order] for c in cols)
        dev = np.array(sorted(set(self.dev)), dtype=np.int64).reshape(-1, 2)
        a = self.acc
        return World(
            seed=seed, n_days=self.n_days, n_banks=self.n_banks,
            bank=np.array(a["bank"], dtype=np.int64), kind=np.array(a["kind"]),
            is_mule=np.array(a["is_mule"], dtype=bool), role=np.array(a["role"]),
            ring=np.array(a["ring"], dtype=np.int64), age_days=np.array(a["age_days"]),
            name=np.array(a["name"]), acct_no=np.array(a["acct_no"]), phone=np.array(a["phone"]),
            src=src, dst=dst, amt=amt, ts=ts, ch=ch,
            dev_acct=dev[:, 0], dev_id=dev[:, 1], next_dev=self.next_dev, next_ring=self.next_ring,
            ring_start=np.array(self.ring_start, dtype=float),
        )

    @classmethod
    def from_world(cls, w: World, rng: np.random.Generator) -> "Builder":
        b = cls(rng, w.n_days, w.n_banks)
        for k in b.acc:
            b.acc[k] = getattr(w, k).tolist()
        b.tx = [(w.src, w.dst, w.amt, w.ts, w.ch)]
        b.dev = list(zip(w.dev_acct.tolist(), w.dev_id.tolist()))
        b.next_dev, b.next_ring = w.next_dev, w.next_ring
        b.ring_start = w.ring_start.tolist()
        return b


# ---------------------------------------------------------------------------
# Legitimate activity
# ---------------------------------------------------------------------------
def _legit_activity(b: Builder) -> None:
    rng = b.rng
    ind, mer, biz = b.idx("individual"), b.idx("merchant"), b.idx("business")
    col, pas = b.idx("collector"), b.idx("passthrough")
    persons = np.concatenate([ind, col, pas])
    rate = np.concatenate([np.full(len(ind), 16.0), np.full(len(col), 10.0), np.full(len(pas), 6.0)])

    # Everyday merchant payments (mostly to a handful of favourite shops).
    cnt = rng.poisson(rate)
    src = np.repeat(persons, cnt)
    row = np.repeat(np.arange(len(persons)), cnt)
    fav = rng.choice(mer, size=(len(persons), 5))
    dst = fav[row, rng.integers(0, 5, len(src))]
    other = rng.random(len(src)) < 0.2
    dst[other] = rng.choice(mer, int(other.sum()))
    t = b.day_times(len(src))
    owl = (rng.random(len(persons)) < 0.06)[row]  # shift workers and night owls
    t[owl] = np.floor(t[owl] / DAY) * DAY + rng.uniform(22 * 60, 28 * 60, int(owl.sum()))
    b.add_tx(src, dst, np.clip(rng.lognormal(np.log(350), 0.9, len(src)), 10, 20000), t)

    # Person-to-person transfers inside small friend circles (often cross-bank).
    cnt = rng.poisson(4, len(ind))
    src = np.repeat(ind, cnt)
    row = np.repeat(np.arange(len(ind)), cnt)
    friends = rng.choice(ind, size=(len(ind), 4))
    dst = friends[row, rng.integers(0, 4, len(src))]
    b.add_tx(src, dst, np.clip(rng.lognormal(np.log(1200), 1.0, len(src)), 50, 50000), b.day_times(len(src)))

    # ATM cash withdrawals.
    cnt = rng.poisson(1.2, len(persons))
    src = np.repeat(persons, cnt)
    b.add_tx(src, -1, rng.integers(1, 21, len(src)) * 500, b.day_times(len(src)), CH_ATM)

    # Salaries, clustered at the start of the month.
    paid = ind[rng.random(len(ind)) < 0.7]
    day = np.where(rng.random(len(paid)) < 0.7, rng.integers(0, 5, len(paid)), rng.integers(0, b.n_days, len(paid)))
    b.add_tx(rng.choice(biz, len(paid)), paid,
             np.clip(rng.lognormal(np.log(32000), 0.5, len(paid)), 8000, 300000),
             day * DAY + rng.uniform(9 * 60, 13 * 60, len(paid)), CH_NEFT)

    # Hard negative 1: households that receive money and pass most of it on at once
    # (salary moved to a family member, rent, EMI). Looks like a layer-2 mule.
    for p in pas:
        from_employer = rng.random() < 0.6
        source = rng.choice(biz) if from_employer else rng.choice(ind)
        beneficiary = rng.choice(ind)
        for t in b.day_times(int(rng.integers(1, 4))):
            credit = float(np.clip(rng.lognormal(np.log(25000), 0.5), 5000, 200000))
            b.add_tx(source, p, credit, t, CH_NEFT if from_employer else CH_UPI)
            b.add_tx(p, beneficiary, credit * rng.uniform(0.7, 0.97), t + rng.uniform(8, 240))

    # Hard negative 2: collection agents (self-help group, chit fund, rent or tuition
    # collector). Many people pay in, then nearly everything goes to one beneficiary.
    # Looks like a layer-1 mule.
    for c in col:
        members = rng.choice(ind, size=int(rng.integers(8, 26)), replace=False)
        beneficiary = rng.choice(biz) if rng.random() < 0.6 else rng.choice(ind)
        share = float(rng.choice([500, 1000, 2000, 3000, 5000]))
        n_events = int(rng.choice([1, 2, 4], p=[0.4, 0.3, 0.3]))
        first = rng.uniform(1, max(2.0, b.n_days / n_events - 2)) * DAY
        for e in range(n_events):
            t0 = first + e * (b.n_days / n_events) * DAY
            pay = members[rng.random(len(members)) < 0.9]
            if len(pay) == 0:
                continue
            window = rng.uniform(4, 30) * 60
            t_pay = t0 + rng.uniform(0, window, len(pay))
            b.add_tx(pay, c, share, t_pay)
            b.add_tx(c, beneficiary, share * len(pay) * rng.uniform(0.85, 0.98),
                     t_pay.max() + rng.uniform(20, 600))

    # Hard negative 3: casual sellers and freelancers. A burst of one-off payments
    # from strangers, then most of the money is moved on or withdrawn.
    for s in ind[rng.random(len(ind)) < 0.06]:
        k = int(rng.integers(3, 13))
        t_pay = rng.uniform(0, b.n_days - 4) * DAY + rng.uniform(0, 3 * DAY, k)
        amounts = np.clip(rng.lognormal(np.log(4000), 0.8, k), 300, 60000)
        b.add_tx(rng.choice(ind, k, replace=False), s, amounts, t_pay)
        moved, t_out = amounts.sum() * rng.uniform(0.6, 0.95), t_pay.max() + rng.uniform(15, 600)
        if rng.random() < 0.6:
            b.add_tx(s, rng.choice(ind), moved, t_out)
        else:
            c = int(np.ceil(moved / 20000))
            b.add_tx(np.full(c, s), -1, np.full(c, moved / c), t_out + np.sort(rng.uniform(0, 240, c)), CH_ATM)

    # Merchants settle with suppliers; businesses pay each other.
    receipts = np.zeros(len(b.acc["bank"]))
    for s, d, a, _, _ in b.tx:
        ok = d >= 0
        np.add.at(receipts, d[ok], a[ok])
    for m in mer:
        if receipts[m] <= 0:
            continue
        k = 1 + int(rng.poisson(2))
        suppliers = rng.choice(biz, size=int(rng.integers(1, 3)))
        amounts = receipts[m] * rng.uniform(0.3, 0.7) * rng.dirichlet(np.ones(k))
        b.add_tx(np.full(k, m), rng.choice(suppliers, k), amounts, b.day_times(k), CH_NEFT)
    cnt = rng.poisson(5, len(biz))
    src = np.repeat(biz, cnt)
    b.add_tx(src, rng.choice(biz, len(src)), np.clip(rng.lognormal(np.log(80000), 0.8, len(src)), 5000, 2e6),
             b.day_times(len(src)), CH_NEFT)

    # Shared devices among ordinary people (family phone, one person with two banks).
    for p in persons[rng.random(len(persons)) < 0.08]:
        b.new_device(int(p), int(rng.choice(persons)))


# ---------------------------------------------------------------------------
# Mule rings
# ---------------------------------------------------------------------------
def add_ring(b: Builder, t0: float | None = None, style: str | None = None,
             burst: float | None = None, tempo: float = 1.0) -> dict:
    """Inject one mule ring. Returns a summary of what was created.

    tempo scales every forwarding delay; below 1 the ring moves money faster.
    """
    rng = b.rng
    ring_id = b.next_ring
    b.next_ring += 1
    style = style or ("fast" if rng.random() < 0.6 else "slow")
    fast = style == "fast"
    # "smurf" is a pattern the models are never trained on: many small payments,
    # slow forwarding and no shared devices. It is used to test robustness.
    smurf = style == "smurf"
    n1, n2, nc = int(rng.integers(3, 8)), int(rng.integers(1, 4)), int(rng.integers(1, 3))
    roles = ["L1"] * n1 + ["L2"] * n2 + ["CASHOUT"] * nc

    kind = np.array(b.acc["kind"])
    is_mule = np.array(b.acc["is_mule"])
    bank = np.array(b.acc["bank"])
    age = np.array(b.acc["age_days"])
    clean = (kind == "individual") & ~is_mule
    victims_pool = np.flatnonzero(clean)

    banks = rng.integers(0, b.n_banks, len(roles))
    banks[1] = (banks[0] + 1) % b.n_banks  # always spans at least two banks
    controllers = [b.new_device() for _ in range(int(rng.integers(1, 4)))]
    members: list[int] = []
    for role, bk in zip(roles, banks):
        if rng.random() < 0.45:  # freshly opened mule account
            own = bool(rng.random() < 0.4)
            i = b.add_account(int(bk), "mule", rng.uniform(3, 45), own_device=own)
            if rng.random() < 0.4:  # a little ordinary-looking spend as cover
                k = int(rng.poisson(3))
                b.add_tx(np.full(k, i), rng.choice(b.idx("merchant"), k),
                         np.clip(rng.lognormal(np.log(300), 0.8, k), 10, 5000), b.day_times(k))
            on_controller = (not own) or rng.random() < 0.75
        else:  # rented account of a real customer: keeps its normal history
            pool = np.flatnonzero(clean & (bank == bk) & (age > 120))
            pool = pool[~np.isin(pool, members)]
            if len(pool) == 0:
                pool = victims_pool[~np.isin(victims_pool, members)]
            i = int(rng.choice(pool))
            on_controller = rng.random() < 0.75
        b.acc["is_mule"][i], b.acc["role"][i], b.acc["ring"][i] = True, role, ring_id
        if on_controller and not smurf:
            b.dev.append((i, int(rng.choice(controllers))))
        members.append(i)
    victims_pool = victims_pool[~np.isin(victims_pool, members)]

    l1, l2, co = members[:n1], members[n1:n1 + n2], members[n1 + n2:]
    if burst is None:
        burst = rng.uniform(4, 30) * 60 if fast else rng.uniform(2, 6) * DAY
    if t0 is None:
        t0 = rng.uniform(1 * DAY, b.n_days * DAY - burst - 1.5 * DAY)
    b.ring_start.append(float(t0))   # ring ids are sequential, so this list is indexed by ring id
    merchants = b.idx("merchant")
    stolen = 0.0

    def any_time(n):  # mules operate round the clock
        return rng.uniform(0, burst, n)

    for m in l1:
        nv = int(rng.integers(12, 36)) if smurf else int(rng.integers(2, 13))
        v = rng.choice(victims_pool, nv, replace=False)
        amt = (np.clip(rng.lognormal(np.log(1500), 0.6, nv), 300, 5000) if smurf
               else np.clip(rng.lognormal(np.log(9000), 0.9, nv), 1000, 100000))
        t_in = t0 + any_time(nv)
        b.add_tx(v, m, amt, t_in)
        stolen += float(amt.sum())
        delay = (rng.uniform(3, 60, nv) if fast else rng.uniform(120, 1200, nv)) * tempo
        primary = l2[int(rng.integers(len(l2)))]
        to = np.where(rng.random(nv) < 0.8, primary, rng.choice(l2, nv))
        amt2, t2 = amt * rng.uniform(0.88, 0.99, nv), t_in + delay
        b.add_tx(np.full(nv, m), to, amt2, t2)
        for dest, a2, tt in zip(to, amt2, t2):
            d3 = (rng.uniform(5, 90) if fast else rng.uniform(60, 720)) * tempo
            c = co[0] if rng.random() < 0.8 else int(rng.choice(co))
            a3, t3 = a2 * rng.uniform(0.9, 0.99), tt + d3
            b.add_tx(dest, c, a3, t3)
            if rng.random() < 0.7:  # cash out at ATMs in chunks
                k = int(np.ceil(a3 / 20000))
                b.add_tx(np.full(k, c), -1, np.full(k, a3 * 0.98 / k),
                         t3 + np.sort(rng.uniform(10, 240, k)) * tempo, CH_ATM)
            else:  # or spend on easily resold goods
                b.add_tx(c, int(rng.choice(merchants)), a3 * 0.97, t3 + rng.uniform(10, 300) * tempo)

    return {"ring": ring_id, "style": style, "members": members, "n_l1": n1, "n_l2": n2, "n_cashout": nc,
            "collectors": l1, "banks": sorted({int(b.acc["bank"][i]) for i in members}), "stolen": stolen}


# ---------------------------------------------------------------------------
def window(w: World, t_lo: float, t_hi: float) -> World:
    """A 30-day style view of a longer world: only what happened in [t_lo, t_hi).

    An account counts as a mule in the view only if its ring was active in it.
    """
    keep = (w.ts >= t_lo) & (w.ts < t_hi)
    start = w.ring_start[np.maximum(w.ring, 0)] if len(w.ring_start) else np.zeros(w.n)
    active = w.is_mule & (start >= t_lo - DAY) & (start < t_hi)
    days_after = (w.n_days * DAY - t_hi) / DAY
    return replace(w, src=w.src[keep], dst=w.dst[keep], amt=w.amt[keep], ts=w.ts[keep] - t_lo, ch=w.ch[keep],
                   is_mule=active, age_days=np.maximum(w.age_days - days_after, 1.0),
                   n_days=int(round((t_hi - t_lo) / DAY)), ring_start=w.ring_start - t_lo)


def generate_world(seed: int, n_per_bank: int = 900, n_rings: int = 10,
                   n_banks: int = 3, n_days: int = 30, ring_style: str | None = None) -> World:
    rng = np.random.default_rng(seed)
    b = Builder(rng, n_days, n_banks)
    for bank in range(n_banks):
        n_m, n_b, n_c, n_p = (int(n_per_bank * f) for f in (0.10, 0.03, 0.05, 0.08))
        n_i = n_per_bank - n_m - n_b - n_c - n_p
        for kind, n in (("individual", n_i), ("passthrough", n_p), ("collector", n_c)):
            for _ in range(n):
                new = rng.random() < 0.18
                b.add_account(bank, kind, rng.uniform(5, 90) if new else rng.uniform(90, 3000))
        for _ in range(n_m):
            b.add_account(bank, "merchant", rng.uniform(20, 90) if rng.random() < 0.1 else rng.uniform(90, 3500))
        for _ in range(n_b):
            b.add_account(bank, "business", rng.uniform(200, 5000))
    _legit_activity(b)
    for _ in range(n_rings):
        add_ring(b, style=ring_style)
    return b.finalize(seed)


def plan_ring(w: World, seed: int, t0: float, burst: float = 70.0, tempo: float = 0.3) -> tuple[World, dict, dict]:
    """Plan a ring that starts at t0 and plays out over the following minutes.

    Returns the world with the ring's accounts and devices added (they exist
    before they act), a summary, and the ring's future transactions, sorted by
    time, for the live stream to release as the clock reaches them.
    """
    rng = np.random.default_rng(seed)
    b = Builder.from_world(w, rng)
    base = len(b.tx)
    info = add_ring(b, t0=t0, style="fast", burst=burst, tempo=tempo)
    cols = [np.concatenate([t[k] for t in b.tx[base:]]) for k in range(5)]
    future = (cols[3] >= t0) & (cols[2] >= 1) & (cols[0] != cols[1])
    past = ~future
    b.tx = b.tx[:base] + [tuple(c[past] for c in cols)]  # cover spending already in the past
    order = np.argsort(cols[3][future], kind="stable")
    names = ("src", "dst", "amt", "ts", "ch")
    pending = {k: c[future][order] for k, c in zip(names, cols)}
    return b.finalize(w.seed, cut=False), info, pending


def live_legit(w: World, rng: np.random.Generator, t1: float, t2: float, rate: float) -> dict:
    """Ordinary customer activity between two instants of the simulation clock."""
    hour = (t1 % DAY) / 60
    quiet = 0.15 if (hour >= 23 or hour < 6) else 1.0
    k = int(rng.poisson(rate * (t2 - t1) * quiet))
    persons = np.flatnonzero(np.isin(w.kind, ["individual", "collector", "passthrough"]))
    merchants = np.flatnonzero(w.kind == "merchant")
    src = rng.choice(persons, k)
    what = rng.random(k)
    dst = np.where(what < 0.70, rng.choice(merchants, k), np.where(what < 0.92, rng.choice(persons, k), -1))
    amt = np.where(what < 0.70, np.clip(rng.lognormal(np.log(350), 0.9, k), 10, 20000),
                   np.where(what < 0.92, np.clip(rng.lognormal(np.log(1200), 1.0, k), 50, 50000),
                            rng.integers(1, 21, k) * 500.0))
    keep = src != dst
    ts = np.sort(rng.uniform(t1, t2, k))
    return {"src": src[keep].astype(np.int64), "dst": dst[keep].astype(np.int64), "amt": np.round(amt[keep]),
            "ts": ts[keep], "ch": np.where(dst[keep] < 0, CH_ATM, CH_UPI).astype(np.int8)}
