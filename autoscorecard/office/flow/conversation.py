"""What the Boss types: routing, the free rule-based replies (status, timeline, cost), meeting turns."""
from __future__ import annotations

import re
import time

from ..agent import AgentReply
from ..characters import CANDID, TEAM
from ..meetings import choice
from ..orders import is_order
from ..summary import summarize
from ..timeline import STATUS_LABEL, gantt_svg
from .common import ACTION_LABEL


class ConversationMixin:
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
        return {"agent": agent, "line": self.agents.working_line(agent, text)}

    def say(self, text: str, meeting_id: str | None = None, to: str | None = None,
            cost_ok: bool = False, candid: bool = False) -> dict:
        """The Boss types. In a meeting the answer joins the transcript and any new
        proposal becomes an ACC / reject choice in the same meeting. `candid` is a
        relaxed chat at an agent's desk (same rules, friendlier tone)."""
        key = meeting_id or f"desk:{to or ''}"
        pending = self._asks.pop(key, None)
        explain = None
        if pending and not cost_ok:
            if self.YES.match(text):                      # "ya" / "ok": the waiting question
                text, cost_ok = pending["text"], True
            elif self.MONEY.search(text):                 # "kok minta uang terus?"
                self._asks[key] = pending
                explain = self._cost_explained(pending)
        if cost_ok:
            cap = 2 * max(pending["high"] if pending else 0.0,
                          self.settings.budget.ask_above_usd)
            self._cost_ok = {"until": time.time() + self.COST_OK_MINUTES * 60,
                             "cap": max(cap, self._cost_ok["cap"])}
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
        round_ = self._round_speakers(text, m) if m is not None else []
        if round_ and not to:
            to = round_[0]
        r = explain or self._work_status_reply(text, to, default)   # "Bening sampai mana?"
        if r is None:
            r = self._timeline_reply(text, m)               # "kamus dan deck done di timeline"
        if r is None:
            r = self._relay_reply(text, m)                  # several teammates in turn
        if r is None:
            r = self._order_reply(text, to, default, m)    # orders the office can do now
        order = None
        if r is None:
            if is_order(text) and not self.agents.is_shortcut(text):
                who = self.agents.who(text, to, default, m.attendees if m is not None else None)
                order = self.orders.add(text, who, summary=summarize(self, [text], literal=True))
            standing = self._cost_ok["cap"] if time.time() < self._cost_ok["until"] else None
            r = self.agents.respond(text, to, context, history, cost_ok, default,
                                    meeting_id=m.id if m is not None else None,
                                    present=m.attendees if m is not None else None,
                                    ask_above=standing)
            if r.needs_cost_ok:
                self._asks[key] = {"text": text, "high": r.estimate["high_usd"]}
            if order is not None and not r.needs_cost_ok:
                if next(o for o in self.orders.list() if o["id"] == order["id"])["status"] == "baru":
                    self.orders.update(order["id"], "dikerjakan", f"diterima {TEAM[r.agent].name}")
                r.text += f" (Dicatat sebagai perintah #{order['id']}.)"
        if m is not None:
            m = self.meetings.get(m.id)
            self.meetings.add_turn(m, r.agent, r.text, r.mood,
                                   (r.actual or {}).get("actual", 0.0),
                                   board=r.board[-1]["board"] if r.board else None)
            if r.proposals:
                m = self.meetings.get(m.id)
                for pid in r.proposals:
                    p = next(x for x in self.proposals.list() if x.id == pid)
                    m.choices.insert(0, choice(f"acc_{pid}", f"ACC: {p.summary}", "proposal",
                                               pid=pid, accept=True))
                    m.choices.insert(1, choice(f"rej_{pid}", f"Tolak: {p.summary}", "proposal",
                                               pid=pid, accept=False))
                self.meetings.save(m)
            more = [] if r.needs_cost_ok or r.offline else \
                self._meeting_round(text, m.id, [a for a in round_ if a != r.agent])
        else:
            more = []
            self.events.emit("dialogue", r.agent, mood=r.mood, text=r.text)
        self.conversations.add(
            where=m.id if m is not None else "desk", boss=text, to=to, agent=r.agent,
            text=r.text, mood=r.mood, usd=(r.actual or {}).get("actual", 0.0),
            estimate_high_usd=(r.estimate or {}).get("high_usd"), provider=r.provider,
            kind=r.kind, thought=r.thought)
        return {**r.to_dict(), "more": more}

    # "kalian", "semua jawab", "yang lain juga": everyone in the meeting answers in turn
    ROUND = re.compile(r"\b(?:masing[- ]?masing|(?:setiap|tiap)\s+(?:orang|member|anggota|"
                       r"peserta)|bergantian|gantian|giliran|"
                       r"satu[- ]?(?:per[- ]?)?satu|yang\s+lain\w*|lainnya|semua(?:nya)?\s+"
                       r"(?:orang|member|anggota|tim|jawab|bicara|ngomong|cerita|ikut))\b", re.I)
    OTHERS = re.compile(r"\b(?:yang\s+lain\w*|lainnya|juga|gantian|giliran)\b", re.I)

    def _round_speakers(self, text: str, m) -> list[str]:
        """The attendees who answer in turn: everyone in a coordination meeting, or when the
        Boss asks all of them (or several by name) elsewhere; the one who just spoke goes
        last, or not at all when the Boss asked the others."""
        named = [a for a in dict.fromkeys(n.lower() for n in self.NAME.findall(text))
                 if a in m.attendees]
        if len(named) >= 2:
            return named
        if len(named) == 1 and not self.ROUND.search(text):      # "@citra ...": just her
            return []
        # coordination and casual meetings: everyone takes a turn; result meetings: the host
        # answers unless the Boss asks the others (each turn is a paid answer)
        if m.kind != "adhoc" and not self.ROUND.search(text):
            return []
        last = next((t["who"] for t in reversed(m.transcript[:-1]) if t["who"] in m.attendees), None)
        order = [a for a in m.attendees if a != last]
        if last and not self.OTHERS.search(text):
            order.append(last)
        return order

    def _meeting_round(self, text: str, meeting_id: str, speakers: list[str]) -> list[dict]:
        """The rest of the round: each attendee answers the Boss in turn, seeing what the
        others just said."""
        out = []
        for a in speakers:
            m = self.meetings.get(meeting_id)
            q = max(i for i, t in enumerate(m.transcript) if t["who"] == "boss")
            history = [{"role": "boss" if t["who"] == "boss" else "agent",
                        "text": t["text"] if t["who"] == "boss" else f"{TEAM[t['who']].name}: {t['text']}"}
                       for t in m.transcript[:q]]
            said = "\n".join(f"- {TEAM[t['who']].name}: {t['text']}" for t in m.transcript[q + 1:]
                             if t["who"] in TEAM)
            context = (m.context + "\nSlide: " + ", ".join(s["title"] for s in m.slides) +
                       (f"\nJawaban rekan barusan:\n{said}" if said else "") +
                       f"\nBoss bertanya ke semua peserta; sekarang giliranmu ({TEAM[a].name}). "
                       "Jawab dengan suaramu sendiri, singkat; jangan mengulang jawaban rekan, "
                       "jangan menutup rapat.")
            r = self.agents.respond(text, a, context, history, True, a, meeting_id=meeting_id,
                                    present=m.attendees, consult=False)
            if r.offline:
                break
            self.meetings.add_turn(m, r.agent, r.text, r.mood, (r.actual or {}).get("actual", 0.0),
                                   board=r.board[-1]["board"] if r.board else None)
            self.conversations.add(where=meeting_id, boss=text, to=a, agent=r.agent, text=r.text,
                                   mood=r.mood, usd=(r.actual or {}).get("actual", 0.0),
                                   estimate_high_usd=(r.estimate or {}).get("high_usd"),
                                   provider=r.provider, kind=r.kind, thought=r.thought)
            out.append(r.to_dict())
        return out

    TIMELINE_WORD = re.compile(r"\b(?:timeline|milestone|gantt|jadwal)\b", re.I)
    MS_STATUS = [("done", r"\b(?:selesai|done|beres|rampung|kelar|tuntas|finish\w*)\b"),
                 ("in_progress", r"\b(?:sedang dikerjakan|in progress|on progress|berjalan|jalan)\b"),
                 ("waiting_boss", r"\b(?:menunggu|tunggu)\s+boss\b"),
                 ("not_started", r"\bbelum\s+(?:mulai|dimulai|jalan)\b")]

    def _milestones_named(self, text: str) -> list:
        t = text.lower()
        found = []
        if re.search(r"\bkamus\b|\bdictionary\b", t):
            found.append("dictionary")
        t = re.sub(r"kamus\s+data", " ", t)
        for mid, pat in (("kickoff", r"\bkick\s?off\b"), ("data", r"\bjoin\b|\bdata\b"),
                         ("target", r"\btarget\b"), ("waterfall_plan", r"\bwaterfall\b"),
                         ("deck", r"\bdeck\b|\bpresentasi\b|\bpdf\b|\bpptx\b")):
            if re.search(pat, t):
                found.append(mid)
        found += [f"cut{n}" for n in re.findall(r"\bcut\s*(\d+)", t)]
        for x in self.timeline.items:                       # milestones added later, by title
            if x.id.startswith("x") and x.title.lower() in t:
                found.append(x.id)
        ids = {x.id for x in self.timeline.items}
        return [self.timeline.get(i) for i in dict.fromkeys(found) if i in ids]

    def _timeline_reply(self, text: str, m) -> AgentReply | None:
        """A plain timeline instruction ("tandai kamus dan deck selesai di timeline", "kenapa
        timeline masih menunggu Boss, kan sudah done") is done at once by rule ($0): the
        milestones named (else the ones waiting for the Boss) get the status said."""
        if not self.TIMELINE_WORD.search(text) or "?" in text and not re.search(
                r"\b(?:kan|sudah|udah)\b", text, re.I):
            return None
        status = next((st for st, pat in self.MS_STATUS if re.search(pat, text, re.I)), None)
        if status is None:
            return None
        named = self._milestones_named(text)
        if not named and status == "done":
            named = [x for x in self.timeline.items if x.status == "waiting_boss"]
        if not named:
            return None
        done, refused = [], []
        for x in named:
            if x.status == status:
                continue
            try:
                self.timeline.update(x.id, status, note=f"ditandai {STATUS_LABEL[status]} atas arahan Boss")
                self.project.audit("laras", "timeline_edit", {"id": x.id, "status": status,
                                                              "by": "boss"})
                done.append(x.title)
            except ValueError as e:
                refused.append(str(e))
        shown = [self.board.show("Timeline proyek", gantt_svg(self.timeline), "laras",
                                 m.id if m is not None else None)] if done else []
        for o in self.orders.open_for("laras"):            # "tolong update di timeline"
            if self.TIMELINE_WORD.search(o["text"]):
                self.orders.update(o["id"], "selesai", "timeline sudah diperbarui")
        parts = []
        if done:
            parts.append(f"Sudah saya ubah di timeline: {', '.join(done)} jadi "
                         f"{STATUS_LABEL[status]}. Timeline barunya di papan.")
        if refused:
            parts.append("Yang tidak bisa: " + "; ".join(refused) + ".")
        if not parts:
            parts.append(f"{', '.join(x.title for x in named)} memang sudah "
                         f"{STATUS_LABEL[status]} di timeline, Boss.")
        mood = "apologetic" if refused and not done else "happy"
        return AgentReply("laras", ("Siap, Boss. " if done else "") + " ".join(parts), mood,
                          kind="command", board=shown)

    COST_OK_MINUTES = 30
    YES = re.compile(r"^\s*(?:ya+|iya+|yes|y|ok|oke|okay|okey|sip|lanjut\w*|gas\w*|boleh|"
                     r"silahkan|silakan|setuju|go)\b[^?]{0,20}$", re.I)
    MONEY = re.compile(r"\b(?:uang|duit|biaya|budget|bayar|mahal|izin|ijin|ongkos)\b", re.I)

    def _cost_explained(self, pending: dict) -> AgentReply:
        lim = self.settings.budget.ask_above_usd
        return AgentReply("laras", (
            f"Maaf, Boss. Pertanyaan yang memakai model berbayar saya kasih perkiraan biayanya dulu "
            f"kalau bisa lebih dari ${lim:.2f} (batas di config); yang tadi paling mahal "
            f"~${pending['high']:.4f}. Yang masih menunggu: \"{pending['text'][:80]}\". Ketik \"ya\" "
            f"atau klik Ya untuk lanjut. Setelah Boss setuju sekali, {self.COST_OK_MINUTES} menit ke "
            f"depan tidak saya tanya lagi kecuali biayanya jauh lebih besar."),
            "apologetic", kind="command")

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
        r"\b(?:update|status|kabar|cek)\s+(?:#|nomor\s*|no\.?\s*|perintah\s*|tugas\s*|alur\s*)#?\d+|"
        r"(?:tugas|pekerjaan|kerjaan)\w*\s+(?:rekan|tim|teman|semua)", re.I)
    ORDER_REF = re.compile(r"(?:#|\b(?:perintah|tugas|alur|nomor|no\.?)\s*#?)(\d+)\b", re.I)

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
