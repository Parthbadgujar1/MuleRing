"""Train and test the same model on public real-world data.

No public dataset of labelled mule accounts across Indian banks exists, so the
live demonstration runs on synthetic data. To check that the method itself is
sound on real data, this module uses the Elliptic data set: about 200,000 real
Bitcoin transactions joined by 234,000 real payment flows, of which about
46,000 carry a label ("illicit" for scams, ransomware and similar; "licit" for
exchanges, miners and so on), spread over 49 consecutive time steps.

What carries over from the product:
  * the same glass-box gradient-boosted model
  * the same two-stage idea: score each node on its own features, then add
    network features built from the risk of the nodes it pays and is paid by
  * a strict split in time: train on earlier steps, test on later ones
  * the sharing question: the graph is divided among three imaginary
    institutions, and the network features are built once from what a single
    institution can see and once from the whole graph

What does not carry over: the features are anonymised by the publisher, so
there are no plain-language reason codes here, and Bitcoin is not UPI.

    python -m app.realdata download --yes   fetch the data (about 146 MB) into data/elliptic
    python -m app.realdata run              train, test and write app/reports/elliptic.json

The data set is published by Elliptic under CC BY-NC-ND 4.0: research use,
not for redistribution. It is kept out of the repository; only the summary
report is committed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .model import GlassBoxGBM, average_precision, best_f1_threshold, prf

SOURCE = "https://data.pyg.org/datasets/elliptic/"
FILES = ["elliptic_txs_features.csv", "elliptic_txs_classes.csv", "elliptic_txs_edgelist.csv"]
DATA = Path("data") / "elliptic"
REPORT = Path(__file__).parent / "reports" / "elliptic.json"
N_LOCAL = 93           # the publisher's features about the transaction itself; the remaining 72 summarise its neighbours
TRAIN_UNTIL, VALIDATE_UNTIL = 29, 34   # time steps 1-29 train, 30-34 choose the threshold, 35-49 test


def download(folder: Path = DATA) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = folder / name
        if target.exists():
            print(f"have {name}")
            continue
        archive = folder / (name + ".zip")
        print(f"fetching {SOURCE}{name}.zip ...")
        urllib.request.urlretrieve(SOURCE + name + ".zip", archive)
        with zipfile.ZipFile(archive) as z:
            member = next(m for m in z.namelist() if m.endswith(name))
            with z.open(member) as src, target.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
        archive.unlink()
        print(f"  saved {target} ({target.stat().st_size / 1e6:.0f} MB)")


def load(folder: Path = DATA) -> dict:
    cache = folder / "elliptic.npz"
    if cache.exists():
        with np.load(cache) as z:
            return {k: z[k] for k in z.files}
    for name in FILES:
        if not (folder / name).exists():
            raise FileNotFoundError(f"{folder / name} is missing. Run: python -m app.realdata download --yes")
    print("reading features (this takes a minute the first time) ...")
    raw = np.loadtxt(folder / FILES[0], delimiter=",", dtype=np.float64)
    ids, step, X = raw[:, 0].astype(np.int64), raw[:, 1].astype(np.int64), raw[:, 2:].astype(np.float32)
    index = {int(t): i for i, t in enumerate(ids)}
    y = np.full(len(ids), -1, dtype=np.int64)        # -1 unknown, 0 licit, 1 illicit
    with (folder / FILES[1]).open(encoding="utf-8") as f:
        next(f)
        for line in f:
            tx, cls = line.strip().split(",")
            cls = cls.strip('"')
            if cls in ("1", "2"):
                y[index[int(tx)]] = 1 if cls == "1" else 0
    edges = np.loadtxt(folder / FILES[2], delimiter=",", dtype=np.int64, skiprows=1)
    es = np.fromiter((index[int(a)] for a in edges[:, 0]), dtype=np.int64, count=len(edges))
    ed = np.fromiter((index[int(b)] for b in edges[:, 1]), dtype=np.int64, count=len(edges))
    out = {"ids": ids, "step": step, "X": X, "y": y, "es": es, "ed": ed}
    np.savez_compressed(cache, **out)
    return out


def network_features(n: int, es: np.ndarray, ed: np.ndarray, risk: np.ndarray) -> np.ndarray:
    """Risk of the nodes a node pays and is paid by: the same idea as the product's network features."""
    def mean(of, at):
        total = np.bincount(at, risk[of], minlength=n)
        count = np.bincount(at, minlength=n)
        return np.where(count > 0, total / np.maximum(count, 1), np.nan)
    hot = risk >= 0.5
    in_deg = np.bincount(ed, minlength=n).astype(float)
    out_deg = np.bincount(es, minlength=n).astype(float)
    risky = np.bincount(es, hot[ed], minlength=n) + np.bincount(ed, hot[es], minlength=n)
    funnel = np.bincount(ed, hot[es] * in_deg[es], minlength=n)
    return np.column_stack([mean(ed, es), mean(es, ed), risky, funnel, in_deg, out_deg])


NET_NAMES = ["nbr_out_risk", "nbr_in_risk", "risky_nbrs", "upstream_funnel", "in_degree", "out_degree"]


def _metrics(y, score, thr) -> dict:
    m = prf(y, score >= thr)
    return {"precision": round(m["precision"], 4), "recall": round(m["recall"], 4), "f1": round(m["f1"], 4),
            "pr_auc": round(average_precision(y, score), 4), "tp": m["tp"], "fp": m["fp"], "fn": m["fn"]}


def run(folder: Path = DATA, rounds: int = 300) -> dict:
    t0 = time.perf_counter()
    d = load(folder)
    X, y, step, es, ed = d["X"].astype(np.float64), d["y"], d["step"], d["es"], d["ed"]
    n = len(y)
    labelled = y >= 0
    train = labelled & (step <= TRAIN_UNTIL)
    val = labelled & (step > TRAIN_UNTIL) & (step <= VALIDATE_UNTIL)
    test = labelled & (step > VALIDATE_UNTIL)
    yb = y == 1
    local = X[:, :N_LOCAL]
    local_names = [f"local_{j + 1}" for j in range(N_LOCAL)]
    print(f"{n} transactions, {len(es)} flows; train {int(train.sum())} labelled "
          f"({int(yb[train].sum())} illicit), test {int(test.sum())} ({int(yb[test].sum())} illicit)")

    # Stage 1: risk from a node's own features. Cross-fitted on the training rows so the
    # network features built from it are as noisy in training as they will be later.
    rng = np.random.default_rng(7)
    fold = rng.integers(0, 3, n)
    risk = np.zeros(n)
    for f in range(3):
        fit = train & (fold != f)
        risk[train & (fold == f)] = GlassBoxGBM(n_rounds=rounds).fit(local[fit], yb[fit], local_names) \
            .predict(local[train & (fold == f)])
    stage1 = GlassBoxGBM(n_rounds=rounds).fit(local[train], yb[train], local_names)
    risk[~train] = stage1.predict(local[~train])
    print(f"  stage 1 done ({time.perf_counter() - t0:.0f} s)")

    # Divide the graph among three imaginary institutions and build both views.
    inst = ((d["ids"] * 2654435761) % (2 ** 32)) % 3
    same = inst[es] == inst[ed]
    net_all = network_features(n, es, ed, risk)
    net_one = network_features(n, es[same], ed[same], risk)

    variants = [
        ("own", "Own features only", local, local_names),
        ("single", "Plus network risk, one institution's view", np.hstack([local, net_one]), local_names + NET_NAMES),
        ("shared", "Plus network risk, shared across institutions", np.hstack([local, net_all]), local_names + NET_NAMES),
        ("publisher", "Reference: plus the publisher's own neighbour summaries", X,
         local_names + [f"aggregated_{j + 1}" for j in range(X.shape[1] - N_LOCAL)]),
    ]
    results = []
    for key, label, F, names in variants:
        m = stage1 if key == "own" else GlassBoxGBM(n_rounds=rounds).fit(F[train], yb[train], names)
        thr = best_f1_threshold(yb[val], m.predict(F[val]))
        score = m.predict(F[test])
        row = {"key": key, "label": label, "features": F.shape[1], "threshold": round(thr, 3), **_metrics(yb[test], score, thr)}
        early, late = test & (step <= 42), test & (step > 42)
        row["steps_35_42"] = _metrics(yb[early], m.predict(F[early]), thr)
        row["steps_43_49"] = _metrics(yb[late], m.predict(F[late]), thr)
        results.append(row)
        print(f"  {label}: precision {row['precision']:.3f} recall {row['recall']:.3f} F1 {row['f1']:.3f} "
              f"PR-AUC {row['pr_auc']:.3f} ({time.perf_counter() - t0:.0f} s)")

    own, shared = results[0], results[2]
    e1, e2 = own["steps_35_42"], own["steps_43_49"]
    gain = shared["pr_auc"] - own["pr_auc"]
    verdict = ("did not help" if abs(gain) < 0.01 else "helped" if gain > 0 else "made things worse")
    findings = [
        f"Before the market change (steps 35 to 42), a transaction's own features were enough to catch "
        f"{e1['recall']:.0%} of illicit transactions at {e1['precision']:.0%} precision (PR-AUC {e1['pr_auc']:.2f}).",
        f"Network risk features {verdict} on this data: PR-AUC is {own['pr_auc']:.3f} without them and "
        f"{shared['pr_auc']:.3f} with the whole graph. They moved recall from {own['recall']:.0%} to {shared['recall']:.0%} "
        f"and precision from {own['precision']:.0%} to {shared['precision']:.0%}. The benefit of sharing measured on "
        f"synthetic data is not reproduced here.",
        f"From step 43, after a dark market closed, the model caught {e2['tp']} of {e2['tp'] + e2['fn']} illicit "
        f"transactions. A model trained on the earlier period does not recognise what came after. This is the same "
        f"weakness the synthetic \"unseen pattern\" test shows, now seen on real data.",
    ]
    report = {
        "findings": findings,
        "dataset": "Elliptic Data Set (real Bitcoin transactions)",
        "source": SOURCE, "licence": "CC BY-NC-ND 4.0, published by Elliptic. Not redistributed with this project.",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transactions": int(n), "flows": int(len(es)), "labelled": int(labelled.sum()),
        "illicit": int(yb[labelled].sum()), "time_steps": int(step.max()),
        "split": {"train": f"time steps 1 to {TRAIN_UNTIL}", "threshold": f"time steps {TRAIN_UNTIL + 1} to {VALIDATE_UNTIL}",
                  "test": f"time steps {VALIDATE_UNTIL + 1} to {int(step.max())}",
                  "train_labelled": int(train.sum()), "train_illicit": int(yb[train].sum()),
                  "test_labelled": int(test.sum()), "test_illicit": int(yb[test].sum())},
        "model": "Gradient-boosted decision stumps (additive, glass-box), the same model class as the product",
        "results": results, "seconds": round(time.perf_counter() - t0, 1),
        "notes": [
            "Real data, real labels, tested strictly on later time steps than it was trained on.",
            "The features are anonymised by the publisher, so plain-language reason codes are not possible here.",
            "This is Bitcoin, not UPI. It tests the method, not the Indian-payments model used in the live demonstration.",
            "The three institutions are imaginary: each transaction is assigned to one by a hash of its ID.",
            "A dark market closed around time step 43. The data set's authors report that the models they tested "
            "also lost most of their recall after that point.",
            "Thresholds were chosen on only five time steps, so precision and recall at the threshold are noisy. "
            "PR-AUC does not depend on a threshold and is the fairer comparison between rows.",
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {REPORT}")
    return report


def _main(argv: list[str]) -> int:
    if argv[:1] == ["download"]:
        if "--yes" not in argv:
            print(f"This downloads about 146 MB from {SOURCE} into {DATA}.\n"
                  "The data is licensed CC BY-NC-ND 4.0 (research use, no redistribution).\n"
                  "Run again with --yes to proceed.")
            return 1
        download()
        return 0
    if argv[:1] == ["run"]:
        try:
            run()
        except FileNotFoundError as err:
            print(err)
            return 1
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
