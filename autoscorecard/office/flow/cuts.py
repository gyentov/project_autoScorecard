"""Model cuts: build (tuning on the final cut), results meeting with the deck on the projector."""
from __future__ import annotations

import threading

import pandas as pd

from ...engine.pipeline import ScorecardConfig, build_scorecard
from .. import prep
from ..meetings import choice, html_table, say
from ..timeline import gantt_svg
from .common import _JOB_LOCK


class CutsMixin:
    def build_cut(self, cut: str) -> dict:
        st = self.state
        st.stage, st.current_cut = "cut", cut
        st.cuts[cut] = {"status": "building"}
        st.save(self.project)
        self.timeline.set(cut, "in_progress")
        if self.inline_jobs:
            self._build_job(cut)
        else:
            t = threading.Thread(target=self._build_job, args=(cut,), daemon=True)
            self.jobs[cut] = t
            t.start()
        return {"job": cut}

    def _build_job(self, cut: str) -> None:
        with _JOB_LOCK:
            self.events.emit("job_started", "citra", job="build", cut=cut)
            self._status("citra", "working", f"membangun {cut.replace('cut', 'Cut ')}")
            try:
                df, y = self.dataset()
                st = self.state
                feats, _ = prep.feature_list(df, st.target, st.exclude)

                def progress(action, detail, reason):
                    if action in ("waterfall_step", "split", "outliers", "model", "performance"):
                        self.events.emit("job_progress", "citra", job="build", cut=cut,
                                         step=detail.get("step", action),
                                         detail={k: v for k, v in detail.items()
                                                 if k in ("n_in", "n_dropped", "n_out",
                                                          "criterion")})

                t = dict(self.settings.modelling.tuning)
                tune = ({k: v for k, v in t.items() if k != "enabled"}
                        if t.get("enabled", True) and self._is_final(cut) else None)
                if tune:                              # finalisation: tune once, on train only
                    self._status("citra", "working", "hyperparameter tuning (finalisasi)")
                cfg = ScorecardConfig(date_col=st.target.get("date_col"),
                                      oot_from=st.target.get("oot_from"),
                                      outliers=self.settings.modelling.outliers, tune=tune)
                res = build_scorecard(df, y, feats, cfg, project=self.project,
                                      require_approved=True, progress=progress, snapshot=cut)
                perf = res.performance
                info = {"status": "built", "build_id": res.build_id,
                        "built_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="microseconds"),
                        "n_features": len(res.scorecard.features),
                        "features": res.scorecard.features,
                        "gini_train": perf["train"]["gini"], "gini_test": perf["test"]["gini"],
                        "gini_oot": perf.get("oot", {}).get("gini"),
                        "psi_oot": res.score_psi.get("oot"), "psi_test": res.score_psi.get("test"),
                        "tuning": res.tuning.to_dict() if res.tuning is not None else None}
                st = self.state
                st.cuts[cut] = info
                st.save(self.project)
                self.events.emit("job_finished", "citra", job="build", cut=cut, ok=True)
                self._results(cut, res)
            except Exception as e:                    # report, never crash the office
                st = self.state
                st.cuts[cut] = {"status": "failed", "error": str(e)}
                st.save(self.project)
                self.events.emit("job_finished", "citra", job="build", cut=cut, ok=False,
                                 error=str(e))
                self.timeline.set(cut, "waiting_boss", f"gagal: {e}")
                self.inbox.send("citra", f"{cut}: build gagal", f"Maaf Boss, build gagal:\n\n"
                                f"`{e}`\n\nSaya tunggu arahan Boss.\n\nCitra", "memo")
                self._status("citra", "waiting_boss", "build gagal")

    def _is_final(self, cut: str) -> bool:
        return self.timeline.cuts()[-1].id == cut and cut != "cut1"

    def _results(self, cut: str, res) -> None:
        st = self.state
        info = st.cuts[cut]
        wf = res.waterfall
        art = self.project.dir("artifacts") / cut
        rel = lambda name: str((art / name).relative_to(self.project.root))   # noqa: E731
        n_in, n_sel = len(wf.features_in), len(wf.selected)
        g_test, g_oot = info["gini_test"], info["gini_oot"]
        psi = info["psi_oot"] if info["psi_oot"] is not None else info["psi_test"]
        good = (g_oot or g_test) >= 0.4 and (psi or 0) < 0.1
        mood = "proud" if good else ("worried" if (g_oot or g_test) < 0.3 or (psi or 0) > 0.25
                                     else "neutral")
        corr = wf.final_corr.where(~pd.DataFrame(
            [[i == j for j in range(len(wf.final_corr))] for i in range(len(wf.final_corr))],
            index=wf.final_corr.index, columns=wf.final_corr.columns)).abs()
        max_r = float(corr.max().max()) if len(corr) > 1 else 0.0
        title = cut.replace("cut", "Cut ")
        final = self._is_final(cut)

        # what changed since the previous cut
        cuts = [c for c in st.cuts if st.cuts[c].get("status") in ("built", "confirmed")]
        prev = cuts[cuts.index(cut) - 1] if cuts.index(cut) > 0 else None
        compare = ""
        if prev:
            p = st.cuts[prev]
            added = sorted(set(info["features"]) - set(p["features"]))
            gone = sorted(set(p["features"]) - set(info["features"]))
            d = (g_oot or g_test) - (p["gini_oot"] or p["gini_test"])
            compare = (f"Dibanding {prev.replace('cut', 'Cut ')}: Gini {d:+.3f}"
                       + (f", fitur baru {', '.join(added)}" if added else "")
                       + (f", keluar {', '.join(gone)}" if gone else "") + ".")
            changes = self._accepted_since(p.get("built_at"))
            fb = st.feedback.get(prev, [])
            if changes:
                compare += f" Perubahan yang di-ACC sejak {prev.replace('cut', 'Cut ')}: " \
                           + "; ".join(changes) + "."
            elif fb:
                compare += (" Masukan Boss belum menjadi perubahan yang di-ACC, jadi modelnya "
                            "sama. Maaf Boss; ketik instruksi atau tanya Citra untuk usulan.")

        forced = wf.forced
        rep = self._score_report(cut, res)
        pages, an = self._cut_deck(cut)
        at = {}                                   # deck slides by tracker (first) and variable
        for k, pg in enumerate(pages):
            at.setdefault(pg["var"] or pg["tracker"], k)
        slides = [(pg["action"][:90], pg["html"], "page") for pg in pages]
        # working material for the board tab, apart from the deck
        board = [("Korelasi WoE antar fitur model", (art / "woe_correlation.svg").read_text(encoding="utf-8"))]
        join_rows = self._join_contribution(art) if cut == "cut1" else []
        if join_rows:
            board.append(("Join data (Bening)", html_table(join_rows)))
        if final:
            board.append(("Usulan kategori risiko (VL–VH)", rep["levels_html"]))
        board.append(("Timeline", gantt_svg(self.timeline)))
        bk = {t: k for k, (t, _) in enumerate(board)}
        top = rep["contribution"][:3]
        script = [
            say("laras", "happy", f"Boss, hasil {title} sudah siap; deck-nya di proyektor. Citra "
                "presentasikan ya. Kalau Boss minta data tambahan, hasilnya muncul di tab Papan, "
                "tidak mencampuri deck.", at.get("Executive summary", 0)),
        ]
        if join_rows:
            best = max(join_rows, key=lambda r: r["fitur di model"])
            script.append(say("bening", "thinking", f"Soal join: saya pakai {len(join_rows) - 1} "
                              f"join dari tabel dasar. Hasil percobaannya: tabel {best['tabel']} "
                              f"menyumbang paling banyak, {best['fitur di model']} fitur di model. "
                              "Detailnya di tab Papan dan di memo join.", board=bk["Join data (Bening)"]))
        script += [
            say("citra", "neutral", f"Dari {n_in} kandidat, {n_sel} fitur masuk model.",
                at.get("Model waterfall")),
            say("citra", mood, f"Gini test {g_test:.3f}"
                + (f", OOT {g_oot:.3f}" if g_oot is not None else "")
                + (f"; PSI {psi:.3f}" if psi is not None else "")
                + (". PSI di atas 0,25 artinya populasi terbaru bergeser; cut-off perlu dikalibrasi "
                   "ulang sebelum dipakai." if (psi or 0) >= 0.25 else "."), at.get("Model performance")),
        ]
        tuning = getattr(res, "tuning", None)
        if tuning is not None:
            ch = tuning.changed
            script.append(say("citra", "thinking", f"Karena ini finalisasi, saya lakukan hyperparameter "
                              f"tuning: {len(tuning.table)} kombinasi diuji di potongan validasi dari data "
                              "train; test dan OOT tidak dipakai untuk memilih. "
                              + ("Setelan rencana ternyata sudah yang terbaik." if not ch else
                                 "Yang dipakai: " + ", ".join(f"{k} {a} → {b}" for k, (a, b) in ch.items())
                                 + "."), at.get("Hyperparameter tuning")))
        script += [
            say("citra", "thinking" if max_r < 0.5 else "worried",
                f"Korelasi WoE tertinggi antar fitur model {max_r:.2f}.",
                board=bk["Korelasi WoE antar fitur model"]),
            say("citra", "neutral", "Kontribusi skor terbesar dari "
                + ", ".join(f"{r['variabel']} ({r['kontribusi']:.0%})" for r in top) + ".",
                at.get("Score drivers")),
        ]
        for var, a in an.items():                 # every variable, bin by bin
            script.append(say("citra", "worried" if a["doubt"] or a["breaks"] else "neutral",
                              self._bin_line(var, a), at.get(var)))
        op = rep["opt"]
        ksm, ksb = op["ks_model"].get("train", 0), op["ks_banded"].get("train", 0)
        script.append(say("citra", "thinking", f"Kelompok skor: saya mulai dari {op['start']} kelompok "
                          f"sama besar, lalu menggabungkan pasangan yang paling mirip satu per satu. "
                          f"Yang ideal {op['bands']} kelompok: {op['rule']}. KS model {ksm:.3f}; "
                          f"setelah dikelompokkan {ksb:.3f}, jadi pemisahannya "
                          + ("hampir utuh." if ksb >= ksm - 0.02 else "sedikit berkurang."),
                          at.get("Score band selection", at.get("Recommended score binning"))))
        script.append(say("citra", "serious" if not rep["reversals"] else "worried",
                          f"Dengan {rep['n_bands']} kelompok itu"
                          + (", bad rate turun konsisten dari skor rendah ke tinggi; tabel KS per "
                             "kelompok ada di deck." if not rep["reversals"] else
                             f", bad rate belum turun konsisten di kelompok {', '.join(map(str, rep['reversals']))}.")
                          + " Kategori VL–VH saya turunkan dari kelompok ini.",
                          at.get("Recommended score binning")))
        if forced:
            kept = [r["feature"] for r in forced if r["result"] == "kept"]
            script.append(say("citra", "serious", f"{len(forced)} fitur dipaksa dicoba; "
                              f"{len(kept)} bertahan" + (f" ({', '.join(kept)})" if kept else "")
                              + ". Detailnya di finalization review.", at.get("Model waterfall")))
        if compare:
            script.append(say("citra", "neutral", compare, at.get("Model performance")))
        if final:
            script.append(say("citra", "thinking", "Untuk kategori risiko, usul saya VH sampai VL "
                              "seperti di deck, masing-masing sekitar seperlima akun. Saya ingin "
                              "diskusikan dulu dengan Boss: kalau batasnya mau digeser, buka "
                              "editornya atau bilang saja.", at.get("Risk levels · proposal",
                                                                    at.get("Risk levels"))))
            script.append(say("laras", "serious", "Ini cut final. Kalau Boss puas, konfirmasi; "
                              "kalau belum, ketik masukan dan pilih perpanjang.", board=bk["Timeline"]))
            choices = [choice("levels_acc", "ACC kategori risiko VL–VH", "levels_acc"),
                       choice("levels_edit", "🎚 Ubah batas kategori risiko", "edit_levels"),
                       choice("confirm", "Konfirmasi model final", "confirm_final"),
                       choice("extend", "Perpanjang ke cut berikutnya (ketik masukan dulu)",
                              "extend")]
        else:
            script.append(say("laras", "neutral", "Silakan beri masukan dengan mengetik; "
                              "usulan yang Boss ACC dipakai di cut berikutnya.", board=bk["Timeline"]))
            nxt = f"cut{int(cut[3:]) + 1}"
            choices = [choice("next", f"Lanjut ke {nxt.replace('cut', 'Cut ')}", "next_cut")]

        body = (f"Boss,\n\n{title} selesai: {n_sel} fitur, Gini test {g_test:.3f}"
                + (f", OOT {g_oot:.3f}" if g_oot is not None else "") + ".\n\n"
                + (compare + "\n\n" if compare else "")
                + "Laporan lengkap terlampir (waterfall report, finalization review, kamus "
                "data). Laras akan mengatur rapat untuk presentasi singkat.\n\nCitra")
        attach = [rel(n) for n in (f"deck_{cut}.html", "waterfall_report.md", "finalization_review.md",
                                   "data_dictionary.md", "scorecard_table.csv",
                                   "score_contribution.csv", "risk_bands.csv")]
        self.inbox.send("citra", f"Hasil {title}", body, "results", attachments=attach)
        self.run_open_orders()                    # e.g. a deck asked for before any model
        self.timeline.set(cut, "waiting_boss")
        self._status("citra", "waiting_boss", f"rapat hasil {title}")
        mt = self.meetings.create(
            "results", f"Presentasi hasil {title}", "laras", ["laras", "citra", "bening"],
            slides, script, choices, cut=cut,
            context=(f"Presentasi {title}. Fitur model: {', '.join(info['features'])}. "
                     f"Gini train {info['gini_train']:.3f}, test {g_test:.3f}, OOT {g_oot}. "
                     f"PSI {psi}. {compare} Pakai tool untuk detail."),
            invite_body=f"Presentasi singkat hasil {title}: deck di proyektor.", attachments=attach)
        for btitle, body_ in board:
            self.board.show(btitle, body_, "citra" if btitle != "Timeline" else "laras", mt.id)
        self._update(open_meeting=mt.id)

    def _cut_deck(self, cut: str) -> tuple[list[dict], dict]:
        """Kirana's deck for this cut (the consulting format, English), saved next to the
        cut's artifacts, its slides as projector pages, and the bin-by-bin analysis."""
        from .. import deck as K
        out = K.build(self, cut)
        art = self.project.dir("artifacts") / cut
        (art / f"deck_{cut}.html").write_text(out["html"], encoding="utf-8")
        return out["pages"], K.bin_analysis(K.facts(self, cut))

    @staticmethod
    def _bin_line(var: str, a: dict) -> str:
        """Citra's spoken read of one variable, bin by bin (Indonesian)."""
        w, b = a["worst"], a["best"]
        line = (f"{var}: porsi {a['share']:.0%} dari rentang skor, IV {a['iv']:.2f}. Bin paling "
                f"berisiko {w['label']} ({w['pct']:.0%} akun) bad rate {w['bad_rate']:.1%}, "
                f"{w['index']:.1f}× rata-rata, dapat {w['points']} poin; paling aman {b['label']} "
                f"({b['pct']:.0%} akun) {b['bad_rate']:.1%}, {b['points']} poin. ")
        if a["breaks"]:
            line += f"Polanya patah di {', '.join(a['breaks'])}; perlu dicek di cut berikutnya. "
        elif a["numeric"]:
            line += ("Polanya searah: " + ("risiko naik seiring nilainya. " if "rises" in a["direction"]
                                          else "risiko turun seiring nilainya. " if a["direction"]
                                          else "bad rate bergerak satu arah. "))
        if a["thin"]:
            line += f"Bin tipis (<5% akun): {', '.join(a['thin'])}. "
        if a["doubt"]:
            line += "Catatan: artinya belum jelas secara bisnis; saya sarankan dibuang sebelum final."
        return line.strip()
