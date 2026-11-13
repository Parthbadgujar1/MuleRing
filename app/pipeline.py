"""The scoring engine: trains the models once, then scores the live world.

Account level, three detectors are compared on data none of them has seen:
  rules      static thresholds, the baseline
  local      what one bank can do alone (own behaviour + own-bank network)
  consortium behaviour + network intelligence assembled by the hub from tokens

Transaction level, every payment is scored as it arrives (see txrisk.py).

The live world moves. `advance()` steps a simulation clock, adds the
transactions that happened in that step (simulated, or received through the
ingestion API), rescores every account and reports what changed as events.

Training data is synthetic by default. Set TRAIN_DATA_DIR to train on a bank's
own records instead (see dataio.py).
"""
from __future__ import annotations

import hashlib
import itertools
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

import networkx as nx
import numpy as np

from . import config, txrisk
from .consortium import assemble, build_share_package, tokens_for
from .features import BEHAVIOUR, CONSORTIUM_ONLY, DAY, NETWORK, behaviour_features, edge_table, network_features
from .ingest import Ingestor
from .model import GlassBoxGBM, average_precision, best_f1_threshold, prf
from .rules import rules_flag
from .synth import World, generate_world, live_legit, plan_ring

LOCAL_NAMES = BEHAVIOUR + NETWORK
CONS_NAMES = BEHAVIOUR + NETWORK + CONSORTIUM_ONLY
US = BEHAVIOUR.index("uniq_senders")
STICKY = 0.7          # a flagged account stays flagged until its score falls below 70% of the threshold
NEW_FOR_SECONDS = 600  # how long an alert is labelled "New"
TX_COLS = ("src", "dst", "amt", "ts", "ch")
MODEL_KIND = "Gradient-boosted decision stumps (additive, glass-box)"


@dataclass
class Models:
    """Everything that was learned, plus the record of how."""
    m0: GlassBoxGBM            # behaviour only
    m_local: GlassBoxGBM       # behaviour + own-bank network
    m_cons: GlassBoxGBM        # behaviour + cross-bank network
    tx_local: GlassBoxGBM      # payment pattern, payer's bank alone
    tx_cons: GlassBoxGBM       # payment pattern with payee-side signals
    thr0: float = 0.5
    thr_local: float = 0.5
    thr: float = 0.5
    thr_tx_local: float = 0.5
    thr_tx: float = 0.5
    train_bins: np.ndarray | None = None   # training distribution of each feature, for drift monitoring
    holdout: np.ndarray | None = None      # accounts kept out of training when no separate validation set exists
    info: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    world: World
    tokens: np.ndarray
    index: dict
    B: np.ndarray
    X: np.ndarray            # consortium feature matrix, world order
    contrib: np.ndarray      # per-feature contributions of the consortium model
    risk0: np.ndarray
    score_local: np.ndarray
    score: np.ndarray        # consortium score
    rules: np.ndarray
    flagged: np.ndarray
    es: np.ndarray
    ed: np.ndarray
    ea: np.ndarray
    ec: np.ndarray
    rings: list = field(default_factory=list)
    ring_of: dict = field(default_factory=dict)   # account index -> ring id
    packages: list = field(default_factory=list)
    timing: dict = field(default_factory=dict)
    version: int = 0
    cache: dict = field(default_factory=dict)     # memoised API payloads for this snapshot


class Engine:
    def __init__(self, seed: int = config.SEED, n_per_bank: int = config.ACCOUNTS_PER_BANK,
                 key: bytes = config.HMAC_KEY, train_dir: str | None = None):
        self.seed, self.n_per_bank, self.key = seed, n_per_bank, key
        self.train_dir = config.TRAIN_DATA_DIR if train_dir is None else train_dir
        self.lock = threading.RLock()
        self.status: dict[str, str] = {}     # token -> held | confirmed | cleared
        self.version = 0
        self.snap: Snapshot | None = None
        self._tokens: tuple | None = None
        self.ring_ids: dict[str, str] = {}
        self.ingest = Ingestor()
        self.started = time.time()
        self.counters = {"ticks": 0, "transactions": 0, "tick_ms_total": 0.0, "tick_ms_max": 0.0, "payments_flagged": 0}
        self._pay_ids = itertools.count(1)
        self.evaluation: dict | None = None  # filled in the background by evaluation.py
        self._train()
        self.benchmark: dict | None = None
        self.reset()

    # shorthand used throughout the app
    m0 = property(lambda self: self.models.m0)
    m_local = property(lambda self: self.models.m_local)
    m_cons = property(lambda self: self.models.m_cons)
    thr = property(lambda self: self.models.thr)
    thr_local = property(lambda self: self.models.thr_local)
    train_info = property(lambda self: self.models.info)

    # ------------------------------------------------------------------ training
    def _train(self) -> None:
        if self.train_dir:
            from .dataio import fingerprint, load_world
            train = load_world(self.train_dir)
            self.models = self.fit(train, None, source="files",
                                   data_id=f"files-{fingerprint(self.train_dir)}")
            self._train_world = train
        else:
            train = generate_world(self.seed + 1000, int(self.n_per_bank * 5 / 3), 20)
            val = generate_world(self.seed + 2000, self.n_per_bank, 10)
            self.models = self.fit(train, val, source="synthetic", data_id=f"synthetic-seed-{self.seed}")
            self._train_world = None

    def fit(self, train: World, val: World | None, source: str = "synthetic", data_id: str = "") -> Models:
        """Train every model on `train`. Thresholds come from `val`, or from a 30% holdout when there is none."""
        t = time.perf_counter()
        y, n = train.is_mule, train.n
        rng = np.random.default_rng(self.seed)
        fold = rng.integers(0, 3, n)
        rows = np.ones(n, bool) if val is not None else np.random.default_rng(self.seed + 1).random(n) < 0.7
        B = behaviour_features(n, train.src, train.dst, train.amt, train.ts, train.age_days)
        # Cross-fitted behavioural risk, so the network features seen in training
        # are as noisy as they will be on new data.
        risk = np.zeros(n)
        for f in range(3):
            fit_rows = rows & (fold != f)
            risk[fold == f] = GlassBoxGBM().fit(B[fit_rows], y[fit_rows], BEHAVIOUR).predict(B[fold == f])
        m0 = GlassBoxGBM().fit(B[rows], y[rows], BEHAVIOUR)
        risk[~rows] = m0.predict(B[~rows])
        es, ed, ea, _ = edge_table(n, train.src, train.dst, train.amt)
        args = (n, es, ed, ea, risk, B[:, US], train.dev_acct, train.dev_id, train.bank)
        Xl = np.hstack([B, network_features(*args, consortium=False)])
        Xc = np.hstack([B, network_features(*args, consortium=True)])
        m_local = GlassBoxGBM().fit(Xl[rows], y[rows], LOCAL_NAMES)
        m_cons = GlassBoxGBM().fit(Xc[rows], y[rows], CONS_NAMES)

        # Payment-level models, trained on payments sent by ordinary customers.
        p, T = txrisk.features(train)
        ty, ok = txrisk.labels(train, p)
        ok &= rows[train.src[p]]
        # Fraud payments are rare, so train on all of them plus a sample of ordinary ones,
        # then shift the intercept back so the scores still reflect the true base rate.
        neg = np.flatnonzero(ok & ~ty)
        sample = rng.choice(neg, size=min(len(neg), 20000), replace=False)
        use = np.concatenate([np.flatnonzero(ok & ty), sample])
        shift = float(np.log(len(sample) / max(len(neg), 1)))
        tx_cons = GlassBoxGBM(n_rounds=250).fit(T[use], ty[use], txrisk.NAMES)
        tx_local = GlassBoxGBM(n_rounds=250).fit(txrisk.hide_other_banks(T, train, p)[use], ty[use], txrisk.NAMES)
        tx_cons.intercept += shift
        tx_local.intercept += shift

        bins = m_cons._bin(Xc[rows])
        K = m_cons.max_bins + 1
        train_bins = np.vstack([np.bincount(bins[:, j], minlength=K) / len(bins) for j in range(bins.shape[1])])
        models = Models(m0=m0, m_local=m_local, m_cons=m_cons, tx_local=tx_local, tx_cons=tx_cons, train_bins=train_bins)

        # Operating thresholds are chosen on data the models were not fitted to.
        v = self._score(val if val is not None else train, {}, models=models)
        vw = v.world
        pick = np.ones(vw.n, bool) if val is not None else ~rows
        models.thr0 = best_f1_threshold(vw.is_mule[pick], v.risk0[pick])
        models.thr_local = best_f1_threshold(vw.is_mule[pick], v.score_local[pick])
        models.thr = best_f1_threshold(vw.is_mule[pick], v.score[pick])
        pay = self.score_payments(v, models)
        vy, vok = txrisk.labels(vw, pay["rows"])
        vok &= pick[vw.src[pay["rows"]]]
        if vy[vok].any():
            models.thr_tx = best_f1_threshold(vy[vok], pay["risk"][vok])
            models.thr_tx_local = best_f1_threshold(vy[vok], pay["risk_local"][vok])

        digest = hashlib.sha256()
        for m in (m0, m_local, m_cons, tx_cons):
            digest.update(m.shape.tobytes())
        digest.update(f"{data_id}|{models.thr:.6f}".encode())
        models.info = {
            "model_version": "mrs-" + digest.hexdigest()[:12], "model_kind": MODEL_KIND,
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_source": source, "data_id": data_id,
            "threshold_from": "a separate validation dataset" if val is not None else "a 30% holdout of the supplied accounts",
            "train_accounts": int(rows.sum()), "train_mules": int(y[rows].sum()),
            "train_transactions": int(len(train.src)), "train_payments": int(ok.sum()),
            "train_fraud_payments": int(ty[ok].sum()),
            "validation_accounts": int(pick.sum()), "seconds": round(time.perf_counter() - t, 2),
            "threshold": round(models.thr, 3), "threshold_local": round(models.thr_local, 3),
            "threshold_behaviour": round(models.thr0, 3), "threshold_payment": round(models.thr_tx, 3),
            "seed": self.seed, "key_version": config.KEY_VERSION,
        }
        if val is None:
            models.holdout = pick
        return models

    # ------------------------------------------------------------------- scoring
    def _tokens_for(self, world: World) -> np.ndarray:
        """Tokens only change when accounts are added, so keep the last set."""
        if self._tokens is None or self._tokens[0] is not world.acct_no:
            self._tokens = (world.acct_no, tokens_for(world, self.key))
        return self._tokens[1]

    def _score(self, world: World, status: dict[str, str], live: bool = False, models: Models | None = None) -> Snapshot:
        t0 = time.perf_counter()
        md = models or self.models
        n = world.n
        tokens = self._tokens_for(world) if live else tokens_for(world, self.key)
        index = {t: i for i, t in enumerate(tokens)}
        B = behaviour_features(n, world.src, world.dst, world.amt, world.ts, world.age_days)
        risk0 = md.m0.predict(B)
        es, ed, ea, ec = edge_table(n, world.src, world.dst, world.amt)

        # Analyst decisions are shared intelligence: a confirmed mule counts as
        # certain risk for its counterparties, a cleared account as none.
        override = {t: (1.0 if s == "confirmed" else 0.0) for t, s in status.items()
                    if s in ("confirmed", "cleared") and t in index}
        risk_seen = risk0.copy()
        for tok, value in override.items():
            risk_seen[index[tok]] = value

        local = network_features(n, es, ed, ea, risk_seen, B[:, US], world.dev_acct, world.dev_id, world.bank,
                                 consortium=False)
        score_local = md.m_local.predict(np.hstack([B, local]))

        # Each bank builds its package; the hub sees nothing else.
        packages = [build_share_package(world, b, tokens, B, risk0, es, ed, ea, ec, self.key)
                    for b in range(world.n_banks)]
        hub = assemble(packages)
        Xh = np.hstack([hub.behaviour, hub.network(override)])
        perm = np.fromiter((index[t] for t in hub.tokens), dtype=np.int64, count=n)
        X = np.empty_like(Xh)
        X[perm] = Xh
        contrib = md.m_cons.contributions(X)
        score = 1.0 / (1.0 + np.exp(-(md.m_cons.intercept + contrib.sum(axis=1))))

        flagged = score >= md.thr
        if live and self.snap is not None:  # hysteresis: no flickering around the threshold
            was = self.snap.flagged
            m = min(len(was), n)
            flagged[:m] |= was[:m] & (score[:m] >= md.thr * STICKY)
        for tok, s in status.items():
            if tok in index:
                flagged[index[tok]] = s != "cleared" and (flagged[index[tok]] or s == "confirmed")

        snap = Snapshot(world=world, tokens=tokens, index=index, B=B, X=X, contrib=contrib, risk0=risk0,
                        score_local=score_local, score=score, rules=rules_flag(B), flagged=flagged,
                        es=es, ed=ed, ea=ea, ec=ec, packages=packages)
        snap.rings, snap.ring_of = detect_rings(snap, self.ring_ids if live else None)
        if live:
            self.version += 1
            snap.version = self.version
        snap.timing = {"accounts": int(n), "transactions": int(len(world.src)),
                       "seconds": round(time.perf_counter() - t0, 3)}
        return snap

    def score_payments(self, s: Snapshot, models: Models | None = None, since: float | None = None) -> dict:
        """Risk of account-to-account payments in a snapshot: all of them, or those at or after `since`."""
        md = models or self.models
        w = s.world
        rows, T = txrisk.features(w)
        if since is not None:   # features need the full history; scoring does not
            pick = w.ts[rows] >= since
            rows, T = rows[pick], T[pick]
        src, dst = w.src[rows], w.dst[rows]
        contrib = md.tx_cons.contributions(T)
        pattern = 1.0 / (1.0 + np.exp(-(md.tx_cons.intercept + contrib.sum(axis=1))))
        pattern_local = md.tx_local.predict(txrisk.hide_other_banks(T, w, rows))
        same = w.bank[src] == w.bank[dst]
        return {"rows": rows, "X": T, "contrib": contrib, "pattern": pattern, "pattern_local": pattern_local,
                "risk": txrisk.combine(pattern, s.score[dst]),
                "risk_local": txrisk.combine(pattern_local, np.where(same, s.score_local[dst], 0.0))}

    def rescore(self) -> list[dict]:
        """Rescore after an analyst decision. Returns what changed."""
        prev = self.snap
        self.snap = self._score(prev.world, self.status, live=True)
        return self._diff(prev, self.snap)

    # --------------------------------------------------------------- live world
    def reset(self) -> None:
        self.status = {}
        self.snap = None
        self.ring_ids = {}
        self.flag_time: dict[str, float] = {}    # token -> wall-clock time it was first flagged live
        self.pending: dict | None = None         # future transactions of an attack in progress
        self.attack: dict | None = None
        self.attacks = 0
        self.payments: deque = deque(maxlen=300)  # payments recommended for review, newest first
        self.ingest.reset()
        self.live_rng = np.random.default_rng(self.seed + 9000)
        if config.LIVE_DATA_DIR:     # real history; new activity arrives only through the ingestion API
            from .dataio import load_world
            world = load_world(config.LIVE_DATA_DIR)
            self.simulated = False
        else:
            world = generate_world(self.seed, self.n_per_bank, 10)
            self.simulated = True
        # The simulation clock starts at 10:00 on the day after the historical window.
        self.sim_now = world.n_days * DAY + 10 * 60
        self.day0 = datetime.combine(date.today(), dtime.min) - timedelta(days=world.n_days)
        self.legit_rate = len(world.src) / (world.n_days * DAY) if self.simulated else 0.0
        self.snap = self._score(world, {}, live=True)
        self._seed_payments(self.snap)
        if self.benchmark is None:   # depends only on the training data and seed, so it is computed once
            self.benchmark = self._benchmark(self.snap)

    def when(self, ts: float) -> str:
        """Simulation minutes -> timestamp on the simulation clock."""
        return (self.day0 + timedelta(minutes=float(ts))).isoformat(timespec="minutes")

    def is_new(self, token: str) -> bool:
        t = self.flag_time.get(token)
        return t is not None and time.time() - t < NEW_FOR_SECONDS

    @property
    def attack_active(self) -> bool:
        return self.attack is not None and not self.attack["done"]

    def launch_attack(self) -> list[dict]:
        """Plan a new mule ring. Its payments are released by `advance()` as time passes."""
        if not self.simulated:
            raise RuntimeError("Simulated attacks are not available when running on supplied data")
        if self.attack_active:
            raise RuntimeError("An attack is already in progress")
        self.attacks += 1
        world, info, pending = plan_ring(self.snap.world, self.seed + 5000 + self.attacks, self.sim_now + 1.0)
        members = np.array(info["members"])
        collectors = np.array(info["collectors"])
        victim_tx = np.isin(pending["dst"], collectors) & ~np.isin(pending["src"], members)
        self.pending = {**pending, "victim": victim_tx, "i": 0}
        prev = self.snap
        self.snap = self._score(world, self.status, live=True)
        self.attack = {
            "n": self.attacks, "members": members, "collectors": set(collectors.tolist()),
            "banks": [config.BANKS[b][1] for b in info["banks"]],
            "total": int(len(pending["ts"])), "executed": 0,
            "planned": float(pending["amt"][victim_tx].sum()), "paid_in": 0.0,
            "victim_payments": int(victim_tx.sum()), "payments_seen": 0, "payments_flagged": 0, "payments_flagged_amount": 0.0,
            "started": self.sim_now, "first_alert_min": None, "paid_before_alert": None,
            "flagged": 0, "ring": None, "done": False, "idle": 0,
        }
        return self._diff(prev, self.snap) + [{"type": "attack", **self._attack_view()}]

    def _attack_view(self) -> dict:
        a = self.attack
        return {k: a[k] for k in ("n", "banks", "total", "executed", "planned", "paid_in", "first_alert_min",
                                  "paid_before_alert", "flagged", "ring", "done", "victim_payments", "payments_seen",
                                  "payments_flagged", "payments_flagged_amount")} | {"members": len(a["members"])}

    def advance(self, minutes: float) -> list[dict]:
        """Step the simulation clock and return events describing what changed."""
        started = time.perf_counter()
        prev, w = self.snap, self.snap.world
        t1, t2 = self.sim_now, self.sim_now + minutes
        self.sim_now = t2
        new = live_legit(w, self.live_rng, t1, t2, self.legit_rate) if self.simulated else \
            {k: np.zeros(0, dtype=w.__dict__[k].dtype) for k in TX_COLS}
        if self.pending is not None:
            p = self.pending
            j = int(np.searchsorted(p["ts"], t2, side="right"))
            if j > p["i"]:
                sl = slice(p["i"], j)
                new = {k: np.concatenate([new[k], p[k][sl]]) for k in TX_COLS}
                self.attack["executed"] = j
                self.attack["paid_in"] += float(p["amt"][sl][p["victim"][sl]].sum())
                self.attack["payments_seen"] += int(p["victim"][sl].sum())
                p["i"] = j
        received, removals = self.ingest.drain(t1, t2)          # events posted by bank adapters
        if len(received["ts"]):
            new = {k: np.concatenate([new[k], received[k]]) for k in TX_COLS}
        events: list[dict] = []
        fresh = slice(0, 0)
        if len(new["ts"]) or removals:
            keep = w.ts >= t2 - w.n_days * DAY   # the window slides: forget what is older than 30 days
            for r in removals:                   # a reversed payment is taken out again
                keep &= ~((w.src == r["src"]) & (w.dst == r["dst"]) & (w.ts == r["ts"]))
            cols = {k: np.concatenate([getattr(w, k)[keep], new[k]]) for k in TX_COLS}
            fresh = slice(int(keep.sum()), len(cols["ts"]))
            self.snap = self._score(replace(w, **cols), self.status, live=True)
            events = self._diff(prev, self.snap)
            events += self._review_payments(self.snap, fresh)
        if self.attack_active:
            events.append(self._attack_progress())
        took = (time.perf_counter() - started) * 1000
        c = self.counters
        c["ticks"] += 1
        c["transactions"] += int(len(new["ts"]))
        c["tick_ms_total"] += took
        c["tick_ms_max"] = max(c["tick_ms_max"], took)
        events.append({"type": "tick", "new_tx": int(len(new["ts"])), "received": int(len(received["ts"])), "fresh": fresh})
        return events

    # ---------------------------------------------------------------- payments
    def _payment_record(self, s: Snapshot, pay: dict, k: int) -> dict:
        w = s.world
        row = int(pay["rows"][k])
        src, dst = int(w.src[row]), int(w.dst[row])
        return {
            "id": next(self._pay_ids), "src": src, "dst": dst, "amount": float(w.amt[row]), "ts": float(w.ts[row]),
            "ch": int(w.ch[row]), "risk": float(pay["risk"][k]), "pattern": float(pay["pattern"][k]),
            "risk_local": float(pay["risk_local"][k]), "payee_score": float(s.score[dst]),
            "reasons": txrisk.reasons(pay["X"][k], pay["contrib"][k], float(s.score[dst]), bool(s.flagged[dst]),
                                      bool(w.bank[src] != w.bank[dst])),
        }

    def _risky(self, s: Snapshot, pay: dict, mask: np.ndarray) -> np.ndarray:
        """Payments worth a review: high risk, and not sent by an account that is itself flagged."""
        w = s.world
        return np.flatnonzero(mask & (pay["risk"] >= self.models.thr_tx) & ~s.flagged[w.src[pay["rows"]]])

    def _seed_payments(self, s: Snapshot) -> None:
        """Start the review list with the last three days of history."""
        pay = self.score_payments(s, since=s.world.ts.max() - 3 * DAY)
        for k in self._risky(s, pay, np.ones(len(pay["rows"]), bool)):
            self.payments.appendleft(self._payment_record(s, pay, int(k)))

    def _review_payments(self, s: Snapshot, fresh: slice) -> list[dict]:
        """Score the payments that just arrived and report the ones to review."""
        if fresh.stop <= fresh.start:
            return []
        pay = self.score_payments(s, since=float(s.world.ts[fresh].min()))
        events = []
        for k in self._risky(s, pay, pay["rows"] >= fresh.start):
            rec = self._payment_record(s, pay, int(k))
            self.payments.appendleft(rec)
            self.counters["payments_flagged"] += 1
            a = self.attack
            if a is not None and not a["done"] and rec["dst"] in a["collectors"]:
                a["payments_flagged"] += 1
                a["payments_flagged_amount"] += rec["amount"]
            events.append({"type": "payment", "rec": rec})
        return events

    def _attack_progress(self) -> dict:
        a, s = self.attack, self.snap
        a["flagged"] = int(s.flagged[a["members"]].sum())
        if a["flagged"] and a["first_alert_min"] is None:
            a["first_alert_min"] = round(self.sim_now - a["started"], 1)
            a["paid_before_alert"] = a["paid_in"]
        rings = Counter(s.ring_of[int(i)] for i in a["members"] if int(i) in s.ring_of)
        a["ring"] = rings.most_common(1)[0][0] if rings else None
        if a["executed"] >= a["total"]:
            a["idle"] += 1
            a["done"] = a["idle"] >= 2
            if a["done"]:
                self.pending = None
        return {"type": "attack", **self._attack_view()}

    def _diff(self, prev: Snapshot, cur: Snapshot) -> list[dict]:
        """Compare two snapshots and describe the differences as events."""
        events = []
        was = set(prev.tokens[prev.flagged].tolist())
        now_flagged = set(cur.tokens[cur.flagged].tolist())
        for tok in sorted(now_flagged - was, key=lambda t: -cur.score[cur.index[t]]):
            self.flag_time[tok] = time.time()
            events.append({"type": "alert", "kind": "new", "token": tok})
        for tok in sorted(was - now_flagged):
            if tok in cur.index and self.status.get(tok) != "cleared":
                events.append({"type": "alert", "kind": "resolved", "token": tok})
        before = {r["id"]: len(r["members_idx"]) for r in prev.rings}
        for r in cur.rings:
            if r["id"] not in before:
                events.append({"type": "ring", "kind": "new", "ring": r["id"]})
            elif len(r["members_idx"]) > before[r["id"]]:
                events.append({"type": "ring", "kind": "grew", "ring": r["id"]})
        return events

    # ---------------------------------------------------------------- monitoring
    def drift(self) -> list[dict]:
        """Population stability index of each feature: has live data moved away from the training data?"""
        md, s = self.models, self.snap
        bins = md.m_cons._bin(s.X)
        K = md.m_cons.max_bins + 1
        out = []
        for j, name in enumerate(CONS_NAMES):
            live = np.bincount(bins[:, j], minlength=K) / len(bins)
            a, e = np.maximum(live, 1e-4), np.maximum(md.train_bins[j], 1e-4)
            psi = float(((a - e) * np.log(a / e)).sum())
            out.append({"feature": name, "psi": round(psi, 3),
                        "status": "stable" if psi < 0.1 else "watch" if psi < 0.25 else "drifted"})
        return sorted(out, key=lambda d: -d["psi"])

    def metrics(self) -> dict:
        c = self.counters
        return {"uptime_seconds": int(time.time() - self.started), "ticks": c["ticks"],
                "transactions_processed": c["transactions"],
                "tick_ms_avg": round(c["tick_ms_total"] / c["ticks"], 1) if c["ticks"] else 0.0,
                "tick_ms_max": round(c["tick_ms_max"], 1), "payments_flagged": c["payments_flagged"],
                "accounts": self.snap.timing["accounts"], "transactions_in_window": self.snap.timing["transactions"],
                "alerts": int(self.snap.flagged.sum()), "rings": len(self.snap.rings),
                "snapshot_version": self.snap.version, "simulated_stream": self.simulated}

    # ---------------------------------------------------------------- benchmark
    def test_snapshots(self, live: Snapshot, extra_worlds: int = 4) -> list[tuple[Snapshot, np.ndarray]]:
        """Scored data the models never trained on, each with the rows that may be judged."""
        if self.train_dir:   # supplied data: judge on the accounts held out of training
            s = self._score(self._train_world, {})
            return [(s, self.models.holdout)]
        snaps = [live] + [self._score(generate_world(self.seed + 3000 + k, self.n_per_bank, 10), {})
                          for k in range(extra_worlds)]
        return [(s, np.ones(s.world.n, bool)) for s in snaps]

    def _benchmark(self, live: Snapshot) -> dict:
        """Accuracy on unseen data, pooled, using its labels.

        With synthetic training this is the live world plus four more independent
        ones, so the headline numbers do not hinge on one lucky or unlucky draw.
        With supplied data it is the 30% of accounts held out of training.
        """
        tests = self.test_snapshots(live)
        self._tests = tests                      # kept for the fuller evaluation, then released
        cat = lambda f: np.concatenate([f(s)[m] for s, m in tests])
        y = cat(lambda s: s.world.is_mule)
        age, kind = cat(lambda s: s.world.age_days), cat(lambda s: s.world.kind)
        risk0, score_local, score = cat(lambda s: s.risk0), cat(lambda s: s.score_local), cat(lambda s: s.score)
        md = self.models
        preds = {"rules": cat(lambda s: s.rules), "behaviour": risk0 >= md.thr0,
                 "local": score_local >= md.thr_local, "consortium": score >= md.thr}
        scores = {"behaviour": risk0, "local": score_local, "consortium": score}
        labels = {"rules": "Rules only", "behaviour": "Behaviour only, no network", "local": "Single bank, on its own",
                  "consortium": "Consortium"}
        n_legit = int((~y).sum())
        detectors = []
        for k, pred in preds.items():
            m = prf(y, pred)
            m.update(key=k, label=labels[k], tn=n_legit - m["fp"],
                     fp_per_1000=round(1000 * m["fp"] / max(n_legit, 1), 2))
            if k in scores:
                m["pr_auc"] = average_precision(y, scores[k])
            detectors.append(m)

        legit = ~y
        segments = [
            ("New-to-bank customers (under 90 days)", legit & (age < 90)),
            ("Collection agents (self-help group, chit fund, rent)", legit & (kind == "collector")),
            ("Households passing on salary or remittances", legit & (kind == "passthrough")),
            ("Merchants and businesses", legit & np.isin(kind, ["merchant", "business"])),
            ("All legitimate customers", legit),
        ]
        fairness = [{"segment": name, "accounts": int(m.sum()),
                     **{k: int((preds[k] & m).sum()) for k in ("rules", "local", "consortium")}}
                    for name, m in segments if m.any()]

        n_true, recovered, visible = 0, 0, []
        for s, _ in tests:
            w = s.world
            found = [set(r["members_idx"]) for r in s.rings]
            for rid in np.unique(w.ring[w.ring >= 0]):
                members = np.flatnonzero(w.ring == rid)
                best = max((len(set(members.tolist()) & f) for f in found), default=0)
                n_true += 1
                recovered += best / len(members) >= 0.6
                visible.append(np.bincount(w.bank[members], minlength=w.n_banks).max() / len(members))
        source = (f"{len(tests)} synthetic test datasets the models never saw during training" if not self.train_dir
                  else "the 30% of supplied accounts held out of training")
        return {
            "detectors": detectors, "fairness": fairness, "worlds": len(tests), "source": source,
            "synthetic": not self.train_dir,
            "rings_true": n_true, "rings_recovered": int(recovered),
            "max_single_bank_share": float(np.mean(visible)) if visible else 0.0,
            "mules": int(y.sum()), "accounts": int(len(y)),
            "extra_mules_from_sharing": int((y & ~preds["local"] & preds["consortium"]).sum()),
        }


# ---------------------------------------------------------------------------
# Ring detection
# ---------------------------------------------------------------------------
def detect_rings(s: Snapshot, registry: dict | None = None) -> tuple[list, dict]:
    """Group flagged accounts that move money between each other or share a device.

    `registry` (token -> ring id) keeps a ring's id stable while it grows.
    """
    w, f = s.world, s.flagged
    g = nx.Graph()
    g.add_nodes_from(np.flatnonzero(f).tolist())
    both = f[s.es] & f[s.ed]
    for a, b, amt in zip(s.es[both].tolist(), s.ed[both].tolist(), s.ea[both].tolist()):
        g.add_edge(a, b, weight=g.get_edge_data(a, b, {}).get("weight", 0.0) + amt)
    on = f[w.dev_acct]
    order = np.argsort(w.dev_id[on], kind="stable")
    da, dd = w.dev_acct[on][order].tolist(), w.dev_id[on][order].tolist()
    for i in range(1, len(da)):
        if dd[i] == dd[i - 1] and da[i] != da[i - 1] and not g.has_edge(da[i], da[i - 1]):
            g.add_edge(da[i], da[i - 1], weight=1.0, device=True)

    groups = []
    for comp in nx.connected_components(g):
        if len(comp) > 25:  # split unusually large clusters into communities
            groups += nx.community.louvain_communities(g.subgraph(comp), weight="weight", seed=1)
        else:
            groups.append(comp)
    groups = sorted((c for c in groups if len(c) >= 3), key=len, reverse=True)

    rings, ring_of, taken = [], {}, set()
    p2a = w.dst >= 0
    dst0 = np.where(p2a, w.dst, 0)
    for comp in groups:
        members = sorted(comp, key=lambda i: -s.score[i])
        mask = np.zeros(w.n, bool)
        mask[members] = True
        inside = mask[s.es] & mask[s.ed]
        # The ring's active window: from two days before its first internal transfer
        # to its last one. Money arriving from outside in that window is what the
        # ring collected; older history of a rented account is left out.
        tx_inside = p2a & mask[w.src] & mask[dst0]
        t1, t2 = (w.ts[tx_inside].min(), w.ts[tx_inside].max()) if tx_inside.any() else (w.ts.min(), w.ts.max())
        live = (w.ts >= t1 - 2 * DAY) & (w.ts <= t2 + DAY)
        tx_in = p2a & mask[dst0] & ~mask[w.src] & live
        tx_out = p2a & mask[w.src] & ~mask[dst0] & live & (w.ts >= t1)
        atm = ~p2a & mask[w.src] & live & (w.ts >= t1)
        paid = np.bincount(w.src[tx_in], w.amt[tx_in], minlength=w.n)
        got = np.bincount(dst0[tx_out], w.amt[tx_out], minlength=w.n)
        senders = np.flatnonzero(paid > 0)
        receivers = np.flatnonzero(got > 0)
        in_ext = np.bincount(dst0[tx_in], w.amt[tx_in], minlength=w.n)
        in_ring = np.bincount(s.ed[inside], s.ea[inside], minlength=w.n)
        out_ring = np.bincount(s.es[inside], s.ea[inside], minlength=w.n)
        roles = {}
        for i in members:
            if out_ring[i] == 0 and in_ring[i] > 0:
                roles[i] = "Exit point"
            elif in_ext[i] >= in_ring[i] and out_ring[i] > 0:
                roles[i] = "Collector"
            elif in_ring[i] > 0 or out_ring[i] > 0:
                roles[i] = "Mover"
            else:
                roles[i] = "Linked by device"
        toks = [str(s.tokens[i]) for i in members]
        ring_id = None
        if registry is not None:  # keep the id most members already carry
            known = Counter(registry[t] for t in toks if t in registry and registry[t] not in taken)
            ring_id = known.most_common(1)[0][0] if known else None
        if ring_id is None:
            ring_id = "RING-" + min(toks)[4:10].upper()
            k = 0
            while ring_id in taken:  # two rings must never share an id
                k += 1
                ring_id = "RING-" + sorted(toks)[k % len(toks)][4:10].upper()
        taken.add(ring_id)
        for i, t in zip(members, toks):
            ring_of[i] = ring_id
            if registry is not None:
                registry[t] = ring_id
        rings.append({
            "id": ring_id, "members_idx": members, "roles": roles,
            "banks": sorted({int(w.bank[i]) for i in members}),
            "funnelled": float(w.amt[tx_in].sum()), "senders": int(len(senders)),
            "sender_idx": senders[np.argsort(-paid[senders])].tolist(),
            "receiver_idx": receivers[np.argsort(-got[receivers])].tolist(),
            "cash_withdrawn": float(w.amt[atm].sum()), "sent_onward": float(w.amt[tx_out].sum()),
            "first": float(t1), "last": float(t2),
            "mean_score": float(s.score[members].mean()),
        })
    rings.sort(key=lambda r: -r["funnelled"])
    return rings, ring_of
