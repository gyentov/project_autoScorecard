"""Project timeline: milestones in order (no dates), with owner, status and
dependencies, and the Gantt-style slide Laras projects in meetings.

Positions on the chart are stage slots, not dates: a milestone starts one slot
after the last of its dependencies ends. Extensions (Cut 3, Cut 4, ...) are
inserted before the deck, which then moves one slot to the right.

projects/<id>/work/timeline.json
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from xml.sax.saxutils import escape

from .characters import TEAM

STATUSES = ("not_started", "in_progress", "waiting_boss", "done")
STATUS_LABEL = {"not_started": "belum mulai", "in_progress": "sedang dikerjakan",
                "waiting_boss": "menunggu Boss", "done": "selesai"}
STATUS_ICON = {"not_started": "○", "in_progress": "▶", "waiting_boss": "⏳", "done": "✓"}


@dataclass
class Milestone:
    id: str
    title: str
    owner: str                          # character id
    depends_on: list[str] = field(default_factory=list)
    length: int = 1                     # stage slots it spans
    status: str = "not_started"
    note: str = ""


def default_milestones() -> list[Milestone]:
    return [
        Milestone("kickoff", "Kickoff & timeline", "laras"),
        Milestone("data", "Data & join", "bening", ["kickoff"]),
        Milestone("target", "Definisi target", "bening", ["data"]),
        Milestone("dictionary", "Kamus data", "bening", ["kickoff"], length=3),
        Milestone("waterfall_plan", "Rencana waterfall", "citra", ["target"]),
        Milestone("cut1", "Cut 1", "citra", ["waterfall_plan"]),
        Milestone("cut2", "Cut 2 (final)", "citra", ["cut1"]),
        Milestone("deck", "Deck → PDF/PPTX", "kirana", ["cut2"]),
    ]


class Timeline:
    def __init__(self, project, events=None):
        self.path = project.dir("work") / "timeline.json"
        self.events = events
        if self.path.exists():
            self.items = [Milestone(**m) for m in json.loads(self.path.read_text(encoding="utf-8"))]
        else:
            self.items = default_milestones()
            self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(m) for m in self.items], indent=2),
                             encoding="utf-8")

    def get(self, mid: str) -> Milestone:
        for m in self.items:
            if m.id == mid:
                return m
        raise KeyError(mid)

    def set(self, mid: str, status: str, note: str | None = None) -> Milestone:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status}")
        m = self.get(mid)
        changed = m.status != status or (note is not None and note != m.note)
        m.status = status
        if note is not None:
            m.note = note
        self.save()
        if changed and self.events:
            self.events.emit("milestone", m.owner, id=mid, status=status, note=m.note)
        return m

    def cuts(self) -> list[Milestone]:
        return [m for m in self.items if m.id.startswith("cut")]

    def add_extension(self, reason: str) -> Milestone:
        """Append the next cut (Cut 3, Cut 4, ...) before the deck."""
        last = self.cuts()[-1]
        n = int(last.id[3:]) + 1
        m = Milestone(f"cut{n}", f"Cut {n} (perpanjangan)", "citra", [last.id], note=reason)
        idx = self.items.index(self.get("deck"))
        self.items.insert(idx, m)
        self.get("deck").depends_on = [m.id]
        if last.title.endswith("(final)"):
            last.title = last.title.replace(" (final)", "")
        m.title = f"Cut {n} (final)"
        self.save()
        if self.events:
            self.events.emit("milestone", "laras", id=m.id, status=m.status, note=reason,
                             added=True)
        return m

    def slots(self) -> dict[str, tuple[int, int]]:
        """(first, last) stage slot of every milestone, from the dependencies."""
        pos: dict[str, tuple[int, int]] = {}
        for m in self.items:                         # items are in dependency order
            start = 1 + max((pos[d][1] for d in m.depends_on), default=0)
            pos[m.id] = (start, start + m.length - 1)
        return pos

    def current(self) -> Milestone | None:
        return next((m for m in self.items if m.status != "done"), None)

    def to_list(self) -> list[dict]:
        pos = self.slots()
        return [{**asdict(m), "slot_start": pos[m.id][0], "slot_end": pos[m.id][1]}
                for m in self.items]


# --------------------------------------------------------------------------- Gantt slide

_CSS = """<style>
  .bg{fill:#fcfcfb} .t1{fill:#0b0b0b} .t2{fill:#52514e} .mut{fill:#898781}
  .rule{stroke:#e4e3df} .done{fill:#2a78d6} .prog{fill:#86b6ef}
  .wait{fill:#fab219} .todo{fill:none;stroke:#898781;stroke-dasharray:4 3}
  .now{stroke:#d03b3b}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1a1a19} .t1{fill:#f0efec} .t2{fill:#c3c2b7} .rule{stroke:#383835}
    .done{fill:#3987e5} .prog{fill:#184f95}
  }
  text{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
</style>"""
_CLS = {"done": "done", "in_progress": "prog", "waiting_boss": "wait", "not_started": "todo"}


def gantt_svg(tl: Timeline, title: str = "Timeline proyek") -> str:
    """Rows = milestones (owner, status icon + label), columns = stage slots."""
    pos = tl.slots()
    n_slots = max(e for _, e in pos.values())
    pad, label_w, status_w, row_h = 16, 230, 150, 34
    slot_w = max(44, (900 - pad * 2 - label_w - status_w) // n_slots)
    W = pad * 2 + label_w + status_w + n_slots * slot_w
    top = pad + 44
    H = top + len(tl.items) * row_h + 34
    x0 = pad + label_w + status_w
    cur = tl.current()
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
           f'height="{H}" role="img" aria-label="{escape(title)}">', _CSS,
           f'<rect class="bg" width="{W}" height="{H}"/>',
           f'<text class="t1" x="{pad}" y="{pad + 14}" font-size="16" font-weight="600">'
           f'{escape(title)}</text>',
           f'<text class="mut" x="{x0}" y="{top - 10}" font-size="11">tahap →</text>']
    for k in range(n_slots):
        out.append(f'<text class="mut" x="{x0 + k * slot_w + slot_w / 2}" y="{top - 10}" '
                   f'font-size="11" text-anchor="middle">{k + 1}</text>')
    for i, m in enumerate(tl.items):
        y = top + i * row_h
        s, e = pos[m.id]
        owner = TEAM[m.owner].name
        bx, bw = x0 + (s - 1) * slot_w + 3, (e - s + 1) * slot_w - 6
        tip = f"{m.title} · {owner} · {STATUS_LABEL[m.status]}" + (f" · {m.note}" if m.note else "")
        out.append(
            f'<g><title>{escape(tip)}</title>'
            f'<line class="rule" x1="{pad}" x2="{W - pad}" y1="{y + 0.5}" y2="{y + 0.5}"/>'
            f'<text class="t1" x="{pad}" y="{y + 21}" font-size="12.5" font-weight="600">'
            f'{escape(m.title)}<tspan class="t2" font-weight="400" dx="6">{escape(owner)}'
            f'</tspan></text>'
            f'<text class="t2" x="{pad + label_w}" y="{y + 21}" font-size="12">'
            f'{STATUS_ICON[m.status]} {STATUS_LABEL[m.status]}</text>'
            f'<rect class="{_CLS[m.status]}" x="{bx}" y="{y + 9}" width="{bw}" height="16" '
            f'rx="4"/></g>')
    if cur is not None:
        s, _ = pos[cur.id]
        nx = x0 + (s - 1) * slot_w + 1
        out.append(f'<line class="now" x1="{nx}" x2="{nx}" y1="{top - 4}" '
                   f'y2="{top + len(tl.items) * row_h}" stroke-width="2"/>'
                   f'<text class="t1" x="{nx + 4}" y="{top + len(tl.items) * row_h + 16}" '
                   f'font-size="11" font-weight="600">sekarang</text>')
    out.append("</svg>")
    return "\n".join(out)
