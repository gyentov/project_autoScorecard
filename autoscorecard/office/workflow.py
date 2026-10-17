"""The project flow, run by the team:

    kickoff (Laras)  ->  data & joins (Bening)  ->  target & split (Bening)
    -> waterfall plan (Citra)  ->  Cut 1  ->  Cut 2 (final)  [-> Cut 3, ... on request]
    -> deck (Kirana, Phase 3)

Every stage does real engine work, then Laras arranges a meeting where the
result is presented (visual-novel script over slides) and the Boss decides
with a choice or by typing. Decisions go to the MOM email and the audit log.
Model builds run in a background thread and stream progress as events.
"""
from __future__ import annotations

import json
import threading
from typing import Any

import pandas as pd

from ..engine import data as D
from ..engine import finalization as fin
from ..engine.dictionary import DataDictionary
from ..engine.pipeline import ScorecardConfig, build_scorecard
from ..engine.waterfall import approve_plan, load_plan, parse_steps, save_plan
from ..engine.waterfall_report import plan_svg
from . import prep
from .agent import AgentReply, AgentRuntime
from .characters import TEAM, line
from .events import EventStream
from .inbox import Inbox
from .meetings import Meetings, cards, choice, html_table, say
from .proposals import Proposals
from .state import OfficeState
from .timeline import STATUS_ICON, STATUS_LABEL, Timeline, gantt_svg

_DATA: dict[str, dict] = {}          # project id -> {"df", "y"} (row-level, never sent to LLMs)
_JOB_LOCK = threading.Lock()


class Office:
    def __init__(self, project, router, settings, inline_jobs: bool = False):
        self.project, self.router, self.settings = project, router, settings
        self.inline_jobs = inline_jobs
        self.events = EventStream(project)
        self.inbox = Inbox(project, self.events)
        self.timeline = Timeline(project, self.events)
        self.proposals = Proposals(project, self.events)
        self.meetings = Meetings(project, self.events, self.inbox)
        self.agents = AgentRuntime(self)
        self.jobs: dict[str, threading.Thread] = {}

    @property
    def state(self) -> OfficeState:
        return OfficeState.load(self.project)

    def _update(self, **kw) -> OfficeState:
        st = self.state
        for k, v in kw.items():
            setattr(st, k, v)
        st.save(self.project)
        return st

    def _status(self, agent: str, status: str, task: str = "") -> None:
        self.events.emit("agent_status", agent, status=status, task=task)

    # ================================================================= data access
    def dataset(self) -> tuple[pd.DataFrame, pd.Series | None]:
        pid = self.project.id
        if pid not in _DATA:
            p = self.project.dir("work") / "dataset.parquet"
            if not p.exists():
                raise FileNotFoundError("dataset belum dibuat (join belum di-ACC)")
            _DATA[pid] = {"df": pd.read_parquet(p), "y": None}
        d = _DATA[pid]
        st = self.state
        if d["y"] is None and st.target:
            d["y"] = D.make_target(d["df"], st.target["column"], st.target["rule"],
                                   st.target["value"])
        return d["df"], d["y"]

    def columns(self) -> list[str]:
        try:
            return list(self.dataset()[0].columns)
        except FileNotFoundError:
            return []

    def train_sample(self) -> tuple[pd.DataFrame, pd.Series]:
        df, y = self.dataset()
        if y is None:
            raise ValueError("target belum ditentukan")
        t = self.state.target
        s = D.split(df, y, date_col=t.get("date_col"), oot_from=t.get("oot_from"))
        return df.loc[s["train"]], y.loc[s["train"]].astype(int)

    def _cut_dir(self, cut: str | None = None):
        st = self.state
        built = [c for c, info in st.cuts.items() if info.get("status") in ("built", "confirmed")]
        cut = cut or (built[-1] if built else None)
        if cut is None:
            raise FileNotFoundError("belum ada cut yang selesai dibangun")
        return self.project.dir("artifacts") / cut

    # ================================================================= tool backends
    def status_summary(self) -> dict:
        st = self.state
        return {"stage": st.stage,
                "timeline": [{"id": m.id, "title": m.title, "status": m.status}
                             for m in self.timeline.items],
                "pending_proposals": [p.summary for p in self.proposals.list("pending")],
                "cuts": {c: {k: v for k, v in i.items() if k in ("status", "gini_oot",
                                                                  "gini_test", "n_features")}
                         for c, i in st.cuts.items()}}

    def tables_summary(self) -> dict:
        tables, dicts = prep.split_uploads(self.project)
        out = {"dictionary_files": [p.name for p in dicts], "tables": {}}
        for n, t in tables.items():
            prof = D.profile(t)[["column", "dtype", "role", "missing_pct", "n_unique"]]
            out["tables"][n] = {"rows": len(t), "columns": prof.round(4).to_dict("records")}
        return out

    def target_summary(self) -> dict:
        st = self.state
        if not st.target:
            return {"target": None}
        df, y = self.dataset()
        dates = df[st.target["date_col"]] if st.target.get("date_col") else None
        a = D.assess_target(y, dates, "Q")
        return {"target": {k: v for k, v in st.target.items() if k != "candidates"}, **a}

    def feature_status(self, feature: str) -> dict:
        j = pd.read_csv(self._cut_dir() / "waterfall_features.csv").set_index("feature")
        if feature not in j.index:
            raise KeyError(f"{feature} bukan kandidat fitur")
        iv = pd.read_csv(self._cut_dir() / "iv_table.csv").set_index("feature")["iv"]
        r = j.loc[feature]
        return {"feature": feature, "status": r["status"], "dropped_at": r["dropped_at"],
                "reason": r["reason"], "iv": None if pd.isna(iv.get(feature)) else
                round(float(iv[feature]), 4), "missing_rate": round(float(r["missing_rate"]), 4)}

    def iv_summary(self) -> list[dict]:
        t = pd.read_csv(self._cut_dir() / "iv_table.csv")
        t = t.sort_values("iv", ascending=False, na_position="last")
        return t[["feature", "iv", "status", "dropped_at"]].fillna("").round(4).to_dict("records")

    def bin_summary(self, feature: str) -> dict:
        sc = json.loads((self._cut_dir() / "scorecard.json").read_text(encoding="utf-8"))
        if feature in sc["binnings"]:
            t = pd.DataFrame(sc["binnings"][feature]["table"])
            pts = pd.read_csv(self._cut_dir() / "scorecard_table.csv")
            pts = pts[pts.feature == feature].set_index("bin")["points"]
            t["points"] = t["bin"].map(pts)
            return {"feature": feature, "in_model": True, "iv": round(float(t["iv"].sum()), 4),
                    "bins": t[["bin", "count", "bad_rate", "woe", "points"]].round(4)
                    .to_dict("records")}
        from ..engine.binning import bin_feature
        X, y = self.train_sample()
        b = bin_feature(X[feature], y, name=feature)
        return {"feature": feature, "in_model": False, "iv": round(b.iv, 4),
                "bins": b.table[["bin", "count", "bad_rate", "woe"]].round(4).to_dict("records")}

    def performance_summary(self) -> dict:
        out = {}
        for c in self.state.cuts:
            p = self.project.dir("artifacts") / c / "performance.json"
            if p.exists():
                out[c] = json.loads(p.read_text(encoding="utf-8"))
        return out

    def dictionary_gaps(self) -> dict:
        df, y = self.dataset()
        t = DataDictionary.load(self.project).build(df)
        gaps = t[(t.description == "(not provided)") | (t.variable_type_source == "guessed")]
        return {"n_gaps": len(gaps), "variables": gaps[["variable", "description",
                                                        "variable_type"]].to_dict("records")}

    def cost_status(self) -> dict:
        b, led = self.settings.budget, self.router.ledger
        return {"spent_total_usd": round(led.total_usd, 4), "budget_total_usd": b.total_usd,
                "spent_project_usd": round(led.project_usd(self.project.id), 4),
                "budget_project_usd": b.per_project_usd, "ask_above_usd": b.ask_above_usd}

    # ================================================================= Boss actions
    def start(self) -> dict:
        """Kickoff: Laras introduces the team and the timeline."""
        st = self.state
        if st.stage != "new":
            return {"meeting_id": st.open_meeting, "note": "sudah dimulai"}
        self.timeline.set("kickoff", "in_progress")
        self._status("laras", "working", "menyiapkan kickoff")
        tables, _ = prep.split_uploads(self.project)
        files = ", ".join(tables) if tables else "belum ada"
        team = html_table([{"nama": c.name, "peran": c.role} for c in TEAM.values()])
        policy = ("<ul><li>Semua hitungan oleh engine; kami menjelaskan dan mengusulkan.</li>"
                  "<li>Setiap perubahan model butuh ACC Boss.</li>"
                  "<li>Sebelum pekerjaan berbayar, saya beri perkiraan biaya (rentang); "
                  "di atas batas, saya tanya dulu.</li>"
                  "<li>Cut 1 lalu Cut 2 (final); bisa diperpanjang kalau Boss belum puas.</li></ul>")
        script = [
            say("laras", "happy", f"{line('greet')} Saya Laras, project lead. Ini tim kita.", 0),
            say("bening", "happy", "Saya Bening, urusan data, join, dan target.", 0),
            say("citra", "neutral", "Saya Citra, modeller. Saya yang membangun scorecard.", 0),
            say("laras", "neutral", "Ini urutan pekerjaan kita. Tidak pakai tanggal, hanya "
                "urutan; yang menunggu Boss akan saya tandai.", 1),
            say("laras", "serious", "Aturan kerja kami ada di slide ini, termasuk soal biaya.", 2),
            say("laras", "neutral", f"Data yang sudah diupload: {files}. Kalau timeline-nya "
                "oke, Bening langsung mulai.", 2),
        ]
        m = self.meetings.create(
            "kickoff", "Kickoff proyek", "laras", ["laras", "bening", "citra"],
            [("Tim", team, "html"), ("Timeline", gantt_svg(self.timeline), "svg"),
             ("Cara kerja", policy, "html")],
            script, [choice("acc", "ACC timeline, mulai", "kickoff_acc")],
            context="Rapat kickoff. Jelaskan timeline, tim, dan aturan biaya.",
            invite_body="Kita sepakati timeline dan cara kerja tim.")
        self._update(stage="kickoff", open_meeting=m.id)
        self.timeline.set("kickoff", "waiting_boss")
        self._status("laras", "waiting_boss", "kickoff")
        return {"meeting_id": m.id}

    def choose(self, meeting_id: str, choice_id: str) -> dict:
        m = self.meetings.get(meeting_id)
        if m.status == "ended":
            raise ValueError("rapat sudah selesai")
        c = next((c for c in m.choices if c["id"] == choice_id), None)
        if c is None:
            raise KeyError(choice_id)
        self.meetings.start(meeting_id)
        m = self.meetings.get(meeting_id)
        self.events.emit("meeting_choice", "boss", meeting_id=meeting_id, choice=c["label"])
        self.project.audit("boss", "meeting_choice", {"meeting": m.title, "choice": c["label"]})
        handler = getattr(self, "_do_" + c["action"])
        return handler(m, **c["payload"]) or {}

    def say(self, text: str, meeting_id: str | None = None, to: str | None = None,
            cost_ok: bool = False) -> dict:
        """The Boss types. In a meeting the answer joins the transcript and any new
        proposal becomes an ACC / reject choice in the same meeting."""
        m = self.meetings.get(meeting_id) if meeting_id else None
        if m is not None:
            self.meetings.start(m.id)
            m = self.meetings.get(m.id)
            self.meetings.add_turn(m, "boss", text)
            context = m.context + "\nSlide: " + ", ".join(s["title"] for s in m.slides)
            history = [{"role": "boss" if t["who"] == "boss" else "agent", "text": t["text"]}
                       for t in m.transcript[:-1]]
            default = m.host if m.host != "laras" else (m.attendees[-1] if m.kind == "results"
                                                        else "laras")
        else:
            context, history, default = "Obrolan di meja kerja.", [], "laras"
            self.events.emit("dialogue", "boss", text=text)
        r: AgentReply = self.agents.respond(text, to, context, history, cost_ok, default)
        if m is not None:
            m = self.meetings.get(m.id)
            self.meetings.add_turn(m, r.agent, r.text, r.mood,
                                   (r.actual or {}).get("actual", 0.0))
            if r.proposals:
                m = self.meetings.get(m.id)
                for pid in r.proposals:
                    p = next(x for x in self.proposals.list() if x.id == pid)
                    m.choices.insert(0, choice(f"acc_{pid}", f"ACC: {p.summary}", "proposal",
                                               pid=pid, accept=True))
                    m.choices.insert(1, choice(f"rej_{pid}", f"Tolak: {p.summary}", "proposal",
                                               pid=pid, accept=False))
                self.meetings.save(m)
        else:
            self.events.emit("dialogue", r.agent, mood=r.mood, text=r.text)
        return r.to_dict()

    def decide_proposal(self, pid: str, accept: bool) -> dict:
        p = self.proposals.decide(pid, accept)
        return {"proposal": p.id, "status": p.status, "summary": p.summary}

    # ================================================================= stage handlers
    def _end(self, m, decision: str, next_steps: list[str]) -> None:
        self.meetings.end(m, decision, next_steps, self._timeline_md())
        self._update(open_meeting=None)

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
        self.meetings.add_turn(m, "citra", f"Dicatat, Boss: usulan {verdict}. "
                               + ("Berlaku di cut berikutnya." if accept else ""),
                               "happy" if accept else "neutral")
        return res

    def _do_kickoff_acc(self, m) -> dict:
        self.timeline.set("kickoff", "done")
        self._end(m, "Timeline dan cara kerja disetujui (ACC).",
                  ["Bening: profiling data dan usulan join."])
        self.events.emit("approval", "boss", what="timeline", decision="accepted")
        return self.data_stage()

    def data_stage(self) -> dict:
        self._update(stage="data")
        self.timeline.set("data", "in_progress")
        self.timeline.set("dictionary", "in_progress")
        self._status("bening", "working", "profiling tabel dan mencari kunci join")
        tables, dicts = prep.split_uploads(self.project)
        if not tables:
            self.timeline.set("data", "waiting_boss", "menunggu upload data")
            self._status("bening", "waiting_boss", "menunggu data")
            self.inbox.send("bening", "Data belum ada", "Halo Boss, saya belum menemukan tabel "
                            "di folder upload. Mohon upload datanya (CSV/Excel/Parquet), lalu "
                            "beri tahu saya.\n\nBening", "memo")
            return {"waiting": "upload"}
        plan = prep.propose_join_plan(tables)
        self._update(join_plan=plan, join_approved=False)
        rows = [{"tabel": n, "baris": len(t), "kolom": t.shape[1],
                 "peran": "dasar" if n == plan["base"] else "join"} for n, t in tables.items()]
        jrows = [{"tabel": j["right"], "kunci": f"{j['left_on']} = {j['right_on']}",
                  "relasi": j["relationship"], "cocok": f"{j['coverage']:.1%}",
                  "awalan": j["prefix"]} for j in plan["joins"]]
        many = [j["right"] for j in plan["joins"] if j["relationship"].endswith("many")]
        script = [
            say("bening", "happy", f"Boss, ada {len(tables)} tabel. Saya pakai "
                f"'{plan['base']}' sebagai tabel dasar.", 0),
            say("bening", "thinking", "Ini usulan join-nya. Join selalu left join, jumlah "
                "baris tabel dasar tidak pernah bertambah.", 1),
        ]
        if many:
            script.append(say("bening", "serious", f"Tabel {', '.join(many)} punya banyak baris "
                              "per kunci, jadi saya ringkas dulu (jumlah, rata-rata, maks, "
                              "min, total).", 1))
        if plan["skipped"]:
            script.append(say("bening", "worried", "Ada tabel yang tidak bisa saya join: "
                              + ", ".join(s["table"] for s in plan["skipped"]), 1))
        if dicts:
            script.append(say("bening", "excited", f"Kamus data ditemukan: "
                              f"{', '.join(p.name for p in dicts)}. Akan saya pakai.", 0))
        self._status("bening", "waiting_boss", "rapat data")
        self.timeline.set("data", "waiting_boss")
        mt = self.meetings.create(
            "data", "Rapat data & join", "bening", ["laras", "bening"],
            [("Tabel", html_table(rows), "html"), ("Rencana join", html_table(jrows), "html")],
            script, [choice("acc", "ACC rencana join", "data_acc")],
            context=f"Rapat data. Rencana join: {json.dumps(plan, default=str)[:1500]}",
            invite_body="Bening mengusulkan cara menggabungkan tabel.")
        self._update(open_meeting=mt.id)
        return {"meeting_id": mt.id}

    def _do_data_acc(self, m) -> dict:
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

    def target_stage(self) -> dict:
        self._update(stage="target")
        self.timeline.set("target", "in_progress")
        self._status("bening", "working", "mencari definisi target")
        df, _ = self.dataset()
        st = self.state
        tg = prep.propose_target(df, st.join_plan)
        y = D.make_target(df, tg["column"], tg["rule"], tg["value"])
        sp = prep.propose_split(df, y)
        target = {**tg, **sp}
        self._update(target=target, target_approved=False)
        a = D.assess_target(y, df[sp["date_col"]] if sp["date_col"] else None, "Q")
        feats, out = prep.feature_list(df, target, st.exclude)
        rule = {"ge": ">=", "eq": "==", "in": "in"}[tg["rule"]]
        cand_rows = [{"kolom": c["column"], "jenis": c["kind"], "usulan": c["suggestion"]}
                     for c in tg["candidates"]]
        per = a.get("by_period", [])
        script = [
            say("bening", "thinking", f"Usulan target: bad = {tg['column']} {rule} "
                f"{tg['value']}.", 0),
            say("bening", "neutral", f"Bad rate {a['bad_rate']:.1%} ({a['bads']:,} bad dari "
                f"{a['n']:,}).", 1),
        ]
        if a["warnings"]:
            script.append(say("bening", "worried", "Catatan: " + "; ".join(a["warnings"]), 1))
        if sp["date_col"]:
            script.append(say("bening", "neutral", f"Sampel out-of-time mulai {sp['oot_from']} "
                              f"({sp['oot_share']:.0%} data). Sisanya dibagi train/test "
                              "70/30.", 2))
        script.append(say("bening", "serious", f"{len(feats)} kolom jadi kandidat fitur; "
                          f"{len(out)} tidak dipakai (id, tanggal, data hasil).", 2))
        choices = [choice("acc", "ACC target & split", "target_acc")]
        alts = [c for c in tg["candidates"][1:] if c["score"] > 0][:2]   # outcome-like only
        for k, c in enumerate(alts, 1):
            alt_rule, alt_val = ("eq", c["values"][-1]) if c["kind"] == "binary" else ("ge", 90)
            if c["kind"].startswith(("binary", "numeric")):
                choices.append(choice(f"alt{k}", f"Pakai {c['column']} sebagai target",
                                      "target_alt", column=c["column"], rule=alt_rule,
                                      value=alt_val))
        self.timeline.set("target", "waiting_boss")
        self._status("bening", "waiting_boss", "rapat target")
        mt = self.meetings.create(
            "target", "Rapat definisi target", "bening", ["laras", "bening", "citra"],
            [("Kandidat target", html_table(cand_rows), "html"),
             ("Bad rate per kuartal", html_table(
                 [{"periode": r["period"], "n": r["n"], "bad rate": f"{r['bad_rate']:.2%}"}
                  for r in per]), "html"),
             ("Split & kandidat fitur", cards([
                 ("kandidat fitur", str(len(feats))),
                 ("OOT mulai", sp["oot_from"] or "-"), ("bad rate", f"{a['bad_rate']:.1%}")])
              + html_table([{"kolom": k, "alasan": v} for k, v in out.items()]), "html")],
            script, choices,
            context=f"Rapat target. Usulan: {tg['column']} {rule} {tg['value']}. "
                    f"Bad rate {a['bad_rate']:.3f}. Split: {sp}.",
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
                  ["Bening: cek kelengkapan kamus data.", "Citra: rencana waterfall."])
        self.events.emit("approval", "boss", what="target", decision="accepted")
        df, y = self.dataset()
        feats, _ = prep.feature_list(df, t, st.exclude)
        self.events.emit("handoff", "bening", to="citra",
                         memo=f"Dataset siap: {len(df):,} baris, {len(feats)} kandidat fitur.")
        self.inbox.send("bening", "Serah terima ke Citra", f"Citra, dataset siap: {len(df):,} "
                        f"baris, {len(feats)} kandidat fitur, target {t['column']} {rule} "
                        f"{t['value']}.\n\ncc: Boss\n\nBening", "memo")
        self._dictionary_memo()
        return self.plan_stage()

    def _dictionary_memo(self) -> None:
        g = self.dictionary_gaps()
        if g["n_gaps"] == 0:
            self.timeline.set("dictionary", "done")
            return
        names = "\n".join(f"- {v['variable']}: {v['description']} / {v['variable_type']}"
                          for v in g["variables"][:40])
        self.inbox.send("bening", f"Kamus data: {g['n_gaps']} variabel belum lengkap",
                        "Boss, variabel berikut belum punya deskripsi atau tipenya masih "
                        f"tebakan:\n\n{names}\n\nBisa upload kamus data (kolom: variable, "
                        "description, category, logic) kapan saja; tidak menghambat "
                        "modelling, tapi dokumentasi jadi lengkap.\n\nBening", "memo")
        self.timeline.set("dictionary", "waiting_boss", f"{g['n_gaps']} variabel belum lengkap")

    def plan_stage(self) -> dict:
        self._update(stage="plan")
        self.timeline.set("waterfall_plan", "in_progress")
        self._status("citra", "working", "menyusun rencana waterfall")
        plan = load_plan(self.project, self.settings.modelling.waterfall)
        steps = parse_steps(plan["steps"])
        save_plan(self.project, steps, by="citra")
        rows = [{"#": k + 1, "langkah": s.title, "fitur lolos jika": s.criterion}
                for k, s in enumerate([s for s in steps if s.enabled])]
        script = [
            say("citra", "neutral", "Boss, ini urutan seleksi fitur yang saya usulkan.", 0),
            say("citra", "thinking", "Filter data dulu (missing, identik, IV), lalu klaster "
                "korelasi, lalu stepwise, forward, backward, dan finalisasi.", 1),
            say("citra", "serious", "Model tidak saya bangun sebelum rencana ini di-ACC.", 1),
        ]
        self.timeline.set("waterfall_plan", "waiting_boss")
        self._status("citra", "waiting_boss", "rapat rencana waterfall")
        mt = self.meetings.create(
            "plan", "Rapat rencana waterfall", "citra", ["laras", "citra"],
            [("Rencana", plan_svg(steps), "svg"), ("Kriteria", html_table(rows), "html")],
            script, [choice("acc", "ACC rencana, bangun Cut 1", "plan_acc")],
            context="Rapat rencana waterfall: " + "; ".join(
                f"{s.title}: {s.criterion}" for s in steps if s.enabled),
            invite_body="Citra menjelaskan langkah seleksi fitur sebelum membangun model.")
        self._update(open_meeting=mt.id)
        return {"meeting_id": mt.id}

    def _do_plan_acc(self, m) -> dict:
        approve_plan(self.project, by="boss")
        self.timeline.set("waterfall_plan", "done")
        self._end(m, "Rencana waterfall di-ACC.", ["Citra: membangun Cut 1."])
        self.events.emit("approval", "boss", what="waterfall_plan", decision="accepted")
        return self.build_cut("cut1")

    # ================================================================= cuts
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

                cfg = ScorecardConfig(date_col=st.target.get("date_col"),
                                      oot_from=st.target.get("oot_from"),
                                      outliers=self.settings.modelling.outliers)
                res = build_scorecard(df, y, feats, cfg, project=self.project,
                                      require_approved=True, progress=progress, snapshot=cut)
                perf = res.performance
                info = {"status": "built", "build_id": res.build_id,
                        "built_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
                        "n_features": len(res.scorecard.features),
                        "features": res.scorecard.features,
                        "gini_train": perf["train"]["gini"], "gini_test": perf["test"]["gini"],
                        "gini_oot": perf.get("oot", {}).get("gini"),
                        "psi_oot": res.score_psi.get("oot"), "psi_test": res.score_psi.get("test")}
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

        perf_rows = [{"sampel": k, "n": v["n"], "bad rate": f"{v['bad_rate']:.1%}",
                      "Gini": f"{v['gini']:.3f}", "KS": f"{v['ks']:.3f}"}
                     for k, v in res.performance.items()]
        forced = wf.forced
        slides = [
            ("Ringkasan", cards([("fitur", f"{n_sel} dari {n_in}"),
                                 ("Gini test", f"{g_test:.3f}"),
                                 ("Gini OOT", f"{g_oot:.3f}" if g_oot is not None else "-"),
                                 ("PSI", f"{psi:.3f}" if psi is not None else "-")]), "html"),
            ("Waterfall", (art / "waterfall.svg").read_text(encoding="utf-8"), "svg"),
            ("Performa", html_table(perf_rows), "html"),
            ("Korelasi WoE", (art / "woe_correlation.svg").read_text(encoding="utf-8"), "svg"),
            ("Timeline", gantt_svg(self.timeline), "svg"),
        ]
        script = [
            say("laras", "happy", f"Boss, hasil {title} sudah siap. Citra jelaskan singkat ya.", 0),
            say("citra", "neutral", f"Dari {n_in} kandidat, {n_sel} fitur masuk model.", 1),
            say("citra", mood, f"Gini test {g_test:.3f}"
                + (f", OOT {g_oot:.3f}" if g_oot is not None else "")
                + (f"; PSI {psi:.3f}" if psi is not None else "") + ".", 2),
            say("citra", "thinking" if max_r < 0.5 else "worried",
                f"Korelasi WoE tertinggi antar fitur model {max_r:.2f}.", 3),
        ]
        if forced:
            kept = [r["feature"] for r in forced if r["result"] == "kept"]
            script.append(say("citra", "serious", f"{len(forced)} fitur dipaksa dicoba; "
                              f"{len(kept)} bertahan" + (f" ({', '.join(kept)})" if kept else "")
                              + ". Detailnya di finalization review.", 1))
        if compare:
            script.append(say("citra", "neutral", compare, 2))
        if final:
            script.append(say("laras", "serious", "Ini cut final. Kalau Boss puas, konfirmasi; "
                              "kalau belum, ketik masukan dan pilih perpanjang.", 4))
            choices = [choice("confirm", "Konfirmasi model final", "confirm_final"),
                       choice("extend", "Perpanjang ke cut berikutnya (ketik masukan dulu)",
                              "extend")]
        else:
            script.append(say("laras", "neutral", "Silakan beri masukan dengan mengetik; "
                              "usulan yang Boss ACC dipakai di cut berikutnya.", 4))
            nxt = f"cut{int(cut[3:]) + 1}"
            choices = [choice("next", f"Lanjut ke {nxt.replace('cut', 'Cut ')}", "next_cut")]

        body = (f"Boss,\n\n{title} selesai: {n_sel} fitur, Gini test {g_test:.3f}"
                + (f", OOT {g_oot:.3f}" if g_oot is not None else "") + ".\n\n"
                + (compare + "\n\n" if compare else "")
                + "Laporan lengkap terlampir (waterfall report, finalization review, kamus "
                "data). Laras akan mengatur rapat untuk presentasi singkat.\n\nCitra")
        attach = [rel(n) for n in ("waterfall_report.md", "finalization_review.md",
                                   "data_dictionary.md", "scorecard_table.csv")]
        self.inbox.send("citra", f"Hasil {title}", body, "results", attachments=attach)
        self.timeline.set(cut, "waiting_boss")
        self._status("citra", "waiting_boss", f"rapat hasil {title}")
        mt = self.meetings.create(
            "results", f"Presentasi hasil {title}", "laras", ["laras", "citra", "bening"],
            slides, script, choices, cut=cut,
            context=(f"Presentasi {title}. Fitur model: {', '.join(info['features'])}. "
                     f"Gini train {info['gini_train']:.3f}, test {g_test:.3f}, OOT {g_oot}. "
                     f"PSI {psi}. {compare} Pakai tool untuk detail."),
            invite_body=f"Presentasi singkat hasil {title}.", attachments=attach)
        self._update(open_meeting=mt.id)

    def _accepted_since(self, ts: str | None) -> list[str]:
        return [p.summary for p in self.proposals.list("accepted")
                if ts is None or (p.decided or "") >= ts]

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
        new = self.timeline.add_extension("; ".join(fb)[:200])
        self._end(m, f"Diperpanjang ke {new.title}. Masukan Boss: " + " / ".join(fb),
                  ["Citra: membangun " + new.title + "."])
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
        self.timeline.set("deck", "not_started", "Kirana mulai di Phase 3")
        self._end(m, f"Model {m.cut.replace('cut', 'Cut ')} dikonfirmasi final "
                  f"(build {info['build_id']}).",
                  ["Kirana: deck (HTML draft dulu, lalu PDF/PPTX setelah ACC). Tersedia di "
                   "Phase 3."])
        self.events.emit("approval", "boss", what="final_model", id=info["build_id"],
                         decision="accepted")
        self._status("citra", "idle")
        return {"confirmed": info["build_id"]}

    # ================================================================= briefing
    def briefing(self) -> list[dict]:
        """Laras's morning briefing ($0): what's done, what waits for the Boss."""
        tl = self.timeline
        done = [m.title for m in tl.items if m.status == "done"]
        waiting = [m.title for m in tl.items if m.status == "waiting_boss"]
        doing = [m.title for m in tl.items if m.status == "in_progress"]
        unread = [e for e in self.inbox.list() if not e.read]
        pend = self.proposals.list("pending")
        lines = [say("laras", "happy", line("greet"))]
        if done:
            lines.append(say("laras", "neutral", f"Sudah selesai: {', '.join(done)}."))
        if doing:
            lines.append(say("laras", "neutral", f"Sedang dikerjakan: {', '.join(doing)}."))
        if waiting:
            lines.append(say("laras", "serious", f"Menunggu Boss: {', '.join(waiting)}."))
        if pend:
            lines.append(say("laras", "serious", f"{len(pend)} usulan menunggu ACC."))
        if unread:
            lines.append(say("laras", "neutral", f"Ada {len(unread)} email belum dibaca."))
        return lines
