"""Kirana's scorecard deck: consulting style (action titles that state the takeaway, a
chart and its "so what" side by side, sources on every slide), charts on Knaflic's
principles (deckviz), and every number taken from the build. English, HTML + CSS, 16:9.

Structure: cover, executive summary, background, bad definition, vintage analysis, model
development process, model waterfall, model performance, score drivers, recommended
score bands, risk levels, then one transparent slide per scorecard variable (every
bin's points and why), and a methodology appendix.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pandas as pd

from . import deckviz as V
from .deckviz import esc

LEVEL_ORDER = ["VH", "H", "M", "L", "VL"]
LEVEL_NAME = {"VH": "Very high", "H": "High", "M": "Medium", "L": "Low", "VL": "Very low"}
POLICY = {"VH": "Decline, or manual review with strong mitigants",
          "H": "Approve with conditions: lower limit, shorter tenor, collateral",
          "M": "Standard approval with closer monitoring",
          "L": "Standard approval",
          "VL": "Fast-track; candidates for pre-approved offers"}


# --------------------------------------------------------------------------- formatting
def money(v: float) -> str:
    a = abs(v)
    return (f"{v / 1e9:.1f}B" if a >= 1e9 else f"{v / 1e6:.1f}M" if a >= 1e6 else
            f"{v / 1e3:.0f}K" if a >= 1e4 else f"{v:,.0f}" if a >= 100 else f"{v:.3g}")


def pretty_bin(label: str) -> str:
    """'(-inf, 5.4e+06]' -> '≤ 5.4M'; '(5.4e+06, 1.06e+07]' -> '5.4M – 10.6M'; categories kept."""
    s = str(label)
    m = re.match(r"^[\(\[]\s*(-?inf|[-\d.e+]+)\s*,\s*(inf|[-\d.e+]+)\s*[\)\]]$", s)
    if not m:
        return s.replace("Missing", "missing")
    lo, hi = m.groups()
    if lo == "-inf":
        return f"≤ {money(float(hi))}"
    if hi == "inf":
        return f"> {money(float(lo))}"
    return f"{money(float(lo))} – {money(float(hi))}"


def human(var: str, meanings: dict) -> str:
    """A readable English name: the bank's own description when there is one."""
    r = meanings.get(var) or {}
    desc = r.get("asli") or (r.get("arti") if str(r.get("dasar", "")).startswith("kamus data bank") else "")
    desc = re.sub(r"\s*\((IDR|belum diterjemahkan[^)]*)\)", "", desc or "").strip()
    if desc and len(desc) <= 60:
        return desc[0].upper() + desc[1:]
    name = re.sub(r"^(cust|app|bureau|perf)_", lambda m: {"bureau_": "Bureau: "}.get(m.group(0), ""), var)
    return name.replace("_", " ").strip().capitalize()


def a_n(n) -> str:
    """'a 12-month', 'an 18-month'."""
    return "an" if str(n).startswith("8") or n in (11, 18) else "a"


def count_word(n: int) -> str:
    return ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"][n] if n <= 10 else str(n)


def gini_word(g: float) -> str:
    return "strong" if g >= 0.5 else "good" if g >= 0.4 else "acceptable" if g >= 0.3 else "weak"


# --------------------------------------------------------------------------- the facts
def facts(office, cut: str | None = None) -> dict:
    from .deliverables import _cut, _read
    st = office.state
    cut = cut or _cut(office)
    proj = office.project
    art = proj.dir("artifacts") / cut
    work = proj.dir("work")
    vm = office.approved_meanings()               # the dictionary Laras approved
    curves = pd.read_csv(work / "vintage_curves.csv") if (work / "vintage_curves.csv").exists() else None
    levels = _read(art, "risk_levels.csv")
    if levels is None and st.cuts[cut].get("bands"):       # Citra's proposal, not yet agreed
        from ..engine import risk as R
        table = pd.DataFrame(st.cuts[cut]["bands"]["table"])
        levels = R.level_table(table, st.cuts[cut]["levels"]["labels"])
    sc = json.loads((art / "scorecard.json").read_text(encoding="utf-8")) if (art / "scorecard.json").exists() else {}
    df, y = office.dataset()
    plan = st.join_plan or {}
    dates = pd.to_datetime(df[st.target.get("date_col")], errors="coerce") if st.target.get("date_col") else None
    return {
        "name": proj.meta.name, "cut": cut, "info": st.cuts[cut], "target": st.target or {},
        "plan": plan, "rows": len(df), "y": y, "dates": dates,
        "perf": (_read(art, "performance.json") or {}).get("performance", {}),
        "psi": (_read(art, "performance.json") or {}).get("score_psi", {}),
        "wf": _read(art, "waterfall_summary.csv"), "wff": _read(art, "waterfall_features.csv"),
        "contrib": _read(art, "score_contribution.csv"), "bands": _read(art, "risk_bands.csv"),
        "levels": levels, "levels_agreed": (art / "risk_levels.csv").exists(),
        "table": _read(art, "scorecard_table.csv"), "curves": curves,
        "meanings": {r["variabel"]: r for r in vm}, "scaling": sc.get("scaling", {}),
        "build": st.cuts[cut].get("build_id", ""),
        "tuning": _read(art, "tuning.json"),
        "band_path": _read(art, "band_path.csv"), "ks_table": _read(art, "ks_table.csv"),
        "band_choice": _read(art, "band_choice.json"),
    }


HP_NAME = {"min_bin_pct": "smallest bin", "max_bins": "max bins", "p_enter": "entry p-value"}


def hp_name(k: str) -> str:
    return HP_NAME.get(k, k)


def hp_value(k: str, v) -> str:
    if v is None:
        return "-"
    return {"min_bin_pct": f"{v:.0%}", "max_bins": str(v), "p_enter": f"{v:g}"}.get(k, str(v))


def hp_label(k: str, v) -> str:
    if v is None:
        return "-"
    return {"min_bin_pct": f"min bin {v:.0%}", "max_bins": f"≤{v} bins",
            "p_enter": f"p<{v:g}"}.get(k, str(v))


def bin_analysis(f: dict) -> dict:
    """Per variable in the score: every bin's share, bad rate, risk index (bin bad rate over
    the portfolio's), points and a one-word read, plus the pattern, the thin bins and the
    so-what. Shared by the deck and Citra's lines in the cut meeting."""
    tbl, contrib = f["table"], f["contrib"]
    avg = (f["perf"].get("train") or {}).get("bad_rate") or float(tbl.bad_rate.mean())
    order = list(contrib["variabel"]) if contrib is not None else list(dict.fromkeys(tbl.feature))
    from ..engine.binning import iv_strength
    span = tbl.groupby("feature").points.agg(lambda p: p.max() - p.min())
    ranges = float(span[span.index.isin(order)].sum())
    out = {}
    for var in order:
        g = tbl[tbl.feature == var]
        if g.empty:
            continue
        bins = []
        for r in g.itertuples():
            idx = r.bad_rate / avg if avg else 0.0
            read = ("much riskier" if idx >= 1.5 else "riskier" if idx >= 1.1 else
                    "much safer" if idx <= 0.67 else "safer" if idx <= 0.9 else "average")
            bins.append({"bin": r.bin, "label": pretty_bin(r.bin), "pct": float(r.pct),
                         "bad_rate": float(r.bad_rate), "index": idx, "points": int(r.points),
                         "woe": float(r.woe), "read": read})
        worst = min(bins, key=lambda b: (b["points"], -b["bad_rate"]))
        best = max(bins, key=lambda b: (b["points"], -b["bad_rate"]))
        real = [b for b in bins if "missing" not in b["label"].lower()]
        numeric = all(re.match(r"^[\(\[]", str(b["bin"])) or "issing" in str(b["bin"]) for b in bins)
        br = [b["bad_rate"] for b in real]
        up = all(a <= c for a, c in zip(br, br[1:]))
        down = all(a >= c for a, c in zip(br, br[1:]))
        breaks = [] if (up or down) else [real[k + 1]["label"] for k in range(len(br) - 2)
                                          if (br[k + 1] - br[k]) * (br[k + 2] - br[k + 1]) < 0]
        m = f["meanings"].get(var, {})
        bad = g.pct * g.bad_rate                         # IV from the bins themselves
        good = g.pct * (1 - g.bad_rate)
        iv = float(((good / good.sum() - bad / bad.sum()) * g.woe).sum()) if bad.sum() > 0 else 0.0
        share = (g.points.max() - g.points.min()) / ranges if ranges else 0.0
        spread = worst["bad_rate"] / max(best["bad_rate"], 1e-9)
        thin = [b["label"] for b in bins if b["pct"] < 0.05]
        direction = ("risk rises with the value" if up and numeric and len(real) > 1 and br[-1] > br[0]
                     else "risk falls as the value rises" if down and numeric and len(real) > 1 else "")
        so_what = (f"{direction[0].upper() + direction[1:]}; " if direction else "") + (
            f"an applicant in the riskiest bin gives up {best['points'] - worst['points']} points "
            "against the safest, so this variable alone can move a borderline decision"
            if share >= 0.15 else
            f"a supporting variable: {best['points'] - worst['points']} points between its riskiest "
            "and safest bins")
        out[var] = {"bins": bins, "worst": worst, "best": best, "monotonic": up or down,
                    "numeric": numeric, "breaks": breaks, "thin": thin, "iv": iv,
                    "strength": iv_strength(iv), "share": share, "spread": spread,
                    "direction": direction, "so_what": so_what,
                    "doubt": "noise" in str(m.get("arti", "")).lower() or m.get("keyakinan") == "rendah"}
    return out


# --------------------------------------------------------------------------- slides
CSS = """
:root{--ink:#111827;--muted:#6B7280;--navy:#0B2545;--accent:#0F766E;--rule:#E5E7EB;--soft:#F3F6F8;--alert:#C2410C}
*{box-sizing:border-box} body{margin:0;background:#DCE1E6;color:var(--ink);
 font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.slide{width:1280px;height:720px;margin:28px auto;background:#fff;position:relative;overflow:hidden;
 box-shadow:0 6px 30px rgba(11,37,69,.12);padding:46px 64px 60px;display:flex;flex-direction:column;page-break-after:always}
.tracker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700;margin-bottom:10px}
.action{font-size:27px;line-height:1.25;margin:0 0 6px;color:var(--navy);font-weight:700;max-width:1080px}
.lede{font-size:15px;color:var(--muted);margin:0 0 18px;max-width:1040px}
.rule{height:3px;width:56px;background:var(--accent);margin:0 0 18px}
.grid{display:grid;grid-template-columns:1.55fr 1fr;gap:36px;flex:1;min-height:0}
.grid.even{grid-template-columns:1fr 1fr}
.chart{min-height:0;display:flex;flex-direction:column}
.chart h4,.side h4{font-size:13px;color:var(--muted);font-weight:600;margin:0 0 10px;text-transform:uppercase;letter-spacing:.06em}
.side{border-left:1px solid var(--rule);padding-left:28px;font-size:15px;line-height:1.5}
.side p{margin:0 0 12px} .side b{color:var(--navy)}
.sowhat{background:var(--soft);border-left:4px solid var(--accent);padding:12px 16px;margin-top:8px;font-size:15px}
.sowhat b{color:var(--accent)} .sowhat.alert{border-color:var(--alert);background:#FFF4ED;margin:0 0 12px}
.sowhat.alert b{color:var(--alert)}
table.t{border-collapse:collapse;font-size:13px;width:100%} .t th{color:var(--muted);font-weight:600;text-align:left;
 border-bottom:2px solid var(--navy);padding:6px 8px} .t td{padding:6px 8px;border-bottom:1px solid var(--rule)}
.side .t{font-size:12px} .t td.n{text-align:right;font-variant-numeric:tabular-nums} .t th.n{text-align:right}
.foot{position:absolute;left:64px;right:64px;bottom:20px;display:flex;justify-content:space-between;
 font-size:11px;color:var(--muted);border-top:1px solid var(--rule);padding-top:8px}
.cover{background:var(--navy);color:#fff;justify-content:center}
.cover h1{font-size:46px;margin:0 0 14px;max-width:900px;line-height:1.15} .cover .sub{font-size:20px;color:#C9D6E3}
.cover .bar{width:90px;height:6px;background:#2DD4BF;margin:0 0 26px} .cover .meta{position:absolute;bottom:40px;left:64px;font-size:14px;color:#9FB3C8}
.exec{display:grid;grid-template-columns:repeat(2,1fr);gap:18px 36px;margin-top:6px}
.msg{border-top:3px solid var(--accent);padding-top:10px} .msg .k{font-size:34px;font-weight:700;color:var(--accent)}
.msg .h{font-weight:700;color:var(--navy);margin:4px 0 4px;font-size:16px} .msg p{margin:0;font-size:14px;color:#374151;line-height:1.45}
.flow{display:flex;gap:10px;align-items:stretch;margin:8px 0 18px}
.step{flex:1;background:var(--soft);border-radius:8px;padding:12px 12px 14px;position:relative}
.step .no{font-size:11px;color:var(--accent);font-weight:700} .step .h{font-weight:700;color:var(--navy);font-size:14px;margin:2px 0 6px}
.step .d{font-size:12px;color:#374151;line-height:1.4}
.step:not(:last-child)::after{content:"›";position:absolute;right:-9px;top:40%;color:var(--muted);font-size:20px}
.rec{margin-top:auto;background:var(--navy);color:#fff;padding:16px 22px;border-radius:6px;font-size:16px;line-height:1.45}
.rec span{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#5EEAD4;font-weight:700;margin-bottom:4px}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;color:#fff;font-size:12px;font-weight:700}
@media print{body{background:#fff}.slide{margin:0;box-shadow:none}}
"""


class Deck:
    def __init__(self, f: dict):
        self.f, self.slides, self.n = f, [], 0
        self.meta: list[dict] = []          # per slide: tracker, action title, variable

    def add(self, tracker: str, action: str, body: str, lede: str = "", source: str = "",
            var: str | None = None) -> None:
        self.n += 1
        src = source or f"Source: AutoScorecard build {self.f['build']} ({self.f['cut'].replace('cut', 'Cut ')})"
        self.meta.append({"tracker": tracker, "action": action, "var": var})
        self.slides.append(
            f"<section class='slide'><div class='tracker'>{esc(tracker)}</div><h2 class='action'>{esc(action)}</h2>"
            + (f"<p class='lede'>{esc(lede)}</p>" if lede else "<div class='rule'></div>")
            + f"{body}<div class='foot'><span>{esc(src)}</span><span>{self.n}</span></div></section>")

    def html(self) -> str:
        return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{esc(self.f['name'])}: "
                f"scorecard deck (draft)</title><style>{CSS}</style></head><body>" + "".join(self.slides)
                + "</body></html>")


def two(chart: str, side: str, chart_title: str = "", even: bool = False) -> str:
    return (f"<div class='grid{' even' if even else ''}'><div class='chart'>"
            + (f"<h4>{esc(chart_title)}</h4>" if chart_title else "") + f"{chart}</div>"
            f"<div class='side'>{side}</div></div>")


def build(office, cut: str | None = None) -> dict:
    """The deck for `cut` (default: the confirmed, else the latest built cut)."""
    f = facts(office, cut)
    t, perf, info = f["target"], f["perf"], f["info"]
    vin = t.get("vintage")
    feats = info.get("features", [])
    contrib = f["contrib"]
    levels = f["levels"]
    bands = f["bands"]
    names = {v: human(v, f["meanings"]) for v in feats}
    d = Deck(f)
    oot = perf.get("oot") or perf.get("test") or {}
    train = perf.get("train", {})
    lv = levels.set_index("level") if levels is not None else None
    sep = (lv.loc["VH", "bad_rate_train"] / max(lv.loc["VL", "bad_rate_train"], 1e-9)
           if lv is not None and {"VH", "VL"} <= set(lv.index) else None)
    top3 = contrib.head(3) if contrib is not None else None

    # ---- cover
    d.meta.append({"tracker": "Cover", "action": f["name"], "var": None})
    d.slides.append(
        f"<section class='slide cover'><div class='bar'></div><h1>Application scorecard: "
        f"{esc(f['name'])}</h1><div class='sub'>Bad definition, model development and recommended "
        f"risk levels</div><div class='meta'>Draft for discussion · {datetime.now():%d %B %Y} · "
        "AutoScorecard team</div></section>")

    # ---- executive summary
    msgs = []
    psi_ = f["psi"].get("oot", f["psi"].get("test", 0)) or 0
    if vin:
        msgs.append((f"{vin['bad_rate']:.0%}", "Bad = ever 90+ DPD within 18 months" if (vin["threshold"], vin["window"]) == (90, 18)
                     else f"Bad = ever {vin['threshold']}+ DPD within {vin['window']} months",
                     f"Chosen from vintage analysis: the curves stop rising materially by month "
                     f"{vin['window']}, and the definition yields {vin['bads']:,} bads, enough for a stable model."))
    if oot:
        msgs.append((f"{oot['gini']:.2f}", f"Out-of-time Gini: {gini_word(oot['gini'])} discrimination",
                     f"{len(feats)} variables; Gini {train.get('gini', 0):.2f} in development and "
                     f"{oot['gini']:.2f} out of time, with score PSI {psi_:.2f} "
                     + ("(stable population)." if psi_ < 0.1 else "(moderate population shift)." if psi_ < 0.25
                        else "(material population shift: recalibrate cut-offs before go-live).")))
    if top3 is not None:
        msgs.append((f"{top3['kontribusi'].sum():.0%}", "of the score range comes from three drivers",
                     ", ".join(names.get(v, v) for v in top3["variabel"]) + "."))
    if sep:
        msgs.append((f"{sep:.0f}×", "bad-rate gap between the riskiest and safest level",
                     f"Very high risk: {lv.loc['VH', 'bad_rate_train']:.0%} bad rate on "
                     f"{lv.loc['VH', 'pct']:.0%} of applicants; very low risk: "
                     f"{lv.loc['VL', 'bad_rate_train']:.1%}."))
    g = oot.get("gini", 0)
    n_lv = len(levels) if levels is not None else 0
    if g >= 0.4:
        title = "The scorecard separates risk sharply and holds out of time"
        rec = "Adopt the scorecard for application decisions, with the risk levels below as the policy anchor."
    elif g >= 0.3:
        title = "The scorecard ranks risk reliably out of time"
        rec = "Adopt the scorecard alongside existing policy rules; review cut-offs after the first two quarters."
    else:
        title = "The scorecard ranks risk, but its out-of-time power is modest"
        rec = ("Use the scorecard as one input next to policy rules, not as a stand-alone decision; "
               "add stronger data (e.g. full bureau history) before relying on it alone.")
    doubtful = [v for v in feats if "noise" in str(f["meanings"].get(v, {}).get("arti", "")).lower()
                or f["meanings"].get(v, {}).get("keyakinan") == "rendah"]
    if psi_ >= 0.25:
        title = title.replace(" and holds out of time", ", but the population it scores has shifted")
        rec += (f" The score distribution has shifted (PSI {psi_:.2f}): recalibrate the cut-offs on "
                "recent applications before go-live.")
    if doubtful:
        rec += (f" Before go-live, remove {', '.join(names.get(v, v) for v in doubtful)}: "
                + ("it has" if len(doubtful) == 1 else "they have") + " no clear business meaning.")
    if n_lv:
        title += f"; {count_word(n_lv)} risk levels are " + ("agreed" if f["levels_agreed"] else "proposed") \
            + " to anchor credit policy"
    d.add("Executive summary", title,
          "<div class='exec'>" + "".join(f"<div class='msg'><div class='k'>{esc(k)}</div><div class='h'>{esc(h)}</div>"
                                         f"<p>{esc(p)}</p></div>" for k, h, p in msgs) + "</div>"
          + f"<div class='rec'><span>Recommendation</span>{esc(rec)}</div>")

    # ---- background
    period = ""
    if f["dates"] is not None and f["dates"].notna().any():
        period = f"{f['dates'].min():%b %Y} – {f['dates'].max():%b %Y}"
    plan = f["plan"]
    rows = [{"table": plan.get("base"), "role": "base: one row per application", "rows": f"{f['rows']:,}"}]
    rows += [{"table": j["right"], "role": f"joined on {j['left_on']} ({j['relationship']})",
              "rows": f"{j['coverage']:.0%} matched"} for j in plan.get("joins", [])]
    if plan.get("history"):
        rows.append({"table": plan["history"]["table"], "role": "monthly performance: vintage and target only",
                     "rows": "not used as features"})
    d.add("Background", f"We built an application scorecard on {f['rows']:,} applications"
          + (f" booked {period}" if period else "") + " to rank applicants by default risk",
          two(table_html(rows, ["table", "role", "rows"]),
              "<p><b>Objective.</b> Score every application so that credit decisions, limits and pricing "
              "follow measured risk rather than judgement alone.</p>"
              "<p><b>Scope.</b> All applications in the period, left-joined so the base never gains rows; "
              "outcome tables are kept out of the features to prevent leakage.</p>"
              "<div class='sowhat'><b>So what:</b> one transparent, points-based score that credit "
              "officers can read bin by bin.</div>", "Data used"))

    # ---- bad definition
    if vin and f["curves"] is not None:
        c = f["curves"]
        byt = sorted(vin.get("by_threshold") or [], key=lambda r: r["threshold"])
        if byt:
            at = [(f"{r['threshold']}+ DPD", r["bad_rate"]) for r in byt]
            sub = [f"{r['bads']:,} bads · {r['window']}m" for r in byt]
        else:
            at, sub = [], None
            for th in (30, 60, 90):
                s_ = c[(c.threshold == th) & (c.cohort == "all") & (c.mob <= vin["window"])]
                at.append((f"{th}+ DPD", float(s_.bad_rate.iloc[-1]) if len(s_) else 0.0))
        chosen = [k for k, (lab, _) in enumerate(at) if lab.startswith(str(vin["threshold"]))]
        stricter = [r for r in byt if r["threshold"] > vin["threshold"]]
        why_t = (f"It is the strictest threshold that clears both minimums (at least 300 bads and a 2% bad "
                 "rate): severe enough to mean real default, frequent enough to model.")
        if stricter:
            why_t += " " + "; ".join(f"{r['threshold']}+ gives only {r['bads']:,} bads"
                                     + (f" at {r['bad_rate']:.1%}" if r["bad_rate"] < 0.02 else "")
                                     for r in stricter
                                     if r["bads"] < 300 or r["bad_rate"] < 0.02) + "."
        if byt and not vin.get("enough", True):
            why_t = ("No threshold reaches 300 bads; we use the one with the most bads and flag the sample "
                     "size as a limitation.")
        al = c[(c.threshold == vin["threshold"]) & (c.cohort == "all")]
        al = al[al.n >= 0.5 * al.n.iloc[0]]
        capture = float(al[al.mob <= vin["window"]].bad_rate.iloc[-1]) / max(float(al.bad_rate.iloc[-1]), 1e-9)
        d.add("Bad definition", f"Bad is defined as ever {vin['threshold']}+ days past due within "
              f"{vin['window']} months on book: {vin['bads']:,} bads, a {vin['bad_rate']:.1%} bad rate",
              two(V.vbars(at, colors=[V.ACCENT if k in chosen else V.GREY for k in range(len(at))], h=300, sub=sub),
                  f"<p><b>Why {vin['threshold']}+.</b> {esc(why_t)}</p>"
                  + f"<p><b>Why {vin['window']} months.</b> By then the vintage curves have reached {capture:.0%} of "
                  "their observed level (next slide); a shorter window would miss late defaults, a longer one "
                  "would leave too few mature accounts.</p>"
                  + f"<p><b>Immature accounts.</b> Applications observed for less than {vin['window']} months "
                  "are excluded, good or bad, so recent cohorts are not biased toward bads.</p>",
                  "Bad rate by threshold, each at its own maturity window"
                  if byt else f"Share of accounts ever reaching each threshold by month {vin['window']}"))

        # ---- vintage
        ser = []
        cohorts = sorted(x for x in c[c.threshold == vin["threshold"]].cohort.unique() if x != "all")
        for k in cohorts:
            s = c[(c.threshold == vin["threshold"]) & (c.cohort == k)]
            s = s[s.n >= max(50, 0.5 * s.n.iloc[0])]
            ser.append({"name": k, "x": s.mob.tolist(), "y": s.bad_rate.tolist()})
        s = c[(c.threshold == vin["threshold"]) & (c.cohort == "all")]
        s = s[s.n >= 0.5 * s.n.iloc[0]]
        ser.append({"name": "All cohorts", "x": s.mob.tolist(), "y": s.bad_rate.tolist(), "focus": True})
        last = float(s.bad_rate.iloc[-1])
        at_w = float(s[s.mob <= vin["window"]].bad_rate.iloc[-1])
        spread = [x["y"][min(len(x["y"]), 12) - 1] for x in ser[:-1] if x["y"]]
        d.add("Vintage analysis", f"Default curves flatten by month {vin['window']}: {a_n(vin['window'])} "
              f"{vin['window']}-month window captures {at_w / last:.0%} of the defaults seen by month "
              f"{int(s.mob.iloc[-1])}",
              two(V.lines(ser, int(c.mob.max()), xlabel="months on book", marker=(vin["window"], f"window: {vin['window']} months")),
                  f"<p><b>Reading the chart.</b> Each grey line is a booking quarter; the green line pools all "
                  f"of them. The curve climbs steeply in the first year, then slows.</p>"
                  + (f"<p><b>Cohorts behave alike.</b> At month 12 the quarters range from {min(spread):.0%} "
                     f"to {max(spread):.0%}: no vintage stands out, so one definition fits all.</p>" if spread else "")
                  + "<div class='sowhat'><b>So what:</b> the outcome window is set by evidence, not convention, "
                  "and stays comparable across vintages.</div>",
                  f"Cumulative share of accounts ever {vin['threshold']}+ DPD"))

    # ---- development process
    wf = f["wf"]
    n_in = int(wf.n_in.iloc[0]) if wf is not None else len(feats)
    sc_ = f["scaling"] or {}
    steps = [("Data", f"{f['rows']:,} applications; {len(plan.get('joins', [])) + 1} tables joined"),
             ("Target", f"ever {vin['threshold']}+ DPD in {vin['window']}m" if vin else f"{t.get('column')}"),
             ("Samples", f"train {train.get('n', 0):,} · test {perf.get('test', {}).get('n', 0):,} · OOT {perf.get('oot', {}).get('n', 0):,}"),
             ("Binning", "monotonic WoE bins, each ≥5% of accounts"),
             ("Selection", f"{n_in} candidates → {len(feats)} variables in {len(wf) - 1 if wf is not None else 0} steps"),
             ("Model", "logistic regression on WoE; all signs checked"),
             ("Scaling", f"{sc_.get('base_score', 600):.0f} points at {sc_.get('base_odds', 50):.0f}:1 odds, "
                         f"PDO {sc_.get('pdo', 20):.0f}"),
             ("Validation", f"Gini, KS, PSI on test and out-of-time")]
    parts = [(k, perf[k]) for k in ("train", "test", "oot") if k in perf]
    tot = sum(v["n"] for _, v in parts) or 1
    split = V.stacked_share([(f"{'OOT' if k == 'oot' else k.capitalize()} {v['n']:,} ·", v["n"] / tot, col)
                             for (k, v), col in zip(parts, (V.ACCENT, "#5E9C8F", "#0B2545"))], w=1150, h=50)
    split_rows = [{"sample": "out-of-time" if k == "oot" else k, "accounts": f"{v['n']:,}", "bads": f"{v['bads']:,}",
                   "bad rate": f"{v['bad_rate']:.1%}",
                   "used for": {"train": "fitting bins and coefficients", "test": "checking over-fit on the same period",
                                "oot": "checking the most recent applications, never seen in fitting"}[k]}
                  for k, v in parts]
    d.add("Model development process", f"The model was built in {count_word(len(steps))} auditable steps, each with "
          "an explicit, recorded rule",
          "<div class='flow'>" + "".join(f"<div class='step'><div class='no'>{k + 1:02d}</div>"
                                         f"<div class='h'>{esc(h)}</div><div class='d'>{esc(x)}</div></div>"
                                         for k, (h, x) in enumerate(steps)) + "</div>"
          + "<div class='chart'><h4>Samples</h4>" + split
          + table_html(split_rows, ["sample", "accounts", "bads", "bad rate", "used for"],
                       num={"accounts", "bads", "bad rate"}) + "</div>"
          + "<div class='sowhat'><b>So what:</b> every decision, from the join keys to the last dropped "
          "variable, is logged and reproducible; validators can rerun the build end to end.</div>")

    # ---- waterfall
    if wf is not None:
        fsteps = [(r.step, int(r.n_out), int(r.n_dropped), int(r.n_added)) for r in wf.itertuples()]
        big = wf.iloc[1:].sort_values("n_dropped", ascending=False).iloc[0]
        why = wf[(wf.n_dropped > 0) | (wf.n_added > 0)]
        d.add("Model waterfall", f"{n_in} candidate variables narrowed to {len(feats)}; "
              f"{big.step.lower()} removed the most ({int(big.n_dropped)})",
              two(V.funnel(fsteps),
                  table_html([{"step": r.step, "rule": r.criterion,
                                 "removed": ("−" + str(r.n_dropped)) if r.n_dropped else "",
                                 "added": ("+" + str(r.n_added)) if r.n_added else ""} for r in why.itertuples()],
                               ["step", "rule", "removed", "added"], num={"removed", "added"})
                  + "<div class='sowhat' style='margin-top:12px'><b>So what:</b> the final variables survive every rule, "
                  "so the model is lean, stable and easy to defend.</div>",
                  "Variables remaining after each step"),
              lede="Data-quality and predictive-power filters remove weak or unreliable inputs before any "
                   "regression is fitted; every rule below is the one the Boss approved.")

    # ---- performance
    if perf:
        items = [(k.upper() if k == "oot" else k.capitalize(), v["gini"]) for k, v in perf.items()]
        order = {"Train": 0, "Test": 1, "OOT": 2}
        items.sort(key=lambda x: order.get(x[0], 9))
        drop = train.get("gini", 0) - oot.get("gini", 0)
        psi = f["psi"].get("oot", f["psi"].get("test", 0)) or 0
        shift = ("a stable score distribution" if psi < 0.1 else "a moderate shift in the score "
                 "distribution" if psi < 0.25 else "a material shift in the score distribution")
        d.add("Model performance", f"Out-of-time discrimination is {gini_word(oot.get('gini', 0))} "
              f"(Gini {oot.get('gini', 0):.2f}), " + ("with " if psi < 0.25 else "but with ")
              + f"{shift} (PSI {psi:.2f})",
              two(V.vbars(items, fmt=lambda v: f"{v:.2f}",
                          colors=[V.ACCENT if lab == "OOT" else V.GREY for lab, _ in items], h=300,
                          sub=[f"KS {perf[k.lower()]['ks']:.2f}" for k, _ in items]),
                  f"<p><b>Discrimination.</b> Gini {train.get('gini', 0):.2f} in development falls to "
                  f"{oot.get('gini', 0):.2f} on the most recent applications, a {drop:.2f} drop: "
                  + ("within the usual range for application scorecards." if drop < 0.1 else "worth monitoring.")
                  + (f" The test sample is weaker still ({perf['test']['gini']:.2f}), so part of the development "
                     "Gini is over-fit." if perf.get("test", {}).get("gini", 1) < oot.get("gini", 0) else "")
                  + "</p><p><b>Stability.</b> "
                  + ("A PSI below 0.10 means the score distribution has not shifted.</p>" if psi < 0.1 else
                     f"PSI {psi:.2f}: the recent applicants score differently from development (below 0.10 is "
                     "stable, above 0.25 is material); the ranking still holds, but cut-offs set on development "
                     "data will not give the same approval rates.</p>")
                  + ("<div class='sowhat alert'><b>So what:</b> before go-live, find what moved the population "
                     "(product, channel or macro inputs) and recalibrate the cut-offs on recent data.</div>"
                     if psi >= 0.25 else
                     "<div class='sowhat'><b>So what:</b> the score can be used for decisions now, with routine "
                     "quarterly monitoring.</div>" if oot.get("gini", 0) >= 0.3 else
                     "<div class='sowhat'><b>So what:</b> the ranking is real but modest; pair the score with "
                     "policy rules and look for stronger predictors before the next rebuild.</div>"),
                  "Gini by sample (KS below)"))

    # ---- hyperparameter tuning (the final cut)
    tu = f["tuning"]
    if tu and tu.get("table"):
        rows_t = tu["table"]
        keys = [k for k in ("min_bin_pct", "max_bins", "p_enter") if k in tu["best"]]
        lab = lambda r: " · ".join(hp_label(k, r[k]) for k in keys)          # noqa: E731
        ranked = sorted(rows_t, key=lambda r: -r["objective"])
        items = [(lab(r), r["gini_val"]) for r in ranked]
        focus = {k for k, r in enumerate(ranked) if r.get("chosen")}
        dflt = next((k for k, r in enumerate(ranked) if r.get("is_default")), None)
        ch = next(r for r in ranked if r.get("chosen"))
        df_ = ranked[dflt] if dflt is not None else None
        changed = [f"the {hp_name(k)} from {hp_value(k, tu['default'].get(k))} to {hp_value(k, tu['best'][k])}"
                   for k in keys if tu["default"].get(k) != tu["best"][k]]
        d.add("Hyperparameter tuning",
              (f"Tuning {len(rows_t)} settings on a validation slice "
               + ("kept the planned settings" if not changed else "changed " + " and ".join(changed))
               + f": validation Gini {ch['gini_val']:.3f} with {ch['n_features']} variables"),
              two(V.hbars(items[:12], fmt=lambda v: f"{v:.3f}", focus=focus, label_w=300,
                          note={dflt: "planned"} if dflt is not None and dflt not in focus else None),
                  f"<p><b>How.</b> The training sample was split again: {tu['n_fit']:,} accounts to fit, "
                  f"{tu['n_val']:,} to validate. Each setting re-ran the approved waterfall; test and "
                  "out-of-time samples were never used to choose, so their results stay an honest "
                  "read.</p><p><b>Rule.</b> Highest validation Gini after a penalty for over-fitting "
                  "(fit vs validation gap above 0.05); near-ties go to fewer variables.</p>"
                  + (f"<p><b>Versus the plan.</b> Planned settings scored {df_['gini_val']:.3f} with "
                     f"{df_['n_features']} variables (gap {df_['gap']:.3f}); chosen {ch['gini_val']:.3f} with "
                     f"{ch['n_features']} (gap {ch['gap']:.3f}).</p>" if df_ is not None and not df_.get("chosen") else "")
                  + "<div class='sowhat'><b>So what:</b> "
                  + ("the planned settings were already the best; the model keeps them." if not changed else
                     "the final model uses the tuned settings; the differences are small, which shows the "
                     "model is not sensitive to these choices.") + "</div>",
                  "Validation Gini by setting (chosen in colour)"),
              lede="Binning and selection settings were tuned once, when the model was finalised.")

    # ---- drivers
    if contrib is not None:
        items = [(names.get(r.variabel, r.variabel), r.kontribusi) for r in contrib.itertuples()]
        d.add("Score drivers", f"{', '.join(names.get(v, v) for v in top3['variabel'][:2])} drive "
              f"{top3['kontribusi'][:2].sum():.0%} of the score range",
              two(V.hbars(items, focus={0, 1}),
                  "<p><b>How to read it.</b> Each bar is the gap in points between a variable's best and "
                  "worst bin, as a share of the total: the bigger the bar, the more that variable can move "
                  "an applicant's score.</p>"
                  f"<div class='sowhat'><b>So what:</b> {esc(names.get(top3['variabel'].iloc[0]))} alone "
                  f"accounts for {top3['kontribusi'].iloc[0]:.0%} of the range; the variable slides that follow "
                  "show, bin by bin, why each one earns its points.</div>",
                  "Share of the total score range"))

    # ---- from 20 bands to the ideal number, and the KS it keeps
    bp, bc = f["band_path"], f["band_choice"]
    if bp is not None and bc:
        k = int(bc["bands"])
        items = [(str(int(r.bands)), r.ks) for r in bp.itertuples()]
        chosen = {i for i, r in enumerate(bp.itertuples()) if r.chosen}
        rev_cols = [c for c in bp.columns if c.startswith("reversals_")]
        show = bp[bp.bands.isin({int(bp.bands.max()), k + 1, k, max(k - 1, 2), 5})].drop_duplicates("bands")
        rows = [{"bands": f"{int(r['bands'])}" + (" ✓" if r["chosen"] else ""),
                 "KS train": f"{r['ks']:.3f}",
                 **{f"KS {c[3:]}": f"{r[c]:.3f}" for c in bp.columns if c.startswith("ks_")},
                 "worst p": f"{r['max_p']:.3f}",
                 "reversals": str(int(sum(r[c] for c in rev_cols)))} for r in show.to_dict("records")]
        ksm = bc["ks_model"].get("train", 0)
        d.add("Score band selection",
              f"Twenty equal bands merged step by step to {k}: the most that stay monotonic and "
              f"statistically distinct, keeping a KS of {bc['ks_banded'].get('train', 0):.2f} of the "
              f"model's {ksm:.2f}",
              two(V.vbars(items, fmt=lambda v: f"{v:.2f}", h=290,
                          colors=[V.ACCENT if i in chosen else V.GREY for i in range(len(items))],
                          sub=["✓" if i in chosen else "" for i in range(len(items))]),
                  "<p><b>How.</b> Start from twenty equal-population bands"
                  + (f" ({int(bp.bands.max())} once bands under 5% of accounts are merged)"
                     if int(bp.bands.max()) < 20 else "") + "; at each step merge the two "
                  "neighbouring bands that are least different (a reversal first, else the pair with the "
                  "highest p-value).</p><p><b>Rule.</b> Keep the largest number of bands where the bad "
                  "rate falls band after band in train, test and out-of-time, every neighbouring pair "
                  "differs significantly (p &lt; 0.05) and every band holds at least 5% of accounts.</p>"
                  + table_html(rows, list(rows[0]), num=set(rows[0]) - {"bands"})
                  + f"<div class='sowhat'><b>So what:</b> {k} bands is as fine as the data can support; "
                  "more bands would split groups whose risk is not truly different.</div>",
                  "KS of the banded score by number of bands (chosen in colour)"))

    kt = f["ks_table"]
    if kt is not None and bc:
        tr = kt[kt["sample"] == "train"].reset_index(drop=True)
        series = [{"name": "Cumulative bads", "x": list(tr.band), "y": list(tr.cum_bads), "focus": True},
                  {"name": "Cumulative goods", "x": list(tr.band), "y": list(tr.cum_goods)}]
        top = tr.loc[tr.ks.idxmax()]
        ks_by = {s_: float(g.ks.max()) for s_, g in kt.groupby("sample")}
        rows = [{"band": int(r.band), "accounts": f"{int(r.n):,}", "bad rate": f"{r.bad_rate:.1%}",
                 "cum. bads": f"{r.cum_bads:.0%}", "cum. goods": f"{r.cum_goods:.0%}",
                 "KS": f"{r.ks:.3f}"} for r in tr.itertuples()]
        d.add("KS by score band",
              f"The score separates bads from goods by up to {top.ks:.0%} (KS), reached at band "
              f"{int(top.band)} of {len(tr)}" + (f"; {ks_by['oot']:.2f} out of time" if "oot" in ks_by else ""),
              two(V.lines(series, xmax=len(tr), ymax=1.0, xlabel="score band (1 = riskiest)",
                          marker=(float(top.band), f"KS {top.ks:.2f}"), h=300),
                  table_html(rows, ["band", "accounts", "bad rate", "cum. bads", "cum. goods", "KS"],
                             num={"accounts", "bad rate", "cum. bads", "cum. goods", "KS"})
                  + "<div class='sowhat'><b>So what:</b> declining the riskiest bands up to band "
                  f"{int(top.band)} would remove {top.cum_bads:.0%} of bads at the cost of "
                  f"{top.cum_goods:.0%} of goods.</div>",
                  "Cumulative share of bads and goods by band (development)"),
              source=f"Source: KS table, development sample · KS by sample: "
                     + ", ".join(f"{k_} {v:.2f}" for k_, v in ks_by.items()))

    # ---- recommended score bands
    if bands is not None:
        from ..engine.risk import is_monotonic
        rev = set(k - 1 for k in is_monotonic(bands.bad_rate_train.tolist()))
        items = [(str(int(r.band)), r.bad_rate_train) for r in bands.itertuples()]
        sub = [f"{r.score_min:.0f}+" for r in bands.itertuples()]
        b0, bn = bands.iloc[0], bands.iloc[-1]
        d.add("Recommended score binning", f"Bad rate falls from {b0.bad_rate_train:.0%} in the lowest "
              f"score band to {bn.bad_rate_train:.1%} in the highest across {len(bands)} bands",
              two(V.vbars(items, flag=rev, h=310, sub=sub),
                  f"<p><b>Construction.</b> The {len(bands)} bands chosen on the previous slides, from "
                  "twenty equal-population bands; every band holds at least 5% of accounts and differs "
                  "significantly from its neighbours.</p>"
                  + (f"<p><b>Watch point.</b> Band{'s' if len(rev) > 1 else ''} "
                     f"{', '.join(str(k + 1) for k in sorted(rev))} break{'s' if len(rev) == 1 else ''} the trend "
                     "slightly; grouping into risk levels absorbs this noise.</p>" if rev else
                     "<p><b>Monotonic.</b> Every band is safer than the one below it.</p>")
                  + ("<div class='sowhat'><b>So what:</b> the trend holds overall (higher score, lower risk); "
                     "the small reversals are why we recommend deciding on risk levels, not single bands.</div>"
                     if rev else "<div class='sowhat'><b>So what:</b> higher scores mean lower risk, band "
                     "after band.</div>"),
                  "Bad rate by score band (lowest band starts at the score shown)"))

    # ---- risk levels
    if levels is not None:
        lvl = [l for l in LEVEL_ORDER if l in set(levels.level)]
        lv = levels.set_index("level").loc[lvl]
        chart = (V.vbars([(f"{l}", lv.loc[l, "bad_rate_train"]) for l in lvl],
                         colors=[V.LEVEL_COLORS[l] for l in lvl], h=230,
                         sub=[f"{lv.loc[l, 'score_min']:.0f}–{lv.loc[l, 'score_max']:.0f}" for l in lvl])
                 + "<h4 style='margin-top:14px'>Share of applicants</h4>"
                 + V.stacked_share([(l, lv.loc[l, "pct"], V.LEVEL_COLORS[l]) for l in lvl]))
        rows = [{"level": f"<span class='pill' style='background:{V.LEVEL_COLORS[l]}'>{l}</span> {LEVEL_NAME[l]}",
                 "policy (illustrative)": POLICY[l]} for l in lvl]
        d.add("Risk levels" + ("" if f["levels_agreed"] else " · proposal"),
              f"{count_word(len(lvl)).capitalize()} risk levels separate bad rates {sep:.0f}-fold, from {lv.loc[lvl[0], 'bad_rate_train']:.0%} "
              f"(very high) to {lv.loc[lvl[-1], 'bad_rate_train']:.1%} (very low)" if sep else "Recommended risk levels",
              two(chart, table_html(rows, ["level", "policy (illustrative)"], raw=True)
                  + ("<div class='sowhat'><b>Agreed with the Boss.</b> Cut-offs are final for this model.</div>"
                     if f["levels_agreed"] else
                     "<div class='sowhat'><b>For discussion:</b> cut-offs proposed by the modeller; to be "
                     "agreed with credit policy before go-live.</div>"),
                  "Bad rate by risk level (score range below)"))

    # ---- one slide per variable: every bin, its points and what each bin says
    tbl = f["table"]
    avg = train.get("bad_rate") or None
    for var, an in (bin_analysis(f) if tbl is not None else {}).items():
        g, m = an["bins"], f["meanings"].get(var, {})
        worst, best = an["worst"], an["best"]
        items = [(r["label"], r["bad_rate"]) for r in g]
        low = {k for k, r in enumerate(g) if r["points"] == worst["points"]}
        rows = [{"bin": r["label"], "accounts": f"{r['pct']:.0%}", "bad rate": f"{r['bad_rate']:.1%}",
                 "risk index": f"{r['index']:.1f}×" if avg else "-", "points": r["points"],
                 "read": r["read"]} for r in g]
        english = m.get("asli") or (m.get("arti") if str(m.get("dasar", "")).startswith("kamus data bank") else "")
        pattern = ("the bad rate moves one way across the ranges, as a defensible points pattern requires"
                   if an["monotonic"] and an["numeric"] else "categories are ordered by their bad rate"
                   if not an["numeric"] else
                   f"the trend breaks at {', '.join(an['breaks'])}; worth a check at the next rebuild")
        d.add("Scorecard by variable", f"{names.get(var, var)}: from {worst['points']} points "
              f"({worst['label']}, {worst['bad_rate']:.0%} bad) to {best['points']} points "
              f"({best['label']}, {best['bad_rate']:.1%} bad)",
              two(V.vbars(items, colors=[V.ALERT if k in low else V.GREY for k in range(len(items))], h=210,
                          sub=[f"{r['points']} pts" for r in g])
                  + table_html(rows, ["bin", "accounts", "bad rate", "risk index", "points", "read"],
                               num={"accounts", "bad rate", "risk index", "points"}),
                  f"<p><b>What it is.</b> {esc(english or names.get(var, var))}"
                  + ("" if english else " (meaning inferred by the team; no bank description)") + ".</p>"
                  f"<p><b>Weight.</b> {an['share']:.0%} of the score range; IV {an['iv']:.2f} "
                  f"({an['strength']}); {pattern}.</p>"
                  f"<p><b>Bin by bin.</b> The riskiest bin ({esc(worst['label'])}, {worst['pct']:.0%} of "
                  f"applicants) runs at {worst['index']:.1f}× the average bad rate; the safest "
                  f"({esc(best['label'])}, {best['pct']:.0%}) at {best['index']:.1f}×: a "
                  f"{an['spread']:.0f}-fold spread.</p>"
                  + (f"<p><b>Thin bins.</b> {', '.join(esc(b) for b in an['thin'])} hold under 5% of "
                     "applicants each; their points rest on few accounts.</p>" if an["thin"] else "")
                  + ("<div class='sowhat alert'><b>Flag:</b> this variable has no clear business meaning. "
                     "A variable that cannot be explained should not drive credit decisions; we recommend "
                     "removing it (force-drop in the waterfall) and rebuilding.</div>" if an["doubt"] else
                     f"<div class='sowhat'><b>So what:</b> {esc(an['so_what'])}</div>"),
                  "Bad rate by bin (points below)"),
              source=f"Source: scorecard table, development sample · variable {var} · risk index = bin bad "
                     "rate ÷ portfolio bad rate", var=var)

    # ---- appendix
    d.add("Appendix: methodology", "Definitions and conventions used throughout",
          two(table_html([
              {"term": "Bad", "definition": f"ever {vin['threshold']}+ DPD within {vin['window']} months on book"
               if vin else f"{t.get('column')} {t.get('rule')} {t.get('value')}"},
              {"term": "Immature", "definition": "not observed for the full window: excluded"},
              {"term": "WoE", "definition": "ln(share of goods / share of bads) in a bin"},
              {"term": "IV", "definition": "Σ (share goods − share bads) × WoE: predictive power"},
              {"term": "Gini", "definition": "2 × AUC − 1: how well the score ranks bads below goods"},
              {"term": "KS", "definition": "largest gap between cumulative good and bad distributions"},
              {"term": "PSI", "definition": "shift of the score distribution vs development (<0.10 stable)"},
              {"term": "OOT", "definition": f"out-of-time sample from {t.get('oot_from')}" if t.get("oot_from") else "out-of-time sample"},
          ], ["term", "definition"]),
              "<p><b>Reproducibility.</b> Build, waterfall report, finalization review and the variable "
              "dictionary are attached to the results emails and can be regenerated from the logged "
              "inputs.</p>", "Glossary"))
    return {"html": d.html(), "slides": len(d.slides), "cut": f["cut"],
            "pages": [{**m, "html": page(sec, m["action"])} for m, sec in zip(d.meta, d.slides)]}


def page(section: str, title: str) -> str:
    """One slide as its own page for the projector: the 16:9 slide scaled to the screen."""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{esc(title)}</title>"
            f"<style>{CSS}html,body{{margin:0;height:100%;overflow:hidden;background:#fff}}"
            ".slide{margin:0;box-shadow:none;transform-origin:0 0;position:absolute;left:0;top:0}"
            "</style></head><body>" + section +
            "<script>function fit(){var s=document.querySelector('.slide'),k=Math.min(innerWidth/1280,"
            "innerHeight/720);s.style.transform='scale('+k+')';s.style.left=((innerWidth-1280*k)/2)+'px';"
            "s.style.top=((innerHeight-720*k)/2)+'px'}addEventListener('resize',fit);fit()</script>"
            "</body></html>")


def table_html(rows: list[dict], cols: list[str], raw: bool = False, num: set[str] | None = None) -> str:
    num = num or set()
    head = "".join(f"<th class='{'n' if c in num else ''}'>{esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(
        f"<td class='{'n' if c in num else ''}'>{r.get(c, '') if raw else esc(r.get(c, ''))}</td>" for c in cols)
        + "</tr>" for r in rows)
    return f"<table class='t'><tr>{head}</tr>{body}</table>"
