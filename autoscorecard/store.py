"""Project store: one folder per modelling project.

projects/<id>/
    project.json     metadata (name, created, status, model type)
    raw/             uploaded files, untouched
    work/            intermediate datasets
    artifacts/       charts, model files, scorecard tables, decks
    audit.jsonl      append-only log of every decision and tool call
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

SUBDIRS = ("raw", "work", "artifacts")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_filename(name: str) -> str:
    name = Path(name).name   # strip any directory part
    name = _SAFE_NAME.sub("_", name).strip("._")
    return name or "upload"


@dataclass
class ProjectMeta:
    id: str
    name: str
    model_type: str = "scorecard"
    status: str = "new"
    created: str = field(default_factory=_now)


class Project:
    def __init__(self, root: Path):
        self.root = root

    @property
    def id(self) -> str:
        return self.root.name

    @property
    def meta(self) -> ProjectMeta:
        return ProjectMeta(**json.loads((self.root / "project.json").read_text(encoding="utf-8")))

    def save_meta(self, meta: ProjectMeta) -> None:
        (self.root / "project.json").write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")

    def dir(self, sub: str) -> Path:
        return self.root / sub

    def save_upload(self, filename: str, stream: BinaryIO) -> Path:
        dest = self.dir("raw") / safe_filename(filename)
        with dest.open("wb") as f:
            shutil.copyfileobj(stream, f)
        self.audit("system", "upload", {"file": dest.name, "bytes": dest.stat().st_size})
        return dest

    def raw_files(self) -> list[Path]:
        return sorted(p for p in self.dir("raw").iterdir() if p.is_file())

    def audit(self, actor: str, action: str, detail: dict[str, Any] | None = None,
              reason: str | None = None) -> dict[str, Any]:
        rec = {"ts": _now(), "actor": actor, "action": action,
               "detail": detail or {}, "reason": reason}
        with (self.root / "audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def audit_log(self) -> list[dict[str, Any]]:
        p = self.root / "audit.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class ProjectStore:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def create(self, name: str, model_type: str = "scorecard") -> Project:
        pid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        root = self.base / pid
        root.mkdir()
        for sub in SUBDIRS:
            (root / sub).mkdir()
        proj = Project(root)
        proj.save_meta(ProjectMeta(id=pid, name=name, model_type=model_type))
        proj.audit("system", "project_created", {"name": name, "model_type": model_type})
        return proj

    def get(self, pid: str) -> Project:
        root = self.base / safe_filename(pid)
        if not (root / "project.json").exists():
            raise KeyError(pid)
        return Project(root)

    def list(self) -> list[ProjectMeta]:
        return [Project(p).meta for p in sorted(self.base.iterdir())
                if (p / "project.json").exists()]
