"""The project's deliverables, built from what the team already produced:

- the MODEL DOCUMENTATION (Indonesian, assembled by Laras): data and join, target and
  vintage, the variable dictionary, the waterfall, the scorecard points, score
  contribution, risk bands and levels, decisions and the Boss's orders;
- the DECK draft (English, Kirana): a consulting-style HTML deck (office/deck.py). PDF / PPTX only
  after the Boss approves the draft (Phase 3 export).

Both need at least one built cut; they use the confirmed cut, else the latest one.
"""
from __future__ import annotations

import json

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
    """The model documentation: a consulting-style report in Indonesian (report.py), one
    HTML file that prints to A4."""
    from .report import build
    cut = _cut(office)
    proj = office.project
    page = build(office, cut)
    d = proj.dir("artifacts") / OUT
    d.mkdir(exist_ok=True)
    f = d / "dokumentasi_model.html"
    f.write_text(page, encoding="utf-8")
    import re as _re
    return {"file": str(f.relative_to(proj.root)), "cut": cut,
            "sections": _re.findall(r"<h2>([^<]+)</h2>", page)[1:]}


def selected_features(office) -> dict:
    """Citra's handover: Bening's dictionary matched to the variables in the model, with
    each one's points range and share of the score."""
    cut = _cut(office)
    proj = office.project
    art = proj.dir("artifacts") / cut
    if not (proj.dir("work") / "variable_meanings.json").exists():
        raise ValueError("kamus variabel belum didokumentasikan Bening")
    vm = {r["variabel"]: r for r in office.approved_meanings()}
    if not vm:
        raise ValueError("kamus Bening belum lolos review Laras")
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
