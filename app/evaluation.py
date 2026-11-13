"""The fuller model evaluation shown on the Model page.

Start-up only computes the headline benchmark. This module runs the slower
checks in the background: how the alert threshold trades precision for recall,
how the models hold up when the data is degraded or the fraud pattern changes,
whether a model trained on an earlier month still works on a later one, and
how well single payments are judged.

Everything here uses labelled data the models were not trained on. With the
default set-up that data is synthetic, and the page says so.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import txrisk
from .model import average_precision, prf
from .pipeline import Engine, Snapshot
from .synth import DAY, World, generate_world, window

REPORTS = Path(__file__).parent / "reports"


def _row(y: np.ndarray, pred: np.ndarray) -> dict:
    m = prf(y, pred)
    legit = int((~y).sum())
    return {"precision": m["precision"], "recall": m["recall"], "f1": m["f1"], "tp": m["tp"], "fp": m["fp"],
            "fn": m["fn"], "fp_per_1000": round(1000 * m["fp"] / max(legit, 1), 2)}


def _pooled(e: Engine, snaps: list[Snapshot], models=None) -> dict:
    md = models or e.models
    y = np.concatenate([s.world.is_mule for s in snaps])
    return {"mules": int(y.sum()), "accounts": int(len(y)),
            "rules": _row(y, np.concatenate([s.rules for s in snaps])),
            "local": _row(y, np.concatenate([s.score_local for s in snaps]) >= md.thr_local),
            "consortium": _row(y, np.concatenate([s.score for s in snaps]) >= md.thr)}


def _noisy(w: World, rng: np.random.Generator) -> World:
    """Amounts off by up to about 30% and one transaction in ten missing."""
    keep = rng.random(len(w.src)) > 0.10
    return replace(w, src=w.src[keep], dst=w.dst[keep], ts=w.ts[keep], ch=w.ch[keep],
                   amt=np.maximum(np.round(w.amt[keep] * rng.lognormal(0, 0.3, int(keep.sum()))), 1.0))


def _fewer_devices(w: World, rng: np.random.Generator) -> World:
    keep = rng.random(len(w.dev_acct)) > 0.5
    return replace(w, dev_acct=w.dev_acct[keep], dev_id=w.dev_id[keep])


def threshold_curve(e: Engine, tests) -> dict:
    y = np.concatenate([s.world.is_mule[m] for s, m in tests])
    score = np.concatenate([s.score[m] for s, m in tests])
    points = []
    for t in np.round(np.arange(0.05, 0.96, 0.05), 2):
        r = _row(y, score >= t)
        points.append({"threshold": float(t), "precision": r["precision"], "recall": r["recall"],
                       "alerts": r["tp"] + r["fp"], "fp_per_1000": r["fp_per_1000"]})
    edges = [0, 0.05, 0.2, 0.5, 0.8, 1.0001]
    calibration = []
    for lo, hi in zip(edges, edges[1:]):
        m = (score >= lo) & (score < hi)
        if m.any():
            calibration.append({"range": f"{round(lo * 100)}-{min(round(hi * 100), 100)}", "accounts": int(m.sum()),
                                "predicted": float(score[m].mean()), "observed": float(y[m].mean())})
    return {"points": points, "chosen": round(e.models.thr, 3), "calibration": calibration,
            "why": "The threshold is the score that gave the best balance of precision and recall (highest F1) on "
                   f"{e.models.info['threshold_from']}. It was fixed before any of the test data was scored."}


def payments(e: Engine, tests) -> dict:
    """How well single payments are judged, pooled over the test data."""
    y_all, ok_all, risk, risk_local, amt = [], [], [], [], []
    for s, m in tests:
        pay = e.score_payments(s)
        y, ok = txrisk.labels(s.world, pay["rows"])
        ok &= m[s.world.src[pay["rows"]]] & ~s.flagged[s.world.src[pay["rows"]]]
        y_all.append(y[ok]); risk.append(pay["risk"][ok]); risk_local.append(pay["risk_local"][ok])
        amt.append(s.world.amt[pay["rows"]][ok])
    y, risk, risk_local, amt = (np.concatenate(x) for x in (y_all, risk, risk_local, amt))
    md, total = e.models, float(amt[y].sum())
    out = {"payments": int(len(y)), "fraud_payments": int(y.sum()), "fraud_amount": round(total), "detectors": []}
    for key, label, score, thr in (("local", "Payer's bank alone", risk_local, md.thr_tx_local),
                                   ("consortium", "With consortium signals", risk, md.thr_tx)):
        r = _row(y, score >= thr)
        r.update(key=key, label=label, pr_auc=average_precision(y, score),
                 money_caught=float(amt[y & (score >= thr)].sum() / total) if total else 0.0)
        out["detectors"].append(r)
    return out


def robustness(e: Engine) -> list[dict]:
    n, seed = e.n_per_bank, e.seed + 6000
    rng = np.random.default_rng(seed)
    base = [generate_world(seed + k, n, 10) for k in range(3)]
    scenarios = [
        ("Messy data", "Amounts off by about 30% and one transaction in ten missing.", [_noisy(w, rng) for w in base]),
        ("Missing device data", "Half of all device links removed.", [_fewer_devices(w, rng) for w in base]),
        ("A fraud pattern never seen in training", "Many small payments, forwarded slowly, with no shared devices.",
         [generate_world(seed + 10 + k, n, 10, ring_style="smurf") for k in range(3)]),
        ("Smaller banks", "400 accounts per bank instead of 900.", [generate_world(seed + 20 + k, 400, 5) for k in range(3)]),
        ("Fewer mules", "3 rings per dataset instead of 10.", [generate_world(seed + 30 + k, n, 3) for k in range(3)]),
    ]
    out = [{"name": "Clean test data", "detail": "Same kind of data as the headline benchmark.",
            **_pooled(e, [e._score(w, {}) for w in base])}]
    for name, detail, worlds in scenarios:
        out.append({"name": name, "detail": detail, **_pooled(e, [e._score(w, {}) for w in worlds])})
    return out


def temporal(e: Engine) -> dict:
    """Train on the first 30 days of a 60-day history, test on the last 30."""
    long = generate_world(e.seed + 7000, int(e.n_per_bank * 5 / 3), 36, n_days=60)
    early, late = window(long, 0, 30 * DAY), window(long, 30 * DAY, 60 * DAY)
    models = e.fit(early, None, source="synthetic", data_id="temporal-check")
    s = e._score(late, {}, models=models)
    return {"train": "days 1 to 30", "test": "days 31 to 60", "train_mules": int(early.is_mule.sum()),
            **_pooled(e, [s], models)}


def real_world_report() -> dict | None:
    """Results of training on public real-world data, if `python -m app.realdata` has been run."""
    path = REPORTS / "elliptic.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def evaluate(e: Engine) -> dict:
    tests = e._tests
    out = {"synthetic": not e.train_dir, "source": e.benchmark["source"],
           "threshold": threshold_curve(e, tests), "payments": payments(e, tests)}
    if not e.train_dir:
        out["robustness"] = robustness(e)
        out["temporal"] = temporal(e)
    e._tests = None      # release the test data
    return out
