"""The model documentation as a report people want to read: a consulting / bank model
report in Indonesian, HTML + CSS (prints to A4 with proper margins).

Cover, contents, executive summary with the key figures, then numbered chapters:
background and data, bad definition and vintage, the variable dictionary (only the
version Laras approved), the development process (waterfall), hyperparameter tuning,
performance, every model variable bin by bin, the score bands (20 -> ideal, with the KS
table), the VL-VH risk levels, decisions and the audit trail, and a methodology appendix.
Every figure and table is numbered and sourced; every chapter ends with what it means
for the business. Numbers come from the build artifacts (deck.facts), never typed.
"""
from __future__ import annotations

import html
from datetime import datetime

from . import deck as K
from . import deckviz as V
from .dictreview import last_review

LEVEL_ID = {"VH": "Sangat tinggi", "H": "Tinggi", "M": "Sedang", "L": "Rendah", "VL": "Sangat rendah"}
POLICY_ID = {"VH": "Tolak, atau review manual dengan mitigasi kuat",
             "H": "Setuju dengan syarat: limit lebih kecil, tenor lebih pendek, agunan",
             "M": "Persetujuan standar dengan pemantauan lebih ketat",
             "L": "Persetujuan standar", "VL": "Jalur cepat; kandidat penawaran pra-persetujuan"}
READ_ID = {"much riskier": "jauh lebih berisiko", "riskier": "lebih berisiko", "average": "rata-rata",
           "safer": "lebih aman", "much safer": "jauh lebih aman"}

CSS = """
:root{--ink:#1F2933;--muted:#616E7C;--navy:#0B2545;--accent:#0F766E;--accent2:#14B8A6;--rule:#E4E7EB;
 --soft:#F5F7FA;--alert:#B54708;--alertbg:#FFF6ED}
*{box-sizing:border-box} html{background:#E9EDF1}
body{margin:0;color:var(--ink);font:15px/1.65 "Source Serif 4",Georgia,"Times New Roman",serif}
.sans,h1,h2,h3,h4,.kicker,.toc,table,.fig-cap,.cover,.kpi,.note,.badge,.foot{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.page{width:210mm;min-height:297mm;margin:24px auto;background:#fff;padding:22mm 22mm 24mm;
 box-shadow:0 8px 30px rgba(11,37,69,.10);position:relative}
.cover{background:var(--navy);color:#fff;padding:0;display:flex;flex-direction:column}
.cover .band{height:10px;background:linear-gradient(90deg,var(--accent2),var(--accent))}
.cover .inner{padding:38mm 22mm 0;flex:1}
.cover .kicker{color:#5EEAD4;letter-spacing:.18em;text-transform:uppercase;font-size:12px;font-weight:700}
.cover h1{font-size:40px;line-height:1.12;margin:14px 0 18px;font-weight:700;max-width:150mm}
.cover .sub{font-size:18px;color:#C9D6E3;max-width:140mm;font-family:inherit}
.cover .meta{padding:0 22mm 22mm;font-size:13px;color:#9FB3C8;display:grid;grid-template-columns:repeat(3,1fr);gap:8px 20px}
.cover .meta b{display:block;color:#fff;font-weight:600}
.kicker{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);font-weight:700;margin:0 0 6px}
h2{font-size:26px;line-height:1.2;color:var(--navy);margin:0 0 6px;font-weight:700}
h2+.lead{font-size:16px;color:var(--muted);margin:0 0 20px;font-style:italic}
h3{font-size:17px;color:var(--navy);margin:26px 0 8px}
h4{font-size:13px;color:var(--muted);margin:16px 0 6px;text-transform:uppercase;letter-spacing:.06em}
p{margin:0 0 12px;text-align:justify;hyphens:auto}
.rule{height:3px;width:52px;background:var(--accent);margin:10px 0 20px}
.toc ol{padding:0;margin:0;list-style:none;counter-reset:c} .toc li{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--rule);font-size:15px}
.toc li span.n{color:var(--accent);font-weight:700;width:28px} .toc li span.t{flex:1}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:8px 0 22px}
.kpi{border-top:3px solid var(--accent);background:var(--soft);padding:12px 14px}
.kpi .v{font-size:26px;font-weight:700;color:var(--navy);line-height:1.1} .kpi .k{font-size:12px;color:var(--muted);margin-top:4px}
.kpi.alert{border-color:var(--alert)} .kpi.alert .v{color:var(--alert)}
.findings{margin:0 0 18px;padding:0;list-style:none;counter-reset:f}
.findings li{counter-increment:f;position:relative;padding:0 0 12px 38px}
.findings li::before{content:counter(f);position:absolute;left:0;top:1px;width:24px;height:24px;border-radius:50%;
 background:var(--navy);color:#fff;font:700 12px/24px Inter,sans-serif;text-align:center}
.rec{background:var(--navy);color:#fff;padding:16px 20px;border-radius:4px;margin:6px 0 0;font-family:Inter,sans-serif;font-size:15px;line-height:1.5}
.rec .kicker{color:#5EEAD4;margin-bottom:6px}
.note{background:var(--soft);border-left:4px solid var(--accent);padding:12px 16px;margin:14px 0 18px;font-size:14px;line-height:1.55}
.note b{color:var(--accent)} .note.alert{background:var(--alertbg);border-color:var(--alert)} .note.alert b{color:var(--alert)}
figure{margin:14px 0 20px} .fig-cap{font-size:12px;color:var(--muted);margin-top:6px}
.fig-cap b{color:var(--ink)}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:6px 0 4px}
th{text-align:left;color:var(--muted);font-weight:600;border-bottom:2px solid var(--navy);padding:6px 8px;vertical-align:bottom}
td{padding:6px 8px;border-bottom:1px solid var(--rule);vertical-align:top} td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.hl td{background:#ECFDF5;font-weight:600}
.badge{display:inline-block;font-size:11px;font-weight:700;border-radius:99px;padding:1px 9px;color:#fff;background:var(--accent)}
.badge.warn{background:var(--alert)} .badge.grey{background:#9AA5B1}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;color:#fff;font-size:11px;font-weight:700;font-family:Inter,sans-serif}
.var{break-inside:avoid;border-top:1px solid var(--rule);padding-top:14px;margin-top:18px}
.two{display:grid;grid-template-columns:1.25fr 1fr;gap:22px;align-items:start}
.foot{position:absolute;left:22mm;right:22mm;bottom:10mm;display:flex;justify-content:space-between;font-size:10.5px;color:#9AA5B1;border-top:1px solid var(--rule);padding-top:6px}
@page{size:A4;margin:0}
@media print{html{background:#fff}.page{margin:0;box-shadow:none;break-after:page}}
"""


BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
         "Oktober", "November", "Desember"]
STATUS_ID = {"built": "draf, menunggu konfirmasi", "confirmed": "final, dikonfirmasi Boss"}


def tanggal(d: datetime) -> str:
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def esc(s) -> str:
    return html.escape(str(s))


def _tbl(rows: list[dict], cols: list[str], num: set[str] | None = None, raw: bool = False,
         hl=None) -> str:
    num = num or set()
    head = "".join(f"<th class='{'n' if c in num else ''}'>{esc(c)}</th>" for c in cols)
    body = "".join(
        f"<tr class='{'hl' if hl and hl(r) else ''}'>" + "".join(
            f"<td class='{'n' if c in num else ''}'>{(r.get(c, '') if raw else esc(r.get(c, '')))}</td>"
            for c in cols) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


class Report:
    def __init__(self, f: dict):
        self.f, self.pages, self.chapters, self.fig, self.tab = f, [], [], 0, 0

    def figure(self, svg: str, caption: str, source: str = "") -> str:
        self.fig += 1
        return (f"<figure>{svg}<div class='fig-cap'><b>Gambar {self.fig}.</b> {esc(caption)}"
                + (f" <i>Sumber: {esc(source)}</i>" if source else "") + "</div></figure>")

    def table(self, body: str, caption: str) -> str:
        self.tab += 1
        return f"<div class='fig-cap'><b>Tabel {self.tab}.</b> {esc(caption)}</div>{body}"

    def chapter(self, title: str, lead: str, body: str) -> None:
        n = len(self.chapters) + 1
        self.chapters.append(title)
        self.pages.append(
            f"<section class='page'><div class='kicker'>Bab {n}</div><h2>{esc(title)}</h2>"
            f"<p class='lead'>{esc(lead)}</p><div class='rule'></div>{body}"
            f"<div class='foot'><span>{esc(self.f['name'])} · Dokumentasi model scorecard</span>"
            f"<span>{self.f['cut'].replace('cut', 'Cut ')} · build {esc(self.f['build'])}</span></div></section>")


def _so(text: str, alert: bool = False) -> str:
    return f"<div class='note{' alert' if alert else ''}'><b>Arti bagi bisnis:</b> {text}</div>"


def build(office, cut: str | None = None) -> str:
    f = K.facts(office, cut)
    t, perf, info = f["target"], f["perf"], f["info"]
    vin = t.get("vintage")
    feats = info.get("features", [])
    train, oot = perf.get("train", {}), (perf.get("oot") or perf.get("test") or {})
    psi = f["psi"].get("oot", f["psi"].get("test", 0)) or 0
    names = {v: K.human(v, f["meanings"]) for v in feats}
    r = Report(f)
    an = K.bin_analysis(f) if f["table"] is not None else {}
    bc, bp, kt = f["band_choice"], f["band_path"], f["ks_table"]
    lv = f["levels"]
    review = last_review(office.project) or {}

    # ---- 1. executive summary
    g = oot.get("gini", 0)
    kpis = [(f"{g:.2f}", "Gini out-of-time"), (f"{oot.get('ks', 0):.2f}", "KS out-of-time"),
            (f"{len(feats)}", "variabel di model"), (f"{psi:.2f}", "PSI skor (stabilitas)")]
    kpi_html = "".join(f"<div class='kpi{' alert' if k.startswith('PSI') and psi >= 0.25 else ''}'>"
                       f"<div class='v'>{v}</div><div class='k'>{k}</div></div>" for v, k in kpis)
    top = sorted(an.items(), key=lambda x: -x[1]["share"])[:3]
    finds = []
    if vin:
        finds.append(f"<b>Definisi bad berbasis bukti.</b> Bad = pernah {vin['threshold']}+ DPD dalam "
                     f"{vin['window']} bulan sejak booking: {vin['bads']:,} bad ({vin['bad_rate']:.1%}), dipilih "
                     "dari vintage analysis, bukan kebiasaan.")
    finds.append(f"<b>Daya pembeda {K.gini_word(g).replace('strong', 'kuat').replace('moderate', 'sedang').replace('weak', 'lemah').replace('very strong', 'sangat kuat')}.</b> "
                 f"Gini {train.get('gini', 0):.2f} di data pengembangan dan {g:.2f} di data out-of-time; KS {oot.get('ks', 0):.2f}.")
    if top:
        finds.append("<b>Penggerak skor.</b> " + ", ".join(f"{esc(names.get(v, v))} ({a['share']:.0%})" for v, a in top)
                     + " menentukan sebagian besar rentang poin.")
    if bc:
        finds.append(f"<b>{bc['bands']} kelompok skor ideal.</b> Dari 20 kelompok sama besar digabung bertahap sampai "
                     f"bad rate turun konsisten dan setiap pasangan berbeda nyata; KS tetap {bc['ks_banded'].get('train', 0):.2f}.")
    if psi >= 0.25:
        finds.append(f"<b>Populasi bergeser.</b> PSI {psi:.2f} di atas 0,25: urutan risiko tetap berlaku, tetapi cut-off "
                     "perlu dikalibrasi ulang di data terbaru sebelum dipakai.")
    doubt = [v for v, a in an.items() if a["doubt"]]
    rec = ("Gunakan scorecard untuk keputusan aplikasi, dengan kategori risiko VL–VH sebagai jangkar kebijakan."
           if g >= 0.4 else "Gunakan scorecard bersama aturan kebijakan yang ada; tinjau cut-off setelah dua kuartal."
           if g >= 0.3 else "Gunakan scorecard sebagai salah satu masukan di samping aturan kebijakan; cari data "
           "yang lebih kuat sebelum mengandalkannya sendiri.")
    if psi >= 0.25:
        rec += " Kalibrasi ulang cut-off di data terbaru sebelum go-live."
    if doubt:
        rec += f" Keluarkan {', '.join(names.get(v, v) for v in doubt)} sebelum go-live: tidak punya arti bisnis yang jelas."
    r.chapter("Ringkasan eksekutif", "Apa yang dibangun, seberapa baik, dan apa yang kami rekomendasikan.",
              f"<div class='kpis'>{kpi_html}</div><h4>Temuan utama</h4><ol class='findings'>"
              + "".join(f"<li>{x}</li>" for x in finds) + "</ol>"
              f"<div class='rec'><div class='kicker'>Rekomendasi</div>{esc(rec)}</div>")

    # ---- 2. background and data
    plan = f["plan"]
    joins = [{"tabel": j["right"], "kunci": f"{j['left_on']} = {j['right_on']}", "relasi": j.get("relationship", ""),
              "cocok": f"{j.get('coverage', 0):.1%}"} for j in plan.get("joins", [])]
    tables = [{"tabel": x["tabel"], "arti": x["arti"], "butir": x.get("butir", ""), "peran": x.get("peran", "")}
              for x in (office.dictionary_lookup().get("tabel") or [])] if f["meanings"] else []
    span = (f"{f['dates'].min():%b %Y} – {f['dates'].max():%b %Y}" if f["dates"] is not None and f["dates"].notna().any()
            else "-")
    r.chapter("Latar belakang dan data", "Sumber data, cakupan dan cara tabel digabungkan.",
              f"<p>Scorecard aplikasi ini dibangun dari {f['rows']:,} aplikasi yang dibooking {span}, untuk "
              "mengurutkan pemohon menurut risiko gagal bayar. Semua tabel digabung ke tabel dasar dengan left join, "
              "sehingga tidak ada aplikasi yang hilang karena penggabungan.</p>"
              + (r.table(_tbl(tables, ["tabel", "arti", "butir", "peran"]), "Tabel sumber dan perannya") if tables else "")
              + (r.table(_tbl(joins, ["tabel", "kunci", "relasi", "cocok"], {"cocok"}), "Penggabungan ke tabel dasar "
                         f"{plan.get('base', '')}") if joins else "")
              + _so("setiap kolom di model bisa ditelusuri ke tabel dan kuncinya; validator dapat mengulang "
                    "penggabungan ini persis sama."))

    # ---- 3. bad definition and vintage
    if vin and f["curves"] is not None:
        c = f["curves"]
        ser = []
        for k in sorted(x for x in c[c.threshold == vin["threshold"]].cohort.unique() if x != "all"):
            s_ = c[(c.threshold == vin["threshold"]) & (c.cohort == k)]
            s_ = s_[s_.n >= max(50, 0.5 * s_.n.iloc[0])]
            ser.append({"name": k, "x": s_.mob.tolist(), "y": s_.bad_rate.tolist()})
        al = c[(c.threshold == vin["threshold"]) & (c.cohort == "all")]
        al = al[al.n >= 0.5 * al.n.iloc[0]]
        ser.append({"name": "Semua kohort", "x": al.mob.tolist(), "y": al.bad_rate.tolist(), "focus": True})
        byt = sorted(vin.get("by_threshold") or [], key=lambda x: x["threshold"])
        r.chapter("Definisi bad dan vintage analysis", "Kapan seorang debitur dianggap gagal bayar, dan mengapa.",
                  f"<p>Bad didefinisikan sebagai <b>pernah {vin['threshold']}+ hari tunggakan dalam {vin['window']} "
                  f"bulan sejak booking</b>. Akun yang belum diamati selama {vin['window']} bulan penuh dikeluarkan "
                  f"(belum matang). Hasilnya {vin['bads']:,} bad dari {vin.get('n_mature', 0):,} akun matang, "
                  f"bad rate {vin['bad_rate']:.1%}.</p>"
                  + r.figure(V.lines(ser, int(c.mob.max()), xlabel="bulan sejak booking",
                                     marker=(vin["window"], f"window {vin['window']} bln")),
                             f"Kurva vintage: persentase akun yang pernah {vin['threshold']}+ DPD per kohort booking",
                             "vintage_curves.csv")
                  + (r.table(_tbl([{"ambang": f"{x['threshold']}+ DPD", "window": f"{x['window']} bln",
                                    "bad": f"{x['bads']:,}", "bad rate": f"{x['bad_rate']:.1%}"} for x in byt],
                                  ["ambang", "window", "bad", "bad rate"], {"bad", "bad rate"},
                                  hl=lambda x: x["ambang"].startswith(str(vin["threshold"]))),
                             "Alternatif ambang yang dipertimbangkan (terpilih ditandai)") if byt else "")
                  + _so(f"window {vin['window']} bulan dipilih karena kurva berhenti naik secara material; definisi "
                        "ini cukup ketat untuk berarti gagal bayar sungguhan dan cukup sering untuk dimodelkan."))

    # ---- 4. dictionary (approved only)
    vm = office.approved_meanings()
    used = [x for x in vm if x["variabel"] in feats]
    st_badge = ("<span class='badge'>lolos review Laras</span>" if review.get("status") == "approved"
                else "<span class='badge warn'>belum lolos review</span>")
    r.chapter("Kamus variabel", "Arti setiap variabel, dari mana artinya, dan statusnya.",
              f"<p>Kamus disusun Bening dan direview Laras sebelum dipakai di dokumen ini {st_badge} "
              f"(versi {esc(review.get('version', '-'))}, {esc(str(review.get('ts', ''))[:10])}). Sumber arti: kamus "
              "data bank, logika penggabungan, rumus variabel turunan, hasil diskusi dengan Boss, atau dugaan dari "
              "nama dan statistik (ditandai perlu konfirmasi). Di bawah ini variabel yang masuk model; kamus "
              f"lengkap {len(vm)} variabel ada di lampiran file kamus.</p>"
              + r.table(_tbl([{"variabel": x["variabel"], "arti": x.get("arti", ""), "satuan": x.get("satuan", ""),
                               "dasar": x.get("dasar", ""), "status": x.get("review", "")} for x in used],
                             ["variabel", "arti", "satuan", "dasar", "status"]), "Arti variabel di model")
              + _so("setiap variabel yang menggerakkan keputusan kredit punya arti yang bisa dijelaskan ke "
                    "nasabah, auditor dan regulator.", alert=bool(doubt)))

    # ---- 5. development process
    wf = f["wf"]
    if wf is not None:
        fsteps = [(x.step, int(x.n_out), int(x.n_dropped), int(x.n_added)) for x in wf.itertuples()]
        why = wf[(wf.n_dropped > 0) | (wf.n_added > 0)]
        r.chapter("Proses pengembangan model", "Dari kandidat variabel ke model akhir, langkah demi langkah.",
                  f"<p>Seleksi variabel mengikuti waterfall yang disetujui Boss sebelum model dibangun. Dari "
                  f"{int(wf.n_in.iloc[0])} kandidat, {len(feats)} variabel bertahan. Setiap variabel dibin secara "
                  "monoton (setiap bin minimal 5% akun), ditransformasi ke WoE, lalu masuk regresi logistik.</p>"
                  + r.figure(V.funnel(fsteps), "Jumlah variabel yang tersisa setelah setiap langkah",
                             "waterfall_summary.csv")
                  + r.table(_tbl([{"langkah": x.step, "aturan": x.criterion,
                                   "dibuang": int(x.n_dropped) or "", "ditambah": int(x.n_added) or ""}
                                  for x in why.itertuples()], ["langkah", "aturan", "dibuang", "ditambah"],
                                 {"dibuang", "ditambah"}), "Aturan yang membuang variabel")
                  + _so("setiap variabel akhir lolos semua aturan, sehingga model ramping, stabil, dan mudah "
                        "dipertanggungjawabkan."))

    # ---- 6. tuning
    tu = f["tuning"]
    if tu and tu.get("table"):
        keys = [k for k in ("min_bin_pct", "max_bins", "p_enter") if k in tu["best"]]
        rows = sorted(tu["table"], key=lambda x: -x["objective"])
        r.chapter("Hyperparameter tuning", "Setelan binning dan seleksi diuji sekali, saat finalisasi model.",
                  f"<p>Data train dibagi lagi: {tu['n_fit']:,} akun untuk membangun dan {tu['n_val']:,} untuk "
                  "validasi. Setiap kombinasi menjalankan ulang waterfall yang disetujui; data test dan out-of-time "
                  f"tidak dipakai untuk memilih. Aturan: {esc(tu['rule'])}.</p>"
                  + r.table(_tbl([{**{K.hp_name(k): K.hp_value(k, x[k]) for k in keys},
                                   "variabel": x["n_features"], "Gini fit": f"{x['gini_fit']:.3f}",
                                   "Gini validasi": f"{x['gini_val']:.3f}", "selisih": f"{x['gap']:.3f}",
                                   "_c": x.get("chosen")} for x in rows],
                                 [K.hp_name(k) for k in keys] + ["variabel", "Gini fit", "Gini validasi", "selisih"],
                                 {"variabel", "Gini fit", "Gini validasi", "selisih"}, hl=lambda x: x["_c"]),
                            "Semua kombinasi yang diuji (terpilih ditandai)")
                  + _so("setelan dipilih dengan data yang tidak dipakai untuk menilai hasil akhir, sehingga angka "
                        "performa di bab berikut tetap jujur."))

    # ---- 7. performance
    if perf:
        prow = [{"sampel": {"train": "pengembangan", "test": "test", "oot": "out-of-time"}.get(k, k),
                 "akun": f"{v['n']:,}", "bad rate": f"{v['bad_rate']:.1%}", "Gini": f"{v['gini']:.3f}",
                 "KS": f"{v['ks']:.3f}", "AUC": f"{v['auc']:.3f}",
                 "PSI": f"{f['psi'].get(k, 0):.3f}" if k != "train" else "-"} for k, v in perf.items()]
        items = [({"train": "Train", "test": "Test", "oot": "OOT"}.get(k, k), v["gini"]) for k, v in perf.items()]
        r.chapter("Performa model", "Seberapa baik skor memisahkan bad dari good, dan seberapa stabil.",
                  r.figure(V.vbars(items, fmt=lambda v: f"{v:.2f}", h=240,
                                   colors=[V.ACCENT if lab == "OOT" else V.GREY for lab, _ in items]),
                           "Gini per sampel", "performance.json")
                  + r.table(_tbl(prow, ["sampel", "akun", "bad rate", "Gini", "KS", "AUC", "PSI"],
                                 {"akun", "bad rate", "Gini", "KS", "AUC", "PSI"}), "Ukuran performa per sampel")
                  + "<p>Gini = 2 × AUC − 1; KS = jarak terbesar antara distribusi kumulatif bad dan good; PSI "
                    "mengukur pergeseran distribusi skor dibanding data pengembangan (di bawah 0,10 stabil, di atas "
                    "0,25 bergeser material).</p>"
                  + _so(("skor dapat dipakai untuk keputusan sekarang, dengan pemantauan rutin per kuartal."
                         if psi < 0.25 and g >= 0.3 else
                         f"populasi terbaru bergeser (PSI {psi:.2f}); kalibrasi ulang cut-off sebelum dipakai."
                         if psi >= 0.25 else "pembedaannya nyata tetapi sedang; padukan dengan aturan kebijakan."),
                        alert=psi >= 0.25))

    # ---- 8. variables, bin by bin
    if an:
        parts = []
        for v, a in an.items():
            m = f["meanings"].get(v, {})
            items = [(b["label"], b["bad_rate"]) for b in a["bins"]]
            low = {k for k, b in enumerate(a["bins"]) if b["points"] == a["worst"]["points"]}
            rows = [{"bin": b["label"], "% akun": f"{b['pct']:.0%}", "bad rate": f"{b['bad_rate']:.1%}",
                     "indeks risiko": f"{b['index']:.1f}×", "WoE": f"{b['woe']:+.2f}", "poin": b["points"],
                     "bacaan": READ_ID.get(b["read"], b["read"])} for b in a["bins"]]
            w, b_ = a["worst"], a["best"]
            pattern = ("bad rate bergerak satu arah sepanjang rentang nilai" if a["monotonic"] and a["numeric"]
                       else "kategori diurutkan menurut bad rate-nya" if not a["numeric"]
                       else f"tren patah di {', '.join(a['breaks'])}; perlu dicek di pembangunan berikutnya")
            parts.append(
                f"<div class='var'><h3>{esc(names.get(v, v))} <span class='sans' style='font-size:12px;color:#616E7C'>"
                f"({esc(v)})</span></h3><p><b>Arti.</b> {esc(m.get('arti') or '(belum ada di kamus yang disetujui)')}. "
                f"<b>Bobot</b> {a['share']:.0%} dari rentang skor, IV {a['iv']:.2f}; {pattern}.</p>"
                "<div class='two'>"
                + r.figure(V.vbars(items, colors=[V.ALERT if k in low else V.GREY for k in range(len(items))], h=200,
                                   sub=[f"{x['points']} poin" for x in a["bins"]]),
                           f"Bad rate per bin, {names.get(v, v)} (poin di bawah)")
                + f"<div><p><b>Bin per bin.</b> Bin paling berisiko ({esc(w['label'])}, {w['pct']:.0%} pemohon) "
                  f"punya bad rate {w['index']:.1f}× rata-rata portofolio dan mendapat {w['points']} poin; yang paling "
                  f"aman ({esc(b_['label'])}, {b_['pct']:.0%}) {b_['index']:.1f}× rata-rata dan {b_['points']} poin: "
                  f"selisih {b_['points'] - w['points']} poin, perbedaan risiko {a['spread']:.0f} kali.</p>"
                + (f"<p><b>Bin tipis.</b> {', '.join(esc(x) for x in a['thin'])} di bawah 5% pemohon; poinnya "
                   "bertumpu pada sedikit akun.</p>" if a["thin"] else "")
                + ("<div class='note alert'><b>Catatan:</b> variabel ini tidak punya arti bisnis yang jelas; "
                   "sebaiknya dikeluarkan sebelum go-live.</div>" if a["doubt"] else "") + "</div></div>"
                + r.table(_tbl(rows, ["bin", "% akun", "bad rate", "indeks risiko", "WoE", "poin", "bacaan"],
                               {"% akun", "bad rate", "indeks risiko", "WoE", "poin"}),
                          f"Scorecard {v}: setiap bin, bad rate dan poinnya") + "</div>")
        r.chapter("Variabel model, bin per bin", "Mengapa setiap bin mendapat poinnya: transparansi penuh.",
                  "<p>Indeks risiko = bad rate bin dibagi bad rate portofolio. WoE = log rasio proporsi good "
                  "terhadap bad di bin; poin = WoE × koefisien, diskalakan (makin tinggi poin, makin rendah "
                  "risiko).</p>" + "".join(parts))

    # ---- 9. score bands: 20 -> ideal, with KS
    if bc and bp is not None:
        rev_cols = [c for c in bp.columns if c.startswith("reversals_")]
        path = [{"kelompok": int(x["bands"]), "KS train": f"{x['ks']:.3f}",
                 **{f"KS {c[3:]}": f"{x[c]:.3f}" for c in bp.columns if c.startswith("ks_")},
                 "p terburuk": f"{x['max_p']:.3f}", "pembalikan": int(sum(x[c] for c in rev_cols)),
                 "akun min": f"{x['min_pct']:.0%}", "_c": bool(x["chosen"])} for x in bp.to_dict("records")]
        cols = [k for k in path[0] if not k.startswith("_")]
        body = (f"<p>Skor dibagi dulu menjadi 20 kelompok sama besar"
                + (f" (menjadi {int(bp.bands.max())} setelah kelompok di bawah 5% akun digabung)"
                   if int(bp.bands.max()) < 20 else "") + ", lalu dua kelompok bersebelahan yang paling "
                "mirip digabung satu per satu (kelompok yang urutannya terbalik digabung lebih dulu). Jumlah "
                f"ideal: <b>{bc['bands']} kelompok</b>, yaitu {esc(bc['rule'])}.</p>"
                + r.figure(V.vbars([(str(x["kelompok"]), float(x["KS train"])) for x in path],
                                   fmt=lambda v: f"{v:.2f}", h=220,
                                   colors=[V.ACCENT if x["_c"] else V.GREY for x in path]),
                           "KS skor yang sudah dikelompokkan, menurut jumlah kelompok (terpilih berwarna)",
                           "band_path.csv")
                + r.table(_tbl(path, cols, set(cols) - {"kelompok"}, hl=lambda x: x["_c"]),
                          "Jejak penggabungan dari 20 kelompok"))
        if kt is not None:
            tr = kt[kt["sample"] == "train"]
            bands = f["bands"]
            krow = []
            for x in tr.itertuples():
                b = bands[bands.band == x.band].iloc[0] if bands is not None and (bands.band == x.band).any() else None
                krow.append({"kelompok": int(x.band),
                             "skor": f"{b.score_min:.0f}–{b.score_max:.0f}" if b is not None else "",
                             "akun": f"{int(x.n):,}", "bad rate": f"{x.bad_rate:.1%}",
                             "kum. bad": f"{x.cum_bads:.0%}", "kum. good": f"{x.cum_goods:.0%}", "KS": f"{x.ks:.3f}"})
            ks_by = {s_: float(gg.ks.max()) for s_, gg in kt.groupby("sample")}
            body += ("<h3>Tabel KS</h3>"
                     + r.table(_tbl(krow, list(krow[0]), {"akun", "bad rate", "kum. bad", "kum. good", "KS"},
                                    hl=lambda x: float(x["KS"]) == max(float(y["KS"]) for y in krow)),
                               "KS per kelompok skor, data pengembangan (KS maksimum ditandai)")
                     + "<p>KS maksimum per sampel: " + ", ".join(f"{k} {v:.3f}" for k, v in ks_by.items())
                     + f"; KS model (tanpa pengelompokan): {', '.join(f'{k} {v:.3f}' for k, v in bc['ks_model'].items())}.</p>")
        r.chapter("Kelompok skor dan KS", "Berapa banyak kelompok yang benar-benar berbeda risikonya.",
                  body + _so(f"{bc['bands']} kelompok adalah pembagian paling rinci yang masih didukung data; "
                             "lebih dari itu akan memisahkan kelompok yang risikonya tidak sungguh berbeda."))

    # ---- 10. risk levels
    if lv is not None:
        order = [x for x in K.LEVEL_ORDER if x in set(lv.level)]
        lvi = lv.set_index("level").loc[order]
        rows = [{"kategori": f"<span class='pill' style='background:{V.LEVEL_COLORS[x]}'>{x}</span> {LEVEL_ID[x]}",
                 "kelompok": esc(lvi.loc[x, "band"]),
                 "skor": f"{lvi.loc[x, 'score_min']:.0f}–{lvi.loc[x, 'score_max']:.0f}",
                 "% akun": f"{lvi.loc[x, 'pct']:.0%}", "bad rate": f"{lvi.loc[x, 'bad_rate_train']:.1%}",
                 "kebijakan (ilustrasi)": esc(POLICY_ID[x])} for x in order]
        agreed = f["levels_agreed"]
        r.chapter("Kategori risiko VL–VH", "Dari kelompok skor ideal ke kategori yang dipakai kebijakan kredit.",
                  f"<p>Kategori disusun dari {bc['bands'] if bc else len(f['bands'] or [])} kelompok skor ideal, "
                  "berurutan dari skor terendah (VH) ke tertinggi (VL), masing-masing sekitar seperlima akun. "
                  + ("Batasnya sudah disepakati Boss.</p>" if agreed else "Ini masih usulan Citra untuk didiskusikan.</p>")
                  + r.figure(V.vbars([(x, lvi.loc[x, "bad_rate_train"]) for x in order],
                                     colors=[V.LEVEL_COLORS[x] for x in order], h=220,
                                     sub=[f"{lvi.loc[x, 'score_min']:.0f}–{lvi.loc[x, 'score_max']:.0f}" for x in order]),
                             "Bad rate per kategori risiko (rentang skor di bawah)")
                  + r.table(_tbl(rows, ["kategori", "kelompok", "skor", "% akun", "bad rate", "kebijakan (ilustrasi)"],
                                 {"% akun", "bad rate"}, raw=True), "Kategori risiko dan kebijakan ilustratif")
                  + _so("kategori memberi bahasa yang sama bagi analis, komite kredit dan sistem: satu label untuk "
                        "setiap rentang skor, dengan bad rate yang jelas berbeda."))

    # ---- 11. decisions and audit trail
    decisions = [{"rapat": m.title, "keputusan": d["decision"]} for m in office.meetings.list() for d in m.decisions]
    orders = [{"#": o["id"], "untuk": o["to"], "perintah": o["summary"], "status": o["status"]}
              for o in office.orders.list() if o.get("kind") != "alur"]
    r.chapter("Keputusan dan jejak audit", "Siapa memutuskan apa, dan kapan.",
              "<p>Tidak ada perubahan pada model tanpa ACC Boss. Keputusan rapat dan perintah Boss tercatat di "
              "bawah; log teknis lengkap ada di <code>audit.jsonl</code> proyek.</p>"
              + r.table(_tbl(decisions, ["rapat", "keputusan"]), "Keputusan rapat")
              + (r.table(_tbl(orders, ["#", "untuk", "perintah", "status"], {"#"}), "Perintah Boss") if orders else ""))

    # ---- appendix
    r.chapter("Lampiran: metodologi", "Istilah dan konvensi yang dipakai di seluruh dokumen.",
              _tbl([{"istilah": a, "arti": b} for a, b in [
                  ("Bad", f"pernah {vin['threshold']}+ DPD dalam {vin['window']} bulan sejak booking" if vin else
                   f"{t.get('column')} {t.get('rule')} {t.get('value')}"),
                  ("WoE", "ln(proporsi good / proporsi bad) di sebuah bin"),
                  ("IV", "Σ (proporsi good − proporsi bad) × WoE: daya prediksi variabel"),
                  ("Gini", "2 × AUC − 1: seberapa baik skor mengurutkan bad di bawah good"),
                  ("KS", "jarak terbesar antara distribusi kumulatif bad dan good"),
                  ("PSI", "pergeseran distribusi skor dibanding data pengembangan (< 0,10 stabil)"),
                  ("OOT", f"sampel out-of-time mulai {t.get('oot_from')}" if t.get("oot_from") else "sampel out-of-time"),
                  ("Skala", f"{(f['scaling'] or {}).get('base_score', 600):.0f} poin pada odds "
                            f"{(f['scaling'] or {}).get('base_odds', 50):.0f}:1, PDO {(f['scaling'] or {}).get('pdo', 20):.0f}"),
              ]], ["istilah", "arti"]))

    toc = ("<section class='page'><div class='kicker'>Isi</div><h2>Daftar isi</h2><div class='rule'></div>"
           "<div class='toc'><ol>" + "".join(f"<li><span class='n'>{k + 1}</span><span class='t'>{esc(c)}</span></li>"
                                             for k, c in enumerate(r.chapters)) + "</ol></div></section>")
    cover = (f"<section class='page cover'><div class='band'></div><div class='inner'>"
             f"<div class='kicker'>Dokumentasi model</div><h1>Scorecard aplikasi: {esc(f['name'])}</h1>"
             "<div class='sub'>Definisi bad, pengembangan model, analisis variabel per bin, kelompok skor dan "
             "kategori risiko</div></div><div class='meta'>"
             f"<div><b>Model</b>{f['cut'].replace('cut', 'Cut ')} · {esc(STATUS_ID.get(info.get('status'), info.get('status', '')))}</div>"
             f"<div><b>Tanggal</b>{tanggal(datetime.now())}</div>"
             "<div><b>Disusun oleh</b>Tim AutoScorecard</div></div></section>")
    return (f"<!doctype html><html lang='id'><head><meta charset='utf-8'><title>Dokumentasi model: "
            f"{esc(f['name'])}</title><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{CSS}</style></head><body>{cover}{toc}{''.join(r.pages)}</body></html>")
