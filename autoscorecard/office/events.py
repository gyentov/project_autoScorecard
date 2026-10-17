"""The office event stream: everything that happens in a project, in order.

projects/<id>/events.jsonl, one JSON object per line:
    {"seq": 12, "ts": "...", "type": "agent_status", "actor": "citra", "data": {...}}

The 2b pixel office replays this stream: walking to a desk, a handoff, an email
arriving, a meeting starting. Only REAL events are written here (no fillers);
the needs-driven life simulation (coffee, snacks) runs in the browser.

Event types:
    agent_status      actor is working / idle / waiting_boss, with a task text
    job_started       a background job (build) began        data: job, cut
    job_progress      a step of a job finished              data: job, step, detail
    job_finished      a job ended                           data: job, ok, error?
    handoff           work passed between agents            data: to, memo
    email_sent        an email reached the Boss's inbox     data: email_id, subject, kind
    meeting_scheduled / meeting_started / meeting_ended     data: meeting_id, title
    meeting_choice    the Boss picked a choice              data: meeting_id, choice
    dialogue          a line was spoken                     data: meeting_id?, mood, text
    proposal          a change awaits ACC                   data: proposal_id, summary
    approval          the Boss ACC'd (or rejected)          data: what, id, decision
    milestone         a timeline milestone changed status   data: id, status
    cost_estimate     Laras's estimate before paid work     data: low, high, ...
    cost_actual       what it really cost                   data: usd, estimate_mid
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

_LOCKS: dict[str, threading.Lock] = {}
_SEQ: dict[str, int] = {}          # last seq per file, read from disk once
_GUARD = threading.Lock()


def _lock(path: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


class EventStream:
    def __init__(self, project):
        self.path = project.root / "events.jsonl"

    def _last_seq(self) -> int:
        if not self.path.exists():
            return 0
        last = 0
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["seq"]
        return last

    def emit(self, type: str, actor: str = "system", **data: Any) -> dict:
        key = str(self.path)
        with _lock(key):
            if key not in _SEQ:
                _SEQ[key] = self._last_seq()
            _SEQ[key] += 1
            ev = {"seq": _SEQ[key],
                  "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "type": type, "actor": actor, "data": data}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        return ev

    def since(self, seq: int = 0, limit: int = 500) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    ev = json.loads(line)
                    if ev["seq"] > seq:
                        out.append(ev)
                        if len(out) >= limit:
                            break
        return out
