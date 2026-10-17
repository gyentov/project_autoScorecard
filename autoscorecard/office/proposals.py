"""Proposals: changes an agent suggests, waiting for the Boss's ACC.

Nothing that changes the model happens without ACC. An accepted proposal is
written where the next build reads it (finalization inputs, waterfall plan,
office state); it takes effect at the next cut.

projects/<id>/work/proposals.json
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ..engine import finalization as fin
from ..engine.waterfall import load_plan, parse_steps, save_plan
from .state import OfficeState

KINDS = {
    "force_feature": "paksa fitur dicoba di model",
    "unforce_feature": "batalkan paksa fitur",
    "exclude_feature": "buang fitur dari kandidat",
    "include_feature": "kembalikan fitur ke kandidat",
    "bin_edit": "ubah bin secara manual",
    "woe_override": "ubah WoE secara manual",
    "plan_change": "ubah parameter waterfall",
    "target": "ubah definisi target",
}


@dataclass
class Proposal:
    id: str
    kind: str
    payload: dict
    by: str                    # character id
    summary: str
    reason: str = ""
    status: str = "pending"    # pending | accepted | rejected
    created: str = ""
    decided: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Proposals:
    def __init__(self, project, events=None):
        self.project = project
        self.path = project.dir("work") / "proposals.json"
        self.events = events

    def _all(self) -> list[Proposal]:
        if not self.path.exists():
            return []
        return [Proposal(**p) for p in json.loads(self.path.read_text(encoding="utf-8"))]

    def _write(self, items: list[Proposal]) -> None:
        self.path.write_text(json.dumps([asdict(p) for p in items], indent=2), encoding="utf-8")

    def list(self, status: str | None = None) -> list[Proposal]:
        return [p for p in self._all() if status is None or p.status == status]

    def add(self, kind: str, payload: dict, by: str, reason: str = "") -> Proposal:
        if kind not in KINDS:
            raise ValueError(f"unknown proposal kind {kind}")
        _validate(kind, payload, reason)
        same = next((x for x in self.list("pending") if x.kind == kind and x.payload == payload),
                    None)
        if same is not None:                  # asked twice: one proposal, not two
            return same
        p = Proposal(uuid.uuid4().hex[:8], kind, payload, by, describe(kind, payload), reason,
                     created=_now())
        self._write(self._all() + [p])
        self.project.audit(by, "proposal", asdict(p), reason or None)
        if self.events:
            self.events.emit("proposal", by, proposal_id=p.id, kind=kind, summary=p.summary)
        return p

    def decide(self, pid: str, accept: bool, by: str = "boss") -> Proposal:
        items = self._all()
        p = next((x for x in items if x.id == pid), None)
        if p is None:
            raise KeyError(pid)
        if p.status != "pending":
            raise ValueError(f"proposal {pid} is already {p.status}")
        if accept:
            _apply(self.project, p)
        p.status = "accepted" if accept else "rejected"
        p.decided = _now()
        self._write(items)
        self.project.audit(by, "proposal_" + p.status, {"id": p.id, "kind": p.kind,
                                                       "payload": p.payload})
        if self.events:
            self.events.emit("approval", by, what="proposal", id=p.id,
                             decision=p.status, summary=p.summary)
        return p


def describe(kind: str, pl: dict) -> str:
    f = pl.get("feature", "")
    return {
        "force_feature": lambda: f"Paksa {f} dicoba di model final",
        "unforce_feature": lambda: f"Batalkan paksa {f}",
        "exclude_feature": lambda: f"Buang {f} dari kandidat",
        "include_feature": lambda: f"Kembalikan {f} ke kandidat",
        "bin_edit": lambda: (f"Bin manual {f}: potong di {pl['edges']}" if "edges" in pl
                             else f"Bin manual {f}: grup {pl['groups']}"),
        "woe_override": lambda: f"WoE manual {f}: {pl['overrides']}",
        "plan_change": lambda: f"Waterfall {pl['step']}: {pl.get('params', {})}"
                               + ("" if pl.get("enabled", True) else " (dimatikan)"),
        "target": lambda: f"Target: {pl['column']} {pl['rule']} {pl['value']}",
    }[kind]()


def _validate(kind: str, pl: dict, reason: str) -> None:
    need = {"force_feature": ["feature"], "unforce_feature": ["feature"],
            "exclude_feature": ["feature"], "include_feature": ["feature"],
            "bin_edit": ["feature"], "woe_override": ["feature", "overrides"],
            "plan_change": ["step"], "target": ["column", "rule", "value"]}[kind]
    missing = [k for k in need if k not in pl]
    if missing:
        raise ValueError(f"{kind} needs {missing}")
    if kind == "bin_edit" and ("edges" in pl) == ("groups" in pl):
        raise ValueError("bin_edit needs exactly one of edges / groups")
    if kind == "woe_override" and not reason:
        raise ValueError("a WoE override needs a reason")
    if kind == "plan_change":       # unknown step or bad parameters fail here, not at ACC
        parse_steps([{"type": pl["step"], "params": pl.get("params", {})}])
    if kind == "target" and pl["rule"] not in ("eq", "ge", "in"):
        raise ValueError("target rule must be eq, ge or in")


def _apply(project, p: Proposal) -> None:
    pl = p.payload
    if p.kind in ("force_feature", "unforce_feature", "bin_edit", "woe_override"):
        inputs = fin.load_inputs(project)
        inputs = {k: inputs.get(k) or type(fin.EMPTY[k])() for k in fin.EMPTY}
        f = pl["feature"]
        if p.kind == "force_feature" and f not in inputs["forced_features"]:
            inputs["forced_features"].append(f)
        elif p.kind == "unforce_feature":
            inputs["forced_features"] = [x for x in inputs["forced_features"] if x != f]
        elif p.kind == "bin_edit":
            inputs["manual_bins"][f] = {"edges": pl["edges"]} if "edges" in pl else \
                {"groups": pl["groups"]}
        elif p.kind == "woe_override":
            inputs["woe_overrides"][f] = pl["overrides"]
        if p.reason:
            inputs["reasons"][f] = p.reason
        fin.save_inputs(project, inputs, by="boss")
    elif p.kind in ("exclude_feature", "include_feature"):
        st = OfficeState.load(project)
        if p.kind == "exclude_feature":
            st.exclude[pl["feature"]] = p.reason or "dibuang atas keputusan Boss"
        else:
            st.exclude.pop(pl["feature"], None)
        st.save(project)
    elif p.kind == "plan_change":
        plan = load_plan(project)
        steps = plan["steps"]
        for s in steps:
            if s["type"] == pl["step"]:
                s["params"] = {**s.get("params", {}), **pl.get("params", {})}
                if "enabled" in pl:
                    s["enabled"] = bool(pl["enabled"])
                break
        else:
            steps.insert(0, {"type": pl["step"], "params": pl.get("params", {})})
        save_plan(project, parse_steps(steps), approved=True, by="boss")   # ACC'd change
    elif p.kind == "target":
        st = OfficeState.load(project)
        st.target = {**(st.target or {}), "column": pl["column"], "rule": pl["rule"],
                     "value": pl["value"]}
        st.save(project)
