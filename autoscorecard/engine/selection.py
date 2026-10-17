"""Feature selection: IV filter, correlation clustering, forward/backward/stepwise, VIF.

All selection runs on WoE-transformed features. With WoE = ln(good/bad) and
y = 1 for bad, every coefficient in a sensible scorecard is NEGATIVE (higher
WoE = safer = lower log-odds of bad). A positive coefficient usually means the
feature is fighting a correlated one; `require_negative` guards against it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .logistic import LogitResult, chi2_sf_df1, fit_logit


# --------------------------------------------------------------------------- IV filter

def iv_filter(iv_table: pd.DataFrame, min_iv: float = 0.02, max_iv: float | None = None
              ) -> tuple[list[str], list[dict]]:
    """Keep features with min_iv <= IV (<= max_iv). Returns (kept, dropped-with-reason)."""
    kept, dropped = [], []
    for r in iv_table.itertuples():
        if r.error:
            dropped.append({"feature": r.feature, "reason": f"not binned: {r.error}"})
        elif r.iv < min_iv:
            dropped.append({"feature": r.feature, "reason": f"IV {r.iv:.4f} < {min_iv}"})
        elif max_iv is not None and r.iv > max_iv:
            dropped.append({"feature": r.feature,
                            "reason": f"IV {r.iv:.4f} > {max_iv} (possible leakage)"})
        else:
            kept.append(r.feature)
    return kept, dropped


# --------------------------------------------------------------------------- clustering

@dataclass
class ClusterResult:
    clusters: list[list[str]]           # members, highest IV first
    representatives: list[str]          # one per cluster: the highest-IV member
    dropped: list[dict]                 # features removed, with reason
    linkage: list[dict]                 # every merge: a, b, distance, size (dendrogram)
    corr: pd.DataFrame


def correlation_clusters(X: pd.DataFrame, iv: dict[str, float], threshold: float = 0.7,
                         method: str = "average", corr_method: str = "pearson",
                         enforce_pairwise: bool = True) -> ClusterResult:
    """Agglomerative hierarchical clustering on distance d = 1 - |corr|.

    Start with each feature in its own cluster; repeatedly join the two closest
    clusters. Cluster-to-cluster distance (`method`):
        single   = min pairwise distance
        complete = max pairwise distance
        average  = mean pairwise distance (UPGMA)
    Joins are recorded for the full tree, but clusters are cut at distance
    1 - threshold: features end up together only if they are linked at
    |corr| >= threshold. From each cluster the feature with the highest IV is kept.

    With average (or complete) linkage two features in DIFFERENT clusters can still
    have |corr| >= threshold, because clusters are compared on averages. With
    `enforce_pairwise` (default) the threshold is a hard limit: after clustering,
    while any two kept features have |corr| >= threshold, the one with the lower
    IV is dropped. So every pair of survivors has |corr| < threshold.
    """
    if method not in ("single", "complete", "average"):
        raise ValueError("method must be single, complete or average")
    feats = list(X.columns)
    corr = X.corr(method=corr_method).fillna(0.0)
    D = 1.0 - corr.abs().to_numpy()
    np.fill_diagonal(D, 0.0)
    cut = 1.0 - threshold

    # cluster id -> member indices; new clusters get ids n, n+1, ... like scipy
    active: dict[int, list[int]] = {i: [i] for i in range(len(feats))}
    at_cut: dict[int, list[int]] | None = None
    linkage: list[dict] = []
    next_id = len(feats)

    def dist(a: list[int], b: list[int]) -> float:
        block = D[np.ix_(a, b)]
        if method == "single":
            return float(block.min())
        if method == "complete":
            return float(block.max())
        return float(block.mean())

    while len(active) > 1:
        ids = sorted(active)
        best = None
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                d = dist(active[a], active[b])
                if best is None or d < best[0] - 1e-12:
                    best = (d, a, b)
        d, a, b = best
        if d > cut and at_cut is None:
            at_cut = {k: list(v) for k, v in active.items()}
        merged = active.pop(a) + active.pop(b)
        active[next_id] = merged
        linkage.append({"a": a, "b": b, "id": next_id, "distance": d,
                        "abs_corr": 1 - d, "size": len(merged),
                        "members": [feats[k] for k in merged]})
        next_id += 1
    if at_cut is None:
        at_cut = active

    clusters, reps, dropped = [], [], []
    for members in at_cut.values():
        names = sorted((feats[k] for k in members), key=lambda f: -iv.get(f, 0.0))
        clusters.append(names)
        reps.append(names[0])
        for f in names[1:]:
            dropped.append({"feature": f, "reason":
                            f"correlated with {names[0]} (|corr|={abs(corr.loc[f, names[0]]):.2f}, "
                            f"IV {iv.get(f, 0):.3f} < {iv.get(names[0], 0):.3f})"})
    order = sorted(range(len(clusters)), key=lambda k: -iv.get(reps[k], 0.0))
    clusters, reps = [clusters[k] for k in order], [reps[k] for k in order]
    if enforce_pairwise:
        kept: list[str] = []
        for f in reps:                                 # highest IV first
            clash = next((g for g in kept if abs(corr.loc[f, g]) >= threshold), None)
            if clash is None:
                kept.append(f)
            else:
                dropped.append({"feature": f, "reason":
                                f"|corr| with {clash} = {abs(corr.loc[f, clash]):.2f} >= "
                                f"{threshold:g} (pairwise check after clustering; IV "
                                f"{iv.get(f, 0):.3f} < {iv.get(clash, 0):.3f})"})
        reps = kept
    return ClusterResult(clusters, reps, dropped, linkage, corr)


# --------------------------------------------------------------------------- stepwise

@dataclass
class SelectionResult:
    selected: list[str]
    model: LogitResult
    steps: list[dict] = field(default_factory=list)

    def steps_table(self) -> pd.DataFrame:
        return pd.DataFrame(self.steps)


def _fit(X: pd.DataFrame, y, feats: list[str]) -> LogitResult:
    return fit_logit(X[feats], y)


def _positive(m: LogitResult) -> list[str]:
    return [f for f in m.features if m.coef[f] > 0]


def forward(X: pd.DataFrame, y, p_enter: float = 0.05, max_features: int = 15,
            require_negative: bool = True, candidates: list[str] | None = None,
            start: list[str] | None = None) -> SelectionResult:
    """Add the candidate with the smallest likelihood-ratio p-value, while it is
    below p_enter (and, if required, all coefficients stay negative)."""
    return stepwise(X, y, p_enter=p_enter, p_remove=None, max_features=max_features,
                    require_negative=require_negative, candidates=candidates, start=start)


def backward(X: pd.DataFrame, y, p_remove: float = 0.05, require_negative: bool = True,
             candidates: list[str] | None = None) -> SelectionResult:
    """Start with all candidates; repeatedly drop the worst feature: first any
    positive coefficient (if required), else the largest Wald p-value > p_remove."""
    sel = list(candidates or X.columns)
    steps: list[dict] = []
    model = _fit(X, y, sel)
    while sel:
        pos = _positive(model) if require_negative else []
        if pos:
            worst = max(pos, key=lambda f: model.coef[f])
            why = f"positive coefficient {model.coef[worst]:.4f}"
        else:
            worst = max(sel, key=lambda f: model.p_value[f])
            if model.p_value[worst] <= p_remove:
                break
            why = f"Wald p={model.p_value[worst]:.4g} > {p_remove}"
        sel.remove(worst)
        steps.append({"step": len(steps) + 1, "action": "remove", "feature": worst,
                      "p_value": model.p_value[worst], "reason": why})
        model = _fit(X, y, sel)
        steps[-1]["aic"] = model.aic
    return SelectionResult(sel, model, steps)


def stepwise(X: pd.DataFrame, y, p_enter: float = 0.05, p_remove: float | None = 0.10,
             max_features: int = 15, require_negative: bool = True,
             candidates: list[str] | None = None, start: list[str] | None = None
             ) -> SelectionResult:
    """Forward entry by likelihood-ratio test, then (if p_remove is set) backward
    removal of any feature whose Wald p-value rose above p_remove. A feature that
    was removed is not re-entered, so the procedure cannot cycle."""
    pool = [c for c in (candidates or X.columns)]
    sel = list(start or [])
    banned: set[str] = set()
    steps: list[dict] = []
    model = _fit(X, y, sel)

    while len(sel) < max_features:
        best = None
        for f in pool:
            if f in sel or f in banned:
                continue
            m = _fit(X, y, sel + [f])
            if require_negative and _positive(m):
                continue
            lr = 2 * (m.log_likelihood - model.log_likelihood)
            p = chi2_sf_df1(lr)
            if best is None or p < best[0]:
                best = (p, f, m, lr)
        if best is None or best[0] >= p_enter:
            break
        p, f, model, lr = best
        sel.append(f)
        steps.append({"step": len(steps) + 1, "action": "add", "feature": f,
                      "p_value": p, "aic": model.aic,
                      "reason": f"LR chi2={lr:.2f}, p={p:.4g} < {p_enter}"})
        if p_remove is None:
            continue
        while True:
            out = [g for g in sel if model.p_value[g] > p_remove]
            if not out:
                break
            worst = max(out, key=lambda g: model.p_value[g])
            sel.remove(worst)
            banned.add(worst)
            pv = model.p_value[worst]
            model = _fit(X, y, sel)
            steps.append({"step": len(steps) + 1, "action": "remove", "feature": worst,
                          "p_value": pv, "aic": model.aic,
                          "reason": f"Wald p={pv:.4g} > {p_remove} after adding {f}"})
    return SelectionResult(sel, model, steps)


# --------------------------------------------------------------------------- VIF

def vif(X: pd.DataFrame) -> pd.Series:
    """Variance inflation factor: VIF_j = 1 / (1 - R^2_j), where R^2_j comes from
    regressing feature j on all the others (with intercept) by least squares."""
    out = {}
    cols = list(X.columns)
    for c in cols:
        others = [o for o in cols if o != c]
        yj = X[c].to_numpy(dtype=float)
        if not others:
            out[c] = 1.0
            continue
        A = np.column_stack([np.ones(len(X))] + [X[o].to_numpy(dtype=float) for o in others])
        beta = np.linalg.lstsq(A, yj, rcond=None)[0]
        ss_res = float(((yj - A @ beta) ** 2).sum())
        ss_tot = float(((yj - yj.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
        out[c] = float("inf") if r2 >= 1 else 1 / (1 - r2)
    return pd.Series(out, name="vif")
