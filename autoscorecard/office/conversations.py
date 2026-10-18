"""The conversation log: every time the Boss types, what came back and what it cost.

projects/<id>/conversations.jsonl, one exchange per line:
    {"seq", "ts", "where": "desk" | meeting id, "boss", "to", "agent", "text", "mood",
     "usd", "estimate_high_usd", "provider", "kind", "thought"}

`thought` is whatever the model wrote besides its spoken answer (its <batin>). It is
kept for audit only: the screen never shows it, and the API returns it only when
asked for the audit view.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ConversationLog:
    def __init__(self, project):
        self.path = project.root / "conversations.jsonl"

    def add(self, **entry) -> dict:
        with _LOCK:
            seq = len(self._read()) + 1
            rec = {"seq": seq, "ts": _now(), **entry}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]

    def list(self, since: str | None = None, audit: bool = False) -> list[dict]:
        out = []
        for r in self._read():
            if since and r["ts"] < since:
                continue
            if not audit:                               # say whether there is one, not what
                r = {k: v for k, v in r.items() if k != "thought"} | {
                    "has_thought": bool(r.get("thought"))}
            out.append(r)
        return out

    def total_usd(self, since: str | None = None) -> float:
        return round(sum(r.get("usd") or 0 for r in self.list(since)), 6)
