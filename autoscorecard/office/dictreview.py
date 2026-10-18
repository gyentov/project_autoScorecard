"""Laras reviews Bening's dictionary before it counts as documentation.

Every version of the dictionary (the first one, a derived variable, a meaning changed
after a discussion) goes through: Laras checks it -> Bening fixes what Laras found ->
Laras checks again (at most three rounds). Only an approved version is copied to
work/variable_meanings_approved.json (and table_meanings_approved.json); the model
documentation, the deck and the selected-features file read only those.

What Laras checks (rule-based, $0):
  blocking   a meaning that is empty, only repeats the variable name, or is still an
             untranslated bank description; a low-confidence guess not marked as such;
             a derived variable whose meaning does not give its formula; a table whose
             meaning is unknown and not marked for the Boss
  notes      meanings still waiting for the Boss's confirmation; variables with no clear
             business meaning (noise) that are candidates or in the model
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

UNCLEAR = "(belum jelas, perlu konfirmasi Boss)"
GUESS_MARK = "(dugaan, perlu konfirmasi)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def version(rows: list[dict], tables: list[dict]) -> str:
    raw = json.dumps([[r.get("variabel"), r.get("arti"), r.get("satuan")] for r in rows]
                     + [[t.get("tabel"), t.get("arti")] for t in tables], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def check(rows: list[dict], tables: list[dict], important: set[str]) -> list[dict]:
    """Laras's findings: {"what", "item", "problem", "blocking"}."""
    out = []
    for r in rows:
        v, arti = r["variabel"], str(r.get("arti", "")).strip()
        bare = re.sub(r"[_\W]+", " ", v).strip().lower()
        if not arti:
            out.append({"what": "variabel", "item": v, "problem": "arti kosong", "blocking": True})
        elif arti.lower().strip(" .") in (v.lower(), bare):
            out.append({"what": "variabel", "item": v, "problem": "arti hanya mengulang nama variabel",
                        "blocking": True})
        elif "belum diterjemahkan" in arti:
            out.append({"what": "variabel", "item": v, "problem": "deskripsi bank belum diterjemahkan",
                        "blocking": True})
        elif r.get("keyakinan") == "rendah" and "konfirmasi" not in arti:
            out.append({"what": "variabel", "item": v, "problem": "dugaan berkeyakinan rendah belum "
                        "ditandai sebagai dugaan", "blocking": True})
        if str(r.get("dasar", "")).startswith("variabel turunan") and "=" not in arti and \
                "rumus" not in arti:
            out.append({"what": "variabel", "item": v, "problem": "variabel turunan tanpa rumus",
                        "blocking": True})
        if v in important and "konfirmasi" in arti:
            out.append({"what": "variabel", "item": v, "problem": "artinya masih menunggu konfirmasi Boss",
                        "blocking": False})
        if v in important and ("noise" in arti.lower() or "tanpa arti bisnis" in arti):
            out.append({"what": "variabel", "item": v, "problem": "tidak punya arti bisnis yang jelas; "
                        "sebaiknya tidak dipakai model", "blocking": False})
    for t in tables:
        arti = str(t.get("arti", ""))
        if arti.startswith("(belum jelas") and "konfirmasi" not in arti:
            out.append({"what": "tabel", "item": t["tabel"], "problem": "arti tabel belum diketahui",
                        "blocking": True})
    return out


def fix(rows: list[dict], tables: list[dict], findings: list[dict], office=None) -> list[str]:
    """Bening fixes what Laras found: an LLM read (when there is one) for unclear meanings,
    else an honest mark; returns what she changed."""
    from . import meaning as MEAN
    done = []
    by_var = {r["variabel"]: r for r in rows}
    todo = [f for f in findings if f["blocking"]]
    untrans = [by_var[f["item"]] for f in todo if f["problem"].startswith("deskripsi bank")]
    if untrans and office is not None:
        for r in untrans:                                  # translate() works from the original
            r["arti"] = r.get("asli") or r["arti"].replace(" (belum diterjemahkan: perlu LLM)", "")
        MEAN.translate(untrans, office)
    unclear = [by_var[f["item"]] for f in todo if f["problem"] in ("arti kosong",
                                                                  "arti hanya mengulang nama variabel")]
    if unclear and office is not None:
        for r in unclear:
            r["arti"] = ""
        MEAN._llm_guess(unclear, office)
    for f in todo:
        if f["what"] == "tabel":
            t = next(t for t in tables if t["tabel"] == f["item"])
            t["arti"] = UNCLEAR
            done.append(f"tabel {f['item']}: ditandai perlu konfirmasi Boss")
            continue
        r = by_var[f["item"]]
        p = f["problem"]
        if p in ("arti kosong", "arti hanya mengulang nama variabel"):
            if not r["arti"] or r["arti"].lower() == r["variabel"].lower():
                r.update(arti=UNCLEAR, dasar="-", keyakinan="rendah")
                done.append(f"{f['item']}: belum bisa disimpulkan, ditandai perlu konfirmasi Boss")
            else:
                r["arti"] = f"{r['arti']} {GUESS_MARK}"
                done.append(f"{f['item']}: diberi arti dugaan, ditandai")
        elif p.startswith("deskripsi bank"):
            if "belum diterjemahkan" in r["arti"]:
                r["arti"] = (f"{r.get('asli') or r['arti']} (deskripsi asli bank; terjemahan "
                             "menunggu, perlu konfirmasi)")
                r["keyakinan"] = "sedang"
                done.append(f"{f['item']}: deskripsi asli dipakai, terjemahan ditandai menunggu")
            else:
                done.append(f"{f['item']}: diterjemahkan")
        elif p.startswith("dugaan"):
            r["arti"] = f"{r['arti']} {GUESS_MARK}"
            done.append(f"{f['item']}: ditandai sebagai dugaan")
        elif p.startswith("variabel turunan"):
            r["arti"] = f"{r['arti']} (rumus: {r['dasar'].split(':', 1)[-1].strip()})"
            done.append(f"{f['item']}: rumusnya ditulis")
    return done


def run(office, rows: list[dict], tables: list[dict], important: set[str],
        rounds: int = 3) -> dict:
    """Review -> fix -> review until nothing blocks (or `rounds` is reached)."""
    log = []
    for k in range(1, rounds + 1):
        found = check(rows, tables, important)
        blocking = [f for f in found if f["blocking"]]
        log.append({"round": k, "found": found})
        if not blocking:
            break
        log[-1]["fixed"] = fix(rows, tables, blocking, office)
    found = check(rows, tables, important)
    ok = not any(f["blocking"] for f in found)
    return {"status": "approved" if ok else "revisi", "version": version(rows, tables),
            "rounds": log, "notes": [f for f in found if not f["blocking"]],
            "blocking": [f for f in found if f["blocking"]], "reviewed_by": "laras", "ts": _now()}


# --------------------------------------------------------------------------- the store
def _p(project, name: str):
    return project.dir("work") / name


def approved_rows(project) -> list[dict]:
    p = _p(project, "variable_meanings_approved.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def approved_tables(project) -> list[dict]:
    p = _p(project, "table_meanings_approved.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def last_review(project) -> dict | None:
    p = _p(project, "dictionary_review.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(project, rows: list[dict], tables: list[dict], review: dict) -> None:
    w = lambda name, obj: _p(project, name).write_text(                       # noqa: E731
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    w("variable_meanings.json", rows)                       # Bening's working copy
    w("table_meanings.json", tables)
    hist = []
    p = _p(project, "dictionary_review_history.json")
    if p.exists():
        hist = json.loads(p.read_text(encoding="utf-8"))
    w("dictionary_review_history.json", hist + [review])
    w("dictionary_review.json", review)
    if review["status"] == "approved":                     # only now it is documentation
        for r in rows:
            r["review"] = "perlu konfirmasi Boss" if "konfirmasi" in str(r.get("arti")) else "lolos review Laras"
        w("variable_meanings_approved.json", rows)
        w("table_meanings_approved.json", tables)
