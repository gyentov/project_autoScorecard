"""Coarse classing (binning), Weight of Evidence and Information Value.

Conventions (y = 1 is BAD):
    dist_good_i = goods_i / total_goods
    dist_bad_i  = bads_i  / total_bads
    WoE_i       = ln(dist_good_i / dist_bad_i)      higher WoE = safer bin
    IV          = sum_i (dist_good_i - dist_bad_i) * WoE_i

A count of zero in a bin is replaced by 0.5 before taking the log, so WoE stays
finite; the merge rules below normally stop that from happening anyway.

Numeric algorithm:
  1. Fine classing: up to `n_init` quantile bins (or one bin per distinct value
     when there are few of them). Intervals are right-closed: (lo, hi].
  2. Merge adjacent bins, one pair at a time, always choosing the pair with
     the smallest chi-square (the two most similar bins), until:
       a. every bin has at least one good and one bad,
       b. every bin holds at least `min_bin_pct` of the non-missing rows,
       c. bad rate is STRICTLY monotonic in the value (if `monotonic` is set;
          equal neighbours count as a violation). With monotonic="auto" both
          directions are tried and the one with the higher IV is kept.
          The missing bin is outside this rule: it has no position on the axis.
       d. there are at most `max_bins` bins.
  3. Missing values get their own bin (never merged), if there are any.

Categorical algorithm:
  1. Categories with less than `rare_pct` of rows are pooled into "__OTHER__".
  2. Groups are sorted by bad rate and then merged as in step 2 above (sorting
     by bad rate makes them monotonic by construction).
  3. Categories never seen in training map to "__OTHER__" if it exists,
     otherwise get WoE 0 (neutral). Missing works the same way.

Every merge is recorded in `merge_log` with the reason, for the audit trail.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

MISSING = "__MISSING__"
OTHER = "__OTHER__"


# --------------------------------------------------------------------------- helpers

def _chi2(g1: float, b1: float, g2: float, b2: float) -> float:
    """Pearson chi-square of the 2x2 table [[g1, b1], [g2, b2]]."""
    n = g1 + b1 + g2 + b2
    if n == 0:
        return 0.0
    chi = 0.0
    for obs, row, col in ((g1, g1 + b1, g1 + g2), (b1, g1 + b1, b1 + b2),
                          (g2, g2 + b2, g1 + g2), (b2, g2 + b2, b1 + b2)):
        exp = row * col / n
        if exp > 0:
            chi += (obs - exp) ** 2 / exp
    return chi


@dataclass
class _Unit:
    goods: float
    bads: float
    members: Any            # numeric: (lo, hi) edges; categorical: list of categories

    @property
    def n(self) -> float:
        return self.goods + self.bads

    @property
    def bad_rate(self) -> float:
        return self.bads / self.n if self.n else 0.0


def _merge(units: list[_Unit], i: int, numeric: bool) -> None:
    a, b = units[i], units[i + 1]
    members = (a.members[0], b.members[1]) if numeric else a.members + b.members
    units[i:i + 2] = [_Unit(a.goods + b.goods, a.bads + b.bads, members)]


def _pair_chi(units: list[_Unit], i: int) -> float:
    a, b = units[i], units[i + 1]
    return _chi2(a.goods, a.bads, b.goods, b.bads)


def _best_pair_for(units: list[_Unit], i: int) -> int:
    """Merge partner for one problem bin i: the neighbour it is most similar to."""
    cands = [j for j in (i - 1, i) if 0 <= j < len(units) - 1]
    return min(cands, key=lambda j: _pair_chi(units, j))


def _merge_units(units: list[_Unit], min_count: float, max_bins: int,
                 direction: int, numeric: bool) -> list[str]:
    """Apply merge rules a-d in priority order. direction: +1 bad rate rising,
    -1 falling, 0 no monotonic constraint. Returns a human-readable merge log."""
    log: list[str] = []

    def describe(j: int) -> str:
        return f"{_label(units[j], numeric)} + {_label(units[j + 1], numeric)}"

    while len(units) > 1:
        pure = [i for i, u in enumerate(units) if u.goods == 0 or u.bads == 0]
        small = [i for i, u in enumerate(units) if u.n < min_count]
        if pure:
            j, why = _best_pair_for(units, pure[0]), "bin has no goods or no bads"
        elif small:
            i = min(small, key=lambda k: units[k].n)
            j, why = _best_pair_for(units, i), f"bin below minimum size ({min_count:.0f} rows)"
        else:
            # strict: two neighbours with equal bad rate are also merged (no information)
            viol = [k for k in range(len(units) - 1)
                    if direction and (units[k + 1].bad_rate - units[k].bad_rate) * direction <= 0]
            if viol:
                j, why = min(viol, key=lambda k: _pair_chi(units, k)), "bad rate not monotonic"
            elif len(units) > max_bins:
                j = min(range(len(units) - 1), key=lambda k: _pair_chi(units, k))
                why = f"more than {max_bins} bins"
            else:
                break
        log.append(f"merge {describe(j)} (chi2={_pair_chi(units, j):.2f}): {why}")
        _merge(units, j, numeric)
    return log


def _units_iv(units: list[_Unit]) -> float:
    G = sum(u.goods for u in units)
    B = sum(u.bads for u in units)
    iv = 0.0
    for u in units:
        dg, db = max(u.goods, 0.5) / G, max(u.bads, 0.5) / B
        iv += (dg - db) * math.log(dg / db)
    return iv


def _fmt(v: float) -> str:
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return f"{v:g}"


def _label(u: _Unit, numeric: bool) -> str:
    if numeric:
        return f"({_fmt(u.members[0])}, {_fmt(u.members[1])}]"
    cats = u.members
    return ", ".join(cats) if len(cats) <= 4 else ", ".join(cats[:3]) + f", ... (+{len(cats) - 3})"


def _stats_table(labels: list[str], goods: list[float], bads: list[float]) -> pd.DataFrame:
    t = pd.DataFrame({"bin": labels, "goods": goods, "bads": bads})
    t["count"] = t["goods"] + t["bads"]
    G, B = t["goods"].sum(), t["bads"].sum()
    t["pct"] = t["count"] / t["count"].sum()
    t["bad_rate"] = t["bads"] / t["count"]
    t["dist_good"] = t["goods"].clip(lower=0.5) / G
    t["dist_bad"] = t["bads"].clip(lower=0.5) / B
    t["woe"] = np.log(t["dist_good"] / t["dist_bad"])
    t["iv"] = (t["dist_good"] - t["dist_bad"]) * t["woe"]
    return t[["bin", "count", "pct", "goods", "bads", "bad_rate", "dist_good", "dist_bad",
              "woe", "iv"]]


# --------------------------------------------------------------------------- result

@dataclass
class VariableBinning:
    name: str
    kind: str                                   # "numeric" | "categorical"
    table: pd.DataFrame                         # one row per bin (missing bin last)
    edges: list[float] | None = None            # numeric: [-inf, c1, ..., inf]
    groups: list[list[str]] | None = None       # categorical: categories per bin
    has_missing_bin: bool = False
    direction: int = 0                          # +1/-1/0, numeric only
    merge_log: list[str] = field(default_factory=list)
    manual: bool = False                        # True when bins were set by hand
    woe_overrides: dict[str, float] = field(default_factory=dict)   # bin label -> WoE set by hand

    @property
    def iv(self) -> float:
        """IV from the data. A WoE override changes the points, not the IV."""
        return float(self.table["iv"].sum())

    def with_woe_overrides(self, overrides: dict[str, float]) -> "VariableBinning":
        """Copy with some bins' WoE set by hand. The calculated value is kept in
        `woe_calc` and the bin is marked `woe_edited`, so the edit stays visible."""
        unknown = set(overrides) - set(self.table["bin"])
        if unknown:
            raise ValueError(f"{self.name}: no bin(s) named {sorted(unknown)}; "
                             f"bins are {self.table['bin'].tolist()}")
        t = self.table.copy()
        if "woe_calc" not in t:
            t["woe_calc"] = t["woe"]
        t["woe"] = [float(overrides.get(b, w)) for b, w in zip(t["bin"], t["woe_calc"])]
        t["woe_edited"] = t["bin"].isin(list(overrides))
        return VariableBinning(self.name, self.kind, t, self.edges, self.groups,
                               self.has_missing_bin, self.direction, list(self.merge_log),
                               self.manual, {**self.woe_overrides, **overrides})

    @property
    def woe_is_monotonic(self) -> bool:
        """WoE strictly monotonic across non-missing numeric bins (can only fail
        after a manual WoE override, since the bad rate is monotonic)."""
        if self.kind != "numeric":
            return True
        w = self.table.loc[self.table["bin"] != MISSING, "woe"].to_numpy()
        d = np.diff(w)
        return bool(len(w) < 2 or (d > 0).all() or (d < 0).all())

    @property
    def is_monotonic(self) -> bool:
        """Bad rate strictly monotonic across the non-missing bins. Categorical bins
        have no natural order (they are sorted by bad rate), so they always pass."""
        if self.kind != "numeric":
            return True
        br = self.table.loc[self.table["bin"] != MISSING, "bad_rate"].to_numpy()
        if len(br) < 2:
            return True
        d = np.diff(br)
        return bool((d > 0).all() or (d < 0).all())

    @property
    def woe_map(self) -> dict[str, float]:
        return dict(zip(self.table["bin"], self.table["woe"]))

    def assign(self, x: pd.Series) -> pd.Series:
        """Bin label for each value (labels match `table['bin']`)."""
        labels = self.table["bin"].tolist()
        out = pd.Series(MISSING, index=x.index, dtype=object)
        miss = x.isna()
        if self.kind == "numeric":
            idx = pd.cut(pd.to_numeric(x[~miss], errors="coerce").astype(float), self.edges,
                         right=True, labels=False, include_lowest=True)
            out[~miss] = [labels[int(i)] for i in idx]
        else:
            # unseen categories (and missing, if training had none) go to OTHER;
            # with no OTHER bin they get label None, i.e. WoE 0 in transform()
            lookup = {c: labels[k] for k, grp in enumerate(self.groups) for c in grp}
            fallback = lookup.get(OTHER)
            out[~miss] = [lookup.get(v, fallback) for v in x[~miss].astype(str)]
            out[miss] = lookup.get(MISSING, fallback)
        return out

    def transform(self, x: pd.Series) -> pd.Series:
        """WoE value for each row. Anything without a training bin gets 0 (neutral)."""
        m = self.woe_map
        return self.assign(x).map(lambda b: m.get(b, 0.0)).astype(float)

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "edges": self.edges,
                "groups": self.groups, "has_missing_bin": self.has_missing_bin,
                "direction": self.direction, "iv": self.iv, "manual": self.manual,
                "is_monotonic": self.is_monotonic, "woe_overrides": self.woe_overrides,
                "merge_log": self.merge_log,
                "table": self.table.replace({np.inf: None, -np.inf: None}).to_dict("records")}

    @classmethod
    def from_dict(cls, d: dict) -> "VariableBinning":
        edges = [(-np.inf if i == 0 else np.inf) if e is None else e
                 for i, e in enumerate(d["edges"])] if d.get("edges") else None
        return cls(name=d["name"], kind=d["kind"], table=pd.DataFrame(d["table"]),
                   edges=edges, groups=d.get("groups"),
                   has_missing_bin=d["has_missing_bin"], direction=d.get("direction", 0),
                   merge_log=d.get("merge_log", []), manual=d.get("manual", False),
                   woe_overrides=d.get("woe_overrides") or {})


# --------------------------------------------------------------------------- binning

def bin_numeric(x: pd.Series, y, name: str | None = None, n_init: int = 20,
                min_bin_pct: float = 0.05, max_bins: int = 8,
                monotonic: str | None = "auto", edges: list[float] | None = None
                ) -> VariableBinning:
    """Bin a numeric feature. Pass `edges` (interior cut points) to set bins by hand;
    then no merging happens. monotonic: "auto", "ascending", "descending" or None."""
    name = name or str(x.name)
    y = pd.Series(np.asarray(y, dtype=int), index=x.index)
    x = pd.to_numeric(x, errors="coerce").astype(float)
    miss = x.isna()
    xv, yv = x[~miss], y[~miss]
    if xv.nunique() < 2 and edges is None:
        raise ValueError(f"{name}: fewer than 2 distinct non-missing values")

    if edges is not None:
        cuts = sorted(set(float(e) for e in edges))
    else:
        uniq = np.sort(xv.unique())
        if len(uniq) <= n_init:
            cuts = list(uniq[:-1])
        else:
            cuts = list(np.unique(np.quantile(xv, np.linspace(0, 1, n_init + 1))[1:-1]))
            cuts = [c for c in cuts if c < uniq[-1]]
    full = [-np.inf] + cuts + [np.inf]

    idx = pd.cut(xv, full, right=True, labels=False, include_lowest=True)
    grp = pd.DataFrame({"i": idx, "y": yv}).groupby("i")["y"].agg(["sum", "count"])
    units = []
    for k in range(len(full) - 1):
        b = float(grp["sum"].get(k, 0))
        n = float(grp["count"].get(k, 0))
        units.append(_Unit(n - b, b, (full[k], full[k + 1])))

    log: list[str] = []
    direction = {"ascending": 1, "descending": -1}.get(monotonic or "", 0)
    if edges is None:
        units = [u for u in units if u.n > 0] or units
        # drop empty fine bins by stretching neighbours over them
        for k in range(len(units) - 1):
            units[k] = _Unit(units[k].goods, units[k].bads, (units[k].members[0],
                             units[k + 1].members[0]))
        units[-1] = _Unit(units[-1].goods, units[-1].bads, (units[-1].members[0], np.inf))
        units[0] = _Unit(units[0].goods, units[0].bads, (-np.inf, units[0].members[1]))
        min_count = min_bin_pct * len(xv)
        if monotonic == "auto":
            # Try both directions and keep the one that preserves more information.
            # (The sign of a correlation can point the wrong way for curved shapes.)
            tries = {}
            for d in (1, -1):
                u = list(units)
                tries[d] = (u, _merge_units(u, min_count, max_bins, d, numeric=True))
            iv_of = {d: _units_iv(tries[d][0]) for d in tries}
            direction = 1 if iv_of[1] >= iv_of[-1] else -1
            units, log = tries[direction]
            log = [f"direction: {'ascending' if direction > 0 else 'descending'} bad rate "
                   f"(IV of non-missing bins {iv_of[direction]:.4f} vs {iv_of[-direction]:.4f} "
                   "the other way)"] + log
        else:
            log = _merge_units(units, min_count, max_bins, direction, numeric=True)
    elif monotonic in (None, "auto"):
        # manual bins: never changed, but report the trend they actually have
        br = [u.bad_rate for u in units if u.n > 0]
        direction = 1 if len(br) < 2 or br[-1] >= br[0] else -1

    labels = [_label(u, True) for u in units]
    goods = [u.goods for u in units]
    bads = [u.bads for u in units]
    has_miss = bool(miss.any())
    if has_miss:
        labels.append(MISSING)
        bads.append(float(y[miss].sum()))
        goods.append(float(miss.sum()) - bads[-1])
    out_edges = [u.members[0] for u in units] + [np.inf]
    return VariableBinning(name, "numeric", _stats_table(labels, goods, bads), edges=out_edges,
                           has_missing_bin=has_miss, direction=direction, merge_log=log,
                           manual=edges is not None)


def bin_categorical(x: pd.Series, y, name: str | None = None, min_bin_pct: float = 0.05,
                    max_bins: int = 8, rare_pct: float = 0.01,
                    groups: list[list[str]] | None = None) -> VariableBinning:
    """Bin a categorical feature. Pass `groups` (lists of category values, use
    "__MISSING__" / "__OTHER__" as needed) to set bins by hand."""
    name = name or str(x.name)
    y = pd.Series(np.asarray(y, dtype=int), index=x.index)
    xs = x.astype(object).where(x.notna(), MISSING).astype(str)
    stats = pd.DataFrame({"x": xs, "y": y}).groupby("x")["y"].agg(["sum", "count"])
    log: list[str] = []

    if groups is not None:
        units = []
        for grp in groups:
            sub = stats.loc[stats.index.intersection(grp)]
            b, n = float(sub["sum"].sum()), float(sub["count"].sum())
            units.append(_Unit(n - b, b, list(grp)))
        manual = True
    else:
        rare = stats.index[(stats["count"] < rare_pct * len(xs)) & (stats.index != MISSING)]
        if len(rare) > 0:
            log.append(f"pool {len(rare)} rare categories into {OTHER}: {list(rare)[:10]}")
            pooled = stats.loc[rare].sum()
            stats = stats.drop(index=rare)
            stats.loc[OTHER] = pooled
        if len(stats) < 2:
            raise ValueError(f"{name}: fewer than 2 usable categories")
        stats = stats.assign(br=stats["sum"] / stats["count"]).sort_values("br")
        units = [_Unit(float(r["count"] - r["sum"]), float(r["sum"]), [str(c)])
                 for c, r in stats.iterrows()]
        log += _merge_units(units, min_bin_pct * len(xs), max_bins, 0, numeric=False)
        manual = False

    labels = [_label(u, False) for u in units]
    # labels must be unique; truncated labels of different bins could collide
    seen: dict[str, int] = {}
    for k, lab in enumerate(labels):
        if lab in seen:
            labels[k] = f"{lab} #{k}"
        seen[lab] = k
    table = _stats_table(labels, [u.goods for u in units], [u.bads for u in units])
    return VariableBinning(name, "categorical", table, groups=[u.members for u in units],
                           has_missing_bin=any(MISSING in u.members for u in units),
                           merge_log=log, manual=manual)


def is_numeric(x: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x)


def bin_feature(x: pd.Series, y, **kw) -> VariableBinning:
    if is_numeric(x):
        kw.pop("rare_pct", None)
        return bin_numeric(x, y, **kw)
    for k in ("n_init", "monotonic"):
        kw.pop(k, None)
    return bin_categorical(x, y, **kw)


def bin_all(df: pd.DataFrame, y, features: list[str] | None = None, **kw
            ) -> tuple[dict[str, VariableBinning], pd.DataFrame]:
    """Bin every feature. Returns (binnings, iv_table). Features that cannot be
    binned are listed in iv_table with the error instead of an IV."""
    features = features or list(df.columns)
    binnings: dict[str, VariableBinning] = {}
    rows = []
    for f in features:
        try:
            b = bin_feature(df[f], y, name=f, **kw)
            binnings[f] = b
            rows.append({"feature": f, "kind": b.kind, "iv": b.iv,
                         "n_bins": len(b.table), "error": None})
        except ValueError as e:
            rows.append({"feature": f, "kind": None, "iv": np.nan, "n_bins": 0, "error": str(e)})
    iv = pd.DataFrame(rows).sort_values("iv", ascending=False, na_position="last")
    iv["strength"] = iv["iv"].map(iv_strength)
    return binnings, iv.reset_index(drop=True)


def iv_strength(iv: float) -> str:
    """Siddiqi's rule of thumb."""
    if iv is None or np.isnan(iv):
        return "n/a"
    if iv < 0.02:
        return "useless"
    if iv < 0.1:
        return "weak"
    if iv < 0.3:
        return "medium"
    if iv < 0.5:
        return "strong"
    return "suspicious (>0.5, check for leakage)"


def woe_transform(df: pd.DataFrame, binnings: dict[str, VariableBinning],
                  features: list[str] | None = None) -> pd.DataFrame:
    features = features or list(binnings)
    return pd.DataFrame({f: binnings[f].transform(df[f]) for f in features}, index=df.index)
