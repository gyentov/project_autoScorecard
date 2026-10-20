"""Work the Boss can watch, the stage meetings (kickoff, data, target) and the vintage analysis."""
from __future__ import annotations

import threading
import time
from typing import Any

import pandas as pd

from ...engine import data as D, vintage as V
from ...engine.dictionary import DataDictionary
from .. import prep
from ..characters import TEAM
from ..meetings import cards, choice, html_table, mom_markdown, say
from ..summary import summarize
from ..timeline import STATUS_ICON, STATUS_LABEL
from .common import _DATA, adjourn_choice, json_safe


class StagesMixin:
    WORK_PAUSE = 4.0                 # seconds per step of desk work before a meeting

    def _work(self, agent: str, steps: list[str], then) -> dict:
        """The agent works at her desk (a status per step, a few seconds each) and then
        `then()` runs, usually scheduling the next meeting. Inline (tests): no waiting."""
        def run():
            try:
                for s in steps:
                    self._status(agent, "working", s)
                    if not self.inline_jobs:
                        time.sleep(self.WORK_PAUSE)
                then()
            except Exception as e:                       # tell the Boss, don't hang silently
                self._status(agent, "waiting_boss", "ada kendala")
                self.inbox.send(agent, "Ada kendala", f"Boss, pekerjaan saya berhenti: {e}\n\n"
                                f"{TEAM[agent].name}", "memo")
        if self.inline_jobs:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()
        return {"working": agent}

    # a meeting the Boss does not ACC yet: who reworks it, what to redo, which milestone
    ADJOURN = {"kickoff": ("laras", "_kickoff_meeting", "kickoff"),
               "target": ("bening", "target_stage", "target"),
               "plan": ("citra", "plan_stage", "waterfall_plan")}

    def _do_adjourn(self, m) -> dict:
        """Close the meeting without ACC. The owner works on what the Boss typed, then
        Laras calls a follow-up meeting that starts from that input."""
        fb = self._feedback(m)
        if not fb:
            return {"note": "ketik masukan dulu, supaya tim tahu apa yang harus dikerjakan"}
        owner, redo, milestone = self.ADJOURN[m.kind]
        st = self.state
        st.feedback[m.kind] = st.feedback.get(m.kind, []) + fb
        st.save(self.project)
        self.timeline.set(milestone, "in_progress")
        gist = summarize(self, fb)
        steps = [f"{TEAM[owner].name}: tindak lanjuti arahan Boss ({gist.rstrip('.')}).",
                 "Laras: jadwalkan rapat lanjutan."]
        self._end(m, "Belum di-ACC. Rapat dibubarkan; tim menindaklanjuti arahan Boss dulu.",
                  steps, boss_summary=gist, carry_out=False)
        mom = mom_markdown(m, steps, "", gist)

        def again():
            done = self.carry_out_mom(m.title, mom, fb)
            r = getattr(self, redo)()
            if r.get("meeting_id"):
                nm = self.meetings.get(r["meeting_id"])
                for p in [p for p in self.proposals.list("pending") if p.id in done["proposals"]]:
                    nm.choices.insert(0, choice(f"acc_{p.id}", f"ACC: {p.summary}", "proposal",
                                                pid=p.id, accept=True))
                    nm.choices.insert(1, choice(f"rej_{p.id}", f"Tolak: {p.summary}", "proposal",
                                                pid=p.id, accept=False))
                nm.script.insert(0, say(owner, "neutral", "Ini rapat lanjutan untuk "
                                        f"menindaklanjuti arahan Boss: {gist[0].lower()}{gist[1:]} "
                                        "Perubahan yang Boss ACC sudah kami pakai; yang lain "
                                        "bisa Boss putuskan di sini.", 0))
                self.meetings.save(nm)
        return self._work(owner, ["membaca masukan Boss", "mengerjakan revisi",
                                  "menyiapkan rapat lanjutan"], again)

    # ================================================================= stage handlers
    def _end(self, m, decision: str, next_steps: list[str], boss_summary: str | None = None,
             carry_out: bool = True) -> None:
        fb = self._feedback(m)
        if boss_summary is None:                  # the Boss's words, summarised for the MOM
            boss_summary = summarize(self, fb)
        self.meetings.end(m, decision, next_steps, self._timeline_md(), boss_summary)
        self._update(open_meeting=None)
        if fb and carry_out:                      # Laras acts on the MOM, not just files it
            mom = mom_markdown(m, next_steps, "", boss_summary)
            if self.inline_jobs:
                self.carry_out_mom(m.title, mom, fb)
            else:
                threading.Thread(target=self.carry_out_mom, args=(m.title, mom, fb),
                                 daemon=True).start()

    def carry_out_mom(self, title: str, mom_md: str, directions: list[str]) -> dict:
        """Laras turns the Boss's directions from a meeting into actions: proposals for ACC,
        tasks for the team, data on the board. She mails what she did and what it cost."""
        self._status("laras", "working", "menindaklanjuti MOM")
        r = self.agents.carry_out(mom_md, directions)
        lines = [f"- tugas ke {TEAM[t['to']].name}: {t['summary']}" for t in r["tasks"]]
        props = [p for p in self.proposals.list("pending") if p.id in r["proposals"]]
        lines += [f"- usulan menunggu ACC Boss: {p.summary}" for p in props]
        lines += [f"- di papan: {b['title']}" for b in r["board"]]
        body = (f"Boss,\n\nTindak lanjut rapat **{title}**:\n\n" + ("\n".join(lines) or "- (tidak ada aksi)")
                + f"\n\n{r['text']}\n\n"
                + (f"Biaya: ${r['usd']:.4f} ({r['provider']})." if r["usd"] else "Biaya: $0.")
                + "\n\nLaras")
        self.inbox.send("laras", f"Tindak lanjut MOM: {title}", body, "memo")
        self._status("laras", "idle", "")
        return r

    def _timeline_md(self) -> str:
        return "\n".join(f"- {STATUS_ICON[i.status]} {i.title} ({TEAM[i.owner].name}): "
                         f"{STATUS_LABEL[i.status]}" for i in self.timeline.items)

    def _do_proposal(self, m, pid: str, accept: bool) -> dict:
        res = self.decide_proposal(pid, accept)
        m = self.meetings.get(m.id)
        m.choices = [c for c in m.choices if c["payload"].get("pid") != pid]
        m.decisions.append({"decision": ("ACC: " if accept else "Ditolak: ") + res["summary"],
                            "ts": ""})
        self.meetings.save(m)
        verdict = "di-ACC" if accept else "ditolak"
        if accept and m.kind == "plan":
            self._refresh_plan_slides(m)
            self.meetings.add_turn(m, "citra", f"Dicatat, Boss: usulan di-ACC ({res['summary']}). "
                                   "Rencana di proyektor sudah saya perbarui.", "happy", slide=0)
            return res
        self.meetings.add_turn(m, "citra", f"Dicatat, Boss: usulan {verdict}. "
                               + ("Berlaku di cut berikutnya." if accept else ""),
                               "happy" if accept else "neutral")
        return res

    def _do_kickoff_acc(self, m) -> dict:
        self.timeline.set("kickoff", "done")
        self._end(m, "Timeline dan cara kerja disetujui (ACC).",
                  ["Bening: join data, dicatat di memo dan dilaporkan di Cut 1."])
        self.events.emit("approval", "boss", what="timeline", decision="accepted")
        return self._work("bening", ["membaca tabel-tabel", "mencoba kunci join",
                                     "menggabungkan tabel", "mencari kandidat target"],
                          self.data_stage)

    def data_stage(self) -> dict:
        self._update(stage="data")
        self.timeline.set("data", "in_progress")
        self.timeline.set("dictionary", "in_progress")
        self._status("bening", "working", "profiling tabel dan mencari kunci join")
        tables, dicts = prep.split_uploads(self.project)
        if not tables:
            self.timeline.set("data", "waiting_boss", "menunggu upload data")
            self._status("bening", "waiting_boss", "menunggu data")
            self.inbox.send("bening", "Data belum ada", "Halo Boss, saya belum menerima tabel. "
                            "Mohon serahkan datanya ke Laras lewat 📁 Data (CSV/Excel/Parquet); "
                            "begitu diterima, saya langsung lanjut.\n\nBening", "memo")
            return {"waiting": "upload"}
        return self._join_and_record(tables, dicts)

    def _join_and_record(self, tables, dicts) -> dict:
        """Bening joins the tables without a meeting: she tries the candidate keys for
        every table, keeps the best by rule, writes down what she tried in a memo, and
        reports what each table brought to the model in the Cut 1 meeting. Left joins
        only, so the base table never gains rows."""
        plan = prep.propose_join_plan(tables)
        ddict = DataDictionary.load(self.project)
        for p in dicts:
            ddict.import_file(p)
        df, reports = prep.apply_join_plan(tables, plan, ddict)
        ddict.save(self.project)
        df.to_parquet(self.project.dir("work") / "dataset.parquet")
        _DATA[self.project.id] = {"df": df, "y": None}
        for j, rep in zip(plan["joins"], reports):
            j.update(matched_pct=rep["matched_pct"], new_columns=rep["new_columns"])
        plan["result"] = {"rows": len(df), "cols": df.shape[1]}
        self._update(join_plan=plan, join_approved=True)
        self.project.audit("bening", "join", {"plan": plan}, reason="aturan join otomatis")
        lines = [f"| {j['right']} | {j['left_on']} = {j['right_on']} | {j['relationship']} | "
                 f"{j['coverage']:.1%} | {j['new_columns']} | "
                 + "; ".join(f"{t['key']} ({t['coverage']:.0%})" for t in j["tried"]
                             if not t["chosen"]) + " |" for j in plan["joins"]]
        body = (f"Boss,\n\nTabel dasar: **{plan['base']}** ({plan['rows'][plan['base']]:,} baris). "
                "Semua join adalah left join, jadi jumlah baris tabel dasar tetap.\n\n"
                "| tabel | kunci dipakai | relasi | cocok | kolom baru | kunci lain yang dicoba |\n"
                "|---|---|---|---|---|---|\n" + "\n".join(lines)
                + (f"\n\nTidak bisa di-join: " + ", ".join(f"{s['table']} ({s['reason']})"
                                                       for s in plan["skipped"])
                   if plan["skipped"] else "")
                + (f"\n\nKamus data dipakai: {', '.join(p.name for p in dicts)}." if dicts else "")
                + (f"\n\nTabel {plan['history']['table']} berisi riwayat performa bulanan, jadi "
                   "TIDAK saya join sebagai fitur (bisa bocor). Saya pakai untuk vintage analysis "
                   "saat menentukan target." if plan.get("history") else "")
                + f"\n\nHasil: {len(df):,} baris, {df.shape[1]} kolom. Apa yang disumbangkan "
                "tiap tabel ke model saya laporkan di rapat Cut 1. Kalau Boss mau join yang lain, "
                "bilang saja ke saya.\n\nBening")
        self.inbox.send("bening", "Catatan join data", body, "memo")
        self.timeline.set("data", "done")
        self.events.emit("handoff", "bening", to="citra", memo="join selesai, dicatat")
        return self.target_stage()

    def _do_data_acc(self, m) -> dict:      # projects that still have the old data meeting open
        st = self.state
        tables, dicts = prep.split_uploads(self.project)
        ddict = DataDictionary.load(self.project)
        for p in dicts:
            ddict.import_file(p)
        df, reports = prep.apply_join_plan(tables, st.join_plan, ddict)
        ddict.save(self.project)
        df.to_parquet(self.project.dir("work") / "dataset.parquet")
        _DATA[self.project.id] = {"df": df, "y": None}
        self._update(join_approved=True)
        self.timeline.set("data", "done")
        self._end(m, "Rencana join di-ACC.",
                  [f"Dataset gabungan: {len(df):,} baris, {df.shape[1]} kolom.",
                   "Bening: usulan definisi target dan split."])
        self.events.emit("approval", "boss", what="join_plan", decision="accepted")
        return self.target_stage()

    # ================================================================= vintage analysis
    def vintage(self) -> dict | None:
        """Bening's vintage analysis from the monthly performance table, if there is one:
        cumulative X+ DPD curves per cohort by months on book, the recommended window
        and threshold, and the resulting bad flag per account (NaN = immature)."""
        st = self.state
        spec = (st.join_plan or {}).get("history")
        if not spec:
            return None
        tables, _ = prep.split_uploads(self.project)
        hist, base = tables.get(spec["table"]), tables.get(st.join_plan["base"])
        if hist is None or base is None:
            return None
        prof = D.profile(base)
        dates = prof.loc[prof.role == "date", "column"].tolist()
        if not dates:
            return None
        curves = V.vintage(hist, base, spec, dates[0])
        rec = V.recommend(curves)
        curves.to_csv(self.project.dir("work") / "vintage_curves.csv", index=False)
        y = V.ever_bad(hist, base, spec, dates[0], rec["threshold"], rec["window"])
        return {"spec": spec, "date_col": dates[0], "curves": curves, "rec": rec, "y": y}

    def _add_dataset_column(self, name: str, values: pd.Series) -> None:
        df, _ = self.dataset()
        df[name] = values.to_numpy()
        df.to_parquet(self.project.dir("work") / "dataset.parquet")
        _DATA[self.project.id] = {"df": df, "y": None}

    def _vintage_slides(self, vin: dict) -> tuple[list, list]:
        """(slides, script) for the target meeting: the evidence before the proposal."""
        rec, c, spec = vin["rec"], vin["curves"], vin["spec"]
        T, Wd = rec["threshold"], rec["window"]
        pick = next(r for r in rec["by_threshold"] if r["threshold"] == T)
        rows = [{"ambang": f"{r['threshold']}+ DPD", "MOB matang": r["mature_mob"],
                 "window": f"{r['window']} bln", "bad rate": f"{r['bad_rate']:.1%}",
                 "bad": r["bads"], "akun matang": r["n_mature"]} for r in rec["by_threshold"]]
        note = ("<p class='muted'>Window = bulan standar (6, 9, 12, 18, 24) saat kurva mencapai "
                f"{V.MATURE:.0%} dari nilai akhirnya; kalau akun yang sudah selama itu kurang dari "
                f"{V.MIN_MATURE_SHARE:.0%}, dipakai window terpanjang yang masih "
                f"{V.MIN_MATURE_SHARE:.0%}. Ambang = yang paling ketat dengan minimal "
                f"{V.MIN_BADS} bad dan bad rate {V.MIN_RATE:.0%}.</p>")
        slides = [
            (f"Vintage {T}+ DPD per kohort", V.curves_svg(c, T, Wd, f"Pernah {T}+ DPD, "
                                                         "per kuartal booking"), "html"),
            ("Kurva 30+ / 60+ / 90+", V.curves_svg(c, None, Wd, "Semua kohort"), "html"),
            ("Rekomendasi definisi", html_table(rows) + note, "html"),
        ]
        script = [
            say("bening", "thinking", f"Sebelum menentukan target, saya lakukan vintage analysis "
                f"dari tabel {spec['table']}: berapa persen akun yang pernah {T}+ DPD, per "
                "kohort booking, menurut bulan sejak booking.", 0),
            say("bening", "neutral", f"Kurva {T}+ baru mendatar sekitar bulan ke-"
                f"{pick['mature_mob']}; di bulan {Wd} sudah {pick['share_of_last']:.0%} dari bad "
                f"yang terlihat di akhir. Jadi window kinerja yang saya usulkan {Wd} bulan."
                + (" Kurvanya masih naik sedikit, tapi window lebih panjang membuang terlalu "
                   "banyak akun yang belum matang, jadi ini kompromi." if pick["traded_off"]
                   else ""), 1),
            say("bening", "serious" if rec["enough"] else "worried",
                f"Dengan ambang {T}+ ada {rec['bads']:,} bad ({rec['bad_rate']:.1%}) dari "
                f"{rec['n_mature']:,} akun yang sudah {Wd} bulan"
                + (", cukup untuk scorecard." if rec["enough"] else
                   ", masih di bawah 300 bad; hati-hati, modelnya bisa kurang stabil."), 2),
        ]
        return slides, script

    def target_stage(self) -> dict:
        self._update(stage="target")
        self.timeline.set("target", "in_progress")
        self._status("bening", "working", "vintage analysis dan definisi target")
        df, _ = self.dataset()
        st = self.state
        tg = prep.propose_target(df, st.join_plan)
        tg["outcome_prefixes"] = [j["prefix"] for j in st.join_plan["joins"]
                                  if prep.OUTCOME_HINT.search(j["right"])]
        vin = self.vintage()
        vin_slides, vin_script = [], []
        if vin:                                   # the vintage evidence decides the target
            rec = vin["rec"]
            col = V.column_name(rec["threshold"], rec["window"])
            self._add_dataset_column(col, df[vin["spec"]["key"]].map(vin["y"]))
            df, _ = self.dataset()
            tg = {**tg, "column": col, "rule": "eq", "value": 1, "source_prefix": None,
                  "vintage": json_safe({k: rec[k] for k in ("threshold", "window", "bad_rate", "bads",
                                                            "n_mature", "by_threshold", "enough")}),
                  "candidates": [{"column": col, "kind": "binary", "score": 9, "values": [0, 1],
                                  "suggestion": f"bad = pernah {rec['threshold']}+ DPD dalam "
                                                f"{rec['window']} bulan (vintage)"}]
                  + tg["candidates"]}
            vin_slides, vin_script = self._vintage_slides(vin)
        chosen = [p for p in self.proposals.list("accepted") if p.kind == "target"]
        if chosen:                                # the Boss already decided: keep it
            tg = {**tg, **{k: chosen[-1].payload[k] for k in ("column", "rule", "value")}}
        y = D.make_target(df, tg["column"], tg["rule"], tg["value"])
        sp = prep.propose_split(df, y)
        target = json_safe({**tg, **sp})
        self._update(target=target, target_approved=False)
        a = D.assess_target(y, df[sp["date_col"]] if sp["date_col"] else None, "Q")
        feats, out = prep.feature_list(df, target, st.exclude)
        rule = {"ge": ">=", "eq": "==", "in": "in"}[tg["rule"]]
        cand_rows = [{"kolom": c["column"], "jenis": c["kind"], "usulan": c["suggestion"]}
                     for c in tg["candidates"]]
        per = a.get("by_period", [])
        o = len(vin_slides)                       # the usual slides come after the vintage ones
        v = tg.get("vintage") if tg["column"].startswith("vin_") else None
        what = (f"pernah {v['threshold']}+ DPD dalam {v['window']} bulan pertama; akun yang "
                f"belum {v['window']} bulan dan belum bad tidak dipakai "
                f"({int(y.isna().sum()):,} akun)" if v else f"{tg['column']} {rule} {tg['value']}")
        script = vin_script + [
            say("bening", "thinking", f"Usulan target: bad = {what}.", o),
            say("bening", "neutral", f"Bad rate {a['bad_rate']:.1%} ({a['bads']:,} bad dari "
                f"{a['n']:,}).", o + 1),
        ]
        if a["warnings"]:
            script.append(say("bening", "worried", "Catatan: " + "; ".join(a["warnings"]), o + 1))
        if sp["date_col"]:
            script.append(say("bening", "neutral", f"Sampel out-of-time mulai {sp['oot_from']} "
                              f"({sp['oot_share']:.0%} data). Sisanya dibagi train/test "
                              "70/30.", o + 2))
        script.append(say("bening", "serious", f"{len(feats)} kolom jadi kandidat fitur; "
                          f"{len(out)} tidak dipakai (id, tanggal, data hasil).", o + 2))
        choices = [choice("acc", "ACC target & split", "target_acc")]
        alts = [c for c in tg["candidates"][1:] if c["score"] > 0][:2]   # outcome-like only
        for k, c in enumerate(alts, 1):
            alt_rule, alt_val = ("eq", c["values"][-1]) if c["kind"] == "binary" else ("ge", 90)
            if c["kind"].startswith(("binary", "numeric")):
                choices.append(choice(f"alt{k}", f"Pakai {c['column']} sebagai target",
                                      "target_alt", column=c["column"], rule=alt_rule,
                                      value=alt_val))
        choices.append(adjourn_choice())
        self.timeline.set("target", "waiting_boss")
        self._status("bening", "waiting_boss", "rapat target")
        mt = self.meetings.create(
            "target", "Rapat definisi target", "bening", ["laras", "bening", "citra"],
            vin_slides + [("Kandidat target", html_table(cand_rows), "html"),
             ("Bad rate per kuartal", html_table(
                 [{"periode": r["period"], "n": r["n"], "bad rate": f"{r['bad_rate']:.2%}"}
                  for r in per]), "html"),
             ("Split & kandidat fitur", cards([
                 ("kandidat fitur", str(len(feats))),
                 ("OOT mulai", sp["oot_from"] or "-"), ("bad rate", f"{a['bad_rate']:.1%}")])
              + html_table([{"kolom": k, "alasan": v} for k, v in out.items()]), "html")],
            script, choices,
            context=f"Rapat target. Usulan: {tg['column']} {rule} {tg['value']}. "
                    f"Bad rate {a['bad_rate']:.3f}. Split: {sp}. "
                    + (f"Vintage: {tg['vintage']}. Pakai tool show_vintage untuk kurva." if v else ""),
            invite_body="Bening mengusulkan definisi 'bad' dan pembagian sampel.")
        self._update(open_meeting=mt.id)
        return {"meeting_id": mt.id}

    def _do_target_alt(self, m, column: str, rule: str, value: Any) -> dict:
        st = self.state
        st.target.update(column=column, rule=rule, value=value)
        st.save(self.project)
        _DATA.get(self.project.id, {}).update(y=None)
        return self._do_target_acc(m, alt=True)

    def _do_target_acc(self, m, alt: bool = False) -> dict:
        st = self._update(target_approved=True)
        t = st.target
        _DATA.get(self.project.id, {}).update(y=None)
        ddict = DataDictionary.load(self.project)
        rule = {"ge": ">=", "eq": "==", "in": "in"}[t["rule"]]
        ddict.set("bad", description="Bad flag: hasil yang diprediksi scorecard",
                  category="target / performance",
                  logic=f"1 jika {t['column']} {rule} {t['value']}, selain itu 0")
        ddict.save(self.project)
        self.timeline.set("target", "done")
        self._end(m, f"Target di-ACC: bad = {t['column']} {rule} {t['value']}"
                  + (f"; OOT mulai {t['oot_from']}." if t.get("oot_from") else "."),
                  ["Bening: dokumentasikan arti tiap variabel, lalu EDA; Citra mereview, Laras "
                   "mengecek notulen diskusinya.", "Citra: rencana waterfall."])
        self.events.emit("approval", "boss", what="target", decision="accepted")

        def eda():                        # the data described, reviewed and discussed
            self.run_eda()
            return self._work("citra", ["membaca kamus dan EDA dari Bening",
                                        "menyusun rencana waterfall"], self.plan_stage)

        def handover():                   # only after the variables are understood and written down
            self.document_variables()
            self._eda_milestone("in_progress")
            return self._work("bening", ["statistik deskriptif tiap variabel",
                                         "hubungan dengan target dan stabilitas",
                                         "menyusun poin EDA untuk Citra"], eda)
        return self._work("bening", ["membaca arti tiap variabel", "mencocokkan dengan kamus data",
                                     "mendokumentasikan kamus variabel"], handover)
