"""The Boss's orders: everything the Boss asked a teammate to DO, so nothing is forgotten.

projects/<id>/work/orders.json, one record per order:
    {"id", "ts", "to", "via", "kind" (perintah | pesan | tugas), "text", "summary",
     "status" (baru | dikerjakan | selesai | terhambat), "notes": [...], "done": ts}

A RELAY ("alur", e.g. Bening documents -> Citra maps it -> Kirana puts it in the deck) is a
parent order to Laras with "chain": [{"to", "task", "action"}, ...] and "step"; each step
is a child order ("parent": id) and the next starts when the previous one is done.

The owner sees her open orders every time she answers and must do them with her tools or
say what blocks them; orders the office can do itself (documenting variables, the deck,
the documentation) run as soon as they can.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()
STATUSES = ("baru", "dikerjakan", "selesai", "terhambat")

# what makes a message an order rather than a question or a chat
IMPERATIVE = re.compile(
    r"\b(tolong|buat(?:kan)?|bikin(?:in)?|lakukan|kerjakan|siapkan|terjemah\w*|dokumentasi\w*|"
    r"inferensi|infer|ubah|ganti|perbaiki|jangan lupa|pastikan|tambah\w*|hapus|cek(?:kan)?|"
    r"periksa|susun|lengkapi|kirim(?:kan)?)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_order(text: str) -> bool:
    return bool(IMPERATIVE.search(text)) and not text.strip().endswith("?")


class Orders:
    def __init__(self, project, events=None):
        self.path = project.dir("work") / "orders.json"
        self.events = events
        self.on_done = None                  # the office: advance a relay when a step is done

    def list(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def _save(self, items: list[dict]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, text: str, to: str, via: str | None = None, kind: str = "perintah",
            summary: str = "", **extra) -> dict:
        with _LOCK:
            items = self.list()
            o = {"id": len(items) + 1, "ts": _now(), "to": to, "via": via or to, "kind": kind,
                 "text": text, "summary": summary or text[:120], "status": "baru", "notes": [],
                 "done": None, **extra}
            items.append(o)
            self._save(items)
        if self.events:
            self.events.emit("order", to, id=o["id"], status="baru", summary=o["summary"])
        return o

    def update(self, oid: int, status: str, note: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError(f"status harus salah satu dari {', '.join(STATUSES)}")
        with _LOCK:
            items = self.list()
            o = next((x for x in items if x["id"] == int(oid)), None)
            if o is None:
                raise KeyError(f"perintah #{oid} tidak ada")
            o["status"] = status
            if note:
                o["notes"].append({"ts": _now(), "note": note})
            o["done"] = _now() if status == "selesai" else None
            self._save(items)
        if self.events:
            self.events.emit("order", o["to"], id=o["id"], status=status, summary=o["summary"],
                             note=note)
        if status == "selesai" and self.on_done:
            self.on_done(o)
        return o

    def get(self, oid: int) -> dict:
        return next(x for x in self.list() if x["id"] == int(oid))

    def set_fields(self, oid: int, **fields) -> dict:
        with _LOCK:
            items = self.list()
            o = next(x for x in items if x["id"] == int(oid))
            o.update(fields)
            self._save(items)
        return o

    def open_for(self, agent: str | None = None) -> list[dict]:
        return [o for o in self.list() if o["status"] in ("baru", "dikerjakan", "terhambat")
                and (agent is None or o["to"] == agent)]
