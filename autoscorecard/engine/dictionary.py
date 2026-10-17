"""Data dictionary of the raw modelling data, plus outlier detection and treatment.

For every column: description, variable type (loan, funding, ...), data type,
role, how it was made (if derived), row count, null count, min, max, distinct
values and outliers.

What comes from where:
    measured   count, nulls, min, max, distinct, data type, outliers
    recorded   logic and source of derived columns (joins, aggregations, target)
    supplied   description and variable type: from the user's dictionary file or
               the API. Missing ones are flagged, never invented; a variable type
               may be guessed from the column name and is then marked "guessed".

Outliers (numeric columns with more than 10 distinct values):
    iqr         Tukey fences: below Q1 - k*IQR or above Q3 + k*IQR (k = 1.5)
    percentile  below the `lower` or above the `upper` percentile (1% / 99%)
Fences are fitted on the training sample and applied to every sample.
Treatment "none" leaves values as they are; "cap" clips them to the fences.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .binning import is_numeric

# Keyword rules for guessing a variable type from the column name. Order matters:
# the first match wins. A user-supplied type always replaces a guess.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("target / performance", ("dpd_12m", "bad", "default", "target", "perf_", "npl")),
    ("bureau / credit history", ("bur_", "bureau", "slik", "prev_", "dpd", "kol", "arrear")),
    ("funding", ("deposit", "saving", "tabungan", "giro", "casa", "balance", "funding")),
    ("loan", ("loan", "amount", "tenor", "plafon", "installment", "angsuran", "rate", "staff")),
    ("income & employment", ("income", "salary", "gaji", "job", "employ", "occupation")),
    ("demographic", ("age", "marital", "province", "gender", "education", "dependent", "umur")),
    ("application", ("channel", "app_", "branch", "agent")),
    ("identifier", ("_id", "id_", "nik", "cif")),
]

LOW_CARDINALITY = 10           # numeric columns with <= this many values: no outlier test


def guess_category(name: str) -> str | None:
    n = name.lower()
    for cat, keys in CATEGORY_RULES:
        if any(k in n for k in keys):
            return cat
    return None


def data_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        v = s.dropna()
        return "integer (stored as decimal)" if len(v) and (v == v.round()).all() else "decimal"
    return "text"


# --------------------------------------------------------------------------- outliers

@dataclass
class OutlierRule:
    method: str = "iqr"            # iqr | percentile
    k: float = 1.5                 # iqr multiplier
    lower: float = 0.01            # percentile method
    upper: float = 0.99
    treatment: str = "none"        # none | cap

    def __post_init__(self):
        if self.method not in ("iqr", "percentile"):
            raise ValueError("outliers.method must be iqr or percentile")
        if self.treatment not in ("none", "cap"):
            raise ValueError("outliers.treatment must be none or cap")

    @property
    def description(self) -> str:
        if self.method == "iqr":
            return (f"IQR (Tukey) fences: a value is an outlier if it is below Q1 − {self.k:g}×IQR "
                    f"or above Q3 + {self.k:g}×IQR, with Q1/Q3 the 25th/75th percentiles of the "
                    "training sample")
        return (f"percentile fences: a value is an outlier if it is below the "
                f"{self.lower:.0%} or above the {self.upper:.0%} percentile of the training sample")

    @property
    def treatment_description(self) -> str:
        if self.treatment == "cap":
            return ("capped: values beyond a fence are replaced by the fence value before "
                    "binning, in every sample (fences from the training sample). When many "
                    "values lie beyond a fence they all become one value, so the end bins can "
                    "merge and their WoE change; new data must be capped the same way")
        return ("none: values are kept as they are. Binning makes this safe: the first and "
                "last bins are open-ended, so an extreme value only receives the WoE of its "
                "end bin and cannot pull the model; the same holds for new data at scoring time")

    def fences(self, x: pd.Series) -> tuple[float, float] | None:
        v = pd.to_numeric(x, errors="coerce").dropna()
        if v.nunique() <= LOW_CARDINALITY:
            return None
        if self.method == "iqr":
            q1, q3 = v.quantile([0.25, 0.75])
            return float(q1 - self.k * (q3 - q1)), float(q3 + self.k * (q3 - q1))
        lo, hi = v.quantile([self.lower, self.upper])
        return float(lo), float(hi)


def treat_outliers(df: pd.DataFrame, features: list[str], train_index, rule: OutlierRule
                   ) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Fences from the training rows; if treatment is "cap", clip every sample.
    Returns (data, {feature: {lower, upper, n_low, n_high, capped}})."""
    out = df.copy() if rule.treatment == "cap" else df
    info = {}
    for f in features:
        if not is_numeric(df[f]):
            continue
        fe = rule.fences(df.loc[train_index, f])
        if fe is None:
            info[f] = {"lower": None, "upper": None, "n_low": 0, "n_high": 0, "capped": False,
                       "note": f"not tested (≤ {LOW_CARDINALITY} distinct values)"}
            continue
        lo, hi = fe
        x = df[f]
        info[f] = {"lower": lo, "upper": hi, "n_low": int((x < lo).sum()),
                   "n_high": int((x > hi).sum()), "capped": rule.treatment == "cap", "note": ""}
        if rule.treatment == "cap":
            out[f] = x.clip(lower=lo, upper=hi)
    return out, info


# --------------------------------------------------------------------------- dictionary

_COLS = {"variable": ("variable", "column", "name", "field", "variabel", "kolom"),
         "description": ("description", "desc", "deskripsi", "keterangan", "definition"),
         "category": ("category", "variable_type", "type", "group", "kategori", "jenis"),
         "logic": ("logic", "formula", "derivation", "rumus", "logika")}


@dataclass
class DataDictionary:
    """User-supplied metadata plus recorded lineage, keyed by column name."""
    entries: dict[str, dict] = field(default_factory=dict)

    def _get(self, col: str) -> dict:
        return self.entries.setdefault(col, {})

    def set(self, col: str, description: str | None = None, category: str | None = None,
            logic: str | None = None, source: str | None = None) -> None:
        e = self._get(col)
        for k, v in (("description", description), ("category", category),
                     ("logic", logic), ("source", source)):
            if v:
                e[k] = v

    def add_table(self, table: str, df: pd.DataFrame) -> None:
        """Columns of an uploaded table: source = table.column (no derivation)."""
        for c in df.columns:
            self._get(f"{table}.{c}")                 # source-level entry
            self._get(c).setdefault("source", f"{table}.{c}")

    def add_join(self, report) -> None:
        for col, lin in report.lineage.items():
            e = self._get(col)
            e.setdefault("logic", lin["logic"])
            e.setdefault("source", lin["source"] or "")
            e.setdefault("agg", lin["agg"])

    def update(self, rows: list[dict] | pd.DataFrame) -> int:
        """Merge user metadata (list of dicts or a DataFrame with variable /
        description / category / logic columns, any common header spelling)."""
        df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
        lower = {str(c).strip().lower(): c for c in df.columns}
        pick = {k: next((lower[a] for a in alts if a in lower), None) for k, alts in _COLS.items()}
        if pick["variable"] is None:
            raise ValueError("dictionary needs a 'variable' (or 'column' / 'name') column")
        n = 0
        for r in df.to_dict("records"):
            var = str(r[pick["variable"]]).strip()
            if not var or var == "nan":
                continue
            vals = {k: (str(r[c]).strip() if c and pd.notna(r[c]) else None)
                    for k, c in pick.items() if k != "variable"}
            self.set(var, vals["description"], vals["category"], vals["logic"])
            if vals["category"]:
                self._get(var)["category_from_user"] = True
            n += 1
        return n

    def import_file(self, path) -> int:
        p = Path(path)
        df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(p)
        return self.update(df)

    # ------------------------------------------------------------- persistence
    def save(self, project) -> None:
        (project.dir("work") / "data_dictionary.json").write_text(
            json.dumps(self.entries, indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, project) -> "DataDictionary":
        p = project.dir("work") / "data_dictionary.json"
        return cls(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})

    # ------------------------------------------------------------- lookups
    def _source_meta(self, col: str, key: str) -> str | None:
        """Description / category inherited from the source column of a derived one."""
        e = self.entries.get(col, {})
        src = e.get("source")
        if not src:
            return None
        se = self.entries.get(src) or self.entries.get(src.split(".", 1)[-1]) or {}
        v = se.get(key)
        if v and key == "description" and e.get("agg"):
            from .data import _AGG_WORDS
            return f"{_AGG_WORDS.get(e['agg'], e['agg']).capitalize()} of: {v[0].lower()}{v[1:]}"
        return v

    def describe(self, col: str) -> tuple[str, str, str, str]:
        """(description, category, category_source, logic)."""
        e = self.entries.get(col, {})
        desc = e.get("description") or self._source_meta(col, "description") or ""
        cat, src = e.get("category"), "user"
        if not cat:
            cat = self._source_meta(col, "category")
            src = "from source" if cat else ""
        if not cat:
            cat = guess_category(col) or ""
            src = "guessed" if cat else ""
        logic = e.get("logic", "")
        se = self.entries.get(e.get("source") or "") or \
            self.entries.get((e.get("source") or "").split(".", 1)[-1]) or {}
        if logic and se.get("logic") and se is not e:
            logic = f"{se['logic']}; then {logic}"      # user's logic on the source column
        return desc, cat, src, logic

    # ------------------------------------------------------------- the table
    def build(self, df: pd.DataFrame, roles: dict[str, str] | None = None,
              outliers: dict[str, dict] | None = None, rule: OutlierRule | None = None
              ) -> pd.DataFrame:
        roles, outliers, rule = roles or {}, outliers or {}, rule or OutlierRule()
        rows = []
        n = len(df)
        for c in df.columns:
            s = df[c]
            desc, cat, cat_src, logic = self.describe(c)
            num = is_numeric(s) or pd.api.types.is_datetime64_any_dtype(s)
            o = outliers.get(c)
            if o is None:
                o_method = "not applicable (non-numeric)" if not is_numeric(s) else \
                    "not tested (not a candidate feature)"
                o_count, o_low, o_high, treat = None, None, None, "none"
            elif o["lower"] is None:
                o_method, o_count, o_low, o_high, treat = o["note"], None, None, None, "none"
            else:
                o_method = rule.method
                o_count, o_low, o_high = o["n_low"] + o["n_high"], o["lower"], o["upper"]
                treat = "capped" if o["capped"] else "none"
            src = self.entries.get(c, {}).get("source")
            logic = logic or (f"raw: {src}" if src else "(not recorded)")
            rows.append({
                "variable": c, "description": desc or "(not provided)",
                "variable_type": cat or "(not provided)", "variable_type_source": cat_src,
                "role": roles.get(c, "not used"), "data_type": data_type(s),
                "logic": logic, "source": src or "",
                "count": n, "non_null": int(s.notna().sum()), "null_count": int(s.isna().sum()),
                "null_pct": float(s.isna().mean()) if n else 0.0,
                "distinct": int(s.nunique(dropna=True)),
                "min": s.min() if num and s.notna().any() else None,
                "max": s.max() if num and s.notna().any() else None,
                "outlier_method": o_method, "outlier_lower": o_low, "outlier_upper": o_high,
                "outlier_count": o_count, "outlier_treatment": treat,
            })
        return pd.DataFrame(rows)


def dictionary_markdown(table: pd.DataFrame, rule: OutlierRule, level: int = 2,
                        samples: dict[str, int] | None = None, title: str = "Raw data") -> str:
    """The raw-data section of the model documentation (level 1 = standalone file)."""
    h = "#" * level
    t = table.copy()
    for c in ("min", "max", "outlier_lower", "outlier_upper"):
        t[c] = t[c].map(_fmt_value)
    t["null_pct"] = t["null_pct"].map(lambda v: f"{v:.1%}")
    t["outlier_count"] = t["outlier_count"].map(lambda v: "" if v is None or pd.isna(v)
                                                else str(int(v)))
    t["variable_type"] = [f"{v} (guessed)" if s == "guessed" else v
                          for v, s in zip(t["variable_type"], t["variable_type_source"])]
    missing_desc = int((table["description"] == "(not provided)").sum())
    guessed = int((table["variable_type_source"] == "guessed").sum())
    out = [f"{h} {title}", ""]
    if samples:
        out += ["Rows: " + ", ".join(f"{k} {v:,}" for k, v in samples.items())
                + f" · columns: {len(table)}.", ""]
    out += [f"**Outlier detection:** {rule.description}. Numeric columns with "
            f"{LOW_CARDINALITY} or fewer distinct values are not tested.", "",
            f"**Outlier treatment:** {rule.treatment_description}.", ""]
    if missing_desc or guessed:
        out += [f"**To complete:** {missing_desc} variable(s) without a description, "
                f"{guessed} with a guessed variable type. Upload a data dictionary "
                "(columns: variable, description, category, logic) to fill them.", ""]
    from .waterfall_report import _md_table
    out += [f"{h}# Variables", "",
            _md_table(t[["variable", "description", "variable_type", "role", "data_type",
                         "logic"]]),
            f"{h}# Statistics", "",
            _md_table(t[["variable", "count", "null_count", "null_pct", "distinct", "min", "max",
                         "outlier_method", "outlier_lower", "outlier_upper", "outlier_count",
                         "outlier_treatment"]])]
    return "\n".join(out)


def _fmt_value(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (float, np.floating)):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    return str(v)
