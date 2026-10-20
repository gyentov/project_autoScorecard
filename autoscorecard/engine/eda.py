"""Descriptive statistics and exploratory data analysis (EDA) of the modelling data.

Bening runs this after the target is agreed and before Citra plans the waterfall. It
measures; the points it raises are rule-based, so every one can be traced to a number.

    eda = describe(df, y, features, dev_index, late_index)
    pts = points(eda)

What is measured, per candidate variable:
    descriptive   count, missing, distinct values, share of the most common value;
                  numeric: mean, std, min, p1, p5, p25, p50, p75, p95, p99, max, skewness,
                  kurtosis, share of zeros / negatives, IQR outliers (Tukey, k = 1.5),
                  sentinel codes (see `is_code`: implausible negative values such as
                  -999999999999; a positive amount is never a code, since 999999 is a real
                  amount in rupiah or dong);
                  categorical: top categories with share and bad rate, rare categories
    vs the target bad rate of missing vs filled rows (two-proportion z-test), the
                  univariate AUC (numeric: the value itself as score; categorical: the
                  category's bad rate as score, in-sample) and the bad-rate trend over
                  five quantile bands (Spearman rho of band order vs bad rate)
    stability     early (development) vs late (out-of-time, or the latest 30% by date):
                  PSI, missing rate in both, and whether the risk ORDER of the bands holds
                  (Spearman rho of the bands' bad rates early vs late)
    together      Spearman correlation of the numeric variables; pairs with |rho| >= 0.9

The target relation is measured on the development rows only (never on late rows), so
EDA does not peek at the out-of-time sample for anything but stability.

Points (Bening's findings, each with a recommendation):
    constant      the most common value covers >= 95% of rows          -> drop
    high_missing  missing >= 30%                                        -> drop
    sentinel      a code value (e.g. -999999999999) covers >= 0.5% of rows -> treat as missing
    outliers      >= 1% of values beyond the IQR fences and a far tail
                  (max above p99 + 2 x (p99 - median), or the mirror below p1) -> cap
    leakage       univariate AUC >= 0.80 (Gini >= 0.60)                 -> drop (suspected leak)
    unstable      PSI >= 0.25 between early and late                    -> drop
    drift         0.10 <= PSI < 0.25                                    -> monitor
    correlated    |rho| >= 0.90 with another variable                   -> keep the stronger one
    weak          univariate AUC < 0.52                                 -> note
    skewed        |skewness| >= 2                                       -> note
    rare          categories under 1% of rows                           -> note
    bad_rate_moves the bad rate per period varies more than 2x          -> note
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

from .binning import is_numeric
from .metrics import auc as _auc, psi as _psi

CONSTANT = 0.95
HIGH_MISSING = 0.30
SENTINEL_SHARE = 0.005
CODE_DIGITS = 10               # -9999999999 and longer: no individual holds that
OUTLIER_SHARE = 0.01
IQR_K = 1.5
LEAKAGE_AUC = 0.80
UNSTABLE_PSI = 0.25
DRIFT_PSI = 0.10
CORRELATED = 0.90
WEAK_AUC = 0.52
SKEWED = 2.0
RARE = 0.01
N_BANDS = 5
LATE_SHARE = 0.30


# --------------------------------------------------------------------------- helpers

def two_proportion_p(b1: float, n1: float, b2: float, n2: float) -> float:
    """Two-sided p-value of H0: bad rate 1 = bad rate 2 (pooled z-test)."""
    if min(n1, n2) == 0:
        return 1.0
    p = (b1 + b2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(b1 / n1 - b2 / n2) / se
    return float(math.erfc(z / math.sqrt(2)))


def _r(v, k: int = 4):
    if v is None:
        return None
    v = float(v)
    return None if not math.isfinite(v) else round(v, k)


def _bands(x: pd.Series, edges: np.ndarray | None = None) -> tuple[pd.Series, np.ndarray]:
    """Quantile bands of a numeric series (edges from `x` unless given)."""
    if edges is None:
        edges = np.unique(np.quantile(x.dropna(), np.linspace(0, 1, N_BANDS + 1)))
    e = edges.astype(float).copy()
    if len(e) < 2:
        return pd.Series(0, index=x.index), edges
    e[0], e[-1] = -np.inf, np.inf
    return pd.Series(pd.cut(x, e, labels=False, include_lowest=True), index=x.index), edges


def _rho(a, b) -> float | None:
    a, b = pd.Series(a, dtype=float), pd.Series(b, dtype=float)
    ok = a.notna() & b.notna()
    if ok.sum() < 3 or a[ok].nunique() < 2 or b[ok].nunique() < 2:
        return None
    return _r(a[ok].rank().corr(b[ok].rank()), 3)


def is_code(value: float) -> bool:
    """A filler code, not a number anyone holds: negative and written only with nines
    (or -1). Positive values are never codes: 999999 or 99999999 are real amounts in
    rupiah or dong."""
    if value >= 0 or value != int(value):
        return False
    digits = str(int(-value))
    return value == -1 or set(digits) == {"9"}


def _sentinels(v: pd.Series) -> list[dict]:
    """Code values in at least 0.5% of rows. A long code (-9999999999 or longer, e.g.
    -999999999999) is a code in any column; a short one (-1, -9, -999, ...) only when
    every other value is >= 0, since a balance can be legitimately negative."""
    out = []
    n = len(v)
    for code, cnt in v[v < 0].value_counts().items():
        if not is_code(code) or cnt / n < SENTINEL_SHARE:
            continue
        rest = v[v != code]
        if len(str(int(-code))) >= CODE_DIGITS or (not rest.empty and rest.min() >= 0):
            out.append({"value": int(code), "share": _r(cnt / n)})
    return out


def _psi_cat(e: pd.Series, a: pd.Series) -> float:
    pe = e.fillna("(missing)").astype(str).value_counts(normalize=True)
    pa = a.fillna("(missing)").astype(str).value_counts(normalize=True)
    cats = pe.index.union(pa.index)
    pe = np.maximum(pe.reindex(cats, fill_value=0).to_numpy(), 1e-4)
    pa = np.maximum(pa.reindex(cats, fill_value=0).to_numpy(), 1e-4)
    return float(((pa - pe) * np.log(pa / pe)).sum())


# --------------------------------------------------------------------------- per variable

def _numeric(x: pd.Series) -> dict:
    v = x.dropna().astype(float)
    if v.empty:
        return {}
    q = v.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    iqr = q[0.75] - q[0.25]
    lo, hi = q[0.25] - IQR_K * iqr, q[0.75] + IQR_K * iqr
    n_out = int(((v < lo) | (v > hi)).sum()) if v.nunique() > 10 else 0
    return {"mean": _r(v.mean()), "std": _r(v.std()), "min": _r(v.min()), "p1": _r(q[0.01]),
            "p5": _r(q[0.05]), "p25": _r(q[0.25]), "p50": _r(q[0.5]), "p75": _r(q[0.75]),
            "p95": _r(q[0.95]), "p99": _r(q[0.99]), "max": _r(v.max()),
            "skew": _r(v.skew(), 3) if len(v) > 2 else None,
            "kurtosis": _r(v.kurt(), 3) if len(v) > 3 else None,
            "zero_share": _r((v == 0).mean()), "negative_share": _r((v < 0).mean()),
            "iqr_fences": [_r(lo), _r(hi)] if v.nunique() > 10 else None,
            "outliers": n_out, "outlier_share": _r(n_out / len(v)),
            "sentinels": _sentinels(v)}


def _categorical(x: pd.Series, y: pd.Series) -> dict:
    s = x.astype("object").where(x.notna(), None)
    share = s.value_counts(normalize=True, dropna=True)
    br = y.groupby(s).mean()
    top = [{"value": str(k), "share": _r(v), "bad_rate": _r(br.get(k))} for k, v in share.head(8).items()]
    rare = share[share < RARE]
    return {"top": top, "rare_categories": int(len(rare)), "rare_share": _r(rare.sum())}


def _vs_target(x: pd.Series, y: pd.Series, numeric: bool) -> dict:
    """Missing vs filled, univariate AUC and the trend over quantile bands (dev rows)."""
    out: dict = {}
    miss = x.isna()
    if miss.any() and (~miss).any():
        b1, n1, b2, n2 = y[miss].sum(), miss.sum(), y[~miss].sum(), (~miss).sum()
        out["missing_bad_rate"] = _r(b1 / n1)
        out["filled_bad_rate"] = _r(b2 / n2)
        out["missing_p_value"] = _r(two_proportion_p(b1, n1, b2, n2), 6)
    ok = ~miss
    if ok.sum() < 20 or y[ok].nunique() < 2:
        return out
    if numeric:
        a = _auc(y[ok], x[ok].astype(float))
        out["direction"] = "naik" if a >= 0.5 else "turun"      # bad rate rises / falls with x
        band, edges = _bands(x[ok].astype(float))
        t = y[ok].groupby(band).agg(["mean", "size"])
        out["bands"] = [{"band": int(k), "from": _r(edges[k]) if k < len(edges) else None,
                         "to": _r(edges[k + 1]) if k + 1 < len(edges) else None,
                         "share": _r(r["size"] / ok.sum()), "bad_rate": _r(r["mean"])}
                        for k, r in t.iterrows()]
        out["trend_rho"] = _rho(list(t.index), t["mean"])
    else:
        s = x[ok].astype(str)
        rate = y[ok].groupby(s).transform("mean")
        a = _auc(y[ok], rate)
    out["auc"] = _r(max(a, 1 - a))
    return out


def _stability(x_dev: pd.Series, x_late: pd.Series, y_dev: pd.Series, y_late: pd.Series,
               numeric: bool) -> dict:
    out = {"missing_dev": _r(x_dev.isna().mean()), "missing_late": _r(x_late.isna().mean())}
    if numeric and x_dev.notna().sum() >= 20 and x_late.notna().sum() >= 20:
        p, _ = _psi(x_dev.dropna().astype(float), x_late.dropna().astype(float))
        out["psi"] = _r(p)
        bd, edges = _bands(x_dev.astype(float))
        bl, _ = _bands(x_late.astype(float), edges)
        rd = y_dev.groupby(bd).mean()
        rl = y_late.groupby(bl).mean().reindex(rd.index)
        out["rank_rho"] = _rho(rd, rl)
    elif not numeric:
        out["psi"] = _r(_psi_cat(x_dev, x_late))
        s_dev, s_late = x_dev.astype(str), x_late.astype(str)
        big = s_dev.value_counts(normalize=True)
        big = big[big >= 0.02].index
        rd = y_dev.groupby(s_dev).mean().reindex(big)
        rl = y_late.groupby(s_late).mean().reindex(big)
        out["rank_rho"] = _rho(rd, rl)
    return out


# --------------------------------------------------------------------------- the analysis

def describe(df: pd.DataFrame, y: pd.Series, features: list[str], dev_index=None,
             late_index=None, date_col: str | None = None) -> dict:
    """The whole EDA as plain numbers (JSON-ready). `dev_index`: rows used for the target
    relation (default: every row with a known y); `late_index`: rows compared with them
    for stability (default: the latest 30% by `date_col`, if there is one)."""
    known = y.dropna().index
    dev = pd.Index(dev_index) if dev_index is not None else known
    late = pd.Index(late_index) if late_index is not None else pd.Index([])
    if late_index is None and date_col and date_col in df:
        d = pd.to_datetime(df.loc[known, date_col], errors="coerce", format="mixed")
        cut = d.quantile(1 - LATE_SHARE)
        late = known[(d >= cut).to_numpy()]
        dev = dev.difference(late)
    late = late.intersection(known)
    yd, yl = y.loc[dev].astype(int), y.loc[late].astype(int)
    over = {"rows": int(len(df)), "rows_known": int(len(known)), "rows_dev": int(len(dev)),
            "rows_late": int(len(late)), "features": len(features),
            "bad_rate": _r(y.loc[known].mean()), "bads": int(y.loc[known].sum()),
            "duplicate_rows": int(df[features].duplicated().sum()) if features else 0,
            "late_from": None}
    if date_col and date_col in df and len(late):
        d = pd.to_datetime(df.loc[late, date_col], errors="coerce", format="mixed")
        over["late_from"] = str(d.min().date()) if d.notna().any() else None
    if date_col and date_col in df:
        d = pd.to_datetime(df.loc[known, date_col], errors="coerce", format="mixed")
        q = y.loc[known].groupby(d.dt.to_period("Q").astype(str)).agg(["mean", "size"])
        over["by_period"] = [{"period": k, "n": int(r["size"]), "bad_rate": _r(r["mean"])}
                             for k, r in q.iterrows() if k != "NaT"]
    rows = []
    for c in features:
        x = df[c]
        num = is_numeric(x)
        n = len(x)
        top_share = x.value_counts(normalize=True, dropna=False).iloc[0] if n else 0
        rec = {"variable": c, "kind": "numerik" if num else "kategorik", "n": int(x.notna().sum()),
               "missing": int(x.isna().sum()), "missing_rate": _r(x.isna().mean()),
               "n_unique": int(x.nunique(dropna=True)), "top_share": _r(top_share)}
        rec.update(_numeric(x) if num else _categorical(x.loc[dev], yd))
        rec["target"] = _vs_target(x.loc[dev], yd, num)
        if len(late) >= 50:
            rec["stability"] = _stability(x.loc[dev], x.loc[late], yd, yl, num)
        rows.append(rec)
    nums = [c for c in features if is_numeric(df[c]) and df[c].nunique() > 1]
    pairs = []
    if len(nums) > 1:
        cm = df.loc[dev, nums].rank().corr()
        for i, a in enumerate(nums):
            for b in nums[i + 1:]:
                r = cm.loc[a, b]
                if pd.notna(r) and abs(r) >= CORRELATED:
                    pairs.append({"a": a, "b": b, "rho": _r(r, 3)})
    return {"overview": over, "variables": rows, "correlated_pairs": pairs,
            "thresholds": {"constant": CONSTANT, "high_missing": HIGH_MISSING,
                           "sentinel_share": SENTINEL_SHARE, "code_digits": CODE_DIGITS, "outlier_share": OUTLIER_SHARE,
                           "iqr_k": IQR_K, "leakage_auc": LEAKAGE_AUC, "unstable_psi": UNSTABLE_PSI,
                           "drift_psi": DRIFT_PSI, "correlated": CORRELATED, "weak_auc": WEAK_AUC,
                           "skewed": SKEWED, "rare": RARE}}


# --------------------------------------------------------------------------- Bening's points

def _pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def points(eda: dict) -> list[dict]:
    """Bening's findings, numbered P1, P2, ...: {"id", "kind", "variables", "finding",
    "evidence", "recommendation", "action", "severity"}. `action` is what she asks for:
    drop, treat_missing, cap, keep_one, monitor or note."""
    out: list[dict] = []

    def add(kind, variables, finding, evidence, recommendation, action, severity):
        out.append({"id": f"P{len(out) + 1}", "kind": kind, "variables": variables,
                    "finding": finding, "evidence": evidence, "recommendation": recommendation,
                    "action": action, "severity": severity})

    ov = eda["overview"]
    per = [p for p in ov.get("by_period", []) if p["n"] >= 30 and p["bad_rate"]]
    if len(per) >= 2:
        lo, hi = min(p["bad_rate"] for p in per), max(p["bad_rate"] for p in per)
        if hi > 2 * lo:
            add("bad_rate_moves", [], f"Bad rate per kuartal bergerak dari {_pct(lo)} sampai {_pct(hi)} "
                f"(lebih dari 2x).", {"min": lo, "max": hi}, "Dicatat; sampel OOT dan kalibrasi "
                "cut-off perlu memperhatikan pergeseran ini.", "note", "info")
    if ov.get("duplicate_rows"):
        add("duplicates", [], f"{ov['duplicate_rows']:,} baris identik di semua kandidat fitur.",
            {"duplicate_rows": ov["duplicate_rows"]}, "Dicatat; bisa wajar (profil sama), tidak "
            "dibuang.", "note", "info")
    strength = {v["variable"]: v["target"].get("auc") or 0 for v in eda["variables"]}
    for v in eda["variables"]:
        name, t, s = v["variable"], v["target"], v.get("stability", {})
        if v["top_share"] is not None and v["top_share"] >= CONSTANT:
            add("constant", [name], f"{name}: satu nilai menutupi {_pct(v['top_share'])} baris.",
                {"top_share": v["top_share"]}, "Buang: hampir tidak ada variasi untuk membedakan risiko.",
                "drop", "tinggi")
            continue
        if v["missing_rate"] >= HIGH_MISSING:
            ev = {k: t.get(k) for k in ("missing_bad_rate", "filled_bad_rate", "missing_p_value")}
            add("high_missing", [name], f"{name}: missing {_pct(v['missing_rate'])} "
                f"(bad rate missing {_pct(t.get('missing_bad_rate'))}, terisi {_pct(t.get('filled_bad_rate'))}).",
                {"missing_rate": v["missing_rate"], **ev}, f"Buang: missing di atas {HIGH_MISSING:.0%}.",
                "drop", "tinggi")
        for sn in v.get("sentinels", []):
            add("sentinel", [name], f"{name}: nilai {sn['value']:,} muncul di {_pct(sn['share'])} baris; "
                "angka ini tidak mungkin dimiliki perorangan (kemungkinan kode, bukan angka sungguhan).",
                {"value": sn["value"], "share": sn["share"], "p95": v.get("p95")},
                f"Perlakukan {sn['value']:,} sebagai missing (bin sendiri), bukan nilai ekstrem.",
                "treat_missing", "sedang")
        far = v.get("max") is not None and (
            v["max"] > v["p99"] + 2 * (v["p99"] - v["p50"]) or v["min"] < v["p1"] - 2 * (v["p50"] - v["p1"]))
        if v.get("outlier_share") and v["outlier_share"] >= OUTLIER_SHARE and far:
            add("outliers", [name], f"{name}: {v['outliers']:,} nilai ({_pct(v['outlier_share'])}) di luar "
                f"pagar IQR {v['iqr_fences']}.", {"outlier_share": v["outlier_share"],
                                                 "fences": v["iqr_fences"], "max": v.get("max"),
                                                 "p99": v.get("p99")},
                "Cap di pagar IQR sebelum binning.", "cap", "sedang")
        if t.get("auc") and t["auc"] >= LEAKAGE_AUC:
            add("leakage", [name], f"{name}: AUC univariat {t['auc']:.2f} (Gini {2 * t['auc'] - 1:.2f}), "
                "terlalu kuat untuk satu variabel aplikasi.", {"auc": t["auc"]},
                "Buang: dicurigai bocor (tercatat setelah keputusan atau mengandung hasil).", "drop", "tinggi")
        if s.get("psi") is not None and s["psi"] >= UNSTABLE_PSI:
            add("unstable", [name], f"{name}: PSI {s['psi']:.2f} antara data pengembangan dan data terbaru.",
                {"psi": s["psi"], "rank_rho": s.get("rank_rho"), "missing_dev": s.get("missing_dev"),
                 "missing_late": s.get("missing_late")},
                "Buang: distribusinya bergeser terlalu jauh.", "drop", "tinggi")
        elif s.get("psi") is not None and s["psi"] >= DRIFT_PSI:
            add("drift", [name], f"{name}: PSI {s['psi']:.2f} (bergeser sedang).",
                {"psi": s["psi"], "rank_rho": s.get("rank_rho")},
                "Tetap kandidat, dipantau PSI-nya di setiap cut.", "monitor", "sedang")
        if t.get("auc") is not None and t["auc"] < WEAK_AUC:
            add("weak", [name], f"{name}: AUC univariat {t['auc']:.3f}, hampir tidak membedakan bad.",
                {"auc": t["auc"]}, "Dicatat; kemungkinan gugur di filter IV.", "note", "info")
        if v.get("skew") is not None and abs(v["skew"]) >= SKEWED:
            add("skewed", [name], f"{name}: sangat menceng (skewness {v['skew']:.1f}; median "
                f"{v['p50']:g}, maks {v['max']:g}).", {"skew": v["skew"], "p50": v["p50"], "max": v["max"]},
                "Dicatat; binning kuantil tidak terpengaruh, tidak perlu transformasi.", "note", "info")
        if v.get("rare_categories"):
            add("rare", [name], f"{name}: {v['rare_categories']} kategori di bawah {RARE:.0%} "
                f"(total {_pct(v['rare_share'])}).", {"rare_categories": v["rare_categories"],
                                                      "rare_share": v["rare_share"]},
                "Dicatat; kategori kecil digabung otomatis saat binning.", "note", "info")
    for p in eda["correlated_pairs"]:
        keep, drop = sorted((p["a"], p["b"]), key=lambda c: -strength.get(c, 0))
        add("correlated", [keep, drop], f"{p['a']} dan {p['b']} berkorelasi Spearman {p['rho']:.2f}.",
            {"rho": p["rho"], "auc_" + keep: strength.get(keep), "auc_" + drop: strength.get(drop),
             "keep": keep, "drop": drop},
            f"Pakai {keep} (AUC lebih tinggi), buang {drop}.", "keep_one", "sedang")
    return out


OUTCOME_NAME = re.compile(r"(dpd|default|bad|writeoff|write_off|wo_|collect|recover|arrear|"
                          r"tunggak|kol_|npl|status_akhir|perf)", re.I)
