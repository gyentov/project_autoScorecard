"""Bening's EDA report as a file people read (HTML), the slide for Citra's plan meeting, and
the plain-text summaries for the emails. Every number comes from work/eda.json; the
points, Citra's review and the discussion minutes from the reviewed EDA document."""
from __future__ import annotations

import html

from .edareview import OUTCOMES
from .meetings import html_table

CSS = """
:root{--bg:#f7f7f5;--paper:#fff;--ink:#1f2933;--muted:#616e7c;--rule:#e4e7eb;--acc:#0f766e;
 --soft:#f1f5f4;--warn:#b54708;--warnbg:#fff6ed;--ok:#047857;--okbg:#ecfdf5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111416;--paper:#1a1e21;
 --ink:#e6e8ea;--muted:#9aa5b1;--rule:#2c3236;--acc:#2dd4bf;--soft:#20262a;--warn:#fdba74;
 --warnbg:#2b1d12;--ok:#6ee7b7;--okbg:#10241d}}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:28px;margin:0 0 4px} h2{font-size:20px;margin:34px 0 8px;border-bottom:2px solid var(--acc);
 padding-bottom:4px} h3{font-size:16px;margin:18px 0 6px}
.muted{color:var(--muted)} .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card{background:var(--paper);border:1px solid var(--rule);border-top:3px solid var(--acc);padding:10px 14px}
.card .v{font-size:24px;font-weight:700} .card .k{font-size:12px;color:var(--muted)}
.scroll{overflow-x:auto;background:var(--paper);border:1px solid var(--rule)}
table{border-collapse:collapse;width:100%;font-size:13px} th,td{padding:5px 9px;border-bottom:1px solid var(--rule);
 text-align:left;vertical-align:top} th{color:var(--muted);font-weight:600;white-space:nowrap}
.pt{background:var(--paper);border:1px solid var(--rule);padding:12px 16px;margin:10px 0}
.pt .h{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.tag{display:inline-block;font-size:11px;font-weight:700;border-radius:99px;padding:1px 9px;background:var(--soft);color:var(--muted)}
.tag.ok{background:var(--okbg);color:var(--ok)} .tag.warn{background:var(--warnbg);color:var(--warn)}
.turn{margin:6px 0 0 0;padding-left:12px;border-left:3px solid var(--rule)} .turn b{color:var(--acc)}
.res{margin-top:8px;padding:8px 12px;background:var(--soft)}
.note{background:var(--warnbg);border-left:4px solid var(--warn);padding:10px 14px;margin:12px 0}
"""

NAME = {"bening": "Bening", "citra": "Citra", "laras": "Laras"}


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def _num(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1e4 else f"{v:.4g}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "<p class='muted'>(tidak ada)</p>"
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{esc(r.get(c, ''))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<div class='scroll'><table><tr>{head}</tr>{body}</table></div>"


def outcome_of(doc: dict, pid: str) -> str:
    rv = doc["review"].get(pid, {})
    d = doc["discussions"].get(pid)
    if d:
        return OUTCOMES.get(d["resolution"].get("outcome"), "-")
    return "disepakati" if rv.get("stance") == "setuju" else "-"


def points_rows(doc: dict) -> list[dict]:
    return [{"poin": p["id"], "variabel": ", ".join(p["variables"]) or "(umum)", "temuan": p["finding"],
             "usul Bening": p["recommendation"],
             "Citra": doc["review"].get(p["id"], {}).get("stance", "belum direview"),
             "hasil": outcome_of(doc, p["id"])} for p in doc["points"]]


def slide(doc: dict, review: dict) -> str:
    """The plan meeting's slide: points that ask for something, with the outcome."""
    rows = [r for r, p in zip(points_rows(doc), doc["points"]) if p["action"] != "note"]
    n_note = sum(p["action"] == "note" for p in doc["points"])
    status = "lolos review Laras" if review.get("status") == "approved" else "belum lolos review Laras"
    return (f"<p class='muted'>{len(doc['points'])} poin EDA Bening ({n_note} catatan saja); notulen "
            f"diskusi {status}. Lengkapnya di laporan EDA.</p>"
            + html_table(rows, ["poin", "variabel", "usul Bening", "Citra", "hasil"]))


def report(eda: dict, doc: dict, review: dict, title: str = "") -> str:
    ov, vs = eda["overview"], eda["variables"]
    num = [v for v in vs if v["kind"] == "numerik"]
    cat = [v for v in vs if v["kind"] == "kategorik"]
    n_dis = len(doc["discussions"])
    approved = review.get("status") == "approved"
    cards = [(f"{ov['rows']:,}", "baris"), (f"{ov['rows_known']:,}", "baris dengan target"),
             (_pct(ov["bad_rate"]), f"bad rate ({ov['bads']:,} bad)"), (str(ov["features"]), "kandidat fitur"),
             (str(len(doc["points"])), "poin Bening"), (str(n_dis), "poin didiskusikan")]
    out = [f"<!doctype html><html lang='id'><head><meta charset='utf-8'><meta name='viewport' "
           f"content='width=device-width,initial-scale=1'><title>Laporan EDA</title><style>{CSS}</style>"
           f"</head><body><main><h1>Laporan EDA {esc(title)}</h1>"
           f"<p class='muted'>Disusun Bening (prepper) · direview Citra (modeller) · notulen diskusi dicek "
           f"Laras: <span class='tag {'ok' if approved else 'warn'}'>"
           f"{'lolos review' if approved else 'belum lolos review'}</span> (versi {esc(review.get('version'))})</p>",
           "<div class='cards'>" + "".join(f"<div class='card'><div class='v'>{esc(v)}</div>"
                                           f"<div class='k'>{esc(k)}</div></div>" for v, k in cards) + "</div>",
           "<p>Hubungan dengan target dihitung di data pengembangan "
           f"({ov['rows_dev']:,} baris); stabilitas membandingkan data pengembangan dengan data terbaru "
           f"({ov['rows_late']:,} baris" + (f", mulai {esc(ov['late_from'])}" if ov.get("late_from") else "")
           + "). Semua angka dihitung mesin (engine/eda.py), bukan diketik.</p>"]
    if ov.get("by_period"):
        out.append("<h2>1. Target per periode</h2>" + _table(
            [{"kuartal": p["period"], "baris": f"{p['n']:,}", "bad rate": _pct(p["bad_rate"])}
             for p in ov["by_period"]], ["kuartal", "baris", "bad rate"]))
    out.append("<h2>2. Statistik deskriptif: numerik</h2>" + _table(
        [{"variabel": v["variable"], "n": _num(v["n"]), "missing": _pct(v["missing_rate"]),
          "unik": _num(v["n_unique"]), "mean": _num(v.get("mean")), "std": _num(v.get("std")),
          "min": _num(v.get("min")), "p5": _num(v.get("p5")), "median": _num(v.get("p50")),
          "p95": _num(v.get("p95")), "max": _num(v.get("max")), "skew": _num(v.get("skew")),
          "nol": _pct(v.get("zero_share")), "outlier IQR": _pct(v.get("outlier_share"))} for v in num],
        ["variabel", "n", "missing", "unik", "mean", "std", "min", "p5", "median", "p95", "max", "skew",
         "nol", "outlier IQR"]))
    if cat:
        out.append("<h2>3. Statistik deskriptif: kategorik</h2>" + _table(
            [{"variabel": v["variable"], "n": _num(v["n"]), "missing": _pct(v["missing_rate"]),
              "kategori": _num(v["n_unique"]), "teratas": "; ".join(
                  f"{t['value']} {_pct(t['share'])} (bad {_pct(t['bad_rate'])})" for t in v.get("top", [])[:4]),
              "kategori <1%": _num(v.get("rare_categories"))} for v in cat],
            ["variabel", "n", "missing", "kategori", "teratas", "kategori <1%"]))
    out.append("<h2>4. Hubungan dengan target</h2><p class='muted'>AUC univariat (0,5 = tidak membedakan); "
               "bad rate saat missing vs terisi dengan uji dua proporsi; tren bad rate di 5 band kuantil "
               "(rho Spearman, ±1 = monoton).</p>" + _table(
                   [{"variabel": v["variable"], "AUC": _num(v["target"].get("auc")),
                     "bad rate missing": _pct(v["target"].get("missing_bad_rate")),
                     "bad rate terisi": _pct(v["target"].get("filled_bad_rate")),
                     "p (missing)": _num(v["target"].get("missing_p_value")),
                     "tren": ("bad naik bila nilai naik" if v["target"].get("direction") == "naik"
                              else "bad turun bila nilai naik" if v["target"].get("direction") else "-"),
                     "rho tren": _num(v["target"].get("trend_rho"))}
                    for v in sorted(vs, key=lambda v: -(v["target"].get("auc") or 0))],
                   ["variabel", "AUC", "bad rate missing", "bad rate terisi", "p (missing)", "tren", "rho tren"]))
    stab = [v for v in vs if v.get("stability")]
    if stab:
        out.append("<h2>5. Stabilitas</h2><p class='muted'>PSI &lt; 0,10 stabil, 0,10–0,25 bergeser sedang, "
                   "&gt; 0,25 bergeser jauh; rho urutan = apakah band yang berisiko di data lama tetap berisiko "
                   "di data baru.</p>" + _table(
                       [{"variabel": v["variable"], "PSI": _num(v["stability"].get("psi")),
                         "missing lama": _pct(v["stability"].get("missing_dev")),
                         "missing baru": _pct(v["stability"].get("missing_late")),
                         "rho urutan": _num(v["stability"].get("rank_rho"))}
                        for v in sorted(stab, key=lambda v: -(v["stability"].get("psi") or 0))],
                       ["variabel", "PSI", "missing lama", "missing baru", "rho urutan"]))
    out.append("<h2>6. Korelasi tinggi</h2>" + _table(
        [{"variabel A": p["a"], "variabel B": p["b"], "rho Spearman": p["rho"]} for p in eda["correlated_pairs"]],
        ["variabel A", "variabel B", "rho Spearman"]))
    out.append("<h2>7. Poin Bening dan tanggapan Citra</h2>")
    for p in doc["points"]:
        rv = doc["review"].get(p["id"], {"stance": "belum direview", "reason": "menunggu Citra."})
        stance_tag = "ok" if rv["stance"] == "setuju" else "warn"
        out.append(f"<div class='pt'><div class='h'><b>{esc(p['id'])}</b><span class='tag'>{esc(p['severity'])}</span>"
                   f"<span>{esc(p['finding'])}</span></div><div><b>Usul Bening:</b> {esc(p['recommendation'])}</div>"
                   f"<div><b>Citra:</b> <span class='tag {stance_tag}'>{esc(rv['stance'])}</span> {esc(rv['reason'])}"
                   + (f" <span class='muted'>(usulan {esc(rv['proposal_id'])} menunggu ACC Boss)</span>"
                      if rv.get("proposal_id") else "") + "</div></div>")
    out.append("<h2>8. Notulen diskusi Bening–Citra</h2>")
    if not doc["discussions"]:
        out.append("<p class='muted'>Citra setuju dengan semua poin; tidak ada yang perlu didiskusikan.</p>")
    for pid, d in doc["discussions"].items():
        r = d["resolution"]
        out.append(f"<div class='pt'><div class='h'><b>{esc(pid)}</b><span>{esc(', '.join(d['variables']))}</span>"
                   + (" <span class='tag'>diangkat Citra</span>" if d.get("raised_by") else "") + "</div>"
                   + "".join(f"<div class='turn'><b>{esc(NAME.get(t['by'], t['by']))}:</b> {esc(t['text'])}</div>"
                             for t in d["turns"])
                   + f"<div class='res'><b>Hasil: {esc(OUTCOMES.get(r['outcome'], r['outcome']))}.</b> "
                   f"{esc(r['decision'])} <span class='muted'>Alasan: {esc(r['reason'])}</span>"
                   + (f"<br><b>Pertanyaan untuk Boss:</b> {esc(r['question_for_boss'])}" if r.get("question_for_boss") else "")
                   + (f"<br><span class='muted'>Usulan {esc(r['proposal_id'])} menunggu ACC Boss.</span>"
                      if r.get("proposal_id") else "") + "</div></div>")
    out.append("<h2>9. Review Laras atas notulen</h2>")
    for rd in review.get("rounds", []):
        blk = [f for f in rd["found"] if f["blocking"]]
        out.append(f"<p><b>Putaran {rd['round']}:</b> " + (
            "; ".join(f"{esc(f['point'])}: {esc(f['problem'])}" for f in blk) if blk else "tidak ada temuan yang menghambat.")
            + ("<br><span class='muted'>Diperbaiki Bening: " + esc("; ".join(rd["fixed"])) + "</span>" if rd.get("fixed") else "")
            + "</p>")
    if review.get("notes"):
        out.append("<div class='note'><b>Catatan untuk Boss:</b><br>" + "<br>".join(
            f"{esc(n['point'])}: {esc(n['problem'])}" for n in review["notes"]) + "</div>")
    out.append("</main></body></html>")
    return "".join(out)
