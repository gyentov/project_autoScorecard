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

import html
import json
import threading
import time
from typing import Any

import pandas as pd

from ..engine import data as D
from ..engine import finalization as fin
from ..engine import risk as R
from ..engine import vintage as V
from ..engine.dictionary import DataDictionary
from ..engine.pipeline import ScorecardConfig, build_scorecard
from ..engine.waterfall import approve_plan, load_plan, parse_steps, save_plan
from ..engine.waterfall_report import plan_svg
from . import prep
from .agent import AgentReply, AgentRuntime
from .board import Board
from .characters import CANDID, TEAM, line
from .conversations import ConversationLog
from .events import EventStream
from .inbox import Inbox
import re

from . import deliverables as DELIV
from . import meaning as MEAN
from .orders import Orders, is_order
from .meetings import (Meetings, _page, cards, choice, html_table, light_only, mom_markdown,
                       say)
from .proposals import Proposals
from .summary import summarize
from .state import OfficeState
from .timeline import STATUS_ICON, STATUS_LABEL, Timeline, gantt_svg

ACTION_LABEL = {"document_variables": "mendokumentasikan arti variabel",
                "feature_meanings": "memasangkan kamus ke fitur model",
                "make_deck": "menyusun draft deck",
                "make_documentation": "menyusun dokumentasi model"}

_DATA: dict[str, dict] = {}          # project id -> {"df", "y"} (row-level, never sent to LLMs)
_JOB_LOCK = threading.Lock()


def plan_diff(old, new) -> list[str]:
    """What changed between two waterfall plans, in words: order, on/off, parameters."""
    out = []
    o = {s.type: s for s in old}
    n = {s.type: s for s in new}
    for t in n.keys() - o.keys():
        out.append(f"{n[t].title} ditambahkan")
    for t in o.keys() - n.keys():
        out.append(f"{o[t].title} dihapus")
    for t in [s.type for s in new if s.type in o]:
        a, b = o[t], n[t]
        if a.enabled != b.enabled:
            out.append(f"{b.title} {'diaktifkan' if b.enabled else 'dimatikan'}")
        diffs = [f"{k} {a.params.get(k)} → {v}" for k, v in b.params.items() if a.params.get(k) != v]
        if diffs:
            out.append(f"{b.title}: " + ", ".join(diffs))
    on_old = [s.type for s in old if s.enabled and s.type in n]
    on_new = [s.type for s in new if s.enabled and s.type in o]
    if [t for t in on_old if t in on_new] != [t for t in on_new if t in on_old]:
        out.append("urutan: " + " → ".join(n[t].title for t in [s.type for s in new if s.enabled]))
    return out


def json_safe(obj):
    """NaN / numpy values out, so the state always serialises (the API sends it)."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and obj != obj:
        return None
    return obj


def adjourn_choice() -> dict:
    """Every decision meeting can end without ACC (the Boss is never forced)."""
    return choice("adjourn", "Belum ACC: bubarkan rapat, kerjakan masukan saya dulu", "adjourn")


class Office:
    def __init__(self, project, router, settings, inline_jobs: bool = False):
        self.project, self.router, self.settings = project, router, settings
        self.inline_jobs = inline_jobs
        self.events = EventStream(project)
        self.inbox = Inbox(project, self.events)
        self.timeline = Timeline(project, self.events)
        self.proposals = Proposals(project, self.events)
        self.meetings = Meetings(project, self.events, self.inbox)
        self.board = Board(project, self.events, self.meetings)
        self.agents = AgentRuntime(self)
        self.conversations = ConversationLog(project)
        self.orders = Orders(project, self.events)
        self.orders.on_done = self._order_done
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
                "timeline": [{"id": m.id, "title": m.title, "owner": m.owner, "status": m.status,
                              "note": m.note, "depends_on": m.depends_on}
                             for m in self.timeline.items],
                "open_orders": [{"id": o["id"], "to": o["to"], "summary": o["summary"],
                                 "status": o["status"]} for o in self.orders.open_for()],
                "open_meeting": st.open_meeting,
                "pending_proposals": [p.summary for p in self.proposals.list("pending")],
                "cuts": {c: {k: v for k, v in i.items() if k in ("status", "gini_oot",
                                                                  "gini_test", "n_features")}
                         for c, i in st.cuts.items()}}

    def team_work(self, agent: str | None = None, order: int | None = None) -> dict:
        """What a teammate is working on: her open tasks with the latest notes, what she
        finished last, the relays she is a step of, and her milestones on the timeline."""
        def row(o: dict) -> dict:
            r = {"id": o["id"], "to": TEAM[o["to"]].name, "from": TEAM[o["via"]].name
                 if o.get("via") in TEAM else o.get("via"), "summary": o["summary"],
                 "status": o["status"], "notes": [n["note"] for n in o["notes"]][-3:]}
            if o.get("chain"):
                k = o.get("step", 0)
                r["relay"] = [f"{i + 1}. {TEAM[c['to']].name}: {c['task']}"
                              + (" (selesai)" if i < k or o["status"] == "selesai" else
                                 " (sekarang)" if i == k else "") for i, c in enumerate(o["chain"])]
            if o.get("parent"):
                r["step_of_relay"] = o["parent"]
            return r
        items = self.orders.list()
        if order is not None:
            o = next((x for x in items if x["id"] == int(order)), None)
            if o is None:
                raise KeyError(f"perintah/tugas #{order} tidak ada")
            out = row(o)
            if o.get("children"):
                out["steps"] = [row(x) for x in items if x["id"] in o["children"]]
            return out
        if agent is not None and agent not in TEAM:
            raise ValueError(f"tidak ada anggota tim '{agent}'")
        who = [agent] if agent else list(TEAM)
        out = {}
        for a in who:
            mine = [x for x in items if x["to"] == a]
            out[TEAM[a].name] = {
                "open": [row(x) for x in mine if x["status"] != "selesai"],
                "recently_done": [row(x) for x in mine if x["status"] == "selesai"][-3:],
                "milestones": [{"id": m.id, "title": m.title, "status": m.status, "note": m.note}
                               for m in self.timeline.items if m.owner == a and m.status != "done"]}
        return out

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
        tables, _ = prep.split_uploads(self.project)
        if st.data_files is None or not tables:          # Laras confirms the data first
            files = [p.name for p in self.project.raw_files()]
            note = (f"Boss, di proyek ini sudah ada {len(files)} file data. Sebelum kickoff, "
                    "tolong konfirmasi dulu file mana yang dipakai." if files else
                    "Boss, datanya belum ada. Upload dulu ya, baru kita mulai kickoff.")
            return {"needs_data": True, "note": note}
        return self._kickoff_meeting()

    def _kickoff_meeting(self) -> dict:
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
            say("kirana", "excited", "Saya Kirana, pembuat presentasi. Hasil akhirnya nanti "
                "saya susun jadi deck yang enak dibaca, Boss.", 0),
            say("laras", "neutral", "Ini urutan pekerjaan kita. Tidak pakai tanggal, hanya "
                "urutan; yang menunggu Boss akan saya tandai.", 1),
            say("laras", "serious", "Aturan kerja kami ada di slide ini, termasuk soal biaya.", 2),
            say("laras", "neutral", f"Data yang sudah diupload: {files}. Kalau timeline-nya "
                "oke, Bening langsung mulai.", 2),
        ]
        m = self.meetings.create(
            "kickoff", "Kickoff proyek", "laras", ["laras", "bening", "citra", "kirana"],
            [("Tim", team, "html"), ("Timeline", gantt_svg(self.timeline), "svg"),
             ("Cara kerja", policy, "html")],
            script, [choice("acc", "ACC timeline, mulai", "kickoff_acc"), adjourn_choice()],
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

    # ================================================================= data handover
    DATA_OPEN = ("new", "kickoff")          # after this the data feeds the model: locked

    def _data_open(self) -> bool:            # also open while Bening still waits for data
        st = self.state
        return st.stage in self.DATA_OPEN or (st.stage == "data" and st.join_plan is None)

    def default_files(self) -> set[str]:
        """Files that came with the demo (synthetic), not from the Boss."""
        p = self.project.dir("work") / "default_files.json"
        if p.exists():
            return set(json.loads(p.read_text(encoding="utf-8")))
        if self.project.meta.name.startswith("Demo"):       # demo projects made before the list
            from ..demo_data import make_demo_data
            return {f"{n}.csv" for n in make_demo_data(n=10)} | {"data_dictionary.csv"}
        return set()

    def remove_data(self, files: list[str]) -> dict:
        """Take files out of the project before the data stage: moved to work/removed/
        (recoverable), and dropped from the Boss's selection."""
        if not self._data_open():
            raise ValueError("data sudah dipakai sejak tahap data; tidak bisa dihapus di proyek ini")
        names = {p.name: p for p in self.project.raw_files()}
        missing = [f for f in files if f not in names]
        if missing:
            raise KeyError(", ".join(missing))
        dest = self.project.dir("work") / "removed"
        dest.mkdir(exist_ok=True)
        for f in files:
            names[f].replace(dest / f)
        st = self.state
        if st.data_files is not None:
            self._update(data_files=[f for f in st.data_files if f not in files] or None)
        self.project.audit("boss", "data_removed", {"files": files, "to": "work/removed"})
        return {"removed": files}

    def data_files(self) -> dict:
        """The raw/ files with their shape (counts only, never rows) and whether chosen."""
        chosen = self.state.data_files
        defaults = self.default_files()
        cache_p = self.project.dir("work") / "data_shapes.json"     # read each file once
        cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
        out = []
        for p in self.project.raw_files():
            st_ = p.stat()
            item = {"name": p.name, "bytes": st_.st_size,
                    "kind": "kamus" if prep.is_dictionary(p) else "tabel",
                    "selected": chosen is None or p.name in chosen,
                    "default": p.name in defaults}
            key = f"{p.name}|{st_.st_size}|{int(st_.st_mtime)}"
            if key not in cache:
                try:
                    t = D.load_table(p)
                    cache[key] = {"rows": len(t), "cols": len(t.columns)}
                except Exception as e:                   # unreadable: shown, not usable
                    cache[key] = {"rows": None, "cols": None, "error": str(e)[:200]}
            item.update(cache[key])
            out.append(item)
        cache_p.write_text(json.dumps(cache), encoding="utf-8")
        return {"files": out, "locked": not self._data_open(),
                "confirmed": self.state.data_files is not None,
                "stage": self.state.stage}

    def hand_over_data(self, files: list[str]) -> dict:
        """The Boss gives Laras the files to use. Laras thanks the Boss, says what she
        got, and passes it to Bening ($0)."""
        if not self._data_open():
            raise ValueError("data sudah dipakai sejak tahap data; untuk data lain, "
                             "buat proyek baru")
        names = {p.name for p in self.project.raw_files()}
        unknown = [f for f in files if f not in names]
        if unknown:
            raise KeyError(", ".join(unknown))
        if not files:
            raise ValueError("pilih minimal satu file")
        self._update(data_files=sorted(files))
        tables = [f for f in files if not prep.is_dictionary(self.project.dir("raw") / f)]
        dicts = [f for f in files if f not in tables]
        self.project.audit("boss", "data_handover", {"files": sorted(files)})
        self.events.emit("data_received", "laras", files=sorted(files))
        text = (f"Terima kasih, Boss. Saya terima {len(tables)} tabel"
                + (f" ({', '.join(tables)})" if tables else "")
                + (f" dan {len(dicts)} kamus data" if dicts else ", tanpa kamus data")
                + ". Saya teruskan ke Bening.")
        if self.state.stage == "new":
            text += " Kalau sudah siap, kita mulai kickoff."
        self.events.emit("dialogue", "laras", mood="happy", text=text)
        self.events.emit("handoff", "laras", to="bening", memo="data dari Boss")
        self.conversations.add(where="desk", boss="(menyerahkan data: " + ", ".join(sorted(files))
                               + ")", to="laras", agent="laras", text=text, mood="happy",
                               usd=0.0, estimate_high_usd=None, provider="", kind="handover",
                               thought="")
        if self.state.stage == "data":           # Bening was waiting for exactly this
            self._work("bening", ["membaca data dari Boss", "mencoba kunci join",
                                  "menggabungkan tabel"], self.data_stage)
        return {"files": sorted(files), "text": text}

    # ================================================================= messages ("titip pesan")
    def relay_message(self, via: str, to: str, text: str, kind: str = "pesan") -> dict:
        """A note from the Boss left with `via` for `to` (or a task Laras hands out after a
        meeting): `via` walks it over, `to` confirms she got it, the Boss gets a memo, and
        the note stays in `to`'s working context for her next answers."""
        gist = summarize(self, [text], literal=True)
        msg = self.orders.add(text, to, via=via, kind=kind, summary=gist)
        self.project.audit("boss" if kind == "pesan" else via, f"{kind}_relayed", msg)
        if via != to:
            self.events.emit("handoff", via, to=to, memo=f"{kind} dari Boss")
        what = "pesan Boss" if kind == "pesan" else "tugas dari rapat"
        ack = (f"Siap, {what} sudah saya terima" + (f" dari {TEAM[via].name}" if via != to else "")
               + f": {gist[0].lower()}{gist[1:]} Saya kerjakan.")
        self.events.emit("dialogue", to, mood="happy", text=ack)
        self.inbox.send(to, f"{what.capitalize()} diterima", f"Boss,\n\n{ack}\n\n{TEAM[to].name}",
                        "memo")
        if kind == "tugas" or is_order(text):           # a task is worked on, not just filed
            self._dispatch(msg)
        return msg

    # ================================================================= teamwork
    def _dispatch(self, o: dict) -> None:
        """Get an order done: what the office can do runs now (or is retried when it can);
        anything else the owner works on at her desk with her tools."""
        action = o.get("action")
        if action is None:
            cap = self._capability(o["text"])
            if cap and cap[1] == o["to"]:
                action = cap[0]
                self.orders.set_fields(o["id"], action=action)
        if action:
            o = self.orders.get(o["id"])
            self._status(o["to"], "working", ACTION_LABEL.get(action, "mengerjakan tugas"))
            try:
                done = self.run_order(o)
                self.orders.update(o["id"], "selesai", done)
            except ValueError as e:
                self.orders.update(o["id"], "terhambat", str(e))
            finally:
                self._status(o["to"], "idle", "")
            return
        self.orders.update(o["id"], "dikerjakan", f"diterima {TEAM[o['to']].name}")

        def work():
            try:
                self.agents.work_on(self.orders.get(o["id"]))
            except Exception as e:                          # never hang silently
                self.orders.update(o["id"], "terhambat", f"kendala: {e}")
        if self.inline_jobs:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    def start_relay(self, steps: list[dict], via: str = "laras", text: str = "") -> dict:
        """Several teammates in turn: each step starts when the one before is done, and
        Laras tells the Boss at every step."""
        chain = []
        for st_ in steps:
            to = st_["to"]
            if to not in TEAM:
                raise ValueError(f"tidak ada anggota tim '{to}'")
            action = st_.get("action") or self._step_action(to, st_["task"], chain)
            prev = TEAM[chain[-1]["to"]].name if chain else ""
            chain.append({"to": to, "task": self._step_task(st_["task"], action, prev), "action": action})
        plan = " → ".join(f"{TEAM[c['to']].name}: {c['task']}" for c in chain)
        parent = self.orders.add(text or plan, "laras", via=via, kind="alur",
                                 summary=f"Alur: {plan}"[:240], chain=chain, step=0)
        self.orders.update(parent["id"], "dikerjakan", "alur dimulai")
        self.inbox.send("laras", f"Alur kerja #{parent['id']} dimulai", "Boss,\n\nAlurnya saya atur "
                        "begini:\n\n" + "\n".join(f"{k + 1}. {TEAM[c['to']].name}: {c['task']}"
                                                   for k, c in enumerate(chain))
                        + "\n\nSaya laporkan setiap langkah selesai.\n\nLaras", "memo")
        self._start_step(parent["id"], 0)
        return self.orders.get(parent["id"])

    STEP_TASK = {"document_variables": "dokumentasikan arti variabel dan tabel (kamus)",
                 "feature_meanings": "pasangkan kamus ke fitur terpilih di model",
                 "make_deck": "susun draft deck",
                 "make_documentation": "susun dokumentasi model"}

    def _step_task(self, seg: str, action: str | None, prev: str = "") -> str:
        """The Boss's words for one step, without the routing ('suruh Bening', 'kasih ke
        Citra'): the standard task when the office knows it, plus any detail he gave."""
        t = re.sub(r"\b(?:kasih|serahkan|berikan|kirim\w*|teruskan|oper)\s+(?:ke\s+)?"
                   r"(?:laras|bening|citra|kirana)\w*", " ", seg, flags=re.I)
        t = re.sub(r"\b(?:suruh|minta|tolong)\s+(?:laras|bening|citra|kirana)\w*|"
                   r"\b(?:laras|bening|citra|kirana)\w*", " ", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip(" ,.;")
        t = re.sub(r"^(?:untuk|buat|agar|supaya|dan|di|ke)\s+|\s+(?:kerjakan|segera)$", "", t, flags=re.I).strip(" ,.")
        if action in self.STEP_TASK:
            std = self.STEP_TASK[action]
            extra = [w for w in t.split() if w.lower() not in std and w.lower() not in
                     ("kamus", "data", "deck", "dokumentasikan", "dipasangkan", "fitur", "terpilih",
                      "draft", "bisa", "dibuat", "buat", "bikin", "susun", "jadi")]
            return std + (f" ({t})" if len(extra) >= 2 else "")
        if prev and re.search(r"\b(dengan|sama|ke)$", t):     # "align dengan" -> "align dengan Bening"
            t = f"{t} {prev}"
        if len(t.split()) < 2:                   # "kerjakan": the gist is the whole order
            return "kerjakan bagiannya dari arahan Boss"
        return t

    def _step_action(self, to: str, task: str, before: list[dict]) -> str | None:
        cap = self._capability(task)
        if cap and cap[1] == to:
            return cap[0]
        if to == "kirana":                       # what Kirana makes is the deck
            return "make_deck"
        if to == "citra" and any(c["action"] == "document_variables" for c in before):
            return "feature_meanings"            # the dictionary, matched to the model
        if to == "bening" and re.search(r"kamus|arti|makna|variabel", task, re.I):
            return "document_variables"
        return None

    def _start_step(self, pid: int, k: int) -> None:
        parent = self.orders.get(pid)
        chain = parent["chain"]
        c = chain[k]
        prev = self.orders.get(parent["children"][-1]) if parent.get("children") else None
        ctx = (f"arahan Boss: “{parent['text']}”; langkah {k + 1} dari {len(chain)} alur #{pid} ("
               + " → ".join(f"{TEAM[x['to']].name}: {x['task']}" for x in chain) + ")"
               + (f"; hasil {TEAM[prev['to']].name}: {prev['notes'][-1]['note']}"
                  if prev and prev["notes"] else ""))
        child = self.orders.add(c["task"], c["to"], via="laras", kind="tugas",
                                summary=c["task"][:160], parent=pid, context=ctx,
                                **({"action": c["action"]} if c["action"] else {}))
        self.orders.set_fields(pid, step=k, children=parent.get("children", []) + [child["id"]])
        if k:
            self.events.emit("handoff", chain[k - 1]["to"], to=c["to"], memo=f"alur #{pid}")
        self._dispatch(child)

    def _order_done(self, o: dict) -> None:
        """A step of a relay is done: report to the Boss, start the next one."""
        pid = o.get("parent")
        if not pid:
            return
        parent = self.orders.get(pid)
        if parent["status"] == "selesai":
            return
        k, chain = parent.get("step", 0), parent["chain"]
        note = o["notes"][-1]["note"] if o["notes"] else "selesai"
        last = k + 1 >= len(chain)
        msg = (f"Progress alur #{pid}: langkah {k + 1}/{len(chain)} ({TEAM[o['to']].name}) selesai: "
               f"{note}" + (" Semua langkah selesai." if last else
                            f" Sekarang lanjut ke {TEAM[chain[k + 1]['to']].name}."))
        self.events.emit("dialogue", "laras", mood="happy", text=msg)
        self.inbox.send("laras", f"Progress alur #{pid}", f"Boss,\n\n{msg}\n\nLaras", "memo")
        if last:
            self.orders.update(pid, "selesai", "semua langkah selesai")
        else:
            self._start_step(pid, k + 1)

    RELAY_SPLIT = re.compile(r"\s*,?\s*\b(?:lalu|kemudian|terus|setelah itu|habis itu|sesudah itu|"
                             r"abis itu)\b\s*|\s*;\s*")
    HANDOVER = re.compile(r",?\s*(?=\b(?:kasih|serahkan|berikan|kirim\w*|teruskan|oper)\s+(?:ke\s+)?"
                          r"(?:laras|bening|citra|kirana))", re.I)
    JOINT = re.compile(r"\s*,?\s*\b(?:dan|supaya|agar|sehingga|biar)\b\s*"
                       r"(?=(?:\S+\s+){0,6}?\S*(?:laras|bening|citra|kirana))", re.I)
    NAME = re.compile(r"\b(laras|bening|citra|kirana)\w*", re.I)

    def parse_relay(self, text: str) -> list[dict]:
        """'kamus suruh Bening kerjakan, kasih ke Citra untuk dipasangkan ke fitur terpilih
        lalu berikan ke Kirana' -> three steps. Fewer than two people: not a relay."""
        segs = [y for part in self.RELAY_SPLIT.split(text) for x in self.HANDOVER.split(part)
                for y in self.JOINT.split(x)]
        steps: list[dict] = []
        for seg in (x.strip(" ,.") for x in segs):
            if not seg:
                continue
            names = [n.lower() for n in self.NAME.findall(seg)]
            who = names[-1] if names else None
            if who is None and re.search(r"\b(deck|slide|presentasi)\b", seg, re.I):
                who = "kirana"
            if who is None:
                if steps:
                    steps[-1]["task"] += f", {seg}"
                    continue
                who = "laras"
            if steps and steps[-1]["to"] == who:
                steps[-1]["task"] += f", {seg}"
            else:
                steps.append({"to": who, "task": seg})
        return steps if len({s_["to"] for s_ in steps}) >= 2 else []

    def call_meeting(self, topic: str, attendees: list[str], agenda: str = "",
                     host: str = "laras") -> dict:
        """A coordination meeting the Boss asked for, outside the stage meetings."""
        if self.state.open_meeting:
            raise ValueError("masih ada rapat yang terbuka; masuk ke rapat itu dulu")
        who = [a for a in TEAM if a in set(attendees) | {host}]
        slides = [("Agenda", f"<h3>{html.escape(topic)}</h3>"
                   + (f"<p>{html.escape(agenda)}</p>" if agenda else ""), "html"),
                  ("Timeline", gantt_svg(self.timeline), "svg")]
        deck = self.project.dir("artifacts") / DELIV.OUT / "deck_draft.html"
        files = [str(deck.relative_to(self.project.root))] if deck.exists() and re.search(
            r"deck|slide|presentasi", topic + agenda, re.I) else None
        script = [say(host, "neutral", f"Rapat koordinasi: {topic}." + (f" {agenda}" if agenda else ""), 0),
                  say(host, "happy", "Silakan, Boss. Ketik arahan atau pertanyaan ke siapa saja yang "
                      "hadir; arahan Boss saya jalankan setelah rapat.", 0)]
        m = self.meetings.create("adhoc", f"Rapat: {topic}"[:80], host, who, slides, script,
                                 [choice("done", "Selesai, tutup rapat", "adhoc_end")],
                                 context=f"Rapat koordinasi tentang: {topic}. {agenda}",
                                 invite_body=agenda or topic, attachments=files)
        self._update(open_meeting=m.id)
        return {"meeting_id": m.id}

    def _do_adhoc_end(self, m) -> dict:
        self._end(m, "Rapat koordinasi selesai.", ["Laras: jalankan arahan Boss dari rapat ini."])
        return {"ended": True}

    def messages_for(self, agent: str, last: int = 8) -> list[dict]:
        """The Boss's open orders and messages for `agent` (what she must not forget)."""
        return self.orders.open_for(agent)[-last:]

    def _default_speaker(self, m) -> str:
        if m is None:
            return "laras"
        return m.host if m.host != "laras" else (m.attendees[-1] if m.kind == "results" else "laras")

    def who_answers(self, text: str, meeting_id: str | None = None, to: str | None = None) -> dict:
        """Before the answer ($0, instant): who will answer, and her first words."""
        m = self.meetings.get(meeting_id) if meeting_id else None
        agent = self.agents.who(text, to, self._default_speaker(m), m.attendees if m else None)
        return {"agent": agent, "line": self.agents.working_line(agent)}

    def say(self, text: str, meeting_id: str | None = None, to: str | None = None,
            cost_ok: bool = False, candid: bool = False) -> dict:
        """The Boss types. In a meeting the answer joins the transcript and any new
        proposal becomes an ACC / reject choice in the same meeting. `candid` is a
        relaxed chat at an agent's desk (same rules, friendlier tone)."""
        m = self.meetings.get(meeting_id) if meeting_id else None
        if m is not None:
            self.meetings.start(m.id)
            m = self.meetings.get(m.id)
            self.meetings.add_turn(m, "boss", text)
            context = m.context + "\nSlide: " + ", ".join(s["title"] for s in m.slides)
            history = [{"role": "boss" if t["who"] == "boss" else "agent", "text": t["text"]}
                       for t in m.transcript[:-1]]
            default = self._default_speaker(m)
        else:
            context = CANDID if candid else "Obrolan di meja kerja."
            default = "laras"
            history = self._desk_history(self.agents.who(text, to, default))
            self.events.emit("dialogue", "boss", text=text, to=to)
        r = self._work_status_reply(text, to, default)      # "Bening lagi ngerjain apa?"
        if r is None:
            r = self._relay_reply(text, m)                  # several teammates in turn
        if r is None:
            r = self._order_reply(text, to, default, m)    # orders the office can do now
        order = None
        if r is None:
            if is_order(text) and not self.agents.is_shortcut(text):
                who = self.agents.who(text, to, default, m.attendees if m is not None else None)
                order = self.orders.add(text, who, summary=summarize(self, [text], literal=True))
            r = self.agents.respond(text, to, context, history, cost_ok, default,
                                    meeting_id=m.id if m is not None else None,
                                    present=m.attendees if m is not None else None)
            if order is not None and not r.needs_cost_ok:
                if next(o for o in self.orders.list() if o["id"] == order["id"])["status"] == "baru":
                    self.orders.update(order["id"], "dikerjakan", f"diterima {TEAM[r.agent].name}")
                r.text += f" (Dicatat sebagai perintah #{order['id']}.)"
        if m is not None:
            m = self.meetings.get(m.id)
            self.meetings.add_turn(m, r.agent, r.text, r.mood,
                                   (r.actual or {}).get("actual", 0.0),
                                   slide=r.board[-1]["slide"] if r.board else None)
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
        self.conversations.add(
            where=m.id if m is not None else "desk", boss=text, to=to, agent=r.agent,
            text=r.text, mood=r.mood, usd=(r.actual or {}).get("actual", 0.0),
            estimate_high_usd=(r.estimate or {}).get("high_usd"), provider=r.provider,
            kind=r.kind, thought=r.thought)
        return r.to_dict()

    def _desk_history(self, agent: str, n: int = 4) -> list[dict]:
        """The last exchanges with this teammate at her desk, so a follow-up ("2", "yang
        tadi", "aku percaya kamu") is understood."""
        out = []
        for r in [r for r in self.conversations.list() if r.get("where") == "desk"
                  and r.get("agent") == agent and r.get("text")][-n:]:
            out += [{"role": "boss", "text": r["boss"]}, {"role": "agent", "text": r["text"]}]
        return out

    WORK_STATUS = re.compile(
        r"\b(?:lagi|sedang|masih)\s+(?:ngerjain|ngerjakan|mengerjakan|kerjain|apa)\b|"
        r"sampai\s+mana|udah\s+sampai|sudah\s+sampai|progres\w*|progress|"
        r"status\s+(?:tugas|pekerjaan|kerjaan|perintah|alur|tim)|"
        r"(?:tugas|pekerjaan|kerjaan)\w*\s+(?:rekan|tim|teman|semua)", re.I)
    ORDER_REF = re.compile(r"(?:#|\b(?:perintah|tugas|alur)\s*#?)(\d+)\b", re.I)

    def _work_status_reply(self, text: str, to: str | None, default: str) -> AgentReply | None:
        """Where a teammate's work stands, answered from the orders and the timeline:
        free, and never a guess."""
        if not self.WORK_STATUS.search(text) or self.parse_relay(text):
            return None
        speaker = self.agents.who(text, to, default)
        ref = self.ORDER_REF.search(text)
        if ref:
            try:
                w = self.team_work(order=int(ref.group(1)))
            except KeyError as e:
                return AgentReply(speaker, f"{e.args[0].capitalize()}, Boss.", "neutral",
                                  kind="command")
            if w.get("relay"):
                steps = {k: x for k, x in enumerate(w.get("steps", []))}
                lines = [f"{c.rsplit(' (', 1)[0]} — " + (self._work_state(steps[k]) if k in steps
                                                         else "belum mulai")
                         for k, c in enumerate(w["relay"])]
                body = f"Alur #{w['id']} ({w['status']}): " + "; ".join(lines) + "."
            else:
                body = f"{w['to']}: {self._work_line(w)}."
            return AgentReply(speaker, "Saya cek catatannya, Boss. " + body, "neutral",
                              kind="command")
        you = re.search(r"\bkamu\b", text, re.I)
        names = list(dict.fromkeys(n.lower() for n in self.NAME.findall(text)))
        if len(names) > 1 and speaker in names and not you:       # "Laras, Bening sampai mana?"
            names.remove(speaker)
        if not names:
            names = [speaker] if you else list(TEAM)
        out = []
        for a in names:
            w = self.team_work(a)[TEAM[a].name]
            who = "Tugas saya" if a == speaker else TEAM[a].name
            if w["open"]:
                parts = "; ".join(self._work_line(x) for x in w["open"])
                out.append(f"{who}: {parts}.")
            else:
                last = w["recently_done"][-1]["summary"] if w["recently_done"] else ""
                ms = w["milestones"][0] if w["milestones"] else None
                out.append(f"{who}: tidak ada tugas terbuka"
                           + (f"; terakhir selesai: {last}" if last else "")
                           + (f"; tahap timeline berikutnya: {ms['title']} "
                              f"({STATUS_LABEL[ms['status']]})" if ms else "") + ".")
        return AgentReply(speaker, "Saya cek catatan tugasnya, Boss. " + " ".join(out),
                          "neutral", kind="command")

    @staticmethod
    def _work_state(r: dict) -> str:
        label = {"baru": "baru diterima", "dikerjakan": "sedang dikerjakan"}.get(r["status"],
                                                                               r["status"])
        return label + (f" ({r['notes'][-1]})" if r["notes"] else "")

    def _work_line(self, r: dict) -> str:
        if r.get("relay"):
            now = next((x for x in r["relay"] if x.endswith("(sekarang)")), None)
            return (f"alur #{r['id']} ({len(r['relay'])} langkah), "
                    + (f"sekarang di langkah {now.rsplit(' (', 1)[0]}" if now else r["status"]))
        return f"#{r['id']} {r['summary']} — {self._work_state(r)}"

    def _relay_reply(self, text: str, m) -> AgentReply | None:
        if not is_order(text) or self.agents.is_shortcut(text):
            return None
        steps = self.parse_relay(text)
        if not steps:
            return None
        p = self.start_relay(steps, text=text)
        lines = "; ".join(f"{k + 1}. {TEAM[c['to']].name}: {c['task']}" for k, c in enumerate(p["chain"]))
        now = self.orders.get(p["id"])
        cur = self.orders.get(now["children"][-1])
        state = ("Semua langkah sudah selesai." if now["status"] == "selesai" else
                 f"Langkah {now['step'] + 1} ({TEAM[cur['to']].name}) "
                 + (f"menunggu: {cur['notes'][-1]['note']}; begitu bisa, langsung jalan."
                    if cur["status"] == "terhambat" else "sedang dikerjakan."))
        return AgentReply("laras", f"Siap, Boss. Alurnya saya atur: {lines}. {state} Saya laporkan "
                          f"setiap langkah selesai. (Alur #{p['id']}.)", "happy", kind="order")

    def _order_reply(self, text: str, to, default: str, m) -> AgentReply | None:
        """An order the office can do itself runs now; if it cannot yet, it is recorded as
        blocked with the reason and retried automatically later (never forgotten)."""
        if not is_order(text) or self.agents.is_shortcut(text):
            return None
        cap = self._capability(text)
        if cap is None:
            return None
        action, owner = cap
        if m is not None and owner not in m.attendees:
            owner = self._default_speaker(m)
        o = self.orders.add(text, owner, summary=summarize(self, [text], literal=True))
        o["action"] = action
        items = self.orders.list()
        next(x for x in items if x["id"] == o["id"])["action"] = action
        self.orders._save(items)
        self._status(owner, "working", ACTION_LABEL[action])
        try:
            done = self.run_order(o)
            self.orders.update(o["id"], "selesai", done)
            text_, mood = f"Siap, Boss. {done} (Perintah #{o['id']} selesai.)", "happy"
        except ValueError as e:
            self.orders.update(o["id"], "terhambat", str(e))
            text_, mood = (f"Dicatat sebagai perintah #{o['id']}, Boss. Belum bisa sekarang: {e}. "
                           "Begitu bisa, langsung saya kerjakan dan kabari."), "apologetic"
        finally:
            self._status(owner, "idle", "")
        return AgentReply(owner, text_, mood, kind="order")

    def decide_proposal(self, pid: str, accept: bool) -> dict:
        p = self.proposals.decide(pid, accept)
        return {"proposal": p.id, "status": p.status, "summary": p.summary}

    # ================================================================= work the Boss can watch
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
                  ["Bening: dokumentasikan arti tiap variabel, lalu serahkan ke Citra.",
                   "Citra: rencana waterfall."])
        self.events.emit("approval", "boss", what="target", decision="accepted")

        def handover():                   # only after the variables are understood and written down
            self.document_variables()
            return self._work("citra", ["membaca kamus variabel dari Bening",
                                        "menyusun rencana waterfall"], self.plan_stage)
        return self._work("bening", ["membaca arti tiap variabel", "mencocokkan dengan kamus data",
                                     "mendokumentasikan kamus variabel"], handover)

    # ================================================================= orders the office can do
    # (action, owner, what in the Boss's words asks for it); the first match wins
    CAPABILITIES = [
        ("make_documentation", "laras", re.compile(
            r"dokumentasi\w*\s+(?:\w+\s+){0,2}(model|proyek|scorecard)|(model|proyek)\w*\s+.*dokumentasi", re.I)),
        ("make_deck", "kirana", re.compile(r"\b(deck|slide|presentasi|ppt|pptx)\b", re.I)),
        ("feature_meanings", "citra", re.compile(
            r"(pasang|cocok|map)\w*\W+(?:\w+\W+){0,4}?fitur|fitur\s+(terpilih|final|model)\W+(?:\w+\W+){0,3}?"
            r"(arti|makna|kamus)|(arti|makna|kamus)\W+(?:\w+\W+){0,3}?fitur\s+(terpilih|final|model)", re.I)),
        ("document_variables", "bening", re.compile(
            r"(infer\w*|arti|makna|terjemah\w*|kamus|dokumentasi\w*|jelaskan)\W+(?:\w+\W+){0,4}?"
            r"(variabel|kolom|tabel|kamus|data)|(variabel|kolom|tabel)\W+(?:\w+\W+){0,4}?"
            r"(infer\w*|arti|makna|terjemah\w*)", re.I)),
    ]

    def _capability(self, text: str) -> tuple[str, str] | None:
        return next(((a, o) for a, o, rx in self.CAPABILITIES if rx.search(text)), None)

    def run_order(self, order: dict) -> str:
        """Do an order the office knows how to do. Returns what was done; raises
        ValueError (with the reason) when it cannot be done yet."""
        action = order.get("action")
        if action == "document_variables":
            r = self.document_variables(translate=True)
            return (f"Kamus variabel dan tabel sudah saya dokumentasikan ({len(r['rows'])} variabel, "
                    f"{len(r['tables'])} tabel" + (f", {r['translated']} deskripsi diterjemahkan" if r["translated"] else "")
                    + "). Filenya terlampir di email.")
        if action == "make_documentation":
            r = self.make_documentation()
            return f"Dokumentasi model ({r['cut'].replace('cut', 'Cut ')}) sudah jadi, terlampir di email."
        if action == "feature_meanings":
            r = self.feature_meanings()
            return (f"Kamus sudah saya pasangkan ke {r['n']} fitur di model {r['cut'].replace('cut', 'Cut ')} "
                    "(arti, poin, kontribusi); terlampir di email.")
        if action == "make_deck":
            r = self.make_deck()
            return f"Draft deck ({r['slides']} slide, HTML) sudah jadi untuk Boss review; terlampir di email."
        raise ValueError("belum ada cara otomatis; saya kerjakan dan laporkan")

    def run_open_orders(self) -> None:
        """Retry the orders that were blocked, now that the project moved on."""
        for o in self.orders.open_for():
            if not o.get("action"):
                continue
            try:
                done = self.run_order(o)
            except ValueError:
                continue
            self.orders.update(o["id"], "selesai", done)
            self.events.emit("dialogue", o["to"], mood="happy",
                             text=f"Perintah #{o['id']} selesai, Boss: {done}")

    def make_documentation(self) -> dict:
        r = DELIV.documentation(self)
        self.inbox.send("laras", "Dokumentasi model", f"Boss,\n\nDokumentasi model untuk "
                        f"{r['cut'].replace('cut', 'Cut ')} sudah saya susun: "
                        + ", ".join(r["sections"]) + ".\n\nLaras", "results", attachments=[r["file"]])
        return r

    def feature_meanings(self) -> dict:
        r = DELIV.selected_features(self)
        self.inbox.send("citra", "Arti fitur terpilih", f"Boss,\n\nKamus Bening sudah saya pasangkan "
                        f"ke {r['n']} fitur di model {r['cut'].replace('cut', 'Cut ')}: arti, rentang poin, "
                        "dan kontribusi skor masing-masing.\n\nCitra", "results", attachments=[r["file"]])
        return r

    def make_deck(self) -> dict:
        r = DELIV.deck(self)
        self.inbox.send("kirana", "Draft deck untuk review", f"Boss,\n\nDraft deck "
                        f"({r['slides']} slide, bahasa Inggris, HTML) dari {r['cut'].replace('cut', 'Cut ')} "
                        "sudah siap. Kalau Boss setuju, saya ekspor ke PDF / PPTX.\n\nKirana",
                        "results", attachments=[r["file"]])
        self.timeline.set("deck", "waiting_boss", "draft HTML menunggu review Boss")
        return r

    def document_variables(self, translate: bool = False) -> dict:
        """Bening's documented dictionary: what every variable means, where that comes from
        and how sure she is. Saved as an artifact, filled into the data dictionary where it
        was empty, mailed to the Boss, and handed to Citra with the dataset."""
        st = self.state
        t = st.target or {}
        try:
            df, _ = self.dataset()
        except FileNotFoundError:
            raise ValueError("menunggu data di-join (setelah kickoff)")
        ddict = DataDictionary.load(self.project)
        rows = MEAN.infer(df, ddict, office=self, plan=st.join_plan)
        translated = MEAN.translate(rows, self) if translate else 0
        tables_, dict_files = prep.split_uploads(self.project)
        table_rows = MEAN.tables(tables_, st.join_plan, [p.name for p in dict_files])
        for r in rows:                           # fill the gaps; the bank's own words stay
            e = ddict.entries.get(r["variabel"], {})
            if not e.get("description") and not r["dasar"].startswith("kamus data bank") and r["dasar"] != "-":
                ddict.set(r["variabel"], description=r["arti"] + (f" ({r['satuan']})" if r["satuan"] else ""))
                ddict.entries[r["variabel"]]["description_by"] = f"Bening ({r['dasar']})"
        ddict.save(self.project)
        rule = {"ge": ">=", "eq": "==", "in": "in"}.get(t.get("rule"), "")
        md = MEAN.markdown(rows, f"bad = {t['column']} {rule} {t['value']}" if t else "")
        md = md.replace("| variabel |", MEAN.tables_markdown(table_rows) + "## Variabel\n\n| variabel |", 1)
        out = self.project.dir("artifacts") / "kamus_variabel_bening.md"
        out.write_text(md, encoding="utf-8")
        (self.project.dir("work") / "variable_meanings.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        unclear = [r["variabel"] for r in rows if r["keyakinan"] == "rendah"]
        feats, _ = prep.feature_list(df, t, st.exclude) if t else (list(df.columns), {})
        rel = str(out.relative_to(self.project.root))
        self.inbox.send("bening", "Kamus variabel sudah saya dokumentasikan",
                        f"Boss, sebelum ke Citra saya baca arti {len(rows)} variabel: "
                        f"{sum(r['dasar'] == 'kamus data bank' for r in rows)} dari kamus data bank, "
                        f"{sum(r['dasar'] == 'turunan join' for r in rows)} turunan join, sisanya "
                        "saya simpulkan dari nama dan rentang nilainya."
                        + (f"\n\nYang belum jelas (mohon konfirmasi): {', '.join(unclear)}."
                           if unclear else "") + "\n\nKamus lengkap terlampir.\n\nBening",
                        "memo", attachments=[rel])
        self.events.emit("handoff", "bening", to="citra",
                         memo=f"Dataset + kamus variabel: {len(df):,} baris, {len(feats)} kandidat fitur.")
        if t:
            self.inbox.send("bening", "Serah terima ke Citra", f"Citra, dataset siap: {len(df):,} "
                            f"baris, {len(feats)} kandidat fitur, target {t['column']} {rule} "
                            f"{t['value']}. Arti tiap variabel ada di kamus variabel terlampir."
                            "\n\ncc: Boss\n\nBening", "memo", attachments=[rel])
        self.timeline.set("dictionary", "waiting_boss" if unclear else "done",
                          f"{len(unclear)} variabel perlu konfirmasi" if unclear else "")
        return {"rows": rows, "tables": table_rows, "translated": translated, "unclear": unclear,
                "file": rel}

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
        script = [
            say("citra", "neutral", "Boss, ini urutan seleksi fitur yang saya usulkan.", 0),
            *([say("citra", "thinking", "Arti tiap variabel sudah didokumentasikan Bening dan "
                   "sudah saya baca; kamusnya ada di slide terakhir.", 2)]
              if (self.project.dir("work") / "variable_meanings.json").exists() else []),
            say("citra", "thinking", "Filter data dulu (missing, identik, IV), lalu klaster "
                "korelasi, lalu stepwise, forward, backward, dan finalisasi.", 1),
            say("citra", "serious", "Model tidak saya bangun sebelum rencana ini di-ACC.", 1),
        ]
        self.timeline.set("waterfall_plan", "waiting_boss")
        self._status("citra", "waiting_boss", "rapat rencana waterfall")
        mt = self.meetings.create(
            "plan", "Rapat rencana waterfall", "citra", ["laras", "citra"],
            self._plan_slides(steps), script,
            [choice("acc", "ACC rencana, bangun Cut 1", "plan_acc"),
             choice("edit", "✏ Edit rencana waterfall sendiri (drag & drop)", "edit_waterfall"),
             adjourn_choice()],
            context="Rapat rencana waterfall: " + "; ".join(
                f"{s.title}: {s.criterion}" for s in steps if s.enabled),
            invite_body="Citra menjelaskan langkah seleksi fitur sebelum membangun model.")
        self._update(open_meeting=mt.id)
        return {"meeting_id": mt.id}

    def _plan_slides(self, steps) -> list[tuple[str, str, str]]:
        rows = [{"#": k + 1, "langkah": s.title, "fitur lolos jika": s.criterion}
                for k, s in enumerate([s for s in steps if s.enabled])]
        slides = [("Rencana", plan_svg(steps), "svg"), ("Kriteria", html_table(rows), "html")]
        p = self.project.dir("work") / "variable_meanings.json"
        if p.exists():                            # what Bening handed over, for reference
            vm = json.loads(p.read_text(encoding="utf-8"))
            slides.append(("Kamus variabel (Bening)", html_table(
                [{k: r[k] for k in ("variabel", "arti", "satuan", "dasar", "keyakinan")} for r in vm]),
                "html"))
        return slides

    def _refresh_plan_slides(self, m) -> None:
        """Rewrite the plan meeting's slides from the current plan (the projector reloads)."""
        steps = parse_steps(load_plan(self.project, self.settings.modelling.waterfall)["steps"])
        for k, (title, content, ext) in enumerate(self._plan_slides(steps)[:len(m.slides)]):
            f = self.project.root / m.slides[k]["file"]
            f.write_text(light_only(content) if ext == "svg" else _page(title, content),
                         encoding="utf-8")

    def edit_waterfall(self, steps: list[dict]) -> dict:
        """The Boss edits the waterfall directly (order, on/off, parameters): it is the
        Boss's decision, so it applies at once (no ACC). Citra confirms what changed; in
        the plan meeting the slides on the projector are rebuilt; after Cut 1 it applies
        to the next cut. Bad plans raise ValueError with the reason."""
        old = parse_steps(load_plan(self.project, self.settings.modelling.waterfall)["steps"])
        new = parse_steps(steps)
        changes = plan_diff(old, new)
        if not changes:
            return {"changes": []}
        save_plan(self.project, new, approved=True, by="boss")
        text = "; ".join(changes)
        st = self.state
        st.plan_edits.append({"ts": pd.Timestamp.now(tz="UTC").isoformat(timespec="microseconds"),
                              "summary": f"Rencana waterfall: {text}"})
        st.save(self.project)
        self.project.audit("boss", "waterfall_edit", {"changes": changes})
        self.events.emit("waterfall_edited", "boss", changes=changes)
        m = self.meetings.get(st.open_meeting) if st.open_meeting else None
        if m is not None and m.status != "ended" and m.kind == "plan":
            self._refresh_plan_slides(m)
            self.meetings.add_turn(m, "citra", f"Siap, Boss. Rencana sudah saya perbarui: {text}. "
                                   "Slide di proyektor sudah diganti.", "happy", slide=0)
        elif m is not None and m.status != "ended":
            self.meetings.add_turn(m, "citra", f"Dicatat, Boss: {text}. Berlaku mulai cut "
                                   "berikutnya.", "happy")
        else:
            self.events.emit("dialogue", "citra", mood="happy", text=f"Rencana waterfall "
                             f"diperbarui, Boss: {text}." + (" Berlaku mulai cut berikutnya."
                                                              if st.cuts else ""))
        return {"changes": changes}

    def _do_edit_waterfall(self, m) -> dict:      # the page opens the editor for this choice
        return {"note": "buka editor waterfall"}

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
                        "built_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="microseconds"),
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
        rep = self._score_report(cut, res)
        slides = [
            ("Ringkasan", cards([("fitur", f"{n_sel} dari {n_in}"),
                                 ("Gini test", f"{g_test:.3f}"),
                                 ("Gini OOT", f"{g_oot:.3f}" if g_oot is not None else "-"),
                                 ("PSI", f"{psi:.3f}" if psi is not None else "-")]), "html"),
            ("Waterfall", (art / "waterfall.svg").read_text(encoding="utf-8"), "svg"),
            ("Performa", html_table(perf_rows), "html"),
            ("Korelasi WoE", (art / "woe_correlation.svg").read_text(encoding="utf-8"), "svg"),
            ("Kontribusi skor", rep["contribution_html"], "html"),
            ("Binning per variabel", rep["binning_html"], "html"),
            ("Pembagian risiko (20 kelompok)", rep["bands_html"], "html"),
        ]
        join_rows = self._join_contribution(art) if cut == "cut1" else []
        if join_rows:
            slides.insert(1, ("Join data (Bening)", html_table(join_rows), "html"))
        if final:
            slides.append(("Usulan kategori risiko (VL–VH)", rep["levels_html"], "html"))
        slides.append(("Timeline", gantt_svg(self.timeline), "svg"))
        at = {t: k for k, (t, _, _) in enumerate(slides)}      # lines point at slides by title
        top = rep["contribution"][:3]
        script = [
            say("laras", "happy", f"Boss, hasil {title} sudah siap. Citra jelaskan singkat ya.",
                at["Ringkasan"]),
        ]
        if join_rows:
            best = max(join_rows, key=lambda r: r["fitur di model"])
            script.append(say("bening", "thinking", f"Soal join: saya pakai {len(join_rows) - 1} "
                              f"join dari tabel dasar. Hasil percobaannya: tabel {best['tabel']} "
                              f"menyumbang paling banyak, {best['fitur di model']} fitur di model. "
                              "Detailnya di slide ini dan di memo join.", at["Join data (Bening)"]))
        script += [
            say("citra", "neutral", f"Dari {n_in} kandidat, {n_sel} fitur masuk model.",
                at["Waterfall"]),
            say("citra", mood, f"Gini test {g_test:.3f}"
                + (f", OOT {g_oot:.3f}" if g_oot is not None else "")
                + (f"; PSI {psi:.3f}" if psi is not None else "") + ".", at["Performa"]),
            say("citra", "thinking" if max_r < 0.5 else "worried",
                f"Korelasi WoE tertinggi antar fitur model {max_r:.2f}.", at["Korelasi WoE"]),
            say("citra", "neutral", "Kontribusi skor terbesar dari "
                + ", ".join(f"{r['variabel']} ({r['kontribusi']:.0%})" for r in top) + ".",
                at["Kontribusi skor"]),
            say("citra", "neutral", "Binning tiap variabel, bad rate dan poinnya ada di slide ini; "
                "artinya mengikuti kamus Bening.", at["Binning per variabel"]),
            say("citra", "serious" if not rep["reversals"] else "worried",
                f"Skor saya bagi ke {rep['n_bands']} kelompok risiko"
                + (f" (dari 20; {rep['merged']} digabung karena kurang dari 5% akun)" if rep["merged"] else "")
                + (". Bad rate turun konsisten dari skor rendah ke tinggi." if not rep["reversals"] else
                   f". Bad rate tidak turun konsisten di kelompok {', '.join(map(str, rep['reversals']))}."),
                at["Pembagian risiko (20 kelompok)"]),
        ]
        if forced:
            kept = [r["feature"] for r in forced if r["result"] == "kept"]
            script.append(say("citra", "serious", f"{len(forced)} fitur dipaksa dicoba; "
                              f"{len(kept)} bertahan" + (f" ({', '.join(kept)})" if kept else "")
                              + ". Detailnya di finalization review.", at["Waterfall"]))
        if compare:
            script.append(say("citra", "neutral", compare, at["Performa"]))
        if final:
            script.append(say("citra", "thinking", "Untuk kategori risiko, usul saya VH sampai VL "
                              "seperti di slide ini, masing-masing sekitar seperlima akun. Saya "
                              "ingin diskusikan dulu dengan Boss: kalau batasnya mau digeser, buka "
                              "editornya atau bilang saja.", at["Usulan kategori risiko (VL–VH)"]))
            script.append(say("laras", "serious", "Ini cut final. Kalau Boss puas, konfirmasi; "
                              "kalau belum, ketik masukan dan pilih perpanjang.", at["Timeline"]))
            choices = [choice("levels_acc", "ACC kategori risiko VL–VH", "levels_acc"),
                       choice("levels_edit", "🎚 Ubah batas kategori risiko", "edit_levels"),
                       choice("confirm", "Konfirmasi model final", "confirm_final"),
                       choice("extend", "Perpanjang ke cut berikutnya (ketik masukan dulu)",
                              "extend")]
        else:
            script.append(say("laras", "neutral", "Silakan beri masukan dengan mengetik; "
                              "usulan yang Boss ACC dipakai di cut berikutnya.", at["Timeline"]))
            nxt = f"cut{int(cut[3:]) + 1}"
            choices = [choice("next", f"Lanjut ke {nxt.replace('cut', 'Cut ')}", "next_cut")]

        body = (f"Boss,\n\n{title} selesai: {n_sel} fitur, Gini test {g_test:.3f}"
                + (f", OOT {g_oot:.3f}" if g_oot is not None else "") + ".\n\n"
                + (compare + "\n\n" if compare else "")
                + "Laporan lengkap terlampir (waterfall report, finalization review, kamus "
                "data). Laras akan mengatur rapat untuk presentasi singkat.\n\nCitra")
        attach = [rel(n) for n in ("waterfall_report.md", "finalization_review.md",
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
            invite_body=f"Presentasi singkat hasil {title}.", attachments=attach)
        self._update(open_meeting=mt.id)

    # ================================================================= Citra's score report
    def _meanings(self) -> dict[str, str]:
        p = self.project.dir("work") / "variable_meanings.json"
        return {r["variabel"]: r["arti"] for r in json.loads(p.read_text(encoding="utf-8"))} \
            if p.exists() else {}

    def _score_report(self, cut: str, res) -> dict:
        """Every cut: each variable's bins and points, its share of the score, and the
        score cut into 20 risk bands (any band under 5% of accounts merged). The final
        cut also gets Citra's proposed VL-VH grouping, to discuss with the Boss."""
        from .board import bars_svg
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
        bands = R.risk_bands(*samples["train"])
        table = R.band_table(bands, samples)
        table.to_csv(art / "risk_bands.csv", index=False)
        reversals = R.is_monotonic(table["bad_rate_train"].tolist())
        rate_cols = [c for c in table.columns if c.startswith("bad_rate_")]
        bands_html = html_table([{
            "kelompok": int(b["band"]), "skor": f"{b['score_min']:.0f}–{b['score_max']:.0f}",
            "akun": int(b["n"]), "% akun": f"{b['pct']:.1%}",
            **{c.replace("bad_rate_", "bad rate "): (f"{b[c]:.1%}" if pd.notna(b[c]) else "-")
               for c in rate_cols}} for b in table.to_dict("records")]) + (
            f"<p class='muted'>Kelompok 1 = skor terendah (risiko tertinggi). Dari 20 kelompok "
            f"sama besar, {bands['merged']} digabung dengan tetangganya karena kurang dari 5% "
            "akun.</p>" if bands["merged"] else
            "<p class='muted'>Kelompok 1 = skor terendah (risiko tertinggi).</p>")
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
                "levels_html": self._levels_html(table, labels)}

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
            k = next((i for i, s in enumerate(m.slides) if s["title"].startswith("Usulan kategori")), None)
            if k is not None:
                (self.project.root / m.slides[k]["file"]).write_text(
                    _page(m.slides[k]["title"], self._levels_html(table, labels)), encoding="utf-8")
            m.decisions.append({"decision": f"{what}: {said}.", "ts": ""})
            self.meetings.save(m)
            self.meetings.add_turn(m, "citra", f"Dicatat, Boss. Kategori risiko sekarang: {said}.",
                                   "happy", slide=k)

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
        opn = self.orders.open_for()
        if opn:                                          # never forget what the Boss asked
            lines.append(say("laras", "serious", f"Ada {len(opn)} perintah Boss yang belum selesai: "
                             + "; ".join(f"#{o['id']} {TEAM[o['to']].name} ({o['status']})"
                                         for o in opn[:4]) + ". Lengkapnya di 📋 Perintah."))
        if self.state.stage == "new":                    # nothing starts before the data is confirmed
            files = self.project.raw_files()
            if not files:
                lines.append(say("laras", "serious", "Proyek baru, tapi datanya belum ada. "
                                 "Sebelum kickoff, upload dulu lewat 📁 Data ya, Boss."))
            elif self.state.data_files is None:
                lines.append(say("laras", "neutral", f"Di proyek ini sudah ada {len(files)} file "
                                 "data. Sebelum kickoff, tolong konfirmasi dulu yang dipakai "
                                 "lewat 📁 Data."))
            else:
                lines.append(say("laras", "happy", "Datanya sudah siap. Kalau Boss siap, klik "
                                 "Mulai kickoff; saya siapkan rapat perkenalan dan timeline."))
        elif self._data_open() and not prep.selected_files(self.project):
            lines.append(say("laras", "serious", "Bening masih menunggu data. Klik 📁 Data di "
                             "atas, lalu pilih file yang mau dipakai."))
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
