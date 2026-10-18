"""Vintage analysis: how the share of accounts that ever went X+ days past due grows
with months on book (MOB), per origination cohort. It is the evidence for the target:

- the PERFORMANCE WINDOW: the MOB where the curve flattens (most accounts that will go
  bad have gone bad), and
- the BAD THRESHOLD: 30+, 60+ or 90+ DPD, the strictest one that still gives enough bads.

Input: a performance-history table, many rows per account (account key, a MOB column
or a snapshot date, a DPD column), plus the base table with the booking date.

    spec = find_history(tables, base_table)
    curves = vintage(history, base_df, spec)
    rec = recommend(curves)
    y_source = ever_bad(history, spec, rec["threshold"], rec["window"])  # 1 / 0 / NaN

Accounts not yet observed for the whole window and not bad yet are immature: NaN,
excluded from modelling.
"""
from __future__ import annotations

import html
import re

import numpy as np
import pandas as pd

from . import data as D

MOB_HINT = re.compile(r"^(mob|months?_on_book|month_on_book|bulan_ke|umur_bulan)$", re.I)
DPD_HINT = re.compile(r"(dpd|days_past_due|tunggakan|hari_tunggak)", re.I)
THRESHOLDS = (30, 60, 90)
WINDOWS = (6, 9, 12, 18, 24)
MATURE = 0.85                    # the window: where the curve reaches 85% of its last value
MIN_MATURE_SHARE = 0.6           # ...unless that leaves fewer than 60% of accounts mature
MIN_BADS, MIN_RATE = 300, 0.02   # enough bads for a stable scorecard


def find_history(tables: dict[str, pd.DataFrame], base: str) -> dict | None:
    """The first table that looks like monthly performance: several rows per account
    key of the base table, a MOB column (or a snapshot date) and a DPD column."""
    bt = tables[base]
    keys = [c for c in bt.columns if bt[c].is_unique and bt[c].notna().all()]
    for name, t in tables.items():
        if name == base:
            continue
        key = next((k for k in keys if k in t.columns and t[k].duplicated().any()), None)
        dpd = next((c for c in t.columns if DPD_HINT.search(str(c)) and D.is_numeric(t[c])), None)
        if not key or not dpd:
            continue
        mob = next((c for c in t.columns if MOB_HINT.match(str(c))), None)
        snap = None
        if mob is None:
            prof = D.profile(t)
            dates = prof.loc[prof.role == "date", "column"].tolist()
            snap = dates[0] if dates else None
        if mob or snap:
            return {"table": name, "key": key, "mob": mob, "snapshot": snap, "dpd": dpd}
    return None


def _mob(hist: pd.DataFrame, base: pd.DataFrame, spec: dict, date_col: str) -> pd.Series:
    if spec.get("mob"):
        return pd.to_numeric(hist[spec["mob"]], errors="coerce")
    booked = hist[spec["key"]].map(base.set_index(spec["key"])[date_col])
    snap = pd.to_datetime(hist[spec["snapshot"]], errors="coerce", format="mixed")
    booked = pd.to_datetime(booked, errors="coerce", format="mixed")
    return (snap.dt.year - booked.dt.year) * 12 + snap.dt.month - booked.dt.month


def _first_bad(h: pd.DataFrame, t: int) -> pd.Series:
    return h[h["dpd"] >= t].groupby("key")["mob"].min()


def vintage(hist: pd.DataFrame, base: pd.DataFrame, spec: dict, date_col: str,
            freq: str = "Q", max_mob: int = 24) -> pd.DataFrame:
    """Long table: threshold, cohort ('all' = every cohort), mob, n (accounts observed at
    least that long), bad_rate (share that went threshold+ DPD by that mob).

    Months not observed yet are censored, not counted as good: the rate is chained from
    each month's new bads among the accounts still at risk (a life table), so younger
    cohorts do not pull the curve down at high MOB."""
    h = pd.DataFrame({"key": hist[spec["key"]], "mob": _mob(hist, base, spec, date_col),
                      "dpd": pd.to_numeric(hist[spec["dpd"]], errors="coerce")}).dropna()
    h = h[(h["mob"] >= 1) & (h["mob"] <= max_mob)]
    seen = h.groupby("key")["mob"].max()
    cohort = (pd.to_datetime(base.set_index(spec["key"])[date_col], errors="coerce",
                             format="mixed").dt.to_period(freq).astype(str))
    acc = pd.DataFrame({"seen": seen}).join(cohort.rename("cohort"), how="left")
    rows = []
    for t in THRESHOLDS:
        acc["first"] = _first_bad(h, t).reindex(acc.index)
        for name, grp in [("all", acc), *acc.groupby("cohort")]:
            alive = 1.0
            for m in range(1, max_mob + 1):
                at = grp[grp["seen"] >= m]
                if len(at) == 0:
                    break
                risk = at[~(at["first"] < m)]                    # not bad before this month
                new = int((risk["first"] == m).sum())
                alive *= 1 - (new / len(risk) if len(risk) else 0.0)
                rows.append({"threshold": t, "cohort": name, "mob": m, "n": len(at),
                             "bad_rate": 1 - alive})
    out = pd.DataFrame(rows)
    # anyone ever 90+ was ever 60+ and 30+: keep 30+ >= 60+ >= 90+ despite censoring noise
    wide = out.pivot_table(index=["cohort", "mob"], columns="threshold", values="bad_rate")
    for lo, hi in zip(THRESHOLDS[:-1][::-1], THRESHOLDS[1:][::-1]):
        wide[lo] = wide[[lo, hi]].max(axis=1)
    fixed = wide.stack().rename("fixed").reset_index()
    out = out.merge(fixed, on=["cohort", "mob", "threshold"], how="left")
    return out.assign(bad_rate=out["fixed"].fillna(out["bad_rate"])).drop(columns="fixed")


def recommend(curves: pd.DataFrame, n_accounts: int | None = None) -> dict:
    """Window = the first standard MOB (6, 9, 12, 18, 24) where the overall curve reaches
    85% of its last well-observed value; if fewer than 60% of accounts are observed that
    long, the longest standard window that keeps 60% (a stated trade-off). Threshold =
    the strictest of 90+, 60+, 30+ with at least 300 bads and a 2% bad rate there."""
    out = {"by_threshold": []}
    for t in sorted(THRESHOLDS, reverse=True):
        c = curves[(curves.threshold == t) & (curves.cohort == "all")].set_index("mob")
        if c.empty:
            continue
        total = c["n"].iloc[0]
        solid = c[c["n"] >= 0.3 * total]                      # mobs most accounts reached
        last = solid["bad_rate"].iloc[-1]
        reach = solid.index[solid["bad_rate"] >= MATURE * last]
        mature = int(reach[0]) if len(reach) else int(solid.index[-1])
        window = next((w for w in WINDOWS if w >= mature and w in c.index), int(c.index[-1]))
        traded = False
        if c.loc[window, "n"] < MIN_MATURE_SHARE * total:     # too few accounts that old
            ok = [w for w in WINDOWS if w in c.index and c.loc[w, "n"] >= MIN_MATURE_SHARE * total]
            if ok:
                window, traded = max(ok), True
        rate = float(c.loc[window, "bad_rate"])
        n = int(c.loc[window, "n"])
        out["by_threshold"].append({"threshold": t, "mature_mob": mature, "window": window,
                                    "bad_rate": rate, "n_mature": n, "bads": int(rate * n),
                                    "share_of_last": float(rate / last) if last else 0.0,
                                    "traded_off": traded})
    ok = [r for r in out["by_threshold"] if r["bads"] >= MIN_BADS and r["bad_rate"] >= MIN_RATE]
    pick = ok[0] if ok else max(out["by_threshold"], key=lambda r: r["bads"])
    out.update(threshold=pick["threshold"], window=pick["window"], bad_rate=pick["bad_rate"],
               bads=pick["bads"], n_mature=pick["n_mature"], enough=bool(ok))
    return out


def ever_bad(hist: pd.DataFrame, base: pd.DataFrame, spec: dict, date_col: str,
             threshold: int, window: int) -> pd.Series:
    """Per account key: 1 = went threshold+ DPD within `window` MOB, 0 = never did; NaN =
    immature (not observed for the whole window). Immature accounts are left out even
    when they already went bad: counting their early bads but not their goods would make
    recent cohorts look far riskier than they are."""
    h = pd.DataFrame({"key": hist[spec["key"]], "mob": _mob(hist, base, spec, date_col),
                      "dpd": pd.to_numeric(hist[spec["dpd"]], errors="coerce")}).dropna()
    first = _first_bad(h, threshold)
    seen = h.groupby("key")["mob"].max()
    y = pd.Series(np.nan, index=seen.index)
    mature = seen >= window
    y[mature] = 0.0
    bad = first[first <= window].index
    y[bad.intersection(seen[mature].index)] = 1.0
    return y


def column_name(threshold: int, window: int) -> str:
    return f"vin_ever{threshold}_mob{window}"


# --------------------------------------------------------------------------- charts
_PALETTE = ["#2a78d6", "#e08a1e", "#2e7d4f", "#8b1e3f", "#7b5cc4", "#4a9fb5", "#b5803a",
            "#5a6b7d"]


def curves_svg(curves: pd.DataFrame, threshold: int | None = None, window: int | None = None,
               title: str = "") -> str:
    """Line chart, MOB on x, cumulative bad rate on y. With `threshold`: one line per
    cohort (at most 8, newest last) plus the overall line; without: the overall
    30+/60+/90+ lines. A dashed line marks the chosen window."""
    if threshold is None:
        series = [(f"{t}+ DPD", curves[(curves.threshold == t) & (curves.cohort == "all")])
                  for t in THRESHOLDS]
    else:
        c = curves[curves.threshold == threshold]
        cohorts = sorted(x for x in c.cohort.unique() if x != "all")
        step = max(1, len(cohorts) // 8)
        series = [(k, c[c.cohort == k]) for k in cohorts[::step][-8:]]
        series.append(("semua", c[c.cohort == "all"]))
    # drop each line's thin tail: points seen by < 50% of its accounts (or < 50) are noise
    series = [(k, s[s["n"] >= max(50, 0.5 * s["n"].iloc[0])]) for k, s in series if len(s)]
    W, H, L, B, T, R = 640, 300, 48, 30, 14, 110
    top = max(s["bad_rate"].max() for _, s in series) or 0.01
    step = next(st for st in (0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25) if top / st <= 5)
    ymax = np.ceil(top * 1.05 / step) * step                             # round axis steps
    xmax = int(curves["mob"].max())
    sx = lambda m: L + (m - 1) / max(xmax - 1, 1) * (W - L - R)          # noqa: E731
    sy = lambda v: T + (1 - v / ymax) * (H - T - B)                      # noqa: E731
    parts = [f'<rect x="0" y="0" width="{W}" height="{H}" fill="none"/>']
    for k in range(int(round(ymax / step)) + 1):                         # recessive grid
        v = k * step
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" '
                     f'stroke="#e4e3df"/><text x="{L - 6}" y="{sy(v) + 4:.1f}" font-size="11" '
                     f'text-anchor="end" fill="#52514e">{v:.0%}</text>')
    for m in range(3, xmax + 1, 3):
        parts.append(f'<text x="{sx(m):.1f}" y="{H - B + 16}" font-size="11" text-anchor="middle" '
                     f'fill="#52514e">{m}</text>')
    parts.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 2}" font-size="11" text-anchor="middle" '
                 f'fill="#52514e">bulan sejak booking (MOB)</text>')
    if window:
        parts.append(f'<line x1="{sx(window):.1f}" x2="{sx(window):.1f}" y1="{T}" y2="{H - B}" '
                     f'stroke="#c0392b" stroke-dasharray="4 3"/><text x="{sx(window) + 4:.1f}" '
                     f'y="{T + 10}" font-size="11" fill="#c0392b">window {window} bln</text>')
    labels = []
    for k, (name, s) in enumerate(series):
        col = "#0b0b0b" if name == "semua" else _PALETTE[k % len(_PALETTE)]
        wid = 3 if name == "semua" else 2
        pts = " ".join(f"{sx(m):.1f},{sy(v):.1f}" for m, v in zip(s["mob"], s["bad_rate"]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{wid}" '
                     f'stroke-linejoin="round"><title>{html.escape(str(name))}</title></polyline>')
        if len(s):
            labels.append([sy(s.iloc[-1].bad_rate), f"{name} {s.iloc[-1].bad_rate:.1%}", col])
    # labels at the right edge, sorted top-down and pushed apart so they never collide
    labels.sort()
    for k in range(1, len(labels)):
        labels[k][0] = max(labels[k][0], labels[k - 1][0] + 13)
    for y, text, col in labels:
        parts.append(f'<rect x="{W - R + 8}" y="{y - 6:.1f}" width="8" height="3" fill="{col}"/>'
                     f'<text x="{W - R + 20}" y="{y:.1f}" font-size="11" fill="#0b0b0b">'
                     f"{html.escape(text)}</text>")
    hh = max(H, int(labels[-1][0]) + 10) if labels else H
    head = f"<p><b>{html.escape(title)}</b></p>" if title else ""
    return (head + f'<svg viewBox="0 0 {W} {hh}" width="100%" role="img" '
            f'style="max-width:{W}px;max-height:58vh" '
            f'aria-label="{html.escape(title or "vintage")}">{"".join(parts)}</svg>')
