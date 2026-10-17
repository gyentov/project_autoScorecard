"""Where the project stands: stage, approved decisions, cuts. work/office.json"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field

_LOCK = threading.RLock()


@dataclass
class OfficeState:
    stage: str = "new"         # new, kickoff, data, target, plan, cut, final
    join_plan: dict | None = None          # {"base": table, "joins": [...]}
    join_approved: bool = False
    target: dict | None = None             # column, rule, value, date_col, oot_from, ...
    target_approved: bool = False
    exclude: dict[str, str] = field(default_factory=dict)   # feature -> reason
    cuts: dict[str, dict] = field(default_factory=dict)     # cut id -> build info
    current_cut: str = "cut1"
    feedback: dict[str, list[str]] = field(default_factory=dict)
    open_meeting: str | None = None

    @classmethod
    def load(cls, project) -> "OfficeState":
        p = project.dir("work") / "office.json"
        with _LOCK:
            return cls(**json.loads(p.read_text(encoding="utf-8"))) if p.exists() else cls()

    def save(self, project) -> None:
        with _LOCK:
            (project.dir("work") / "office.json").write_text(
                json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")
