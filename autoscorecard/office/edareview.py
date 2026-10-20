"""Bening's EDA points, Citra's review, their discussion, and Laras's check of the minutes.

    Bening raises points from the EDA (engine/eda.py), each with a recommendation
    -> Citra reads every point: setuju / tidak setuju, with her reason (rule-based, $0)
    -> every point she disagrees with is discussed: Bening states her evidence, Citra
       hers, Bening answers, Citra closes; the outcome is one of
           ikut_citra     Bening accepts Citra's view
           ikut_bening    Citra accepts Bening's view
           kompromi       a middle way both accept (e.g. keep it, add a missing flag)
           eskalasi_boss  they still differ: the Boss decides (a proposal waits for ACC)
    -> Laras checks the minutes (notulen) and Bening fixes what she finds, at most three
       rounds; only an approved version is documentation (work/eda_approved.json).

Citra can also raise a point later (`dispute`, her tool or "diskusikan P3: ..."): it is
discussed the same way, and Laras checks the new version.

What Laras checks (blocking):
    a point Citra disagreed with that has no discussion; a discussion without both
    Bening and Citra; an opening argument without a number (evidence); a resolution
    without an outcome, a decision or a reason; an escalation without a question for
    the Boss; a drop that the waterfall will not do by itself without a proposal for ACC
Notes for the Boss: escalated points and proposals still waiting for ACC.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from ..engine.eda import OUTCOME_NAME

OUTCOMES = {"ikut_citra": "ikut Citra", "ikut_bening": "ikut Bening", "kompromi": "kompromi",
            "eskalasi_boss": "diputuskan Boss"}
_NUM = re.compile(r"\d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def version(doc: dict) -> str:
    raw = json.dumps([doc.get("points"), doc.get("review"), doc.get("discussions")],
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def evidence_line(point: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in point.get("evidence", {}).items() if v is not None)


# --------------------------------------------------------------------------- Citra reads
def citra_review(point: dict, eda: dict, plan: dict[str, dict], treatment: str = "none") -> dict:
    """Citra's stance on one point, from the numbers and the waterfall plan:
    {"stance": setuju|tidak setuju, "reason", "waterfall": True when the plan already
    does what Bening asks}. `plan`: enabled step type -> params."""
    k, ev, v = point["kind"], point["evidence"], (point["variables"] or [""])[0]
    br = eda["overview"].get("bad_rate") or 0
    agree = lambda reason, wf=False: {"stance": "setuju", "reason": reason, "waterfall": wf}  # noqa: E731
    differ = lambda reason: {"stance": "tidak setuju", "reason": reason, "waterfall": False}  # noqa: E731
    if k == "high_missing":
        mb, fb, p = ev.get("missing_bad_rate"), ev.get("filled_bad_rate"), ev.get("missing_p_value")
        if None not in (mb, fb, p) and p < 0.05 and abs(mb - fb) >= 0.2 * max(br, 1e-9):
            return differ(f"Missing-nya informatif: bad rate saat missing {_pct(mb)} vs terisi {_pct(fb)} "
                          f"(p = {p:.3g}). Kalau dibuang, informasi itu hilang.")
        mx = plan.get("missing_rate", {}).get("max")
        return agree("Setuju; missing-nya tidak membedakan risiko." + (
            f" Aturan missing rate waterfall (maks {mx:.0%}) akan membuangnya sendiri." if mx is not None
            and ev["missing_rate"] >= mx else ""), mx is not None and ev["missing_rate"] >= mx)
    if k == "constant":
        mx = plan.get("identical_rate", {}).get("max")
        wf = mx is not None and ev["top_share"] >= mx
        return agree("Setuju; tanpa variasi tidak ada yang bisa dibedakan." + (
            f" Aturan identical rate waterfall (maks {mx:.0%}) akan membuangnya." if wf else ""), wf)
    if k == "outliers" and treatment == "none":
        return differ(f"Binning memakai bin ujung yang terbuka, jadi {_pct(ev['outlier_share'])} nilai di luar "
                      "pagar IQR hanya mendapat WoE bin ujung; cap tidak mengubah urutan risiko dan "
                      "menambah langkah yang harus diulang saat scoring.")
    if k == "leakage":
        if ev["auc"] >= 0.9 or OUTCOME_NAME.search(v):
            return agree(f"Setuju; AUC {ev['auc']:.2f}" + (" dan namanya mengarah ke data hasil" if
                                                           OUTCOME_NAME.search(v) else "")
                         + ", terlalu berisiko dipakai.")
        return differ(f"AUC {ev['auc']:.2f} memang kuat, tapi belum tentu bocor: namanya tidak mengarah ke "
                      "data hasil. Yang menentukan adalah kapan variabel ini tercatat.")
    if k == "unstable":
        rho = ev.get("rank_rho")
        if rho is not None and rho >= 0.8:
            return differ(f"Distribusinya bergeser (PSI {ev['psi']:.2f}), tapi urutan risiko per band tetap "
                          f"(rho {rho:.2f} antara data lama dan baru); untuk scorecard, urutan itu yang penting.")
        return agree(f"Setuju; PSI {ev['psi']:.2f} dan urutan risikonya juga berubah"
                     + (f" (rho {rho:.2f})." if rho is not None else "."))
    if k == "correlated":
        cc = plan.get("correlation_cluster")
        if cc and abs(ev["rho"]) >= cc.get("threshold", 0.7):
            return differ(f"Korelasi {ev['rho']:.2f} di atas ambang klaster korelasi waterfall "
                          f"({cc.get('threshold', 0.7)}); di sana yang dipertahankan dipilih dari IV di data "
                          "train. Lebih baik waterfall yang memilih, bukan dibuang manual sekarang.")
        return agree(f"Setuju; klaster korelasi tidak aktif di waterfall, jadi {ev['drop']} perlu dibuang "
                     "manual.")
    if k == "sentinel":
        return agree(f"Setuju; nilai {ev['value']:,} saya pisahkan sebagai bin sendiri (bin manual) kalau "
                     "variabel ini masuk model.")
    if k == "drift":
        return agree("Setuju dipantau; PSI-nya saya laporkan di setiap cut.")
    return agree("Catatan diterima.")


# --------------------------------------------------------------------------- the discussion
def discuss(point: dict, review: dict) -> dict:
    """Bening and Citra discuss a point Citra disagrees with. Returns
    {"point", "turns": [{by, text}], "resolution": {outcome, decision, reason,
    question_for_boss, proposal (a spec the office files, or None)}}."""
    k, ev, vs = point["kind"], point["evidence"], point["variables"]
    v = (vs or [""])[0]
    turns = [{"by": "bening", "text": f"{point['finding']} Usul saya: {point['recommendation']}"},
             {"by": "citra", "text": review["reason"]}]
    res = {"outcome": "eskalasi_boss", "decision": "", "reason": "", "question_for_boss": "",
           "proposal": None}
    if k == "high_missing":
        turns += [{"by": "bening", "text": f"Benar, bad rate saat missing ({_pct(ev.get('missing_bad_rate'))}) "
                   f"memang beda dari yang terisi ({_pct(ev.get('filled_bad_rate'))}). Tapi missing "
                   f"{_pct(ev['missing_rate'])} tetap di atas batas; nilai aslinya tidak bisa diandalkan."},
                  {"by": "citra", "text": f"Kalau begitu variabel aslinya ikut aturan missing rate waterfall, "
                   f"dan informasinya disimpan lewat flag missing {v}_mis (1 = missing). Flag itu diuji di "
                   "waterfall seperti kandidat lain."}]
        res.update(outcome="kompromi", decision=f"{v} tetap ikut aturan waterfall; Bening mengusulkan flag "
                   f"{v}_mis ke Boss.", reason="Missing-nya informatif tapi nilainya terlalu sering kosong.",
                   proposal={"kind": "derive_variable", "payload": {"name": f"{v}_mis"[:60], "op": "missing",
                                                                    "a": v}})
    elif k == "outliers":
        turns += [{"by": "bening", "text": f"Oke, setuju tidak di-cap: nilai maksimum {ev.get('max')} tetap "
                   f"masuk bin ujung. Pagar IQR {ev.get('fences')} dan jumlah outliernya tetap saya catat "
                   "di dokumentasi."},
                  {"by": "citra", "text": "Setuju, dicatat saja; kalau nanti ada kebijakan cap, kita ulang."}]
        res.update(outcome="ikut_citra", decision=f"{v} tidak di-cap; outlier didokumentasikan.",
                   reason="Bin ujung terbuka sudah membatasi pengaruh nilai ekstrem.")
    elif k == "leakage":
        turns += [{"by": "bening", "text": f"AUC {ev['auc']:.2f} untuk satu variabel tetap tidak biasa. Saya "
                   "tidak bisa memastikan dari data kapan variabel ini tercatat."},
                  {"by": "citra", "text": "Saya juga tidak bisa. Kita tanyakan ke Boss; sampai ada jawaban, "
                   "usulan buangnya menunggu ACC."}]
        res.update(outcome="eskalasi_boss", decision=f"Boss memutuskan apakah {v} dibuang.",
                   reason="Bocor atau tidak tergantung waktu pencatatan, yang tidak ada di data.",
                   question_for_boss=f"Apakah {v} sudah tersedia saat aplikasi diajukan (sebelum keputusan "
                   "kredit)? Kalau tidak, ACC usulan buang; kalau ya, tolak usulannya.",
                   proposal={"kind": "exclude_feature", "payload": {"feature": v}})
    elif k == "unstable":
        turns += [{"by": "bening", "text": f"Urutannya memang tetap (rho {ev.get('rank_rho')}), tapi dengan "
                   f"PSI {ev['psi']:.2f} cut-off bisa meleset di data baru."},
                  {"by": "citra", "text": "Setuju, itu risikonya. Variabel tetap kandidat, PSI-nya saya "
                   "laporkan di setiap cut, dan kalau masuk model, cut-off dikalibrasi di data terbaru."}]
        res.update(outcome="kompromi", decision=f"{v} tetap kandidat dengan pemantauan PSI di setiap cut.",
                   reason="Urutan risiko stabil walau distribusinya bergeser.")
    elif k == "correlated":
        turns += [{"by": "bening", "text": f"Oke, saya usulkan {ev['drop']} karena AUC-nya lebih rendah di "
                   "data pengembangan, tapi IV di train memang ukuran yang dipakai waterfall."},
                  {"by": "citra", "text": "Ya, biar klaster korelasi yang memilih; hasilnya saya laporkan di "
                   "rapat Cut 1."}]
        res.update(outcome="ikut_citra", decision=f"{vs[0]} dan {vs[1]} tetap kandidat; klaster korelasi "
                   "waterfall memilih salah satu.", reason="Pemilihan dari IV di data train, tercatat di waterfall.")
    else:                                      # a point Citra raised on her own (dispute)
        return dispute_discussion(point, review["reason"])
    return {"point": point["id"], "variables": vs, "turns": turns, "resolution": res, "ts": _now()}


def dispute_discussion(point: dict, argument: str) -> dict:
    """Citra raises a point with her own argument. Bening answers from her evidence;
    when Bening asked for a change and they still differ, the Boss decides."""
    vs = point["variables"]
    turns = [{"by": "bening", "text": f"{point['finding']} Usul saya: {point['recommendation']}"},
             {"by": "citra", "text": argument},
             {"by": "bening", "text": f"Angka saya tetap: {evidence_line(point) or point['finding']}. "
              "Saya belum bisa menarik usul saya dari data saja."},
             {"by": "citra", "text": "Kalau begitu kita serahkan ke Boss, dengan dua pendapat ini."}]
    res = {"outcome": "eskalasi_boss", "decision": f"Boss memutuskan poin {point['id']}.",
           "reason": "Bening dan Citra masih berbeda pendapat setelah diskusi.",
           "question_for_boss": f"Poin {point['id']} ({', '.join(vs) or 'umum'}): pakai usul Bening "
           f"({point['recommendation']}) atau pendapat Citra?", "proposal": None}
    if point["action"] == "drop" and vs:
        res["proposal"] = {"kind": "exclude_feature", "payload": {"feature": vs[0]}}
        res["question_for_boss"] += " ACC usulan buang = ikut Bening; tolak = ikut Citra."
    elif point["action"] == "keep_one":
        res["proposal"] = {"kind": "exclude_feature", "payload": {"feature": point["evidence"]["drop"]}}
        res["question_for_boss"] += " ACC usulan buang = ikut Bening; tolak = ikut Citra."
    return {"point": point["id"], "variables": vs, "turns": turns, "resolution": res, "ts": _now(),
            "raised_by": "citra"}


def agreed_proposal(point: dict, review: dict) -> dict | None:
    """A drop both agree on that the waterfall will not do by itself needs the Boss's ACC."""
    if review["stance"] != "setuju" or review.get("waterfall") or not point["variables"]:
        return None
    if point["action"] == "drop":
        return {"kind": "exclude_feature", "payload": {"feature": point["variables"][0]}}
    if point["action"] == "keep_one":
        return {"kind": "exclude_feature", "payload": {"feature": point["evidence"]["drop"]}}
    return None


# --------------------------------------------------------------------------- Laras checks
def check(doc: dict) -> list[dict]:
    """Laras's findings on the minutes: {"point", "problem", "blocking"}."""
    out = []
    pts = {p["id"]: p for p in doc["points"]}
    for pid, rv in doc["review"].items():
        d = doc["discussions"].get(pid)
        if rv["stance"] == "tidak setuju" and d is None:
            out.append({"point": pid, "problem": "Citra tidak setuju tapi belum didiskusikan", "blocking": True})
        if rv["stance"] == "setuju" and agreed_proposal(pts[pid], rv) and not rv.get("proposal_id"):
            out.append({"point": pid, "problem": "disepakati dibuang tapi belum ada usulan untuk ACC Boss",
                        "blocking": True})
    for pid, d in doc["discussions"].items():
        who = {t["by"] for t in d["turns"]}
        if not {"bening", "citra"} <= who:
            out.append({"point": pid, "problem": "diskusi tidak memuat pendapat Bening dan Citra",
                        "blocking": True})
        for by in ("bening", "citra"):
            first = next((t for t in d["turns"] if t["by"] == by), None)
            if first and not _NUM.search(first["text"]):
                out.append({"point": pid, "problem": f"argumen pertama {by.title()} tanpa angka/bukti",
                            "blocking": True, "who": by})
        r = d["resolution"]
        if r.get("outcome") not in OUTCOMES:
            out.append({"point": pid, "problem": "hasil diskusi belum jelas", "blocking": True})
        for key, what in (("decision", "keputusan"), ("reason", "alasan")):
            if not str(r.get(key, "")).strip():
                out.append({"point": pid, "problem": f"{what} hasil diskusi kosong", "blocking": True,
                            "field": key})
        if r.get("outcome") == "eskalasi_boss":
            if not str(r.get("question_for_boss", "")).strip():
                out.append({"point": pid, "problem": "eskalasi tanpa pertanyaan untuk Boss", "blocking": True})
            out.append({"point": pid, "problem": "menunggu keputusan Boss", "blocking": False})
        if r.get("proposal") and not r.get("proposal_id"):
            out.append({"point": pid, "problem": "hasil diskusi butuh usulan, tapi usulan belum dibuat",
                        "blocking": True})
        elif r.get("proposal_id"):
            out.append({"point": pid, "problem": f"usulan {r['proposal_id']} menunggu ACC Boss",
                        "blocking": False})
    return out


def fix(doc: dict, findings: list[dict], propose) -> list[str]:
    """Bening (the note-taker) fixes the minutes from the evidence on record; `propose`
    files a proposal spec and returns its id."""
    done = []
    pts = {p["id"]: p for p in doc["points"]}
    for f in findings:
        pid, p = f["point"], f["problem"]
        point, d = pts[pid], doc["discussions"].get(pid)
        if p.startswith("Citra tidak setuju"):
            doc["discussions"][pid] = discuss(point, doc["review"][pid])
            done.append(f"{pid}: diskusi dicatat")
        elif p.startswith("disepakati dibuang"):
            spec = agreed_proposal(point, doc["review"][pid])
            doc["review"][pid]["proposal_id"] = propose(spec, f"EDA {pid}: {point['finding']}")
            done.append(f"{pid}: usulan dibuat ({doc['review'][pid]['proposal_id']})")
        elif p.startswith("diskusi tidak memuat"):
            have = {t["by"] for t in d["turns"]}
            if "bening" not in have:
                d["turns"].insert(0, {"by": "bening", "text": f"{point['finding']} Usul saya: "
                                      f"{point['recommendation']}"})
            if "citra" not in have:
                d["turns"].append({"by": "citra", "text": doc["review"][pid]["reason"]})
            done.append(f"{pid}: pendapat yang hilang ditambahkan dari catatan")
        elif p.startswith("argumen pertama"):
            first = next(t for t in d["turns"] if t["by"] == f["who"])
            first["text"] += f" (data: {evidence_line(point) or point['finding']})"
            done.append(f"{pid}: bukti angka ditambahkan ke argumen {f['who'].title()}")
        elif p == "hasil diskusi belum jelas":
            d["resolution"]["outcome"] = "eskalasi_boss"
            done.append(f"{pid}: belum sepakat, dieskalasi ke Boss")
        elif p.endswith("hasil diskusi kosong"):
            r = d["resolution"]
            r[f["field"]] = (d["turns"][-1]["text"] if f["field"] == "reason"
                             else f"Poin {pid}: {OUTCOMES.get(r.get('outcome'), 'diputuskan Boss')}.")
            done.append(f"{pid}: {f['field']} diisi dari diskusi")
        elif p.startswith("eskalasi tanpa"):
            d["resolution"]["question_for_boss"] = (f"Poin {pid}: pakai usul Bening "
                                                    f"({point['recommendation']}) atau pendapat Citra?")
            done.append(f"{pid}: pertanyaan untuk Boss ditulis")
        elif p.startswith("hasil diskusi butuh usulan"):
            r = d["resolution"]
            r["proposal_id"] = propose(r["proposal"], f"EDA {pid}: {r['decision']}")
            done.append(f"{pid}: usulan dibuat ({r['proposal_id']})")
    return done


def run(doc: dict, propose, rounds: int = 3) -> dict:
    """Check -> fix -> check until nothing blocks (or `rounds` is reached)."""
    log = []
    for k in range(1, rounds + 1):
        found = check(doc)
        blocking = [f for f in found if f["blocking"]]
        log.append({"round": k, "found": found})
        if not blocking:
            break
        log[-1]["fixed"] = fix(doc, blocking, propose)
    found = check(doc)
    ok = not any(f["blocking"] for f in found)
    return {"status": "approved" if ok else "revisi", "version": version(doc), "rounds": log,
            "notes": [f for f in found if not f["blocking"]],
            "blocking": [f for f in found if f["blocking"]], "reviewed_by": "laras", "ts": _now()}


# --------------------------------------------------------------------------- the store
def _p(project, name: str):
    return project.dir("work") / name


def _read(project, name: str, default=None):
    p = _p(project, name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def stats(project) -> dict | None:
    return _read(project, "eda.json")


def working(project) -> dict | None:
    return _read(project, "eda_points.json")


def approved(project) -> dict | None:
    return _read(project, "eda_approved.json")


def last_review(project) -> dict | None:
    return _read(project, "eda_review.json")


def save(project, eda: dict | None, doc: dict, review: dict) -> None:
    w = lambda name, obj: _p(project, name).write_text(                       # noqa: E731
        json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if eda is not None:
        w("eda.json", eda)
    w("eda_points.json", doc)                                 # Bening's working copy
    w("eda_review_history.json", _read(project, "eda_review_history.json", []) + [review])
    w("eda_review.json", review)
    if review["status"] == "approved":                        # only now it is documentation
        w("eda_approved.json", {**doc, "review_status": review})
