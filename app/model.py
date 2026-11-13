"""Glass-box gradient boosting in plain NumPy.

The model is gradient-boosted decision trees of depth 1 (stumps) trained with
Newton steps on log-loss, the same recipe as XGBoost with max_depth=1. Because
every tree looks at a single feature, the model is additive:

    logit(risk) = intercept + f_1(x_1) + f_2(x_2) + ... + f_d(x_d)

so each feature's contribution to a decision can be read off exactly. For an
additive model these contributions are the Shapley values, with no sampling or
approximation. This is the idea behind Explainable Boosting Machines.
"""
from __future__ import annotations

import numpy as np


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class GlassBoxGBM:
    def __init__(self, n_rounds: int = 400, lr: float = 0.1, max_bins: int = 24,
                 lam: float = 1.0, min_hess: float = 1.0):
        self.n_rounds, self.lr, self.max_bins, self.lam, self.min_hess = n_rounds, lr, max_bins, lam, min_hess

    # bin 0 is reserved for missing values; real values use bins 1..max_bins
    def _bin(self, X: np.ndarray) -> np.ndarray:
        bins = np.zeros(X.shape, dtype=np.int64)
        for j, edges in enumerate(self.edges):
            x = X[:, j]
            ok = ~np.isnan(x)
            bins[ok, j] = np.searchsorted(edges, x[ok], side="right") + 1
        return bins

    def fit(self, X: np.ndarray, y: np.ndarray, names: list[str]) -> "GlassBoxGBM":
        n, d = X.shape
        self.names = list(names)
        qs = np.linspace(0, 1, self.max_bins + 1)[1:-1]
        self.edges = []
        for j in range(d):
            x = X[:, j]
            x = x[~np.isnan(x)]
            self.edges.append(np.unique(np.quantile(x, qs)) if len(x) else np.array([]))
        bins = self._bin(X)
        K = self.max_bins + 1
        flat = (bins.T + (np.arange(d) * K)[:, None]).ravel()
        shape = np.zeros((d, K))
        y = y.astype(float)
        base = float(np.clip(y.mean(), 1e-4, 1 - 1e-4))
        self.intercept = float(np.log(base / (1 - base)))
        F = np.full(n, self.intercept)
        lam = self.lam
        for _ in range(self.n_rounds):
            p = _sigmoid(F)
            g, h = p - y, np.maximum(p * (1 - p), 1e-6)
            G = np.bincount(flat, np.tile(g, d), minlength=d * K).reshape(d, K)
            H = np.bincount(flat, np.tile(h, d), minlength=d * K).reshape(d, K)
            GL, HL = np.cumsum(G[:, 1:], axis=1)[:, :-1], np.cumsum(H[:, 1:], axis=1)[:, :-1]
            Gt, Ht = G[:, 1:].sum(axis=1, keepdims=True), H[:, 1:].sum(axis=1, keepdims=True)
            GR, HR = Gt - GL, Ht - HL
            gain = GL ** 2 / (HL + lam) + GR ** 2 / (HR + lam) - Gt ** 2 / (Ht + lam)
            gain[(HL < self.min_hess) | (HR < self.min_hess)] = -np.inf
            j, k = np.unravel_index(np.argmax(gain), gain.shape)
            if not np.isfinite(gain[j, k]) or gain[j, k] <= 1e-9:
                break
            upd = np.empty(K)
            upd[0] = -self.lr * G[j, 0] / (H[j, 0] + lam)
            upd[1:k + 2] = -self.lr * GL[j, k] / (HL[j, k] + lam)
            upd[k + 2:] = -self.lr * GR[j, k] / (HR[j, k] + lam)
            shape[j] += upd
            F += upd[bins[:, j]]
        # Centre each shape function so contributions are relative to the average account.
        for j in range(d):
            m = shape[j][bins[:, j]].mean()
            shape[j] -= m
            self.intercept += float(m)
        self.shape = shape
        return self

    def contributions(self, X: np.ndarray) -> np.ndarray:
        """Per-feature contribution to the log-odds, shape (n, d)."""
        bins = self._bin(X)
        return np.column_stack([self.shape[j][bins[:, j]] for j in range(X.shape[1])])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return _sigmoid(self.intercept + self.contributions(X).sum(axis=1))

    def importance(self) -> dict[str, float]:
        """Spread of each shape function: how much a feature can move the log-odds."""
        return {n: float(self.shape[j].max() - self.shape[j].min()) for j, n in enumerate(self.names)}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def prf(y: np.ndarray, pred: np.ndarray) -> dict:
    y, pred = y.astype(bool), pred.astype(bool)
    tp, fp, fn = int((y & pred).sum()), int((~y & pred).sum()), int((y & ~pred).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
            "alerts": tp + fp}


def average_precision(y: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(-score, kind="stable")
    hits = y[order].astype(float)
    if hits.sum() == 0:
        return 0.0
    precision_at_k = np.cumsum(hits) / (np.arange(len(hits)) + 1)
    return float((precision_at_k * hits).sum() / hits.sum())


def best_f1_threshold(y: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(-score, kind="stable")
    s, hits = score[order], y[order].astype(float)
    tp = np.cumsum(hits)
    k = np.arange(len(s)) + 1
    f1 = 2 * tp / (k + hits.sum())
    last = np.r_[s[1:] != s[:-1], True]  # only cut between distinct scores
    f1 = np.where(last, f1, -1)
    i = int(np.argmax(f1))
    return float((s[i] + s[i + 1]) / 2) if i + 1 < len(s) else float(s[i])
