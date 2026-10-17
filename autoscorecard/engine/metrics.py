"""Model performance metrics: AUC/Gini, KS, PSI, gains table.

Convention throughout the engine: y = 1 means BAD, and `score` is higher for
riskier accounts (a PD or logit). For a points scorecard, where a higher score
means safer, pass `-points` or use `higher_is_riskier=False`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _risk(score, higher_is_riskier: bool) -> np.ndarray:
    s = np.asarray(score, dtype=float)
    return s if higher_is_riskier else -s


def auc(y, score, higher_is_riskier: bool = True) -> float:
    """Area under ROC via the Mann-Whitney rank formula (ties get average ranks).

    AUC = (sum of ranks of bads - n_bad*(n_bad+1)/2) / (n_bad * n_good)
    """
    y = np.asarray(y, dtype=int)
    s = pd.Series(_risk(score, higher_is_riskier))
    ranks = s.rank(method="average").to_numpy()
    n_bad = int(y.sum())
    n_good = len(y) - n_bad
    if n_bad == 0 or n_good == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n_bad * (n_bad + 1) / 2) / (n_bad * n_good))


def gini(y, score, higher_is_riskier: bool = True) -> float:
    """Gini (accuracy ratio) = 2*AUC - 1."""
    return 2 * auc(y, score, higher_is_riskier) - 1


def ks(y, score, higher_is_riskier: bool = True) -> float:
    """Kolmogorov-Smirnov: max distance between cumulative bad and good distributions,
    evaluated at each distinct score value (riskiest first)."""
    y = np.asarray(y, dtype=int)
    df = pd.DataFrame({"y": y, "s": _risk(score, higher_is_riskier)})
    g = df.groupby("s", sort=True)["y"].agg(["sum", "count"]).iloc[::-1]
    bad = g["sum"].cumsum() / max(y.sum(), 1)
    good = (g["count"] - g["sum"]).cumsum() / max(len(y) - y.sum(), 1)
    return float((bad - good).abs().max())


def gains_table(y, score, n_bands: int = 10, higher_is_riskier: bool = True) -> pd.DataFrame:
    """Score bands (quantiles, riskiest band first) with counts, bad rate,
    cumulative capture of bads and the KS at each band boundary."""
    y = np.asarray(y, dtype=int)
    r = _risk(score, higher_is_riskier)
    band = pd.qcut(pd.Series(r).rank(method="first"), n_bands, labels=False)
    df = pd.DataFrame({"y": y, "score": np.asarray(score, dtype=float), "band": band})
    t = df.groupby("band").agg(min_score=("score", "min"), max_score=("score", "max"),
                               count=("y", "size"), bads=("y", "sum"))
    t = t.iloc[::-1].reset_index(drop=True)     # riskiest band first
    t.index = pd.RangeIndex(1, len(t) + 1, name="band")
    t["goods"] = t["count"] - t["bads"]
    t["bad_rate"] = t["bads"] / t["count"]
    t["cum_bad_pct"] = t["bads"].cumsum() / t["bads"].sum()
    t["cum_good_pct"] = t["goods"].cumsum() / t["goods"].sum()
    t["ks"] = t["cum_bad_pct"] - t["cum_good_pct"]
    return t


def psi(expected, actual, bins: int = 10, edges=None) -> tuple[float, pd.DataFrame]:
    """Population Stability Index of `actual` against `expected`.

    PSI = sum over bins of (a% - e%) * ln(a% / e%). Bin edges come from the
    quantiles of `expected` unless given. Empty bins get a 0.0001 floor.
    Rule of thumb: < 0.10 stable, 0.10-0.25 watch, > 0.25 shifted.
    """
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    if edges is None:
        edges = np.unique(np.quantile(e[~np.isnan(e)], np.linspace(0, 1, bins + 1)))
    edges = np.asarray(edges, dtype=float).copy()
    edges[0], edges[-1] = -np.inf, np.inf
    e_cnt = pd.Series(pd.cut(e, edges, include_lowest=True)).value_counts(sort=False)
    a_cnt = pd.Series(pd.cut(a, edges, include_lowest=True)).value_counts(sort=False)
    t = pd.DataFrame({"expected": e_cnt.to_numpy(), "actual": a_cnt.to_numpy()},
                     index=e_cnt.index.astype(str))
    t["expected_pct"] = np.maximum(t["expected"] / t["expected"].sum(), 1e-4)
    t["actual_pct"] = np.maximum(t["actual"] / t["actual"].sum(), 1e-4)
    t["psi"] = (t["actual_pct"] - t["expected_pct"]) * np.log(t["actual_pct"] / t["expected_pct"])
    return float(t["psi"].sum()), t


def summary(y, score, higher_is_riskier: bool = True) -> dict:
    y = np.asarray(y, dtype=int)
    return {"n": int(len(y)), "bads": int(y.sum()), "bad_rate": float(y.mean()),
            "auc": auc(y, score, higher_is_riskier), "gini": gini(y, score, higher_is_riskier),
            "ks": ks(y, score, higher_is_riskier)}
