"""Risk bands from the score, and risk levels (VL, L, M, H, VH) built from them.

    bands = risk_bands(score_train, y_train)            # 20 equal-population bands,
                                                        # any < 5% merged with a neighbour
    table = band_table(bands, {"train": (s, y), "test": (s2, y2), "oot": (s3, y3)})
    levels = propose_levels(bands)                       # contiguous groups, ~20% each
    level_table(bands, levels, samples)                  # score range, share, bad rate

Band 1 holds the LOWEST scores (highest risk). Bands are defined by score cut-offs,
so the same cut-offs apply to any sample.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LEVELS = ("VH", "H", "M", "L", "VL")             # from the lowest scores (riskiest) up
LEVEL_NAMES = {"VH": "very high", "H": "high", "M": "medium", "L": "low", "VL": "very low"}


def risk_bands(score: pd.Series, y: pd.Series, n: int = 20, min_pct: float = 0.05) -> dict:
    """Cut the score into `n` equal-population bands (ties keep bands uneven), then merge
    any band under `min_pct` of accounts into its smaller neighbour until none is left.
    Returns {"cuts": inner cut-offs (a score <= cut goes to the lower band), "merged": k}."""
    s = pd.to_numeric(score, errors="coerce").dropna()
    qs = np.unique(np.quantile(s, np.linspace(0, 1, n + 1)[1:-1]))
    cuts = [float(c) for c in qs]
    start = len(cuts) + 1
    while cuts:
        counts = assign(s, cuts).value_counts().reindex(range(1, len(cuts) + 2), fill_value=0)
        pct = counts / len(s)
        if pct.min() >= min_pct:
            break
        i = int(pct.idxmin())                     # 1-based band to merge away
        left = pct.get(i - 1, np.inf)
        right = pct.get(i + 1, np.inf)
        drop = i - 2 if left <= right else i - 1  # remove the cut between i and its neighbour
        cuts.pop(max(0, min(drop, len(cuts) - 1)))
    return {"cuts": cuts, "merged": start - (len(cuts) + 1), "requested": n, "min_pct": min_pct}


def assign(score: pd.Series, cuts: list[float]) -> pd.Series:
    """Band number (1 = lowest scores) for each score."""
    edges = [-np.inf, *cuts, np.inf]
    return pd.cut(pd.to_numeric(score, errors="coerce"), edges, labels=False,
                  right=True).astype("Int64") + 1


def band_table(bands: dict, samples: dict[str, tuple[pd.Series, pd.Series]],
               base: str = "train") -> pd.DataFrame:
    """One row per band: score range, accounts and share in `base`, bad rate per sample."""
    cuts = bands["cuts"]
    s0, y0 = samples[base]
    b0 = assign(s0, cuts)
    rows = []
    for k in range(1, len(cuts) + 2):
        m = b0 == k
        lo = cuts[k - 2] if k > 1 else None
        hi = cuts[k - 1] if k <= len(cuts) else None
        row = {"band": k, "score_from": None if lo is None else lo,
               "score_to": None if hi is None else hi,
               "score_min": float(s0[m].min()) if m.any() else None,
               "score_max": float(s0[m].max()) if m.any() else None,
               "n": int(m.sum()), "pct": float(m.mean()),
               "bads": int(y0[m].sum()), f"bad_rate_{base}": float(y0[m].mean()) if m.any() else None}
        for name, (s, y) in samples.items():
            if name == base:
                continue
            bb = assign(s, cuts) == k
            row[f"bad_rate_{name}"] = float(y[bb].mean()) if bb.any() else None
        rows.append(row)
    t = pd.DataFrame(rows)
    t["cum_pct"] = t["pct"].cumsum()
    return t


def is_monotonic(rates: list[float | None]) -> list[int]:
    """Bands whose bad rate is HIGHER than the band below them (scores should lower risk)."""
    r = [x for x in rates]
    return [k + 1 for k in range(1, len(r)) if r[k] is not None and r[k - 1] is not None
            and r[k] > r[k - 1] + 1e-12]


def propose_levels(table: pd.DataFrame) -> list[str]:
    """A level per band, contiguous from VH (lowest scores) to VL, each about a fifth of
    the accounts. With fewer than 5 bands, fewer levels that still span both ends
    (3 bands: VH, M, VL)."""
    n = len(table)
    k = min(len(LEVELS), n)
    names = {5: LEVELS, 4: ("VH", "H", "L", "VL"), 3: ("VH", "M", "VL"), 2: ("VH", "VL"),
             1: ("M",)}[k]
    cum = table["cum_pct"].to_numpy()
    out, lvl = [], 0
    for i in range(n):
        # move to the next level once this one holds its share, leaving a band for each rest
        if out and lvl < k - 1 and (cum[i - 1] >= (lvl + 1) / k or n - i <= k - 1 - lvl):
            lvl += 1
        out.append(names[lvl])
    return out


def check_levels(levels: list[str], n_bands: int) -> None:
    """Every band labelled, labels known, contiguous and in risk order (VH ... VL)."""
    if len(levels) != n_bands:
        raise ValueError(f"perlu {n_bands} label, ada {len(levels)}")
    order = {name: i for i, name in enumerate(LEVELS)}
    unknown = [x for x in levels if x not in order]
    if unknown:
        raise ValueError(f"label tidak dikenal: {', '.join(unknown)}; pakai {', '.join(LEVELS)}")
    pos = [order[x] for x in levels]
    if any(b < a for a, b in zip(pos, pos[1:])):
        raise ValueError("urutan harus dari skor rendah (VH) ke skor tinggi (VL), tanpa loncat balik")


def level_table(table: pd.DataFrame, levels: list[str]) -> pd.DataFrame:
    t = table.assign(level=levels)
    rate_cols = [c for c in t.columns if c.startswith("bad_rate_")]
    rows = []
    for name in [x for x in LEVELS if x in levels]:
        g = t[t.level == name]
        row = {"level": name, "arti": LEVEL_NAMES[name],
               "band": f"{g.band.min()}–{g.band.max()}" if len(g) > 1 else str(g.band.iloc[0]),
               "score_min": g.score_min.min(), "score_max": g.score_max.max(),
               "n": int(g.n.sum()), "pct": float(g.pct.sum()), "bads": int(g.bads.sum())}
        row["bad_rate_train"] = row["bads"] / row["n"] if row["n"] else None
        for c in rate_cols:
            if c != "bad_rate_train":                 # other samples: weighted by train counts
                w = g["n"].where(g[c].notna(), 0)
                row[c] = float((g[c].fillna(0) * w).sum() / w.sum()) if w.sum() else None
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- the ideal bands
def _counts(score: pd.Series, y: pd.Series, cuts: list[float]) -> pd.DataFrame:
    b = assign(score, cuts)
    k = len(cuts) + 1
    g = pd.DataFrame({"b": b, "y": np.asarray(y, dtype=int)}).groupby("b")["y"].agg(["size", "sum"])
    g = g.reindex(range(1, k + 1), fill_value=0)
    return pd.DataFrame({"n": g["size"].to_numpy(), "bads": g["sum"].to_numpy()},
                        index=pd.RangeIndex(1, k + 1, name="band"))


def _p_diff(n1, b1, n2, b2) -> float:
    """Two-sided p-value that two bands have the same bad rate (two-proportion z-test)."""
    from .logistic import norm_sf
    if min(n1, n2) == 0:
        return 1.0
    p = (b1 + b2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    return float(2 * norm_sf(abs(b1 / n1 - b2 / n2) / se))


def ks_table(score: pd.Series, y: pd.Series, cuts: list[float]) -> pd.DataFrame:
    """Per band, riskiest (band 1, lowest scores) first: accounts, bads, goods, bad rate,
    cumulative share of bads and goods, and the KS at the band's upper boundary."""
    c = _counts(score, y, cuts)
    t = c.assign(goods=c.n - c.bads)
    t["bad_rate"] = t.bads / t.n.where(t.n > 0)
    t["cum_bads"] = t.bads.cumsum() / max(t.bads.sum(), 1)
    t["cum_goods"] = t.goods.cumsum() / max(t.goods.sum(), 1)
    t["ks"] = t.cum_bads - t.cum_goods
    return t.reset_index()


def banded_ks(score, y, cuts) -> float:
    return float(ks_table(score, y, cuts)["ks"].max())


def optimise_bands(samples: dict[str, tuple[pd.Series, pd.Series]], cuts: list[float],
                   alpha: float = 0.05, min_pct: float = 0.05, base: str = "train") -> dict:
    """From the starting bands (20, small ones already merged) down to two: each step merges
    the two neighbouring bands that are least different on `base` (a reversal, a higher
    band riskier than the one below, is merged first; else the highest p-value). Every
    step is recorded. The ideal number of bands is the largest one where the bad rate falls
    band after band in every sample, every neighbouring pair differs significantly on
    `base` (p < alpha) and every band holds at least `min_pct` of accounts. KS of the
    banded score is recorded at each step: how much separation each grouping keeps."""
    s0, y0 = samples[base]
    cuts = list(cuts)
    path = []
    while True:
        c = _counts(s0, y0, cuts)
        rate = (c.bads / c.n.where(c.n > 0)).to_numpy()
        ps = [_p_diff(c.n.iloc[i], c.bads.iloc[i], c.n.iloc[i + 1], c.bads.iloc[i + 1])
              for i in range(len(c) - 1)]
        rev = {name: len(is_monotonic(list((_counts(s, y, cuts).pipe(
            lambda t: t.bads / t.n.where(t.n > 0))).to_numpy())))
            for name, (s, y) in samples.items()}
        path.append({"bands": len(cuts) + 1, "cuts": list(cuts),
                     "ks": round(banded_ks(s0, y0, cuts), 4),
                     **{f"ks_{n}": round(banded_ks(*samples[n], cuts), 4) for n in samples if n != base},
                     "max_p": round(max(ps), 4) if ps else 0.0,
                     "min_pct": round(float(c.n.min() / c.n.sum()), 4),
                     **{f"reversals_{n}": v for n, v in rev.items()}})
        if len(cuts) <= 1:
            break
        worst = [i for i in range(len(rate) - 1) if rate[i + 1] > rate[i]]   # reversals first
        i = worst[0] if worst else int(np.argmax(ps))
        cuts.pop(i)                                  # removes the boundary between i and i+1
    ok = [p for p in path if all(p[f"reversals_{n}"] == 0 for n in samples)
          and p["max_p"] < alpha and p["min_pct"] >= min_pct]
    if not ok:                                       # nothing passes everything: base sample only
        ok = [p for p in path if p[f"reversals_{base}"] == 0 and p["max_p"] < alpha] or path[-1:]
    best = max(ok, key=lambda p: p["bands"])
    for p in path:
        p["chosen"] = p is best
    rule = (f"jumlah kelompok terbanyak yang bad rate-nya turun konsisten di semua sampel, "
            f"setiap pasangan bersebelahan berbeda nyata (p < {alpha}) dan setiap kelompok "
            f"minimal {min_pct:.0%} akun")
    return {"cuts": best["cuts"], "bands": best["bands"], "path": path, "rule": rule,
            "start": path[0]["bands"]}
