"""Meetings, visual-novel style: a script of lines (speaker, mood, slide, text)
played over slides on the meeting-room projector, then choices for the Boss
plus a free-text box. When a meeting ends, Laras sends the MOM by email.

projects/<id>/work/meetings/<meeting_id>.json, slides in .../<meeting_id>/
"""
from __future__ import annotations

import html
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .characters import MOODS, POSES, TEAM


@dataclass
class Meeting:
    id: str
    kind: str                         # kickoff, data, target, plan, results
    title: str
    host: str
    attendees: list[str]
    slides: list[dict]                # {"title", "file"}: the prepared deck on the projector
    script: list[dict]                # {"speaker", "mood", "pose", "slide", "text"}
    choices: list[dict]               # {"id", "label", "action", "payload"}
    context: str = ""                 # facts for the agents when the Boss types
    cut: str | None = None
    status: str = "scheduled"         # scheduled, in_progress, ended
    transcript: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    created: str = ""
    invite_email: str | None = None
    board: list[dict] = field(default_factory=list)   # put up on request (queries): own tab


def pose_for(mood: str, slide: int | None = None) -> str:
    """The standing-art pose that fits a line: the mood decides, a slide on screen
    makes a calm line a presenting one."""
    by_mood = {"proud": "presenting", "thinking": "thinking", "worried": "thinking",
               "apologetic": "bowing", "excited": "cheering", "serious": "arms_crossed",
               "happy": "explaining"}
    if mood in by_mood:
        return by_mood[mood]
    return "presenting" if slide is not None else "neutral"


def say(speaker: str, mood: str, text: str, slide: int | None = None,
        pose: str | None = None, board: int | None = None) -> dict:
    if mood not in MOODS:
        raise ValueError(f"unknown mood {mood}")
    pose = pose or pose_for(mood, slide)
    if pose not in POSES:
        raise ValueError(f"unknown pose {pose}")
    return {"speaker": speaker, "mood": mood, "pose": pose, "slide": slide, "board": board,
            "text": text}


def choice(cid: str, label: str, action: str, **payload) -> dict:
    return {"id": cid, "label": label, "action": action, "payload": payload}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Meetings:
    def __init__(self, project, events, inbox):
        self.project, self.events, self.inbox = project, events, inbox
        self.dir = project.dir("work") / "meetings"
        self.dir.mkdir(exist_ok=True)

    def _path(self, mid: str):
        p = self.dir / f"{mid}.json"
        if p.parent != self.dir:
            raise KeyError(mid)
        return p

    def get(self, mid: str) -> Meeting:
        p = self._path(mid)
        if not p.exists():
            raise KeyError(mid)
        return Meeting(**json.loads(p.read_text(encoding="utf-8")))

    def save(self, m: Meeting) -> None:
        self._path(m.id).write_text(json.dumps(asdict(m), indent=2, default=str),
                                    encoding="utf-8")

    def list(self) -> list[Meeting]:
        return sorted((Meeting(**json.loads(p.read_text(encoding="utf-8")))
                       for p in self.dir.glob("*.json")), key=lambda m: m.created)

    def create(self, kind: str, title: str, host: str, attendees: list[str],
               slides: list[tuple[str, str, str]], script: list[dict], choices: list[dict],
               context: str = "", cut: str | None = None, invite_body: str = "",
               attachments: list[str] | None = None) -> Meeting:
        """slides: (title, content, extension) with extension "svg", "html" (wrapped as a
        projector page) or "page" (a complete page, e.g. a deck slide, written as is)."""
        mid = datetime.now().strftime("%Y%m%d%H%M%S%f-") + uuid.uuid4().hex[:4]
        sdir = self.dir / mid
        sdir.mkdir()
        files = []
        for k, (stitle, content, ext) in enumerate(slides):
            f = sdir / f"slide{k + 1}.{'html' if ext == 'page' else ext}"
            f.write_text(content if ext == "page" else light_only(content) if ext == "svg"
                         else _page(stitle, light_only(content)), encoding="utf-8")
            files.append({"title": stitle, "file": str(f.relative_to(self.project.root))})
        m = Meeting(mid, kind, title, host, attendees, files, script, choices, context, cut,
                    created=_now())
        names = ", ".join(TEAM[a].name for a in attendees)
        body = (f"Halo Boss,\n\nSaya mengundang Boss ke rapat **{title}**.\n\n"
                f"Peserta: Boss, {names}.\n\n{invite_body}\n\nAgenda:\n"
                + "\n".join(f"{k + 1}. {s['title']}" for k, s in enumerate(files))
                + "\n\nSalam,\nLaras")
        email = self.inbox.send("laras", f"Undangan rapat: {title}", body, "invite",
                                attachments=attachments, meeting_id=mid)
        m.invite_email = email.id
        self.save(m)
        self.events.emit("meeting_scheduled", host, meeting_id=mid, title=title, kind=kind)
        return m

    def start(self, mid: str) -> Meeting:
        m = self.get(mid)
        if m.status == "scheduled":
            m.status = "in_progress"
            self.save(m)
            self.events.emit("meeting_started", m.host, meeting_id=mid, title=m.title,
                             attendees=m.attendees)
            for ln in m.script:
                self.events.emit("dialogue", ln["speaker"], meeting_id=mid, mood=ln["mood"],
                                 pose=ln.get("pose"), slide=ln["slide"], board=ln.get("board"),
                                 text=ln["text"])
        return m

    def add_turn(self, m: Meeting, who: str, text: str, mood: str = "neutral",
                 cost: float = 0.0, slide: int | None = None, board: int | None = None) -> None:
        """`slide`: the deck slide this line is about; `board`: what the speaker put up on
        the board tab with it (a query, a table)."""
        m.board = self.get(m.id).board               # keep items the board added this turn
        m.transcript.append({"who": who, "text": text, "mood": mood, "ts": _now(),
                             "slide": slide, "board": board})
        m.cost_usd += cost
        self.save(m)
        self.events.emit("dialogue", who, meeting_id=m.id, mood=mood, text=text, slide=slide,
                         board=board)

    def end(self, m: Meeting, decision: str, next_steps: list[str],
            timeline_md: str = "", boss_summary: str = "") -> Meeting:
        m.status = "ended"
        m.decisions.append({"decision": decision, "ts": _now()})
        self.save(m)
        self.events.emit("meeting_ended", m.host, meeting_id=m.id, decision=decision)
        self.inbox.send("laras", f"MOM: {m.title}",
                        mom_markdown(m, next_steps, timeline_md, boss_summary),
                        "mom", attachments=[s["file"] for s in m.slides], meeting_id=m.id)
        return m


def mom_markdown(m: Meeting, next_steps: list[str], timeline_md: str = "",
                 boss_summary: str = "") -> str:
    """The Boss's input appears as one summarised line (`boss_summary`), not word for
    word; the team's answers follow. (The exact words are in the conversation log.)"""
    names = ", ".join(["Boss"] + [TEAM[a].name for a in m.attendees])
    out = [f"# Minutes of Meeting: {m.title}", "",
           f"**Tanggal:** {datetime.now():%Y-%m-%d %H:%M} · **Peserta:** {names}", "",
           "## Agenda", ""] + [f"{k + 1}. {s['title']}" for k, s in enumerate(m.slides)]
    out += ["", "## Pembahasan", ""] + [f"- **{TEAM[ln['speaker']].name}:** {ln['text']}"
                                        for ln in m.script]
    qa = [t for t in m.transcript if t["who"] != "boss"]
    if boss_summary or qa:
        out += ["", "## Arahan Boss dan tanggapan tim", ""]
        if boss_summary:
            out.append(f"- **Arahan Boss (ringkas):** {boss_summary}")
        out += [f"- **{TEAM[t['who']].name}:** {t['text']}" for t in qa]
    out += ["", "## Keputusan", ""] + [f"- {d['decision']}" for d in m.decisions]
    out += ["", "## Langkah berikutnya", ""] + [f"- {s}" for s in next_steps]
    if m.cost_usd:
        out += ["", f"Biaya LLM selama rapat: ${m.cost_usd:.4f}."]
    if timeline_md:
        out += ["", "## Timeline", "", timeline_md]
    out += ["", "Salam,", "Laras"]
    return "\n".join(out)


def light_only(svg: str) -> str:
    """Remove an SVG's dark-mode block: on the projector (a white screen) slides are
    always light, whatever the computer's theme, so text never ends up dark-on-dark."""
    key = "@media (prefers-color-scheme"
    while (i := svg.find(key)) >= 0:
        j = svg.find("{", i)
        depth, k = 1, j + 1
        while depth and k < len(svg):
            depth += {"{": 1, "}": -1}.get(svg[k], 0)
            k += 1
        svg = svg[:i] + svg[k:]
    return svg


def _page(title: str, body_html: str) -> str:
    """A projector slide: one self-contained HTML page, always light (a white screen)."""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{color-scheme:light;--bg:#fcfcfb;--t1:#0b0b0b;--t2:#52514e;--rule:#e4e3df;--acc:#2a78d6}}
body{{margin:0;padding:24px;background:var(--bg);color:var(--t1);
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
h1{{font-size:22px;margin:0 0 16px}} .big{{font-size:40px;font-weight:700;color:var(--acc)}}
table{{border-collapse:collapse;font-size:13px}} td,th{{padding:5px 10px;border-bottom:1px solid var(--rule);text-align:left}}
th{{color:var(--t2);font-weight:600}} .muted{{color:var(--t2)}} .cards{{display:flex;gap:16px;flex-wrap:wrap}}
.card{{border:1px solid var(--rule);border-radius:8px;padding:12px 16px;min-width:140px}}
</style></head><body><h1>{html.escape(title)}</h1>{body_html}</body></html>"""


def html_table(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return '<p class="muted">(kosong)</p>'
    cols = cols or list(rows[0])
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(_fmt(r.get(c)))}</td>" for c in cols)
                   + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def cards(items: list[tuple[str, str]]) -> str:
    return '<div class="cards">' + "".join(
        f'<div class="card"><div class="muted">{html.escape(k)}</div>'
        f'<div class="big">{html.escape(v)}</div></div>' for k, v in items) + "</div>"


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1e4 else f"{v:.4g}"
    return "" if v is None else str(v)
