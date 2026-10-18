"""Where the project stands: stage, approved decisions, cuts. work/office.json"""
from __future__ import annotations

import copy
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
    data_files: list[str] | None = None    # raw/ files the Boss handed over (None = all)
    plan_edits: list[dict] = field(default_factory=list)   # the Boss's waterfall edits {ts, summary}
    messages: list[dict] = field(default_factory=list)     # notes from the Boss, delivered to a teammate

    @classmethod
    def load(cls, project) -> "OfficeState":
        p = project.dir("work") / "office.json"
        with _LOCK:
            st = cls(**json.loads(p.read_text(encoding="utf-8"))) if p.exists() else cls()
            st._orig = copy.deepcopy(asdict(st))      # what this copy started from
            return st

    def save(self, project) -> None:
        """Write only the fields this copy changed onto the latest saved state, so two
        threads changing different things (the stage, the message list) never undo each
        other's work."""
        p = project.dir("work") / "office.json"
        with _LOCK:
            mine = asdict(self)
            orig = getattr(self, "_orig", None)
            if orig is not None and p.exists():
                merged = json.loads(p.read_text(encoding="utf-8"))
                for k, v in mine.items():
                    if v != orig.get(k):
                        merged[k] = v
            else:
                merged = mine
            p.write_text(json.dumps(merged, indent=2, default=str), encoding="utf-8")
            self._orig = copy.deepcopy(merged)
