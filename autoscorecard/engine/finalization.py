"""Finalization inputs (manual edits, forced features) and the confirmation step.

Inputs live in projects/<id>/work/finalization.json:
    forced_features  features that must be tried in the final model
    manual_bins      feature -> {"edges": [...]} or {"groups": [[...]]}
    woe_overrides    feature -> {bin label: WoE}
    reasons          feature -> why it was edited or forced (required for WoE overrides)

Every build is a DRAFT with its own build_id (artifacts/finalization_status.json).
The user reviews finalization_review.md and confirms that exact build. Editing
the inputs after a build makes that build impossible to confirm: rebuild first.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

EMPTY = {"forced_features": [], "manual_bins": {}, "woe_overrides": {}, "reasons": {}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def inputs_hash(inputs: dict) -> str:
    core = {k: inputs.get(k, EMPTY[k]) for k in EMPTY}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:12]


def validate_inputs(inputs: dict) -> dict:
    unknown = set(inputs) - set(EMPTY) - {"updated"}
    if unknown:
        raise ValueError(f"unknown finalization field(s) {sorted(unknown)}; allowed: {list(EMPTY)}")
    out = {k: inputs.get(k) or type(EMPTY[k])() for k in EMPTY}
    if not all(isinstance(f, str) for f in out["forced_features"]):
        raise ValueError("forced_features must be a list of column names")
    for f, spec in out["manual_bins"].items():
        if set(spec) not in ({"edges"}, {"groups"}):
            raise ValueError(f"manual_bins[{f!r}] needs exactly one of 'edges' or 'groups'")
    for f, ov in out["woe_overrides"].items():
        if not isinstance(ov, dict) or not ov:
            raise ValueError(f"woe_overrides[{f!r}] must map bin labels to WoE values")
        if not out["reasons"].get(f):
            raise ValueError(f"a WoE override on {f!r} needs a reason in 'reasons'")
        for b, w in ov.items():
            float(w)
    return out


def load_inputs(project) -> dict:
    p = project.dir("work") / "finalization.json"
    if not p.exists():
        return {**EMPTY, "updated": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_inputs(project, inputs: dict, by: str = "user") -> dict:
    rec = {**validate_inputs(inputs), "updated": _now()}
    (project.dir("work") / "finalization.json").write_text(json.dumps(rec, indent=2),
                                                            encoding="utf-8")
    project.audit(by, "finalization_inputs_saved", rec)
    return rec


# --------------------------------------------------------------------------- confirmation

def _status_path(project):
    return project.dir("artifacts") / "finalization_status.json"


def load_status(project) -> dict | None:
    p = _status_path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_pending(project, build_id: str, inputs: dict, summary: dict[str, Any]) -> dict:
    rec = {"build_id": build_id, "status": "pending_confirmation", "built": _now(),
           "inputs_hash": inputs_hash(inputs), "inputs": inputs, "summary": summary,
           "confirmed_by": None, "confirmed_at": None, "note": None}
    _status_path(project).write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    project.audit("engine", "finalization_pending", {"build_id": build_id, **summary})
    return rec


def confirm(project, build_id: str, by: str = "user", note: str | None = None) -> dict:
    """Confirm the finalization of one build. Refuses if it is not the latest build,
    or if the edits / forced features changed since that build ran."""
    st = load_status(project)
    if st is None:
        raise ValueError("nothing to confirm: no build yet")
    if st["build_id"] != build_id:
        raise ValueError(f"build {build_id} is not the latest build ({st['build_id']})")
    if st["status"] == "confirmed":
        return st
    current = load_inputs(project)
    if (project.dir("work") / "finalization.json").exists() and \
            inputs_hash(current) != st["inputs_hash"]:
        raise ValueError("the finalization inputs changed after this build; rebuild first")
    st.update(status="confirmed", confirmed_by=by, confirmed_at=_now(), note=note)
    _status_path(project).write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
    sc_path = project.dir("artifacts") / "scorecard.json"
    if sc_path.exists():
        sc = json.loads(sc_path.read_text(encoding="utf-8"))
        sc.setdefault("meta", {}).update(status="confirmed", confirmed_by=by,
                                         confirmed_at=st["confirmed_at"])
        sc_path.write_text(json.dumps(sc, indent=2), encoding="utf-8")
    project.audit(by, "finalization_confirmed", {"build_id": build_id}, note)
    return st
