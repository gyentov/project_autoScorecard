"""Citra's score report: contribution, bins, score bands (20 -> ideal), KS, risk levels."""
from __future__ import annotations

import json

import pandas as pd

from ...engine import finalization as fin, risk as R
from ..meetings import _page, html_table
from ..summary import summarize


class ScoreReportMixin:
    def _meanings(self) -> dict[str, str]:
        """Meanings as documentation: only the version Laras approved."""
        return {r["variabel"]: r["arti"] for r in self.approved_meanings()}

    def _score_report(self, cut: str, res) -> dict:
        """Every cut: each variable's bins and points, its share of the score, and the
        score cut into 20 risk bands (any band under 5% of accounts merged). The final
        cut also gets Citra's proposed VL-VH grouping, to discuss with the Boss."""
        from ..board import bars_svg
        art = self.project.dir("artifacts") / cut
        sc = res.scorecard
        t = sc.table()
        means = self._meanings()
        rows = []
        for f in sc.features:
            g = t[t.feature == f]
            rows.append({"variabel": f, "arti": means.get(f, ""), "IV": round(float(sc.binnings[f].iv), 3),
                         "poin min": int(g.points.min()), "poin max": int(g.points.max()),
                         "rentang": int(g.points.max() - g.points.min())})
        total = sum(r["rentang"] for r in rows) or 1
        for r in rows:
            r["kontribusi"] = r["rentang"] / total
        rows.sort(key=lambda r: -r["kontribusi"])
        pd.DataFrame(rows).to_csv(art / "score_contribution.csv", index=False)
        contribution_html = (bars_svg(pd.DataFrame(rows), ["variabel"], "kontribusi", True)
                             + html_table([{**r, "kontribusi": f"{r['kontribusi']:.1%}"} for r in rows])
                             + "<p class='muted'>Kontribusi = rentang poin variabel dibagi total "
                               "rentang poin semua variabel.</p>")
        parts = []
        for r in rows:
            g = t[t.feature == r["variabel"]]
            parts.append(f"<h3>{r['variabel']}</h3><p class='muted'>{r['arti']}</p>" + html_table(
                [{"bin": b.bin, "akun": int(b.count), "% akun": f"{b.pct:.1%}",
                  "bad rate": f"{b.bad_rate:.1%}", "WoE": round(float(b.woe), 3), "poin": int(b.points)}
                 for b in g.itertuples()]))
        binning_html = "".join(parts)
        df, y = self.dataset()
        samples = {}
        for name, idx in res.splits.items():
            idx = [i for i in idx if pd.notna(y.get(i))]
            if idx:
                samples[name] = (sc.score(df.loc[idx]), y.loc[idx].astype(int))
        start = R.risk_bands(*samples["train"])              # 20 equal bands (small ones merged)
        R.band_table(start, samples).to_csv(art / "risk_bands_20.csv", index=False)
        opt = R.optimise_bands(samples, start["cuts"])      # 20 -> the ideal number of bands
        pd.DataFrame([{k: v for k, v in p.items() if k != "cuts"} for p in opt["path"]]).to_csv(
            art / "band_path.csv", index=False)
        bands = {"cuts": opt["cuts"], "merged": start["merged"], "requested": 20,
                 "min_pct": start["min_pct"]}
        table = R.band_table(bands, samples)
        table.to_csv(art / "risk_bands.csv", index=False)
        kst = {n: R.ks_table(sc_, y_, opt["cuts"]) for n, (sc_, y_) in samples.items()}
        pd.concat([t.assign(sample=n) for n, t in kst.items()]).to_csv(art / "ks_table.csv", index=False)
        ks_model = {n: float(res.performance[n]["ks"]) for n in res.performance}
        (art / "band_choice.json").write_text(json.dumps(
            {"start": opt["start"], "bands": opt["bands"], "rule": opt["rule"],
             "ks_model": ks_model, "ks_banded": {n: float(t.ks.max()) for n, t in kst.items()}},
            indent=2), encoding="utf-8")
        reversals = R.is_monotonic(table["bad_rate_train"].tolist())
        rate_cols = [c for c in table.columns if c.startswith("bad_rate_")]
        bands_html = html_table([{
            "kelompok": int(b["band"]), "skor": f"{b['score_min']:.0f}–{b['score_max']:.0f}",
            "akun": int(b["n"]), "% akun": f"{b['pct']:.1%}",
            **{c.replace("bad_rate_", "bad rate "): (f"{b[c]:.1%}" if pd.notna(b[c]) else "-")
               for c in rate_cols}} for b in table.to_dict("records")]) + (
            f"<p class='muted'>Kelompok 1 = skor terendah (risiko tertinggi). Dimulai dari 20 "
            f"kelompok sama besar, digabung bertahap sampai {opt['bands']} kelompok: "
            f"{opt['rule']}.</p>")
        st = self.state
        old = st.cuts[cut].get("levels", {})
        labels = old.get("labels") if len(old.get("labels", [])) == len(table) else R.propose_levels(table)
        st.cuts[cut]["bands"] = {"cuts": [float(c) for c in bands["cuts"]],     # JSON-safe for the API
                                 "table": json.loads(table.to_json(orient="records"))}
        st.cuts[cut]["levels"] = {"labels": labels, "approved": False, "by": "citra"}
        st.save(self.project)
        return {"contribution": rows, "contribution_html": contribution_html,
                "binning_html": binning_html, "bands_html": bands_html, "n_bands": len(table),
                "merged": bands["merged"], "reversals": reversals,
                "levels_html": self._levels_html(table, labels),
                "opt": {"start": opt["start"], "bands": opt["bands"], "rule": opt["rule"],
                        "ks_model": ks_model, "ks_banded": {n: float(t.ks.max()) for n, t in kst.items()}}}

    def _levels_html(self, table: pd.DataFrame, labels: list[str]) -> str:
        lt = R.level_table(table, labels)
        rate_cols = [c for c in lt.columns if c.startswith("bad_rate_")]
        return html_table([{
            "kategori": r["level"], "arti": r["arti"], "kelompok": r["band"],
            "skor": f"{r['score_min']:.0f}–{r['score_max']:.0f}", "% akun": f"{r['pct']:.1%}",
            **{c.replace("bad_rate_", "bad rate "): (f"{r[c]:.1%}" if pd.notna(r[c]) else "-")
               for c in rate_cols}} for r in lt.to_dict("records")]) + \
            "<p class='muted'>Usulan Citra, untuk didiskusikan: kategori berurutan dari skor " \
            "rendah (VH) ke tinggi (VL), masing-masing sekitar seperlima akun.</p>"

    def risk_levels(self, cut: str) -> dict:
        info = self.state.cuts.get(cut, {})
        if "bands" not in info:
            raise KeyError(cut)
        return {"cut": cut, "bands": info["bands"]["table"], "levels": info["levels"],
                "names": R.LEVEL_NAMES}

    def edit_risk_levels(self, cut: str, labels: list[str]) -> dict:
        """The Boss sets the VL-VH grouping (after talking it through with Citra): applied
        as the Boss's decision; the slide is rebuilt and Citra repeats what it now is."""
        st = self.state
        table = pd.DataFrame(st.cuts[cut]["bands"]["table"])
        R.check_levels(labels, len(table))
        st.cuts[cut]["levels"] = {"labels": labels, "approved": True, "by": "boss"}
        st.save(self.project)
        self._levels_saved(cut, table, labels, "Boss mengubah batas kategori risiko")
        return self.risk_levels(cut)

    def _levels_saved(self, cut: str, table: pd.DataFrame, labels: list[str], what: str) -> None:
        art = self.project.dir("artifacts") / cut
        lt = R.level_table(table, labels)
        lt.to_csv(art / "risk_levels.csv", index=False)
        self.project.audit("boss", "risk_levels", {"cut": cut, "labels": labels})
        said = "; ".join(f"{r['level']} skor {r['score_min']:.0f}–{r['score_max']:.0f} "
                         f"({r['pct']:.0%} akun)" for r in lt.to_dict("records"))
        st = self.state
        m = self.meetings.get(st.open_meeting) if st.open_meeting else None
        if m is not None and m.status != "ended" and m.cut == cut:
            b = next((i for i, x in enumerate(m.board) if x["title"].startswith("Usulan kategori")), None)
            if b is not None:
                (self.project.root / m.board[b]["file"]).write_text(
                    _page(m.board[b]["title"], self._levels_html(table, labels)), encoding="utf-8")
            k = None
            try:                                  # the deck's risk-level slide follows too
                pages, _ = self._cut_deck(cut)
                k = next((i for i, pg in enumerate(pages) if pg["tracker"].startswith("Risk levels")), None)
                if k is not None and k < len(m.slides):
                    (self.project.root / m.slides[k]["file"]).write_text(pages[k]["html"], encoding="utf-8")
            except (ValueError, KeyError, FileNotFoundError):
                k = None
            m.decisions.append({"decision": f"{what}: {said}.", "ts": ""})
            self.meetings.save(m)
            self.meetings.add_turn(m, "citra", f"Dicatat, Boss. Kategori risiko sekarang: {said}.",
                                   "happy", slide=k, board=b)

    def _do_levels_acc(self, m) -> dict:
        st = self.state
        info = st.cuts[m.cut]
        info["levels"]["approved"] = True
        st.save(self.project)
        self._levels_saved(m.cut, pd.DataFrame(info["bands"]["table"]), info["levels"]["labels"],
                           "Kategori risiko di-ACC")
        self.events.emit("approval", "boss", what="risk_levels", decision="accepted")
        return {"levels": info["levels"]}

    def _do_edit_levels(self, m) -> dict:         # the page opens the editor for this choice
        return {"note": "buka editor kategori risiko"}

    def _join_contribution(self, art) -> list[dict]:
        """Per source table: its join key, match rate, and what its columns did in this
        cut (candidates, kept in the model, best IV). The join experiment's report."""
        plan = self.state.join_plan
        if not plan:
            return []
        wf = pd.read_csv(art / "waterfall_features.csv")
        iv = pd.read_csv(art / "iv_table.csv").set_index("feature")["iv"]
        prefixes = {j["prefix"] + "_": j for j in plan["joins"]}
        rows, src = [], {}
        for f in wf["feature"]:
            src[f] = next((p for p in prefixes if str(f).startswith(p)), None)
        for p, j in [(None, None), *prefixes.items()]:
            fs = wf[[src[f] == p for f in wf["feature"]]]
            ivs = iv.reindex(fs["feature"]).dropna()
            rows.append({
                "tabel": plan["base"] if j is None else j["right"],
                "kunci": "(tabel dasar)" if j is None else f"{j['left_on']} = {j['right_on']}",
                "cocok": "-" if j is None else f"{j.get('matched_pct', j['coverage']):.1%}",
                "kandidat fitur": len(fs),
                "fitur di model": int((fs["status"] == "selected").sum()),
                "IV tertinggi": f"{ivs.max():.3f}" if len(ivs) else "-"})
        return rows

    def _accepted_since(self, ts: str | None) -> list[str]:
        """Changes ACC'd after `ts` (both are microsecond UTC timestamps)."""
        return [p.summary for p in self.proposals.list("accepted")
                if ts is None or (p.decided or "") > ts] + \
            [e["summary"] for e in self.state.plan_edits if ts is None or e["ts"] > ts]

    def _feedback(self, m) -> list[str]:
        return [t["text"] for t in m.transcript if t["who"] == "boss"]

    def _do_next_cut(self, m) -> dict:
        cut = m.cut
        st = self.state
        st.feedback[cut] = self._feedback(m)
        st.save(self.project)
        accepted = [p.summary for p in self.proposals.list("accepted")]
        self.timeline.set(cut, "done")
        nxt = f"cut{int(cut[3:]) + 1}"
        self._end(m, f"Lanjut ke {nxt.replace('cut', 'Cut ')}.",
                  [f"Usulan berlaku: {s}" for s in accepted]
                  + [f"Citra: membangun {nxt.replace('cut', 'Cut ')}."])
        return self.build_cut(nxt)

    def _do_extend(self, m) -> dict:
        fb = self._feedback(m)
        if not fb:
            self.meetings.add_turn(m, "laras", "Boss, ketik dulu masukannya supaya bisa saya "
                                   "catat sebagai alasan perpanjangan.", "apologetic")
            return {"note": "ketik masukan dulu"}
        cut = m.cut
        st = self.state
        st.feedback[cut] = fb
        st.save(self.project)
        self.timeline.set(cut, "done")
        if not self._accepted_since(self.state.cuts[cut].get("built_at")):
            self.meetings.add_turn(m, "laras", "Catatan jujur, Boss: belum ada perubahan yang "
                                   "di-ACC, jadi cut berikutnya kemungkinan sama. Masukan "
                                   "Boss tetap saya catat.", "worried")
        gist = summarize(self, fb)
        new = self.timeline.add_extension(gist[:200])
        self._end(m, f"Diperpanjang ke {new.title}. Arahan Boss: {gist}",
                  ["Citra: membangun " + new.title + "."], boss_summary=gist)
        return self.build_cut(new.id)

    def _do_confirm_final(self, m) -> dict:
        info = self.state.cuts[m.cut]
        fin.confirm(self.project, info["build_id"], by="boss",
                    note=f"dikonfirmasi di rapat {m.title}")
        st = self.state
        st.cuts[m.cut]["status"] = "confirmed"
        st.stage = "final"
        st.save(self.project)
        self.timeline.set(m.cut, "done")
        try:                                      # the deliverables, from the confirmed model
            self.make_documentation()
            self.make_deck()
        except ValueError as e:
            self.inbox.send("laras", "Deliverables tertunda", f"Boss, dokumentasi/deck belum "
                            f"bisa dibuat: {e}.\n\nLaras", "memo")
        self.run_open_orders()
        self._end(m, f"Model {m.cut.replace('cut', 'Cut ')} dikonfirmasi final "
                  f"(build {info['build_id']}).",
                  ["Kirana: deck (HTML draft dulu, lalu PDF/PPTX setelah ACC). Tersedia di "
                   "Phase 3."])
        self.events.emit("approval", "boss", what="final_model", id=info["build_id"],
                         decision="accepted")
        self._status("citra", "idle")
        return {"confirmed": info["build_id"]}
