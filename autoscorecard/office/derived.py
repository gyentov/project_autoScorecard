"""Bening's derived variables: new columns made from existing ones, at any point in the
project (also after Cut 1). Each is a small, auditable formula, never free code:

    ratio    a / b            (b = 0 gives missing)
    diff     a - b
    sum      a + b
    product  a * b
    log      ln(1 + a)        (a below 0 gives missing)
    flag     1 if a <op> value else 0     (op: >, >=, <, <=, ==, !=)
    missing  1 if a is missing else 0

A derived variable is a proposal first ("derive_variable", the Boss ACCs it); once
accepted it is stored in work/derived.json, added to the dataset every time the dataset
is loaded, documented in Bening's dictionary (meaning = the formula), and a candidate in
the next cut.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

OPS = ("ratio", "diff", "sum", "product", "log", "flag", "missing")
CMP = {">": np.greater, ">=": np.greater_equal, "<": np.less, "<=": np.less_equal,
       "==": np.equal, "!=": np.not_equal}
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")

# "dti = loan_amount / cust_income", "log_income = log(cust_income)", "old = cust_age >= 50"
_EXPR = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*)\s*=\s*(?:"
    r"(?P<fn>log|missing)\s*\(\s*(?P<x>\w+)\s*\)|"
    r"(?P<a>\w+)\s*(?P<op>[/*+-]|>=|<=|==|!=|>|<)\s*(?P<b>[\w.]+))\s*$")
_ARITH = {"/": "ratio", "-": "diff", "+": "sum", "*": "product"}


def parse(expr: str) -> dict | None:
    """A spec from a typed formula, or None if it is not one."""
    m = _EXPR.match(expr)
    if not m:
        return None
    if m.group("fn"):
        return {"name": m.group("name"), "op": m.group("fn"), "a": m.group("x")}
    op = m.group("op")
    if op in _ARITH:
        return {"name": m.group("name"), "op": _ARITH[op], "a": m.group("a"), "b": m.group("b")}
    try:
        value: float | str = float(m.group("b"))
    except ValueError:
        value = m.group("b")
    return {"name": m.group("name"), "op": "flag", "a": m.group("a"), "cmp": op, "value": value}


def validate(spec: dict, columns: list[str]) -> dict:
    s = {k: v for k, v in spec.items() if v not in (None, "")}
    if s.get("op") not in OPS:
        raise ValueError(f"operasi harus salah satu dari {', '.join(OPS)}")
    if not NAME.match(str(s.get("name", ""))):
        raise ValueError("nama variabel baru: huruf, angka dan garis bawah saja")
    if s["name"] in columns:
        raise ValueError(f"kolom '{s['name']}' sudah ada")
    need = ["a", "b"] if s["op"] in ("ratio", "diff", "sum", "product") else ["a"]
    for k in need:
        if s.get(k) not in columns:
            close = [c for c in columns if str(s.get(k, "")).lower() in c.lower()][:4]
            raise ValueError(f"kolom '{s.get(k)}' tidak ada" + (f"; maksudnya {', '.join(close)}?" if close else ""))
    if s["op"] == "flag":
        if s.get("cmp") not in CMP or "value" not in s:
            raise ValueError("flag butuh pembanding (>, >=, <, <=, ==, !=) dan nilai")
    return {k: s[k] for k in ("name", "op", "a", "b", "cmp", "value") if k in s}


def compute(df: pd.DataFrame, spec: dict) -> pd.Series:
    op, a = spec["op"], df[spec["a"]]
    num = lambda s: pd.to_numeric(s, errors="coerce")            # noqa: E731
    if op in ("ratio", "diff", "sum", "product"):
        x, y = num(a), num(df[spec["b"]])
        out = {"ratio": x / y.where(y != 0), "diff": x - y, "sum": x + y, "product": x * y}[op]
    elif op == "log":
        x = num(a)
        out = np.log1p(x.where(x >= 0))
    elif op == "missing":
        out = a.isna().astype(int)
    else:                                                    # flag
        v = spec["value"]
        x = num(a) if isinstance(v, (int, float)) else a.astype(str)
        out = pd.Series(np.where(a.isna(), np.nan, CMP[spec["cmp"]](x, v).astype(float)), index=df.index)
    return pd.Series(out, index=df.index, name=spec["name"]).replace([np.inf, -np.inf], np.nan)


def formula(spec: dict) -> str:
    op = spec["op"]
    return {"ratio": f"{spec.get('a')} / {spec.get('b')}", "diff": f"{spec.get('a')} - {spec.get('b')}",
            "sum": f"{spec.get('a')} + {spec.get('b')}", "product": f"{spec.get('a')} × {spec.get('b')}",
            "log": f"ln(1 + {spec.get('a')})", "missing": f"1 jika {spec.get('a')} kosong",
            "flag": f"1 jika {spec.get('a')} {spec.get('cmp')} {spec.get('value')}"}[op]


def meaning(spec: dict, meanings: dict[str, str]) -> str:
    """The new variable's meaning in words, from its inputs' meanings."""
    a = meanings.get(spec["a"]) or spec["a"]
    b = meanings.get(spec.get("b", "")) or spec.get("b", "")
    return {"ratio": f"rasio {a} terhadap {b}", "diff": f"selisih {a} dikurangi {b}",
            "sum": f"jumlah {a} dan {b}", "product": f"perkalian {a} dan {b}",
            "log": f"logaritma {a} (meredam nilai ekstrem)",
            "missing": f"penanda {a} kosong (1 = kosong)",
            "flag": f"penanda {a} {spec.get('cmp')} {spec.get('value')} (1 = ya)"}[spec["op"]]


def path(project):
    return project.dir("work") / "derived.json"


def load(project) -> list[dict]:
    p = path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def signature(project) -> float:
    p = path(project)
    return p.stat().st_mtime if p.exists() else 0.0


def add(project, spec: dict, by: str = "bening", reason: str = "") -> dict:
    items = [x for x in load(project) if x["name"] != spec["name"]]
    rec = {**spec, "by": by, "reason": reason, "formula": formula(spec)}
    path(project).write_text(json.dumps(items + [rec], ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def apply_all(df: pd.DataFrame, specs: list[dict]) -> pd.DataFrame:
    """The dataset with every accepted derived variable (in order: one may use another)."""
    if not specs:
        return df
    df = df.copy()
    for s in specs:
        if s["name"] not in df.columns:
            df[s["name"]] = compute(df, s)
    return df
