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
    the accounts. With fewer than 5 bands, fewer levels."""
    n = len(table)
    k = min(len(LEVELS), n)
    names = LEVELS if k == 5 else LEVELS[:k]
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
