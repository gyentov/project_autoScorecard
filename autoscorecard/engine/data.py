"""Loading, profiling, join-key discovery, joins, target assessment and splitting.

These are the Prepper's tools. Each returns plain tables/dicts so an agent can
read them and a person can check them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .binning import is_numeric

# --------------------------------------------------------------------------- loading

def load_table(path) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(p, sep=None, engine="python")   # sniff , ; | delimiters
    elif ext in (".tsv", ".txt"):
        df = pd.read_csv(p, sep="\t")
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(p)
    elif ext == ".parquet":
        df = pd.read_parquet(p)
    else:
        raise ValueError(f"unsupported file type: {p.name}")
    return parse_dates(df)


def parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Turn text columns whose name says date (date, _dt, tanggal, tgl) into dates
    when at least 90% of the values parse; other columns are left alone."""
    for c in df.columns:
        if _guess_role(str(c), df[c], len(df)) == "date" and not \
                pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = pd.to_datetime(df[c], errors="coerce", format="mixed")
    return df


# --------------------------------------------------------------------------- profiling

def _guess_role(name: str, s: pd.Series, n: int) -> str:
    lname = name.lower()
    nun = s.nunique(dropna=True)
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if any(k in lname for k in ("date", "_dt", "tanggal", "tgl")) and not is_numeric(s):
        parsed = pd.to_datetime(s.dropna().head(200), errors="coerce", format="mixed")
        if parsed.notna().mean() > 0.9:
            return "date"
    if nun == n and n > 0 and (lname.endswith("id") or lname.endswith("_no") or not is_numeric(s)):
        return "id"
    if any(k in lname for k in ("_id", "id_", "nik", "cif", "account_no")) and nun > 0.5 * n:
        return "id"
    if nun == 2:
        return "binary"
    if nun <= 1:
        return "constant"
    if is_numeric(s):
        return "numeric"
    return "categorical"


def profile(df: pd.DataFrame) -> pd.DataFrame:
    """One row per column: type, guessed role, missing %, distinct count, stats."""
    n = len(df)
    rows = []
    for c in df.columns:
        s = df[c]
        row = {"column": c, "dtype": str(s.dtype), "role": _guess_role(str(c), s, n),
               "missing_pct": float(s.isna().mean()) if n else 0.0,
               "n_unique": int(s.nunique(dropna=True))}
        if is_numeric(s):
            d = s.describe()
            row.update({"min": d.get("min"), "p50": s.median(), "max": d.get("max"),
                        "mean": d.get("mean")})
        else:
            top = s.value_counts(dropna=True).head(3)
            row["top_values"] = ", ".join(f"{k} ({v})" for k, v in top.items())
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- joins

def suggest_join_keys(left: pd.DataFrame, right: pd.DataFrame, min_overlap: float = 0.5
                      ) -> pd.DataFrame:
    """Candidate key pairs: same (case-insensitive) column name, or values that
    overlap strongly. Reports overlap and uniqueness on each side so the
    relationship (1:1, 1:many, many:1, many:many) is visible before joining."""
    rows = []
    for lc in left.columns:
        lv = left[lc].dropna()
        if lv.empty:
            continue
        lset = set(lv.astype(str).unique())
        for rc in right.columns:
            same_name = str(lc).lower() == str(rc).lower()
            rv = right[rc].dropna()
            if rv.empty or (not same_name and left[lc].nunique() < 20):
                continue                           # skip low-cardinality coincidences
            rset = set(rv.astype(str).unique())
            overlap = len(lset & rset) / max(min(len(lset), len(rset)), 1)
            if not same_name and overlap < min_overlap:
                continue
            l_unique = bool(lv.is_unique)
            r_unique = bool(rv.is_unique)
            rel = {(True, True): "1:1", (True, False): "1:many",
                   (False, True): "many:1", (False, False): "many:many"}[(l_unique, r_unique)]
            rows.append({"left": lc, "right": rc, "same_name": same_name,
                         "value_overlap": overlap, "relationship": rel,
                         "left_coverage": float(lv.astype(str).isin(rset).mean())})
    if not rows:
        return pd.DataFrame(columns=["left", "right", "same_name", "value_overlap",
                                     "relationship", "left_coverage"])
    return (pd.DataFrame(rows)
            .sort_values(["same_name", "value_overlap"], ascending=False)
            .reset_index(drop=True))


AGG_NUMERIC = ("mean", "max", "min", "sum")


def aggregate(df: pd.DataFrame, key: str, prefix: str,
              numeric_aggs: tuple[str, ...] = AGG_NUMERIC) -> pd.DataFrame:
    """Roll a 'many' table up to one row per key: row count, numeric
    mean/max/min/sum, and distinct count for categoricals."""
    g = df.groupby(key)
    parts = [g.size().rename(f"{prefix}_count")]
    for c in df.columns:
        if c == key:
            continue
        if is_numeric(df[c]):
            a = g[c].agg(list(numeric_aggs))
            a.columns = [f"{prefix}_{c}_{x}" for x in a.columns]
            parts.append(a)
        else:
            parts.append(g[c].nunique().rename(f"{prefix}_{c}_nunique"))
    return pd.concat(parts, axis=1).reset_index()


_AGG_WORDS = {"mean": "mean", "max": "maximum", "min": "minimum", "sum": "sum",
              "nunique": "number of distinct values"}


@dataclass
class JoinReport:
    rows_before: int
    rows_after: int
    matched_pct: float
    relationship: str
    new_columns: list[str]
    # new column -> {"source": "table.column" | None, "agg": str | None, "key", "logic"}
    lineage: dict[str, dict] = field(default_factory=dict)


def _join_lineage(right: pd.DataFrame, right_name: str, left_on: str, right_on: str,
                  prefix: str, aggregated: bool) -> dict[str, dict]:
    tail = (f"left join {right_name} on {left_on} = {right_on}; "
            f"missing when the key has no {right_name} record")
    out = {}
    if aggregated:
        out[f"{prefix}_count"] = {"source": None, "agg": "count", "key": right_on,
                                  "logic": f"number of {right_name} rows per {right_on}; {tail}"}
        for c in right.columns:
            if c == right_on:
                continue
            for agg in (AGG_NUMERIC if is_numeric(right[c]) else ("nunique",)):
                out[f"{prefix}_{c}_{agg}"] = {
                    "source": f"{right_name}.{c}", "agg": agg, "key": right_on,
                    "logic": f"{_AGG_WORDS[agg]} of {right_name}.{c} per {right_on}; {tail}"}
    else:
        for c in right.columns:
            if c != right_on:
                out[f"{prefix}_{c}"] = {"source": f"{right_name}.{c}", "agg": None,
                                        "key": right_on, "logic": f"{right_name}.{c}; {tail}"}
    return out


def safe_join(left: pd.DataFrame, right: pd.DataFrame, left_on: str, right_on: str,
              prefix: str, right_name: str | None = None) -> tuple[pd.DataFrame, JoinReport]:
    """Left join that never multiplies rows: if `right` has several rows per key
    it is aggregated first (see `aggregate`). New columns get `prefix`, and the
    report records how each one was made (for the data dictionary)."""
    rel = "many:1"
    right_name = right_name or prefix
    aggregated = not right[right_on].dropna().is_unique
    lineage = _join_lineage(right, right_name, left_on, right_on, prefix, aggregated)
    if aggregated:
        right = aggregate(right, right_on, prefix)
        rel = "1:many (aggregated)"
    else:
        right = right.rename(columns={c: f"{prefix}_{c}" for c in right.columns if c != right_on})
    lk = left[left_on]
    rk = right[right_on]
    if lk.dtype != rk.dtype:                       # e.g. int ids vs "00123" strings
        lk, rk = lk.astype(str), rk.astype(str)
    left2 = left.assign(__key=lk)
    right2 = right.drop(columns=[right_on]).assign(__key=rk.values)
    out = left2.merge(right2, on="__key", how="left").drop(columns="__key")
    new_cols = [c for c in out.columns if c not in left.columns]
    matched = float(lk.isin(set(rk)).mean())
    assert len(out) == len(left)
    return out, JoinReport(len(left), len(out), matched, rel, new_cols,
                           {c: lineage[c] for c in new_cols if c in lineage})


# --------------------------------------------------------------------------- target

def target_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """Columns that could define BAD: binary columns, and DPD / arrears / status
    columns from which a bad flag can be derived. The agent proposes, the user picks."""
    keywords = ("dpd", "bad", "default", "arrear", "overdue", "tunggak", "kol",
                "status", "flag", "target", "macet", "npl", "writeoff", "write_off")
    rows = []
    for c in df.columns:
        s = df[c]
        name = str(c).lower()
        hit = any(k in name for k in keywords)
        nun = s.nunique(dropna=True)
        if nun == 2:
            vals = sorted(s.dropna().unique().tolist(), key=str)
            rate = float((s == vals[-1]).mean())
            rows.append({"column": c, "kind": "binary", "name_hint": hit,
                         "values": vals, "rate_of_last_value": rate,
                         "suggestion": f"bad = ({c} == {vals[-1]!r})"})
        elif hit and is_numeric(s) and nun > 2:
            row = {"column": c, "kind": "numeric (e.g. DPD)", "name_hint": hit,
                   "values": [float(s.min()), float(s.max())]}
            for t in (30, 60, 90):
                row[f"bad_rate_if_>={t}"] = float((s >= t).mean())
            row["suggestion"] = f"bad = ({c} >= 90)"
            rows.append(row)
        elif hit and nun <= 15:
            rows.append({"column": c, "kind": "categorical status", "name_hint": hit,
                         "values": s.value_counts().to_dict(),
                         "suggestion": "choose which statuses count as bad"})
    return pd.DataFrame(rows)


def make_target(df: pd.DataFrame, column: str, rule: str, value) -> pd.Series:
    """Build y (1 = bad). rule: 'eq' (== value), 'ge' (>= value), 'in' (value is a list).
    Rows where the source is missing get NaN (indeterminate; exclude them)."""
    s = df[column]
    if rule == "eq":
        y = (s == value)
    elif rule == "ge":
        y = (pd.to_numeric(s, errors="coerce") >= float(value))
    elif rule == "in":
        y = s.isin(list(value))
    else:
        raise ValueError("rule must be eq, ge or in")
    return y.astype(float).where(s.notna()).rename("bad")


def assess_target(y: pd.Series, dates: pd.Series | None = None, freq: str = "M") -> dict:
    """Bad count/rate, and bad rate by period if a date column is given (to spot
    immature recent cohorts or a definition change)."""
    y = y.dropna()
    out = {"n": int(len(y)), "bads": int(y.sum()), "bad_rate": float(y.mean()),
           "warnings": []}
    if out["bads"] < 300:
        out["warnings"].append(f"only {out['bads']} bads; fewer than ~300 makes the "
                               "scorecard unstable")
    if out["bad_rate"] < 0.01 or out["bad_rate"] > 0.5:
        out["warnings"].append(f"unusual bad rate {out['bad_rate']:.2%}")
    if dates is not None:
        d = pd.to_datetime(dates.loc[y.index], errors="coerce", format="mixed")
        period = d.dt.to_period(freq).astype(str)
        by = y.groupby(period).agg(["size", "mean"]).rename(columns={"size": "n", "mean": "bad_rate"})
        out["by_period"] = by.reset_index(names="period").to_dict("records")
    return out


# --------------------------------------------------------------------------- split

def split(df: pd.DataFrame, y: pd.Series, test_size: float = 0.3, seed: int = 42,
          date_col: str | None = None, oot_from=None) -> dict[str, pd.Index]:
    """Row indices for train/test (stratified random by y) and, if `date_col` and
    `oot_from` are given, an out-of-time sample of rows dated on/after `oot_from`."""
    idx = y.dropna().index
    out: dict[str, pd.Index] = {}
    if date_col and oot_from is not None:
        d = pd.to_datetime(df.loc[idx, date_col], errors="coerce", format="mixed")
        oot = d >= pd.Timestamp(oot_from)
        out["oot"] = idx[oot.to_numpy()]
        idx = idx[~oot.to_numpy()]
    rng = np.random.default_rng(seed)
    test = []
    for cls in (0, 1):
        members = idx[(y.loc[idx] == cls).to_numpy()].to_numpy().copy()
        rng.shuffle(members)
        test.extend(members[: int(round(len(members) * test_size))])
    test_idx = pd.Index(sorted(test))
    out["test"] = test_idx
    out["train"] = idx.difference(test_idx)
    return out
