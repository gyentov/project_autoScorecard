"""The project's deliverables, built from what the team already produced:

- the MODEL DOCUMENTATION (Indonesian, assembled by Laras): data and join, target and
  vintage, the variable dictionary, the waterfall, the scorecard points, score
  contribution, risk bands and levels, decisions and the Boss's orders;
- the DECK draft (English, Kirana): a consulting-style HTML deck (office/deck.py). PDF / PPTX only
  after the Boss approves the draft (Phase 3 export).

Both need at least one built cut; they use the confirmed cut, else the latest one.
"""
from __future__ import annotations

import html
import json
from datetime import datetime

import pandas as pd

from .meetings import _page, html_table

OUT = "deliverables"


def _cut(office) -> str:
    cuts = office.state.cuts
    done = [c for c, i in cuts.items() if i.get("status") in ("built", "confirmed")]
    if not done:
        raise ValueError("belum ada cut yang selesai dibangun")
    confirmed = [c for c in done if cuts[c]["status"] == "confirmed"]
    return (confirmed or done)[-1]


def _read(art, name):
    p = art / name
    if not p.exists():
        return None
    return pd.read_csv(p) if name.endswith(".csv") else json.loads(p.read_text(encoding="utf-8"))


def _table(df, cols=None, fmt=None) -> str:
    if df is None or len(df) == 0:
        return "<p class='muted'>(belum ada)</p>"
    df = df[cols] if cols else df
    fmt = fmt or {}
    return html_table([{k: (fmt[k](v) if k in fmt and pd.notna(v) else v) for k, v in r.items()}
                       for r in df.to_dict("records")])


def _pct(v) -> str:
    return f"{v:.1%}"


def documentation(office) -> dict:
    """The model documentation as one HTML page (and the same sections as Markdown)."""
    st = office.state
    cut = _cut(office)
    proj = office.project
    art = proj.dir("artifacts") / cut
    info = st.cuts[cut]
    t = st.target or {}
    rule = {"ge": ">=", "eq": "==", "in": "in"}.get(t.get("rule"), t.get("rule"))
    perf = (_read(art, "performance.json") or {}).get("performance", {})
    vm = json.loads((proj.dir("work") / "variable_meanings.json").read_text(encoding="utf-8")) \
        if (proj.dir("work") / "variable_meanings.json").exists() else []
    contrib = _read(art, "score_contribution.csv")
    bands = _read(art, "risk_bands.csv")
    levels = _read(art, "risk_levels.csv")
    sc = _read(art, "scorecard_table.csv")
    plan = st.join_plan or {}
    decisions = [{"rapat": m.title, "keputusan": d["decision"]}
                 for m in office.meetings.list() for d in m.decisions]
    orders = [{"#": o["id"], "untuk": o["to"], "perintah": o["summary"], "status": o["status"]}
              for o in office.orders.list()]
    sec = []
    sec.append(("1. Ringkasan", html_table([
        {"hal": "proyek", "isi": proj.meta.name},
        {"hal": "model", "isi": f"{cut.replace('cut', 'Cut ')} ({info.get('status')})"},
        {"hal": "target", "isi": f"bad = {t.get('column')} {rule} {t.get('value')}"},
        {"hal": "fitur di model", "isi": ", ".join(info.get("features", []))},
        *[{"hal": f"Gini / KS {k}", "isi": f"{v['gini']:.3f} / {v['ks']:.3f}"} for k, v in perf.items()],
    ])))
    joins = [{"tabel": j["right"], "kunci": f"{j['left_on']} = {j['right_on']}",
              "relasi": j["relationship"], "cocok": f"{j['coverage']:.1%}"} for j in plan.get("joins", [])]
    sec.append(("2. Data dan join", f"<p>Tabel dasar: <b>{html.escape(str(plan.get('base')))}</b>; "
                "semua join left join.</p>" + html_table(joins)))
    vin = t.get("vintage")
    sec.append(("3. Target", f"<p>bad = {html.escape(str(t.get('column')))} {rule} "
                f"{html.escape(str(t.get('value')))}"
                + (f"; dari vintage analysis: pernah {vin['threshold']}+ DPD dalam {vin['window']} bulan, "
                   f"{vin['bads']:,} bad ({vin['bad_rate']:.1%}) dari {vin['n_mature']:,} akun matang"
                   if vin else "") + (f". OOT mulai {t.get('oot_from')}" if t.get("oot_from") else "")
                + ".</p>"))
    sec.append(("4. Kamus variabel", html_table(
        [{k: r.get(k, "") for k in ("variabel", "arti", "satuan", "dasar", "keyakinan")} for r in vm])
        if vm else "<p class='muted'>(belum didokumentasikan)</p>"))
    sec.append(("5. Seleksi fitur (waterfall)", "<p>Rincian per langkah ada di "
                f"<code>artifacts/{cut}/waterfall_report.md</code>.</p>"
                + (art / "waterfall.svg").read_text(encoding="utf-8")
                if (art / "waterfall.svg").exists() else ""))
    sec.append(("6. Scorecard", _table(sc, ["feature", "bin", "count", "bad_rate", "woe", "points"],
                                       {"bad_rate": _pct, "woe": lambda v: round(v, 3)})))
    sec.append(("7. Kontribusi skor", _table(contrib, None, {"kontribusi": _pct})))
    sec.append(("8. Pembagian risiko", _table(bands, ["band", "score_min", "score_max", "n", "pct",
                                                     "bad_rate_train"],
                                              {"pct": _pct, "bad_rate_train": _pct})
                + ("<h3>Kategori risiko</h3>" + _table(levels, ["level", "arti", "band", "score_min",
                                                              "score_max", "pct", "bad_rate_train"],
                                                       {"pct": _pct, "bad_rate_train": _pct})
                   if levels is not None else "<p class='muted'>Kategori VL–VH belum ditetapkan.</p>")))
    sec.append(("9. Keputusan", html_table(decisions)))
    sec.append(("10. Perintah Boss", html_table(orders)))
    body = (f"<p class='muted'>Disusun {datetime.now():%Y-%m-%d %H:%M} oleh tim AutoScorecard "
            "(Laras, Bening, Citra). Angka dari engine; arti variabel dari kamus Bening.</p>"
            + "".join(f"<h2>{html.escape(h)}</h2>{b}" for h, b in sec))
    d = proj.dir("artifacts") / OUT
    d.mkdir(exist_ok=True)
    f = d / "dokumentasi_model.html"
    f.write_text(_page(f"Dokumentasi model: {proj.meta.name}", body), encoding="utf-8")
    return {"file": str(f.relative_to(proj.root)), "cut": cut, "sections": [h for h, _ in sec]}


def selected_features(office) -> dict:
    """Citra's handover: Bening's dictionary matched to the variables in the model, with
    each one's points range and share of the score."""
    cut = _cut(office)
    proj = office.project
    art = proj.dir("artifacts") / cut
    path = proj.dir("work") / "variable_meanings.json"
    if not path.exists():
        raise ValueError("kamus variabel belum didokumentasikan Bening")
    vm = {r["variabel"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
    feats = office.state.cuts[cut].get("features", [])
    contrib = _read(art, "score_contribution.csv")
    c = contrib.set_index("variabel") if contrib is not None else None
    rows = []
    for f in feats:
        m = vm.get(f, {})
        r = {"variabel": f, "arti": m.get("arti") or "(belum ada)", "satuan": m.get("satuan", ""),
             "dasar": m.get("dasar", "")}
        if c is not None and f in c.index:
            r["poin"] = f"{c.loc[f, 'poin min']} s.d. {c.loc[f, 'poin max']}"
            r["kontribusi"] = f"{c.loc[f, 'kontribusi']:.0%}"
        rows.append(r)
    body = (f"<p class='muted'>Model {cut.replace('cut', 'Cut ')}: {len(feats)} fitur. Arti dari kamus "
            "Bening; poin dan kontribusi dari scorecard Citra.</p>" + html_table(rows))
    d = proj.dir("artifacts") / OUT
    d.mkdir(exist_ok=True)
    f = d / "fitur_terpilih.html"
    f.write_text(_page(f"Arti fitur terpilih: {proj.meta.name}", body), encoding="utf-8")
    return {"file": str(f.relative_to(proj.root)), "cut": cut, "n": len(feats), "rows": rows}


def deck(office) -> dict:
    """Kirana's draft deck (English, HTML, 16:9, consulting style) for the Boss to review."""
    from .deck import build
    cut = _cut(office)
    proj = office.project
    out = build(office)
    d = proj.dir("artifacts") / OUT
    d.mkdir(exist_ok=True)
    f = d / "deck_draft.html"
    f.write_text(out["html"], encoding="utf-8")
    return {"file": str(f.relative_to(proj.root)), "cut": cut, "slides": out["slides"]}
