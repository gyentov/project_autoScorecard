"""The Boss's inbox: MOMs, memos, meeting invites and results, as emails.

projects/<id>/inbox/<email_id>.json. Attachments are paths inside the project
(artifacts or meeting slides), never row-level data.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

KINDS = ("mom", "memo", "invite", "results", "timeline", "info")


@dataclass
class Email:
    id: str
    sender: str                    # character id
    subject: str
    body_md: str
    kind: str = "info"
    attachments: list[str] = field(default_factory=list)
    meeting_id: str | None = None
    sent: str = ""
    read: bool = False


class Inbox:
    def __init__(self, project, events=None):
        self.dir = project.root / "inbox"
        self.dir.mkdir(exist_ok=True)
        self.events = events

    def send(self, sender: str, subject: str, body_md: str, kind: str = "info",
             attachments: list[str] | None = None, meeting_id: str | None = None) -> Email:
        if kind not in KINDS:
            raise ValueError(f"unknown email kind {kind}")
        e = Email(datetime.now().strftime("%Y%m%d%H%M%S%f-") + uuid.uuid4().hex[:4], sender,
                  subject, body_md, kind, list(attachments or []), meeting_id,
                  datetime.now(timezone.utc).isoformat(timespec="seconds"))
        (self.dir / f"{e.id}.json").write_text(json.dumps(asdict(e), indent=2), encoding="utf-8")
        if self.events:
            self.events.emit("email_sent", sender, email_id=e.id, subject=subject, kind=kind,
                             meeting_id=meeting_id)
        return e

    def get(self, email_id: str) -> Email:
        p = self.dir / f"{email_id}.json"
        if not p.exists() or p.parent != self.dir:
            raise KeyError(email_id)
        return Email(**json.loads(p.read_text(encoding="utf-8")))

    def mark_read(self, email_id: str) -> Email:
        e = self.get(email_id)
        e.read = True
        (self.dir / f"{e.id}.json").write_text(json.dumps(asdict(e), indent=2), encoding="utf-8")
        return e

    def list(self) -> list[Email]:
        return sorted((Email(**json.loads(p.read_text(encoding="utf-8")))
                       for p in self.dir.glob("*.json")), key=lambda e: e.id)
