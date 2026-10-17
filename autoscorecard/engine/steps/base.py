"""The waterfall step registry, and what a step receives and returns.

A step is one function registered with @register_step:

    @register_step("my_step", title="My step", defaults={"max": 0.3},
                   help="What it does, for the plan editor and the report.",
                   criterion=lambda p: f"something < {p['max']:.0%}",
                   unit_params=("max",))
    def my_step(pool, params, ctx) -> StepResult:
        dropped = [{"feature": f, "reason": "..."} for f in pool if ...]
        return StepResult(dropped)

    pool    the features still in the waterfall (a list you may not modify)
    params  the step's parameters: defaults merged with the plan's values
    ctx     StepContext: the training data, cached binning/WoE, feature stats,
            the forced features and the outcomes of earlier steps

Return the features you drop (each with a reason). A step that rebuilds the
pool itself (finalization re-adding forced features) returns `kept` and `added`
instead; the runner checks the counts add up either way.

The decorator is all it takes: the plan editor, validation, diagrams, audit log
and report pick the step up by name. Custom steps go in plugins/steps/*.py.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..binning import VariableBinning, bin_categorical, bin_feature, bin_numeric


# --------------------------------------------------------------------------- results

@dataclass
class StepResult:
    dropped: list[dict] = field(default_factory=list)     # {"feature", "reason"}
    detail: dict = field(default_factory=dict)            # shown in the report / audit
    kept: list[str] | None = None      # set only when the step defines the new pool itself
    added: int = 0                     # features brought (back) in by this step
    trace: pd.DataFrame | None = None  # selection trace (stepwise / forward / backward)
    clusters: Any = None               # ClusterResult, for clusters.json


# --------------------------------------------------------------------------- context

def _plain(v):
    """numpy scalar -> Python value, so reasons read 0 rather than np.int64(0)."""
    return v.item() if hasattr(v, "item") else v


def feature_stats(X: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    rows = []
    for f in features:
        s = X[f]
        vc_all = s.value_counts(dropna=False)
        vc = s.value_counts(dropna=True)
        rows.append({"feature": f,
                     "missing_rate": float(s.isna().mean()),
                     "identical_rate": float(vc_all.iloc[0] / len(s)) if len(s) else 1.0,
                     "identical_rate_non_missing": float(vc.iloc[0] / vc.sum()) if len(vc) else 1.0,
                     "top_value": _plain(vc_all.index[0]) if len(vc_all) else None})
    return pd.DataFrame(rows)


class BinningCache:
    """Bins and WoE-transforms features on demand, once each, whatever order the
    steps run in. Applies manual bins and WoE overrides, and records them as edits."""

    def __init__(self, X, y, bin_kw, manual_bins, woe_overrides, reasons):
        self.X, self.y = X, y
        self.bin_kw, self.manual = bin_kw, manual_bins
        self.overrides, self.reasons = woe_overrides, reasons
        self.binnings: dict[str, VariableBinning] = {}
        self.errors: dict[str, str] = {}
        self.edits: list[dict] = []
        self._woe: dict[str, pd.Series] = {}

    def _bin(self, f: str) -> VariableBinning:
        spec = self.manual.get(f)
        if spec and "edges" in spec:
            b = bin_numeric(self.X[f], self.y, name=f, edges=spec["edges"], monotonic=None)
        elif spec:
            b = bin_categorical(self.X[f], self.y, name=f, groups=spec["groups"])
        else:
            b = bin_feature(self.X[f], self.y, name=f, **self.bin_kw)
        if spec:
            try:
                auto = bin_feature(self.X[f], self.y, name=f, **self.bin_kw).table
            except ValueError:
                auto = pd.DataFrame()
            self.edits.append({"feature": f, "kind": "bins", "spec": spec,
                               "reason": self.reasons.get(f, ""), "before": auto,
                               "after": b.table, "monotonic_after": b.is_monotonic})
        if f in self.overrides:
            before = b.table
            b = b.with_woe_overrides(self.overrides[f])
            self.edits.append({"feature": f, "kind": "woe", "spec": self.overrides[f],
                               "reason": self.reasons.get(f, ""), "before": before,
                               "after": b.table, "monotonic_after": b.woe_is_monotonic})
        return b

    def binned(self, pool: list[str]) -> tuple[list[str], list[dict]]:
        """(features that could be binned, drops for those that could not)."""
        ok, bad = [], []
        for f in pool:
            if f not in self.binnings and f not in self.errors:
                try:
                    self.binnings[f] = self._bin(f)
                except ValueError as e:
                    self.errors[f] = str(e)
            if f in self.binnings:
                ok.append(f)
            else:
                bad.append({"feature": f, "reason": f"cannot be binned: {self.errors[f]}"})
        return ok, bad

    def woe(self, feats: list[str]) -> pd.DataFrame:
        for f in feats:
            if f not in self._woe:
                self._woe[f] = self.binnings[f].transform(self.X[f])
        return pd.DataFrame({f: self._woe[f] for f in feats}, index=self.X.index)


@dataclass
class StepContext:
    X: pd.DataFrame                  # training sample, raw values (after outlier treatment)
    y: pd.Series                     # 0/1, 1 = bad
    bins: BinningCache
    stats: pd.DataFrame              # feature_stats, indexed by feature
    forced: list[str]
    history: list                    # StepOutcome of every earlier step
    log: Callable

    def binned(self, pool: list[str]) -> tuple[list[str], list[dict]]:
        return self.bins.binned(pool)

    def woe(self, feats: list[str]) -> pd.DataFrame:
        return self.bins.woe(feats)

    def iv(self, f: str) -> float:
        return self.bins.binnings[f].iv

    def earlier_drop(self, f: str) -> tuple[str, str]:
        """(step title, reason) of the last earlier step that dropped f, or ('', '')."""
        for o in reversed(self.history):
            for d in o.dropped:
                if d["feature"] == f:
                    return o.step.title, d["reason"]
        return "", ""


# --------------------------------------------------------------------------- registry

@dataclass
class StepType:
    name: str
    title: str
    help: str
    defaults: dict[str, Any]
    run: Callable[[list[str], dict, StepContext], StepResult]
    criterion: Callable[[dict], str]
    validate: Callable[[dict], None] | None = None
    unit_params: tuple[str, ...] = ()       # parameters that must lie in [0, 1]
    kind: str = "filter"                    # filter | model (model = uses the regression)
    must_be_last: bool = False
    accepts_forced: bool = False            # brings forced features back in
    source: str = "builtin"                 # builtin | plugin file / module

    def check(self, params: dict) -> None:
        for k in self.unit_params:
            if not (0 <= float(params[k]) <= 1):
                raise ValueError(f"{self.name}.{k} must be between 0 and 1, got {params[k]}")
        if self.validate:
            self.validate(params)

    def describe(self) -> dict:
        return {"type": self.name, "title": self.title, "help": self.help,
                "defaults": self.defaults, "kind": self.kind, "must_be_last": self.must_be_last,
                "accepts_forced": self.accepts_forced, "source": self.source}


REGISTRY: dict[str, StepType] = {}


def register_step(name: str, *, title: str, defaults: dict[str, Any], help: str,
                  criterion: Callable[[dict], str], validate: Callable[[dict], None] | None = None,
                  unit_params: tuple[str, ...] = (), kind: str = "filter",
                  must_be_last: bool = False, accepts_forced: bool = False):
    """Decorator that adds a step function to the registry under `name`."""
    if kind not in ("filter", "model"):
        raise ValueError("kind must be filter or model")

    def deco(fn):
        source = getattr(fn, "__module__", "") or ""
        source = "builtin" if source.startswith("autoscorecard.engine.steps") else source
        old = REGISTRY.get(name)
        if old is not None and old.source != source:
            raise ValueError(f"step {name!r} is already registered by {old.source}")
        REGISTRY[name] = StepType(name, title, help, dict(defaults), fn, criterion, validate,
                                  tuple(unit_params), kind, must_be_last, accepts_forced, source)
        return fn
    return deco


_LOADED: set[str] = set()


def load_plugins(locations: list[str | Path], root: Path | None = None) -> list[str]:
    """Import custom steps. Each location is a directory (every *.py in it, not
    starting with "_") or a dotted module name. Returns the step names added."""
    before = set(REGISTRY)
    for loc in locations:
        p = Path(loc)
        if root is not None and not p.is_absolute():
            p = root / p
        if p.is_dir():
            for f in sorted(p.glob("*.py")):
                if f.name.startswith("_") or str(f) in _LOADED:
                    continue
                mod_name = f"autoscorecard_plugins.{f.stem}"
                spec = importlib.util.spec_from_file_location(mod_name, f)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                try:
                    spec.loader.exec_module(mod)
                except Exception as e:
                    del sys.modules[mod_name]
                    raise ValueError(f"step plugin {f.name} failed to load: {e}") from e
                _LOADED.add(str(f))
        elif "." in str(loc) and not p.suffix and str(loc) not in _LOADED:
            importlib.import_module(str(loc))
            _LOADED.add(str(loc))
    return sorted(set(REGISTRY) - before)
