"""Bening works out what every variable means BEFORE the data goes to Citra, and writes
it down: the documented dictionary (kamus variabel).

For each column of the joined dataset:
  1. the bank's own data dictionary, when it has the column;
  2. else, for a column made by the join, its source column and aggregation
     ("maksimum dari ... per nasabah, tabel bureau");
  3. else a reading of its name and values (age 21-64 -> umur nasabah, tahun);
  4. what is still unclear can go to one batched LLM guess (names and aggregate
     statistics only, never rows), marked as such.
Every meaning says where it came from and how sure Bening is.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from ..engine import data as D
from ..llm import LLMRequest, Message
from ..llm.router import AllProvidersFailed

# name pattern -> (meaning, unit); the most specific patterns first
RULES = [
    (r"is_staff|karyawan|employee", "penanda pinjaman karyawan bank (1 = ya)", None),
    (r"prev_amount", "jumlah pinjaman sebelumnya (biro kredit)", "IDR"),
    (r"prev_max_dpd", "tunggakan terburuk pada pinjaman sebelumnya (biro kredit)", "hari"),
    (r"max_dpd_12m", "tunggakan terburuk dalam 12 bulan setelah booking (data hasil)", "hari"),
    (r"(^|_)(age|umur|usia)($|_)", "umur nasabah", "tahun"),
    (r"spouse_income|penghasilan_pasangan", "penghasilan pasangan per bulan", "IDR"),
    (r"income_band|kelompok_penghasilan", "kelompok penghasilan", None),
    (r"(^|_)(income|penghasilan|gaji|salary)($|_)", "penghasilan per bulan", "IDR"),
    (r"(loan|pinjaman|plafon|plafond)_?(amount|amt|jumlah)?|(^|_)amount($|_)", "jumlah pinjaman", "IDR"),
    (r"tenor|jangka_waktu|term", "tenor pinjaman", "bulan"),
    (r"(^|_)dpd|days_past_due|tunggak", "hari tunggakan", "hari"),
    (r"years_at_job|masa_kerja|lama_kerja", "lama bekerja di tempat sekarang", "tahun"),
    (r"marital|status_nikah|perkawinan", "status pernikahan", None),
    (r"province|provinsi|region|wilayah", "provinsi tempat tinggal", None),
    (r"channel|kanal|saluran", "kanal pengajuan", None),
    (r"(^|_)(app|application)_?date|tgl_pengajuan", "tanggal pengajuan", None),
    (r"(^|_)(app|application)_?id", "nomor aplikasi", None),
    (r"(^|_)(cust|customer|cif)_?id", "nomor nasabah", None),
    (r"noise|random|dummy", "kolom tanpa arti bisnis yang jelas (kemungkinan uji/noise)", None),
    (r"(^|_)mob($|_)|months_on_book", "bulan sejak booking", "bulan"),
]
AGG = {"count": "jumlah catatan", "mean": "rata-rata", "max": "maksimum", "min": "minimum",
       "sum": "total", "nunique": "jumlah nilai berbeda"}


def _stats(s: pd.Series) -> str:
    miss = s.isna().mean()
    if D.is_numeric(s) and s.notna().any():
        return f"{s.min():,.4g} s/d {s.max():,.4g}; missing {miss:.0%}"
    top = s.value_counts().head(3).index.astype(str).tolist()
    return f"{s.nunique()} nilai, mis. {', '.join(top)}; missing {miss:.0%}"


def _rule(name: str, s: pd.Series) -> tuple[str, str | None] | None:
    n = name.lower()
    for pat, meaning, unit in RULES:
        if re.search(pat, n):
            if meaning == "umur nasabah" and D.is_numeric(s) and s.notna().any() \
                    and not (10 <= s.min() and s.max() <= 110):
                continue                                  # 'age' that is not an age in years
            return meaning, unit
    return None


def _per(key: str) -> str:
    k = key.lower()
    return "per nasabah" if re.search(r"cust|cif|nasabah", k) else \
        "per aplikasi" if re.search(r"app|aplikasi", k) else f"per {key}"


def infer(df: pd.DataFrame, ddict, office=None, use_llm: bool = True,
          plan: dict | None = None) -> list[dict]:
    """One row per column: meaning, unit, type, where the meaning came from, confidence.
    `plan` (the join plan) names the table and key behind each joined column."""
    joins = {j["prefix"]: j for j in (plan or {}).get("joins", [])}
    rows = []
    for c in df.columns:
        s = df[c]
        e = ddict.entries.get(c, {})
        src = e.get("source") or ""
        kind = D._guess_role(str(c), s, len(s)) if hasattr(D, "_guess_role") else ""
        desc, _, _, _ = ddict.describe(c)
        row = {"variabel": c, "sumber": src, "tipe": kind or ("angka" if D.is_numeric(s) else "kategori"),
               "statistik": _stats(s), "arti": "", "satuan": "", "dasar": "", "keyakinan": ""}
        base_col = src.split(".", 1)[-1] if src else c
        table = src.split(".", 1)[0] if "." in src else ""
        j = next((j for p, j in joins.items() if str(c).startswith(p + "_")), None)
        if j and not table:
            table = j["right"]
        per = _per(j["left_on"]) if j else "per kunci join"
        r = _rule(base_col, s) or _rule(str(c), s)
        own = bool(e.get("description_by"))         # written by Bening before: work it out again
        if own:
            desc = ""
        if desc and e.get("agg"):                  # the bank's words, aggregated by the join
            row.update(arti=desc, dasar="kamus data bank (turunan join)", keyakinan="tinggi")
        elif desc:
            row.update(arti=desc, dasar="kamus data bank", keyakinan="tinggi")
        elif e.get("agg"):
            inner = desc or (r[0] if r else base_col)
            row.update(arti=f"{AGG.get(e['agg'], e['agg'])} {inner[0].lower()}{inner[1:]} {per} "
                       f"(tabel {table or '?'})" if e["agg"] != "count" else
                       f"jumlah catatan di tabel {table or '?'} {per}",
                       dasar="turunan join", keyakinan="tinggi" if desc else "sedang")
        elif r:
            row.update(arti=r[0], dasar="nama dan rentang nilai", keyakinan="sedang")
        if r and r[1] and not row["satuan"]:
            row["satuan"] = r[1]
        rows.append(row)
    unclear = [r for r in rows if not r["arti"]]
    if unclear and use_llm and office is not None:
        _llm_guess(unclear, office)
    for r in rows:
        if not r["arti"]:
            r.update(arti="(belum jelas, perlu konfirmasi Boss)", dasar="-", keyakinan="rendah")
    return rows


def _llm_guess(rows: list[dict], office) -> None:
    """One call for all unclear columns: names and aggregate stats only, never rows."""
    listing = "\n".join(f"- {r['variabel']} (sumber {r['sumber'] or '-'}): {r['statistik']}"
                        for r in rows)
    prompt = ("Kamu Bening, analis data kredit. Tebak arti bisnis tiap kolom dari nama dan "
              "statistik ringkasnya. Jawab HANYA JSON: {\"kolom\": \"arti singkat bahasa "
              "Indonesia\"}. Kalau tidak bisa ditebak, isi \"\".")
    try:
        resp = office.router.complete(LLMRequest(prompt, [Message.user(listing)], [],
                                                 max_tokens=600, agent="prepper",
                                                 project_id=office.project.id))
        if resp.provider == "offline":
            return
        m = re.search(r"\{.*\}", resp.text, re.S)
        guesses = json.loads(m.group(0)) if m else {}
    except (AllProvidersFailed, ValueError):
        return
    for r in rows:
        g = str(guesses.get(r["variabel"], "")).strip()
        if g:
            r.update(arti=g, dasar="dugaan AI dari nama dan statistik", keyakinan="rendah")


def markdown(rows: list[dict], target: str = "") -> str:
    out = ["# Kamus variabel (Bening)", "",
           "Arti setiap variabel sebelum modelling. Kolom *dasar* menyebut dari mana artinya; "
           "yang berkeyakinan rendah perlu konfirmasi Boss.", ""]
    if target:
        out += [f"Target: {target}", ""]
    out += ["| variabel | arti | satuan | tipe | statistik | dasar | keyakinan |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {r['variabel']} | {r['arti']} | {r['satuan']} | {r['tipe']} | {r['statistik']} | "
            f"{r['dasar']} | {r['keyakinan']} |" for r in rows]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- tables
TABLE_RULES = [
    (r"monthly|bulanan|history|riwayat|snapshot", "riwayat performa bulanan per akun (data hasil)"),
    (r"perf|kinerja|outcome|target", "hasil kinerja pinjaman setelah booking (data hasil)"),
    (r"bureau|biro|slik|ideb|credit_history", "riwayat pinjaman di biro kredit (SLIK)"),
    (r"cust|nasabah|client|debitur", "data nasabah: demografi, penghasilan, pekerjaan"),
    (r"app|aplikasi|loan|pinjam|pengajuan|account|rekening", "data pengajuan pinjaman"),
    (r"dict|kamus|glossary|metadata", "kamus data dari bank"),
]


def tables(tables_: dict[str, pd.DataFrame], plan: dict | None, dict_files: list[str]) -> list[dict]:
    """What each table is, its grain (one row per ...), and its role in the project."""
    plan = plan or {}
    hist = (plan.get("history") or {}).get("table")
    joined = {j["right"]: j for j in plan.get("joins", [])}
    rows = []
    for name, t in tables_.items():
        arti = next((m for pat, m in TABLE_RULES if re.search(pat, name.lower())), "(belum jelas)")
        keys = [c for c in t.columns if t[c].is_unique and t[c].notna().all()]
        grain = f"satu baris per {keys[0]}" if keys else "banyak baris per kunci"
        role = ("tabel dasar" if name == plan.get("base") else
                "riwayat (untuk vintage, tidak di-join)" if name == hist else
                f"di-join lewat {joined[name]['left_on']}" if name in joined else "tidak dipakai")
        rows.append({"tabel": name, "arti": arti, "baris": len(t), "kolom": t.shape[1],
                     "butir": grain, "peran": role})
    for f in dict_files:
        rows.append({"tabel": f, "arti": "kamus data dari bank", "baris": "", "kolom": "",
                     "butir": "satu baris per variabel", "peran": "dipakai untuk arti variabel"})
    return rows


# --------------------------------------------------------------------------- translation
_EN = re.compile(r"\b(the|of|in|to|and|amount|date|number|customer|application|months?|"
                 r"days?|income|declared|requested|previous|loan|worst|after|booking)\b", re.I)


def translate(rows: list[dict], office) -> int:
    """Translate the bank dictionary's English meanings into Indonesian (one LLM call; the
    original is kept). Without an LLM nothing is invented: the row says so."""
    todo = [r for r in rows if r["dasar"].startswith("kamus data bank") and _EN.search(r["arti"])]
    if not todo:
        return 0
    listing = "\n".join(f"- {r['variabel']}: {r['arti']}" for r in todo)
    prompt = ("Terjemahkan deskripsi variabel kredit berikut ke bahasa Indonesia yang baku dan "
              "singkat; pertahankan singkatan teknis (DPD, IDR, CIF). Jawab HANYA JSON: "
              "{\"variabel\": \"terjemahan\"}.")
    guesses = {}
    try:
        resp = office.router.complete(LLMRequest(prompt, [Message.user(listing)], [],
                                                 max_tokens=900, agent="prepper",
                                                 project_id=office.project.id))
        if resp.provider != "offline":
            m = re.search(r"\{.*\}", resp.text, re.S)
            guesses = json.loads(m.group(0)) if m else {}
    except (AllProvidersFailed, ValueError):
        guesses = {}
    n = 0
    for r in todo:
        g = str(guesses.get(r["variabel"], "")).strip()
        if g:
            r["asli"], r["arti"] = r["arti"], g
            n += 1
        else:
            r["asli"] = r["arti"]
            r["arti"] = r["arti"] + " (belum diterjemahkan: perlu LLM)"
    return n


def tables_markdown(rows: list[dict]) -> str:
    out = ["## Tabel", "", "| tabel | arti | baris | kolom | butir | peran |", "|---|---|---|---|---|---|"]
    out += [f"| {r['tabel']} | {r['arti']} | {r['baris']} | {r['kolom']} | {r['butir']} | {r['peran']} |"
            for r in rows]
    return "\n".join(out) + "\n\n"
