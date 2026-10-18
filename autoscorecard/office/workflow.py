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

import threading

from . import prep
from .agent import AgentRuntime
from .board import Board
from .characters import TEAM, line
from .conversations import ConversationLog
from .events import EventStream
from .flow.conversation import ConversationMixin
from .flow.cuts import CutsMixin
from .flow.data import DataMixin
from .flow.deliverables_orders import DeliverableOrdersMixin
from .flow.dictionary import DictionaryMixin
from .flow.handover import HandoverMixin
from .flow.plan import PlanMixin
from .flow.relays import RelayMixin
from .flow.score_report import ScoreReportMixin
from .flow.stages import StagesMixin
from .inbox import Inbox
from .meetings import Meetings, say
from .orders import Orders
from .proposals import Proposals
from .state import OfficeState
from .timeline import Timeline


class Office(DataMixin, HandoverMixin, RelayMixin, ConversationMixin,
             StagesMixin, DeliverableOrdersMixin, DictionaryMixin, PlanMixin,
             CutsMixin, ScoreReportMixin):
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
        self.proposals.on_accept = self._proposal_accepted
        self._asks: dict[str, dict] = {}      # Laras's cost question waiting for the Boss, per chat
        self._cost_ok = {"until": 0.0, "cap": 0.0}   # the Boss's yes holds for a while
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
