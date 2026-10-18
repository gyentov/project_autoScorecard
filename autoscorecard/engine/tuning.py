"""Hyperparameter tuning for the final model, on the training sample only.

The training sample is split again (stratified) into a fit part and a validation part.
Every combination in the grid runs the whole approved waterfall on the fit part and is
scored on the validation part, so the test and out-of-time samples stay untouched and
their numbers remain an honest estimate.

What is tuned (the grid is configurable, config.yaml modelling.tuning):
    min_bin_pct  smallest bin, share of accounts (binning)
    max_bins     most bins per variable (binning)
    p_enter      significance to enter in stepwise / forward selection

Choice: the highest validation Gini after a penalty for over-fitting (the fit-minus-
validation Gini gap above `max_gap`); within `tie` of the best, the combination with
fewer variables wins, then the one closest to the defaults (simpler, easier to defend).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pandas as pd

from . import metrics
from .data import split
from .scorecard import Scaling, Scorecard
from .waterfall import Step, parse_steps, run_waterfall

DEFAULT_GRID = {"min_bin_pct": [0.03, 0.05, 0.08], "max_bins": [5, 8], "p_enter": [0.01, 0.05]}
SELECTION_STEPS = ("stepwise", "forward")


@dataclass
class TuningResult:
    table: pd.DataFrame                  # one row per combination, best first
    best: dict                           # the chosen hyperparameters
    default: dict                        # what the plan / config had
    n_fit: int = 0
    n_val: int = 0
    rule: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> dict:
        return {k: (self.default.get(k), v) for k, v in self.best.items()
                if self.default.get(k) != v}

    def to_dict(self) -> dict:
        return {"best": self.best, "default": self.default, "n_fit": self.n_fit,
                "n_val": self.n_val, "rule": self.rule, "changed": self.changed,
                "table": self.table.to_dict("records")}


def with_params(steps: list[Step], p_enter: float | None) -> list[Step]:
    """The plan with the selection steps' p_enter set (p_remove kept at least p_enter)."""
    if p_enter is None:
        return steps
    spec = []
    for s in steps:
        params = dict(s.params)
        if s.type in SELECTION_STEPS:
            params["p_enter"] = p_enter
            if "p_remove" in params or s.type == "stepwise":
                params["p_remove"] = max(params.get("p_remove", 0.05), p_enter)
        spec.append({"type": s.type, "params": params, "enabled": s.enabled})
    return parse_steps(spec)


def _defaults(steps: list[Step], bin_kw: dict) -> dict:
    sel = next((s for s in steps if s.type in SELECTION_STEPS and s.enabled), None)
    return {"min_bin_pct": bin_kw.get("min_bin_pct"), "max_bins": bin_kw.get("max_bins"),
            "p_enter": (sel.params.get("p_enter") if sel else None)}


def tune(X: pd.DataFrame, y: pd.Series, features: list[str], steps: list[Step], bin_kw: dict,
         inputs: dict, grid: dict | None = None, val_size: float = 0.25, seed: int = 42,
         max_gap: float = 0.05, tie: float = 0.005, log=None) -> TuningResult:
    """X, y: the training sample (after outlier treatment). Returns every combination's
    fit / validation Gini and the chosen one."""
    grid = {k: v for k, v in (grid or DEFAULT_GRID).items() if v}
    default = _defaults(steps, bin_kw)
    if not any(s.type in SELECTION_STEPS and s.enabled for s in steps):
        grid.pop("p_enter", None)
    y = y.astype(int)
    parts = split(X, y, val_size, seed)
    fit, val = parts["train"], parts["test"]
    keys = list(grid)
    rows, errors = [], []
    for combo in itertools.product(*(grid[k] for k in keys)):
        hp = dict(zip(keys, combo))
        kw = {**bin_kw, **{k: hp[k] for k in ("min_bin_pct", "max_bins") if k in hp}}
        try:
            wf = run_waterfall(X.loc[fit], y.loc[fit], features, with_params(steps, hp.get("p_enter")),
                               kw, inputs.get("manual_bins"), forced=inputs.get("forced_features"),
                               woe_overrides=inputs.get("woe_overrides"),
                               edit_reasons=inputs.get("reasons"))
            if not wf.selected:
                raise ValueError("tidak ada fitur yang terpilih")
            sc = Scorecard(wf.binnings, wf.model, Scaling())
            g_fit = metrics.summary(y.loc[fit], sc.score(X.loc[fit]), higher_is_riskier=False)["gini"]
            g_val = metrics.summary(y.loc[val], sc.score(X.loc[val]), higher_is_riskier=False)["gini"]
        except Exception as e:                                # one bad combination never stops it
            errors.append(f"{hp}: {e}")
            continue
        gap = g_fit - g_val
        rows.append({**hp, "n_features": len(wf.selected), "gini_fit": round(g_fit, 4),
                     "gini_val": round(g_val, 4), "gap": round(gap, 4),
                     "objective": round(g_val - max(0.0, gap - max_gap), 4),
                     "distance": sum(k in default and hp[k] != default[k] for k in hp),
                     "is_default": all(hp[k] == default.get(k) for k in hp)})
        if log:
            log("tuning_trial", rows[-1])
    if not rows:
        raise ValueError("tuning gagal di semua kombinasi: " + "; ".join(errors[:3]))
    t = pd.DataFrame(rows)
    top = t.objective.max()
    pick = t[t.objective >= top - tie].sort_values(["n_features", "distance", "objective"],
                                                   ascending=[True, True, False]).iloc[0]
    t["chosen"] = t.index == pick.name
    t = t.sort_values(["chosen", "objective"], ascending=False).reset_index(drop=True)
    best = {k: (pick[k].item() if hasattr(pick[k], "item") else pick[k]) for k in keys}
    rule = (f"Gini validasi tertinggi setelah penalti over-fit (selisih Gini fit−validasi di atas "
            f"{max_gap:.2f}); dalam {tie} dari yang terbaik dipilih yang fiturnya paling sedikit, "
            "lalu yang paling dekat dengan default")
    return TuningResult(t, best, {k: default.get(k) for k in keys}, len(fit), len(val), rule, errors)
