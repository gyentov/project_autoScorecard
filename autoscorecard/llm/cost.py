"""Cost calculation and the persistent usage ledger (the in-game "energy bar").

Every LLM call is appended to a JSONL file, so spend survives restarts and can
be audited line by line.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from autoscorecard.config import Price
from autoscorecard.llm.types import Usage

PER_MILLION = 1_000_000


def cost_usd(usage: Usage, price: Price) -> float:
    cw = price.cache_write if price.cache_write is not None else price.input
    cr = price.cache_read if price.cache_read is not None else price.input
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_write_tokens * cw
        + usage.cache_read_tokens * cr
    ) / PER_MILLION


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.total_usd = 0.0
        self.by_project: dict[str, float] = {}
        self.by_provider: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self._add(json.loads(line))

    def _add(self, rec: dict) -> None:
        c = rec.get("cost_usd", 0.0)
        self.total_usd += c
        if rec.get("project_id"):
            self.by_project[rec["project_id"]] = self.by_project.get(rec["project_id"], 0.0) + c
        self.by_provider[rec["provider"]] = self.by_provider.get(rec["provider"], 0.0) + c

    def record(self, *, provider: str, model: str, agent: str, project_id: str | None,
               usage: Usage, cost: float) -> dict:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": provider, "model": model, "agent": agent,
            "project_id": project_id, **asdict(usage), "cost_usd": round(cost, 6),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self._add(rec)
        return rec

    def project_usd(self, project_id: str | None) -> float:
        return self.by_project.get(project_id, 0.0) if project_id else 0.0
