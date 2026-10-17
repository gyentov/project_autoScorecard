"""Modelling waterfall: an ordered list of feature-selection steps.

Each step receives the features that survived the previous step and returns
which it drops (each drop with a reason). The order and the parameters are
data, not code: they come from config/config.yaml, a project's saved plan
(work/waterfall.json) or the API, so the plan can be rearranged and shown to the
user BEFORE the Modeller runs it.

The steps themselves live in the registry (engine/steps/): one function per
step, built-in or plugin. This module validates plans, stores them and runs them.

Default sequence:
    1. missing_rate         keep if missing rate < 30%
    2. identical_rate       keep if the most frequent value covers < 95% of rows
    3. iv                   keep if 0.02 < IV < 0.5
    4. correlation_cluster  cluster WoE features at |corr| >= 0.7, keep highest IV per cluster
    5. stepwise             LR-test entry p < 0.01, Wald stay p < 0.05
    6. forward              re-check entry on the stepwise survivors, p < 0.01
    7. backward             re-check every coefficient, Wald p < 0.05
    8. finalization         all coefficients negative, p < 0.05, VIF < 5, at most 15 features
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .binning import VariableBinning
from .logistic import LogitResult, fit_logit
from .steps import BUILTIN_ORDER, REGISTRY, BinningCache, StepContext, feature_stats

# The registry, under the name the rest of the code (and the API) already uses.
STEP_TYPES = REGISTRY

DEFAULT_WATERFALL: list[dict] = [{"type": t} for t in BUILTIN_ORDER]


@dataclass
class Step:
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @property
    def title(self) -> str:
        return REGISTRY[self.type].title

    @property
    def help(self) -> str:
        return REGISTRY[self.type].help

    @property
    def kind(self) -> str:
        return REGISTRY[self.type].kind

    @property
    def criterion(self) -> str:
        return REGISTRY[self.type].criterion(self.params)

    def to_dict(self) -> dict:
        return {"type": self.type, "params": self.params, "enabled": self.enabled}


def parse_steps(spec: list[dict] | None = None) -> list[Step]:
    """Turn a plan (list of {"type", "params"?, "enabled"?}) into validated Steps,
    filling unspecified parameters with defaults. Raises ValueError on bad plans."""
    spec = DEFAULT_WATERFALL if spec is None else spec
    steps, seen = [], set()
    for i, raw in enumerate(spec):
        t = raw.get("type")
        if t not in REGISTRY:
            raise ValueError(f"step {i + 1}: unknown type {t!r}; choose from {list(REGISTRY)}")
        if t in seen:
            raise ValueError(f"step {i + 1}: {t!r} appears twice")
        seen.add(t)
        defaults = REGISTRY[t].defaults
        params = dict(raw.get("params") or {})
        unknown = set(params) - set(defaults)
        if unknown:
            raise ValueError(f"step {i + 1} ({t}): unknown parameter(s) {sorted(unknown)}; "
                             f"allowed: {list(defaults)}")
        step = Step(t, {**defaults, **params}, bool(raw.get("enabled", True)))
        REGISTRY[t].check(step.params)
        steps.append(step)
    enabled = [s.type for s in steps if s.enabled]
    for k, t in enumerate(enabled):
        if REGISTRY[t].must_be_last and k != len(enabled) - 1:
            raise ValueError(f"{t} must be the last enabled step")
    return steps


def plan_warnings(steps: list[Step]) -> list[str]:
    """Legal but questionable orderings, to show the user next to the plan."""
    out = []
    enabled = [s for s in steps if s.enabled]
    types = [s.type for s in enabled]
    model_pos = [i for i, s in enumerate(enabled) if s.kind == "model"]
    for i, s in enumerate(enabled):
        if s.kind != "model" and model_pos and i > model_pos[0]:
            out.append(f"{s.title} runs after model selection has started; "
                       "it usually belongs before it")
    if "iv" not in types:
        out.append("no IV filter: weak and leaking features reach the model steps")
    if "correlation_cluster" not in types:
        out.append("no correlation clustering: correlated features may share the model")
    if not model_pos:
        out.append("no model selection step: every surviving feature enters the model")
    return out


# --------------------------------------------------------------------------- plan persistence

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_plan(project, steps: list[Step], approved: bool = False, by: str = "user") -> dict:
    """Store the plan in work/waterfall.json. Any change resets approval."""
    rec = {"steps": [s.to_dict() for s in steps], "approved": approved,
           "approved_at": _now() if approved else None, "updated": _now()}
    (project.dir("work") / "waterfall.json").write_text(json.dumps(rec, indent=2),
                                                         encoding="utf-8")
    project.audit(by, "waterfall_approved" if approved else "waterfall_plan_saved",
                  {"steps": rec["steps"]})
    return rec


def load_plan(project, default: list[dict] | None = None) -> dict:
    p = project.dir("work") / "waterfall.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"steps": [s.to_dict() for s in parse_steps(default)], "approved": False,
            "approved_at": None, "updated": None}


def approve_plan(project, by: str = "user") -> dict:
    plan = load_plan(project)
    return save_plan(project, parse_steps(plan["steps"]), approved=True, by=by)


# --------------------------------------------------------------------------- running

@dataclass
class StepOutcome:
    step: Step
    n_in: int
    kept: list[str]
    dropped: list[dict]                 # {"feature", "reason"}
    detail: dict = field(default_factory=dict)
    n_added: int = 0                    # forced features brought back in (finalization)

    @property
    def n_out(self) -> int:
        return len(self.kept)


@dataclass
class WaterfallResult:
    steps: list[Step]
    outcomes: list[StepOutcome]
    features_in: list[str]
    selected: list[str]
    model: LogitResult
    binnings: dict[str, VariableBinning]
    feature_stats: pd.DataFrame          # one row per input feature
    clusters: Any = None
    selection_steps: pd.DataFrame = field(default_factory=pd.DataFrame)
    forced: list[dict] = field(default_factory=list)     # what happened to each forced feature
    edits: list[dict] = field(default_factory=list)      # manual bin / WoE edits, before & after
    final_corr: pd.DataFrame = field(default_factory=pd.DataFrame)   # WoE corr, final model

    def summary(self) -> pd.DataFrame:
        """One row per step. n_in + n_added - n_dropped = n_out; n_added is only
        non-zero at finalization, where forced features re-enter."""
        rows = [{"order": 0, "step": "Input", "criterion": "candidate features",
                 "n_in": len(self.features_in), "n_added": 0, "n_dropped": 0,
                 "n_out": len(self.features_in)}]
        for k, o in enumerate(self.outcomes, 1):
            rows.append({"order": k, "step": o.step.title, "criterion": o.step.criterion,
                         "n_in": o.n_in, "n_added": o.n_added, "n_dropped": len(o.dropped),
                         "n_out": o.n_out})
        return pd.DataFrame(rows)

    def dropped(self) -> pd.DataFrame:
        rows = [{"feature": d["feature"], "step": o.step.title, "reason": d["reason"]}
                for o in self.outcomes for d in o.dropped]
        return pd.DataFrame(rows, columns=["feature", "step", "reason"])

    def feature_journey(self) -> pd.DataFrame:
        """Every input feature with its stats and where it left the waterfall. A forced
        feature can be dropped twice (by a filter, then at finalization); the last
        event decides its status."""
        d = self.dropped().drop_duplicates("feature", keep="last").set_index("feature")
        forced = {r["feature"]: r for r in self.forced}
        out = self.feature_stats.copy()
        status, at, why = [], [], []
        for f in out.feature:
            if f in forced and f in self.selected:
                status.append("forced → selected")
                r = forced[f]
                at.append("")
                why.append(f"forced in; earlier dropped at {r['previously_dropped_at']}: "
                           f"{r['previous_reason']}" if r["previously_dropped_at"] else "forced in")
            else:
                status.append("selected" if f in self.selected else
                              "forced → discarded" if f in forced else "dropped")
                at.append(d["step"].get(f, ""))
                why.append(d["reason"].get(f, ""))
        out["status"], out["dropped_at"], out["reason"] = status, at, why
        return out


def run_waterfall(X: pd.DataFrame, y, features: list[str], steps: list[Step],
                  bin_kw: dict | None = None, manual_bins: dict | None = None,
                  log=None, forced: list[str] | None = None,
                  woe_overrides: dict[str, dict[str, float]] | None = None,
                  edit_reasons: dict[str, str] | None = None) -> WaterfallResult:
    """Run the enabled steps in order on the training sample X, y (1 = bad).

    forced:        features that must be tried. They skip every earlier step and
                   enter the finalization model directly; they are discarded only
                   if they still fail a finalization check there.
    manual_bins:   feature -> {"edges": [...]} or {"groups": [[...]]}, used as given.
    woe_overrides: feature -> {bin label: WoE}, set by hand on top of the binning.
    edit_reasons:  feature -> why it was edited / forced (shown in the report).
    """
    y = pd.Series(np.asarray(y, dtype=int), index=X.index)
    log = log or (lambda *a, **k: None)
    forced = list(dict.fromkeys(forced or []))
    missing = [f for f in forced if f not in X.columns]
    if missing:
        raise ValueError(f"forced feature(s) not in the data: {missing}")
    if forced and not any(s.enabled and REGISTRY[s.type].accepts_forced for s in steps):
        raise ValueError("forced features need an enabled finalization step")
    features = list(features) + [f for f in forced if f not in features]
    bins = BinningCache(X, y, bin_kw or {}, manual_bins or {}, woe_overrides or {},
                        edit_reasons or {})
    stats = feature_stats(X, features)
    outcomes: list[StepOutcome] = []
    ctx = StepContext(X, y, bins, stats.set_index("feature"), forced, outcomes, log)
    pool = list(features)
    clusters = None
    traces: list[pd.DataFrame] = []

    for step in (s for s in steps if s.enabled):
        n_in = len(pool)
        r = REGISTRY[step.type].run(list(pool), step.params, ctx)
        if r.kept is not None:
            new_pool = list(r.kept)
        else:
            gone = {d["feature"] for d in r.dropped}
            new_pool = [f for f in pool if f not in gone]
        # every step, plugin or built-in, must account for each feature exactly once
        if n_in + r.added - len(r.dropped) != len(new_pool) or \
                len({d["feature"] for d in r.dropped}) != len(r.dropped):
            raise RuntimeError(f"step {step.type!r}: counts do not add up (in {n_in}, added "
                               f"{r.added}, dropped {len(r.dropped)}, out {len(new_pool)})")
        outcomes.append(StepOutcome(step, n_in, new_pool, r.dropped, r.detail, r.added))
        pool = new_pool
        if r.trace is not None:
            traces.append(r.trace.assign(stage=step.title))
        if r.clusters is not None:
            clusters = r.clusters
        for d in r.dropped:
            log("drop_feature", {"feature": d["feature"], "stage": step.type}, d["reason"])
        log("waterfall_step", {"step": step.type, "criterion": step.criterion, "n_in": n_in,
                               "n_added": r.added, "n_dropped": len(r.dropped),
                               "n_out": len(pool)})
        for rec in r.detail.get("forced", []):
            log("forced_feature", rec, rec["reason"] or "kept after forcing")
        if not pool:
            raise ValueError(f"waterfall step '{step.title}' dropped every feature")

    ok, _ = bins.binned(pool)
    W = bins.woe(ok)
    model = fit_logit(W, y)
    steps_df = pd.concat(traces, ignore_index=True) if traces else pd.DataFrame()
    forced_out = next((o.detail["forced"] for o in outcomes if "forced" in o.detail), [])
    for e in bins.edits:
        log("manual_edit", {"feature": e["feature"], "kind": e["kind"], "spec": e["spec"],
                            "monotonic_after": e["monotonic_after"]}, e["reason"] or None)
    return WaterfallResult(steps, outcomes, list(features), ok, model,
                           {f: bins.binnings[f] for f in ok}, stats, clusters, steps_df,
                           forced_out, bins.edits, W.corr())
