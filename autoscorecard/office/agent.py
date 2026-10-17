"""Answering what the Boss types.

1. Route: "@citra ..." goes to Citra; otherwise keywords pick the agent ($0).
2. Commands: short instructions ("paksa province", "buang noise_a",
   "bins cust_age 25 35 45") become proposals directly, no LLM ($0).
3. Everything else: Laras estimates the cost; above budget.ask_above_usd the Boss
   must say yes first. Then the agent answers with tools (max 3 LLM calls), and
   the actual cost is compared with the estimate.
Offline (no LLM available): an honest templated reply; choices still work.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..llm import LLMRequest, Message, ToolResult
from ..llm.router import AllProvidersFailed
from . import costs
from .characters import MOODS, TEAM, line, system_prompt
from .tools import ToolBox

AGENT_KEY = {"laras": "partner", "bening": "prepper", "citra": "modeller",
             "kirana": "deck_creator"}
MOOD_TAG = re.compile(r"^\s*<mood:(\w+)>\s*")


def route(text: str, default: str = "laras") -> str:
    m = re.match(r"\s*@(\w+)", text)
    if m and m.group(1).lower() in TEAM:
        return m.group(1).lower()
    words = re.findall(r"[a-z0-9_]+", text.lower())
    hits = {cid: sum(1 for w in words if any(w.startswith(k) for k in c.keywords))
            for cid, c in TEAM.items() if cid != "kirana"}
    best = max(hits, key=hits.get)
    return best if hits[best] else default


_CMDS = [
    (re.compile(r"^(?:force|paksa)\s+(\S+)\s*$", re.I), "force_feature"),
    (re.compile(r"^(?:unforce|batal\s*paksa)\s+(\S+)\s*$", re.I), "unforce_feature"),
    (re.compile(r"^(?:exclude|buang)\s+(\S+)\s*$", re.I), "exclude_feature"),
    (re.compile(r"^(?:include|kembalikan)\s+(\S+)\s*$", re.I), "include_feature"),
    (re.compile(r"^(?:bins?|potong)\s+(\S+)\s+(?:di\s+)?([-\d.,;\s]+)$", re.I), "bin_edit"),
]


@dataclass
class AgentReply:
    agent: str
    text: str
    mood: str = "neutral"
    proposals: list[str] = field(default_factory=list)
    estimate: dict | None = None
    actual: dict | None = None
    offline: bool = False
    needs_cost_ok: bool = False      # the Boss must agree to the estimate first

    def to_dict(self) -> dict:
        return {"agent": self.agent, "name": TEAM[self.agent].name, "text": self.text,
                "mood": self.mood, "proposals": self.proposals, "estimate": self.estimate,
                "actual": self.actual, "offline": self.offline,
                "needs_cost_ok": self.needs_cost_ok}


class AgentRuntime:
    def __init__(self, office):
        self.office = office

    # ------------------------------------------------------------- commands ($0)
    def command(self, text: str) -> AgentReply | None:
        body = re.sub(r"^\s*@\w+\s*", "", text).strip()
        for rx, kind in _CMDS:
            m = rx.match(body)
            if not m:
                continue
            feature = m.group(1)
            cols = self.office.columns()
            if cols and feature not in cols:
                close = [c for c in cols if feature.lower() in c.lower()][:5]
                return AgentReply("citra", f"Saya tidak menemukan kolom '{feature}', Boss."
                                  + (f" Maksudnya salah satu ini? {', '.join(close)}"
                                     if close else ""), "apologetic")
            payload = {"feature": feature}
            if kind == "bin_edit":
                payload["edges"] = [float(v) for v in re.split(r"[\s,;]+", m.group(2).strip())
                                    if v]
            p = self.office.proposals.add(kind, payload, "citra", "instruksi Boss")
            return AgentReply("citra", f"Siap, Boss. Saya catat sebagai usulan: {p.summary}. "
                              "Berlaku di cut berikutnya setelah Boss ACC.", "serious", [p.id])
        return None

    # ------------------------------------------------------------- LLM answer
    def respond(self, text: str, to: str | None = None, context: str = "",
                history: list[dict] | None = None, cost_ok: bool = False,
                default: str = "laras") -> AgentReply:
        cmd = self.command(text)
        if cmd is not None:
            return cmd
        agent = to or route(text, default)
        office = self.office
        box = ToolBox(office, agent)
        system = system_prompt(TEAM[agent], context)
        msgs = [Message.user(h["text"]) if h["role"] == "boss" else Message.assistant(h["text"])
                for h in (history or [])[-6:]]
        msgs.append(Message.user(text))
        chars = len(system) + sum(len(h.get("text", "")) for h in (history or [])[-6:]) \
            + len(text) + len(json.dumps([s.parameters for s in box.specs()]))
        est = costs.estimate(office.router, AGENT_KEY[agent], chars, office.project,
                             max_out=500, max_calls=3)
        if est.provider == "offline":
            return AgentReply(agent, line("offline"), "apologetic", offline=True,
                              estimate=est.to_dict())
        if not est.affordable:
            return AgentReply("laras", est.say() + " Saya tahan dulu pertanyaan ini, Boss.",
                              "worried", estimate=est.to_dict())
        if est.high_usd > office.settings.budget.ask_above_usd and not cost_ok:
            office.events.emit("cost_estimate", "laras", **est.to_dict(), question=text[:200])
            return AgentReply("laras", est.say() + " Lanjut, Boss?", "serious",
                              estimate=est.to_dict(), needs_cost_ok=True)

        office.events.emit("agent_status", agent, status="working", task="menjawab Boss")
        total, reply_text, provider = 0.0, "", ""
        try:
            for k in range(3):
                last = k == 2
                resp = office.router.complete(LLMRequest(
                    system, msgs, [] if last else box.specs(), max_tokens=500,
                    agent=AGENT_KEY[agent], project_id=office.project.id))
                total += resp.cost_usd
                provider = resp.provider
                if resp.provider == "offline":
                    return AgentReply(agent, line("offline"), "apologetic", box.created,
                                      offline=True)
                if not resp.tool_calls:
                    reply_text = resp.text
                    break
                msgs.append(resp.as_message())
                msgs.append(Message("user", [ToolResult(c.id, *box.call(c.name, c.args))
                                             for c in resp.tool_calls]))
        except AllProvidersFailed:
            return AgentReply(agent, line("offline"), "apologetic", box.created, offline=True)
        finally:
            office.events.emit("agent_status", agent, status="idle", task="")

        mood = "neutral"
        m = MOOD_TAG.match(reply_text)
        if m:
            mood = m.group(1) if m.group(1) in MOODS else "neutral"
            reply_text = reply_text[m.end():]
        actual = costs.record_actual(office.project, est, total, f"{agent}: {text[:60]}")
        if total > 0:
            office.events.emit("cost_actual", "laras", usd=total, estimate_mid=est.mid_usd,
                               within_range=actual["within_range"], provider=provider)
        return AgentReply(agent, reply_text.strip() or line("ack"), mood, box.created,
                          est.to_dict(), actual)
