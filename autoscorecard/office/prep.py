"""Bening's recommendations, by rule (no LLM): join plan, target, split, features.

Each function returns a proposal plus the evidence for it, so the meeting can
show WHY. The Boss approves or changes it; nothing here is final on its own.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ..engine import data as D
from ..engine.dictionary import DataDictionary

DICT_NAME = re.compile(r"(dict|kamus|glossary|metadata)", re.I)
BASE_HINT = re.compile(r"(app|aplikasi|loan|pinjam|account|rekening|contract|kontrak)", re.I)
OUTCOME_HINT = re.compile(r"(perf|target|outcome|kinerja|result|bad|default)", re.I)


def split_uploads(project) -> tuple[dict[str, pd.DataFrame], list[Path]]:
    """(data tables by name, data dictionary files) from the project's raw/ folder."""
    tables, dicts = {}, []
    for p in project.raw_files():
        if DICT_NAME.search(p.stem):
            dicts.append(p)
        else:
            tables[p.stem] = D.load_table(p)
    return tables, dicts


def _prefix(name: str) -> str:
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    return n if len(n) <= 6 else n[:4]


def _unique_id_cols(df: pd.DataFrame) -> list[str]:
    prof = D.profile(df)
    return prof.loc[prof.role == "id", "column"].tolist()


def propose_join_plan(tables: dict[str, pd.DataFrame]) -> dict:
    """Base = the table with a unique id whose name looks like applications/loans
    (else the largest such table). Every other table is joined on its best key:
    same column name first, then value overlap."""
    if not tables:
        raise ValueError("no data tables uploaded")
    with_id = {n: t for n, t in tables.items() if _unique_id_cols(t)}
    pool = with_id or tables
    base = max(pool, key=lambda n: (bool(BASE_HINT.search(n)), not OUTCOME_HINT.search(n),
                                    len(pool[n])))
    joined = tables[base]
    joins, skipped = [], []
    for name, t in tables.items():
        if name == base:
            continue
        keys = D.suggest_join_keys(joined, t)
        keys = keys[keys.left_coverage > 0.3] if len(keys) else keys
        if not len(keys):
            skipped.append({"table": name, "reason": "tidak ada kolom kunci yang cocok"})
            continue
        k = keys.iloc[0]
        j = {"right": name, "left_on": k.left, "right_on": k.right, "prefix": _prefix(name),
             "relationship": k.relationship, "coverage": round(float(k.left_coverage), 4)}
        joins.append(j)
        joined, _ = D.safe_join(joined, t, k.left, k.right, j["prefix"], right_name=name)
    return {"base": base, "joins": joins, "skipped": skipped,
            "rows": {n: len(t) for n, t in tables.items()}}


def apply_join_plan(tables: dict[str, pd.DataFrame], plan: dict,
                    dictionary: DataDictionary | None = None) -> tuple[pd.DataFrame, list[dict]]:
    df = tables[plan["base"]]
    reports = []
    if dictionary is not None:
        for n, t in tables.items():
            dictionary.add_table(n, t)
    for j in plan["joins"]:
        df, rep = D.safe_join(df, tables[j["right"]], j["left_on"], j["right_on"], j["prefix"],
                              right_name=j["right"])
        if dictionary is not None:
            dictionary.add_join(rep)
        reports.append({"right": j["right"], "relationship": rep.relationship,
                        "matched_pct": rep.matched_pct, "new_columns": len(rep.new_columns)})
    return df, reports


def propose_target(df: pd.DataFrame, plan: dict) -> dict:
    """Pick the most outcome-like candidate: columns from an outcome table
    (performance / target / ...) first, then DPD-like numbers, then binary flags."""
    cands = D.target_candidates(df)
    if not len(cands):
        raise ValueError("no target candidate found")
    out_prefixes = [j["prefix"] for j in plan["joins"] if OUTCOME_HINT.search(j["right"])]

    def score(r) -> float:
        col = str(r.column).lower()
        s = 3.0 if any(col.startswith(p + "_") for p in out_prefixes) else 0.0
        s += 2.0 if r.kind.startswith("numeric") and "dpd" in col else 0.0
        s += 2.0 if r.kind == "binary" and OUTCOME_HINT.search(col) else 0.0
        s += 1.0 if re.search(r"(12m|24m|perf|bad|default)", col) else 0.0
        s -= 2.0 if col.startswith(("bur_", "prev")) or "prev" in col else 0.0
        return s

    cands = cands.assign(score=[score(r) for r in cands.itertuples()]).sort_values(
        "score", ascending=False)
    best = cands.iloc[0]
    if best.kind == "binary":
        rule, value = "eq", best["values"][-1]
    elif best.kind.startswith("numeric"):
        rule, value = "ge", 90
    else:
        rule, value = "in", []
    source_prefix = next((p for p in out_prefixes if str(best.column).startswith(p + "_")), None)
    return {"column": best.column, "rule": rule, "value": value,
            "source_prefix": source_prefix,
            "candidates": cands.head(6).to_dict("records")}


def propose_split(df: pd.DataFrame, y: pd.Series) -> dict:
    """Date column from the profile; out-of-time = from the month holding the 85th
    percentile of dates (about the latest 15%). No date column -> no OOT sample."""
    prof = D.profile(df)
    dates = [c for c in prof.loc[prof.role == "date", "column"] if df[c].notna().mean() > 0.9]
    if not dates:
        return {"date_col": None, "oot_from": None, "note": "tidak ada kolom tanggal; tanpa OOT"}
    col = dates[0]
    d = pd.to_datetime(df[col], errors="coerce", format="mixed")
    cut = d.quantile(0.85)
    oot_from = pd.Timestamp(cut.year, cut.month, 1).strftime("%Y-%m-%d")
    return {"date_col": col, "oot_from": oot_from,
            "oot_share": float((d >= pd.Timestamp(oot_from)).mean())}


def feature_list(df: pd.DataFrame, target: dict, exclude: dict[str, str]
                 ) -> tuple[list[str], dict[str, str]]:
    """Candidate features and, for everything left out, the reason."""
    prof = D.profile(df).set_index("column")
    out: dict[str, str] = {}
    for c in df.columns:
        role = prof.loc[c, "role"]
        if c == target["column"]:
            out[c] = "sumber target"
        elif target.get("source_prefix") and str(c).startswith(target["source_prefix"] + "_"):
            out[c] = "data hasil (outcome), bukan fitur"
        elif role in ("id", "date"):
            out[c] = f"kolom {role}"
        elif c == target.get("date_col"):
            out[c] = "kolom tanggal split"
        elif c in exclude:
            out[c] = exclude[c]
    return [c for c in df.columns if c not in out], out
