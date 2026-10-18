"""Teamwork: orders dispatched to the owner, relays (several teammates in turn), ad hoc meetings."""
from __future__ import annotations

import html
import re
import threading

from .. import deliverables as DELIV
from ..characters import TEAM
from ..meetings import choice, say
from ..timeline import gantt_svg
from .common import ACTION_LABEL


class RelayMixin:
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

    def start_relay(self, steps: list[dict], via: str = "laras", text: str = "",
                    for_order: int | None = None) -> dict:
        """Several teammates in turn: each step starts when the one before is done, and
        Laras tells the Boss at every step. `for_order`: the Boss's order the relay carries
        out, closed with it."""
        chain = []
        for st_ in steps:
            to = st_["to"]
            if to not in TEAM:
                raise ValueError(f"tidak ada anggota tim '{to}'")
            action = st_.get("action") or self._step_action(to, st_["task"], chain)
            prev = TEAM[chain[-1]["to"]].name if chain else ""
            chain.append({"to": to, "task": self._step_task(st_["task"], action, prev), "action": action})
        plan = " → ".join(f"{TEAM[c['to']].name}: {c['task']}" for c in chain)
        extra = {}
        if for_order is not None:
            if self.orders.get(for_order)["status"] == "selesai":
                raise ValueError(f"perintah #{for_order} sudah selesai")
            extra["for_order"] = int(for_order)
        parent = self.orders.add(text or plan, "laras", via=via, kind="alur",
                                 summary=f"Alur: {plan}"[:240], chain=chain, step=0, **extra)
        if for_order is not None:
            self.orders.update(for_order, "dikerjakan", f"dikerjakan lewat alur #{parent['id']}")
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
            if parent.get("for_order"):
                self.orders.update(parent["for_order"], "selesai", f"selesai lewat alur #{pid}")
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
                  say(host, "happy", "Silakan, Boss. Ketik pertanyaan ke siapa saja, atau ke kami "
                      "semua; kami jawab bergantian. Sebut satu nama kalau mau dengar satu orang "
                      "saja. Rapat selesai kalau Boss yang menutupnya.", 0)]
        m = self.meetings.create("adhoc", f"Rapat: {topic}"[:80], host, who, slides, script,
                                 [choice("done", "Selesai, tutup rapat", "adhoc_end")],
                                 context=f"Rapat koordinasi tentang: {topic}. {agenda}",
                                 invite_body=agenda or topic, attachments=files)
        self._update(open_meeting=m.id)
        return {"meeting_id": m.id}

    def _do_adhoc_end(self, m) -> dict:
        self._end(m, "Rapat koordinasi selesai.", ["Laras: jalankan arahan Boss dari rapat ini."])
        return {"ended": True}
