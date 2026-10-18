"""Charts for Kirana's deck, drawn as SVG on Cole Nussbaumer Knaflic's principles
(Storytelling with Data): the message in one accent colour and everything else in grey,
labels written on the data instead of legends, no chart borders or heavy gridlines,
and an annotation that points at the insight."""
from __future__ import annotations

import html
import math

INK, MUTED, GREY, LIGHT, RULE = "#111827", "#6B7280", "#B6BCC6", "#D9DDE3", "#ECEEF1"
ACCENT, ACCENT_SOFT, ALERT = "#0F766E", "#99D5CD", "#C2410C"
# sequential, one hue, darker = riskier (VH .. VL)
LEVEL_COLORS = {"VH": "#7F1D1D", "H": "#B45309", "M": "#D9A441", "L": "#5E9C8F", "VL": "#0F766E"}
FONT = "font-family:Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif"


def esc(s) -> str:
    return html.escape(str(s))


def _svg(w: int, h: int, body: str, label: str) -> str:
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{esc(label)}" '
            f'style="{FONT};max-height:100%">{body}</svg>')


def _text(x, y, s, size=13, fill=INK, anchor="start", weight=400) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{esc(s)}</text>')


def hbars(items: list[tuple[str, float]], fmt=lambda v: f"{v:.0%}", focus: set[int] | None = None,
          w: int = 620, label_w: int = 230, row: int = 30, note: dict[int, str] | None = None,
          title: str = "") -> str:
    """Horizontal bars, largest first as given; `focus` rows in the accent, others grey;
    values written at the bar end."""
    focus = focus if focus is not None else {0}
    note = note or {}
    top = max((v for _, v in items), default=1) or 1
    h = row * len(items) + 8
    bw = w - label_w - 70
    out = []
    for k, (lab, v) in enumerate(items):
        y = k * row + 4
        c = ACCENT if k in focus else GREY
        ln = max(2, bw * v / top)
        out.append(_text(label_w - 10, y + row * 0.62, lab[:34], 13, INK if k in focus else MUTED, "end",
                         600 if k in focus else 400))
        out.append(f'<rect x="{label_w}" y="{y + 6}" width="{ln:.1f}" height="{row - 12}" rx="2" fill="{c}"/>')
        out.append(_text(label_w + ln + 8, y + row * 0.62, fmt(v), 13, INK if k in focus else MUTED,
                         weight=600 if k in focus else 400))
        if k in note:
            out.append(_text(label_w + ln + 70, y + row * 0.62, note[k], 12, ACCENT))
    return _svg(w, h, "".join(out), title)


def vbars(items: list[tuple[str, float]], fmt=lambda v: f"{v:.0%}", colors: list[str] | None = None,
          w: int = 640, h: int = 300, flag: set[int] | None = None, title: str = "",
          sub: list[str] | None = None) -> str:
    """Vertical bars with the value on top and the category below; `flag` marks bars in
    the alert colour (e.g. a break in a trend)."""
    flag = flag or set()
    n = len(items)
    top = max((v for _, v in items), default=1) or 1
    pad_b, pad_t = 44 if sub else 30, 24
    slot = w / max(n, 1)
    bw = min(48, slot * 0.66)
    out = [f'<line x1="0" x2="{w}" y1="{h - pad_b}" y2="{h - pad_b}" stroke="{LIGHT}"/>']
    for k, (lab, v) in enumerate(items):
        x = k * slot + (slot - bw) / 2
        bh = (h - pad_b - pad_t) * v / top
        c = ALERT if k in flag else (colors[k] if colors else GREY)
        out.append(f'<rect x="{x:.1f}" y="{h - pad_b - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="{c}"/>')
        out.append(_text(x + bw / 2, h - pad_b - bh - 6, fmt(v), 12, ALERT if k in flag else INK, "middle"))
        out.append(_text(x + bw / 2, h - pad_b + 16, lab, 12, MUTED, "middle"))
        if sub:
            out.append(_text(x + bw / 2, h - pad_b + 31, sub[k], 11, MUTED, "middle"))
    return _svg(w, h, "".join(out), title)


def lines(series: list[dict], xmax: int, ymax: float | None = None, w: int = 660, h: int = 330,
          xlabel: str = "", marker: tuple[float, str] | None = None, title: str = "") -> str:
    """Line chart: series {"name", "x", "y", "focus"}; focus lines in the accent and thick,
    context lines thin grey; names written at the line ends (pushed apart, never stacked)."""
    L, R, T, B = 46, 150, 16, 34
    top = ymax or max(max(s["y"]) for s in series if s["y"]) * 1.12 or 0.01
    step = next(st for st in (0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25) if top / st <= 5)
    top = math.ceil(top / step) * step
    sx = lambda x: L + (x - 1) / max(xmax - 1, 1) * (w - L - R)        # noqa: E731
    sy = lambda v: T + (1 - v / top) * (h - T - B)                      # noqa: E731
    out = []
    for k in range(int(round(top / step)) + 1):
        v = k * step
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" stroke="{RULE}"/>')
        out.append(_text(L - 8, sy(v) + 4, f"{v:.0%}", 11, MUTED, "end"))
    tick = 1 if xmax <= 12 else 3
    for m in range(tick, xmax + 1, tick):
        out.append(_text(sx(m), h - B + 18, m, 11, MUTED, "middle"))
    if xlabel:
        out.append(_text((L + w - R) / 2, h - 2, xlabel, 11, MUTED, "middle"))
    if marker:
        x, lab = marker
        out.append(f'<line x1="{sx(x):.1f}" x2="{sx(x):.1f}" y1="{T}" y2="{h - B}" stroke="{INK}" '
                   f'stroke-dasharray="3 3" stroke-width="1"/>')
        out.append(_text(sx(x) + 6, T + 12, lab, 12, INK, weight=600))
    ends = []
    for s in sorted(series, key=lambda s: s.get("focus", False)):     # focus drawn last, on top
        if not s["x"]:
            continue
        foc = s.get("focus", False)
        pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(s["x"], s["y"]))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{s.get("color") or (ACCENT if foc else LIGHT)}" '
                   f'stroke-width="{3 if foc else 1.5}" stroke-linejoin="round"/>')
        ends.append([sy(s["y"][-1]), sx(s["x"][-1]), s["name"], foc, s.get("color")])
    ends.sort()
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 13)
    for y, x, name, foc, col in ends:
        out.append(_text(w - R + 8, y + 4, name, 12 if foc else 11,
                         col or (ACCENT if foc else MUTED), weight=700 if foc else 400))
    return _svg(w, h, "".join(out), title)


def funnel(steps: list[tuple], w: int = 660, row: int = 34, title: str = "") -> str:
    """The waterfall as a funnel: (step, features left, removed[, added]); what the step
    removed (and added back) is written beside it; the final set in the accent."""
    top = max(s[1] for s in steps) or 1
    label_w = 210
    bw = w - label_w - 120
    out = []
    for k, (name, n, dropped, *more) in enumerate(steps):
        added = more[0] if more else 0
        y = k * row + 4
        last = k == len(steps) - 1
        ln = max(2, bw * n / top)
        out.append(_text(label_w - 10, y + row * 0.6, name, 13, INK if last else MUTED, "end", 600 if last else 400))
        out.append(f'<rect x="{label_w}" y="{y + 7}" width="{ln:.1f}" height="{row - 14}" rx="2" '
                   f'fill="{ACCENT if last else GREY}"/>')
        out.append(_text(label_w + ln + 8, y + row * 0.6, n, 13, INK, weight=700 if last else 500))
        if dropped:
            out.append(_text(label_w + ln + 36, y + row * 0.6, f"−{dropped}", 12, ALERT))
        if added:
            out.append(_text(label_w + ln + (66 if dropped else 36), y + row * 0.6, f"+{added}", 12, ACCENT))
    return _svg(w, row * len(steps) + 8, "".join(out), title)


def stacked_share(parts: list[tuple[str, float, str]], w: int = 640, h: int = 46) -> str:
    """One 100% bar: each level's share of accounts, coloured by level, labelled inside."""
    x, out = 0.0, []
    for lab, v, col in parts:
        ln = w * v
        out.append(f'<rect x="{x:.1f}" y="4" width="{max(ln - 2, 1):.1f}" height="{h - 8}" fill="{col}"/>')
        if ln > 46:
            out.append(_text(x + ln / 2, h / 2 + 5, f"{lab} {v:.0%}", 12, "#fff", "middle", 600))
        x += ln
    return _svg(w, h, "".join(out), "share of accounts by risk level")
