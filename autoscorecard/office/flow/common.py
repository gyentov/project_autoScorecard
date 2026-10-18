"""Shared helpers and constants for the office flow (the Office mixins)."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from ..meetings import choice


ACTION_LABEL = {"document_variables": "mendokumentasikan arti variabel",
                "feature_meanings": "memasangkan kamus ke fitur model",
                "make_deck": "menyusun draft deck",
                "make_documentation": "menyusun dokumentasi model"}

_DATA: dict[str, dict] = {}          # project id -> {"df", "y"} (row-level, never sent to LLMs)
_JOB_LOCK = threading.Lock()


def plan_diff(old, new) -> list[str]:
    """What changed between two waterfall plans, in words: order, on/off, parameters."""
    out = []
    o = {s.type: s for s in old}
    n = {s.type: s for s in new}
    for t in n.keys() - o.keys():
        out.append(f"{n[t].title} ditambahkan")
    for t in o.keys() - n.keys():
        out.append(f"{o[t].title} dihapus")
    for t in [s.type for s in new if s.type in o]:
        a, b = o[t], n[t]
        if a.enabled != b.enabled:
            out.append(f"{b.title} {'diaktifkan' if b.enabled else 'dimatikan'}")
        diffs = [f"{k} {a.params.get(k)} → {v}" for k, v in b.params.items() if a.params.get(k) != v]
        if diffs:
            out.append(f"{b.title}: " + ", ".join(diffs))
    on_old = [s.type for s in old if s.enabled and s.type in n]
    on_new = [s.type for s in new if s.enabled and s.type in o]
    if [t for t in on_old if t in on_new] != [t for t in on_new if t in on_old]:
        out.append("urutan: " + " → ".join(n[t].title for t in [s.type for s in new if s.enabled]))
    return out


def json_safe(obj):
    """NaN / numpy values out, so the state always serialises (the API sends it)."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and obj != obj:
        return None
    return obj


def adjourn_choice() -> dict:
    """Every decision meeting can end without ACC (the Boss is never forced)."""
    return choice("adjourn", "Belum ACC: bubarkan rapat, kerjakan masukan saya dulu", "adjourn")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
