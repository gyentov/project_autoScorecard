"""Render the modelling waterfall: the plan (before the run) and the report (after).

Outputs are Markdown (with a Mermaid flowchart) and standalone SVG, so they work
in docs, the deck (Phase 3) and the game UI (Phase 4) without a plotting library.
The SVG carries its own light/dark colours (prefers-color-scheme).
"""
from __future__ import annotations

import math
from datetime import datetime
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

from .waterfall import Step, WaterfallResult, plan_warnings

# --------------------------------------------------------------------------- shared

_STYLE = """<style>
  .bg{fill:#fcfcfb} .t1{fill:#0b0b0b} .t2{fill:#52514e} .mut{fill:#898781}
  .bar{fill:#2a78d6} .rule{stroke:#e4e3df} .card{fill:#f9f9f7;stroke:#d6d5d0}
  .arrow{stroke:#898781;fill:none}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1a1a19} .t1{fill:#f0efec} .t2{fill:#c3c2b7}
    .bar{fill:#3987e5} .rule{stroke:#383835} .card{fill:#232322;stroke:#45443f}
  }
  text{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
</style>"""


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        cells = []
        for v in r:
            if isinstance(v, float):
                v = f"{v:.4g}"
            cells.append(str(v).replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _mermaid_label(s: str) -> str:
    """Escape text for a quoted Mermaid label (Mermaid uses #name; entity codes)."""
    return s.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


# --------------------------------------------------------------------------- plan

def plan_mermaid(steps: list[Step], counts: list[tuple[int, int]] | None = None) -> str:
    """Top-down flowchart. counts: optional (n_dropped, n_out) per enabled step."""
    lines = ["flowchart TD", '    S0(["Candidate features"])']
    prev = "S0"
    for k, s in enumerate([s for s in steps if s.enabled], 1):
        label = f"{k}. {_mermaid_label(s.title)}<br/>{_mermaid_label(s.criterion)}"
        if counts:
            label += f"<br/><b>-{counts[k - 1][0]} → {counts[k - 1][1]} left</b>"
        lines.append(f'    S{k}["{label}"]')
        lines.append(f"    {prev} --> S{k}")
        prev = f"S{k}"
    lines.append(f'    {prev} --> F(["Scorecard model"])')
    disabled = [s for s in steps if not s.enabled]
    if disabled:
        lines.append(f'    X["Skipped: {", ".join(_mermaid_label(s.title) for s in disabled)}"]')
        lines.append("    style X stroke-dasharray: 4 4")
    return "\n".join(lines)


def plan_markdown(steps: list[Step], approved: bool = False) -> str:
    """The plan as it is shown to the user before the Modeller runs."""
    out = ["## Modelling waterfall plan", "",
           f"Status: **{'approved' if approved else 'waiting for approval'}**", "",
           "```mermaid", plan_mermaid(steps), "```", "",
           "| # | step | keeps a feature when | what it means |", "|---|---|---|---|"]
    k = 0
    for s in steps:
        if s.enabled:
            k += 1
        cells = [str(k) if s.enabled else "–", s.title + ("" if s.enabled else " (skipped)"),
                 s.criterion, s.help]
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    warns = plan_warnings(steps)
    if warns:
        out += ["", "**Check before approving:**"] + [f"- {w}" for w in warns]
    return "\n".join(out) + "\n"


def plan_svg(steps: list[Step]) -> str:
    """The plan as a vertical chain of numbered cards."""
    enabled = [s for s in steps if s.enabled]
    W, card_h, gap, pad = 720, 46, 18, 16
    H = pad * 2 + len(enabled) * (card_h + gap) - gap
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
             f'height="{H}" role="img" aria-label="Modelling waterfall plan">', _STYLE,
             f'<rect class="bg" width="{W}" height="{H}"/>']
    for k, s in enumerate(enabled):
        y = pad + k * (card_h + gap)
        parts.append(f'<g><title>{escape(s.help)}</title>'
                     f'<rect class="card" x="{pad}" y="{y}" width="{W - 2 * pad}" '
                     f'height="{card_h}" rx="6"/>'
                     f'<text class="mut" x="{pad + 14}" y="{y + 28}" font-size="13" '
                     f'font-weight="600">{k + 1}</text>'
                     f'<text class="t1" x="{pad + 40}" y="{y + 19}" font-size="13" '
                     f'font-weight="600">{escape(s.title)}</text>'
                     f'<text class="t2" x="{pad + 40}" y="{y + 36}" font-size="11.5">'
                     f'{escape(s.criterion)}</text></g>')
        if k < len(enabled) - 1:
            ay = y + card_h
            parts.append(f'<path class="arrow" d="M{W / 2} {ay + 3} V{ay + gap - 3} '
                         f'M{W / 2 - 4} {ay + gap - 7} L{W / 2} {ay + gap - 3} '
                         f'L{W / 2 + 4} {ay + gap - 7}" stroke-width="1.5"/>')
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- heatmap

# Diverging scale: blue (positive) <-> gray midpoint <-> red (negative), 5 steps per
# arm, mixed in OKLab so equal steps look equally different. Light and dark mode
# use the same rule with their own poles and midpoint.
_DIV = {"light": {"pos": "#2a78d6", "neg": "#e34948", "mid": "#f0efec"},
        "dark": {"pos": "#3987e5", "neg": "#e66767", "mid": "#383835"}}
_STEPS = 5


def _srgb_to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lin_to_srgb(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _hex_to_oklab(h: str) -> np.ndarray:
    r, g, b = (_srgb_to_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    lms = np.cbrt([0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
                   0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
                   0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b])
    M = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                  [1.9779984951, -2.4285922050, 0.4505937099],
                  [0.0259040371, 0.7827717662, -0.8086757660]])
    return M @ lms


def _oklab_to_hex(lab: np.ndarray) -> str:
    M = np.array([[1, 0.3963377774, 0.2158037573], [1, -0.1055613458, -0.0638541728],
                  [1, -0.0894841775, -1.2914855480]])
    l, m, s_ = (M @ lab) ** 3
    rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s_,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s_,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s_]
    return "#" + "".join(f"{round(_lin_to_srgb(c) * 255):02x}" for c in rgb)


def _luminance(h: str) -> float:
    r, g, b = (_srgb_to_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ink_for(h: str) -> str:
    """Black or white text, whichever contrasts more with the fill."""
    L = _luminance(h)
    return "#0b0b0b" if (L + 0.05) / 0.05 >= 1.05 / (L + 0.05) else "#ffffff"


def _div_classes() -> str:
    css = {"light": [], "dark": []}
    for mode, c in _DIV.items():
        mid = _hex_to_oklab(c["mid"])
        for k in range(-_STEPS, _STEPS + 1):
            pole = _hex_to_oklab(c["pos"] if k > 0 else c["neg"])
            fill = _oklab_to_hex(mid + (pole - mid) * abs(k) / _STEPS)
            name = f"d{k + _STEPS}"
            css[mode].append(f".{name}{{fill:{fill}}} .{name}t{{fill:{_ink_for(fill)}}}")
    return ("<style>" + " ".join(css["light"]) + " .hl{stroke:#0b0b0b}"
            " @media (prefers-color-scheme: dark){" + " ".join(css["dark"])
            + " .hl{stroke:#f0efec}}</style>")


def _div_class(r: float) -> str:
    k = 0 if math.isnan(r) else int(round(max(-1.0, min(1.0, r)) * _STEPS))
    return f"d{k + _STEPS}"


def correlation_heatmap_svg(corr: pd.DataFrame, threshold: float | None = 0.7,
                            title: str = "WoE correlation, final model") -> str:
    """Lower-triangle-and-diagonal heatmap of a correlation matrix, value in each
    cell, rows labelled '1. name' and columns by number. Cells at or above the
    clustering threshold are outlined."""
    feats = list(corr.columns)
    n = len(feats)
    pad, label_w = 16, min(max(len(f) for f in feats) * 7 + 28, 240) if n else 60
    cell = max(min(52, (720 - 2 * pad - label_w) // max(n, 1)), 30)
    top = pad + 28 + 18
    W = max(pad * 2 + label_w + n * cell, 420)
    legend_y = top + n * cell + 22
    H = legend_y + 40
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
             f'height="{H}" role="img" aria-label="{escape(title)}">', _STYLE, _div_classes(),
             f'<rect class="bg" width="{W}" height="{H}"/>',
             f'<text class="t1" x="{pad}" y="{pad + 12}" font-size="14" font-weight="600">'
             f'{escape(title)}</text>']
    x0 = pad + label_w
    for j in range(n):
        parts.append(f'<text class="mut" x="{x0 + j * cell + cell / 2}" y="{top - 6}" '
                     f'font-size="11" text-anchor="middle">{j + 1}</text>')
    for i, fi in enumerate(feats):
        y = top + i * cell
        parts.append(f'<text class="t1" x="{x0 - 8}" y="{y + cell / 2 + 4}" font-size="11.5" '
                     f'text-anchor="end">{i + 1}. {escape(fi)}</text>')
        for j, fj in enumerate(feats[:i + 1]):
            r = float(corr.loc[fi, fj])
            cls = _div_class(r)
            x = x0 + j * cell
            flag = (threshold is not None and i != j and abs(r) >= threshold)
            outline = f' class="{cls} hl" stroke-width="2"' if flag else f' class="{cls}"'
            parts.append(
                f'<g><title>{escape(fi)} × {escape(fj)}: r = {r:.3f}</title>'
                f'<rect x="{x + 1}" y="{y + 1}" width="{cell - 2}" height="{cell - 2}" rx="3"'
                f'{outline}/>'
                f'<text class="{cls}t" x="{x + cell / 2}" y="{y + cell / 2 + 4}" font-size="11" '
                f'text-anchor="middle">{round(r, 2) + 0.0:.2f}</text></g>')   # no "-0.00"
    # legend: the 11 steps from -1 to +1
    lw = 22
    lx = pad
    for k in range(-_STEPS, _STEPS + 1):
        parts.append(f'<rect class="d{k + _STEPS}" x="{lx + (k + _STEPS) * lw}" y="{legend_y}" '
                     f'width="{lw - 2}" height="10" rx="2"/>')
    for v, lab in ((-1, "−1 (negative)"), (0, "0"), (1, "+1 (positive)")):
        x = lx + (v * _STEPS + _STEPS) * lw + (lw - 2) / 2
        anchor = "start" if v < 0 else "end" if v > 0 else "middle"
        x = lx if v < 0 else lx + (2 * _STEPS + 1) * lw - 2 if v > 0 else x
        parts.append(f'<text class="t2" x="{x}" y="{legend_y + 26}" font-size="11" '
                     f'text-anchor="{anchor}">{lab}</text>')
    if threshold is not None:
        parts.append(f'<text class="t2" x="{lx + (2 * _STEPS + 1) * lw + 16}" '
                     f'y="{legend_y + 9}" font-size="11">outlined: |r| ≥ {threshold:g}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- finalization

def _bin_view(t: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in ("bin", "count", "bad_rate", "woe", "woe_calc", "woe_edited") if c in t]
    return t[cols] if len(t) else t


def finalization_markdown(res: WaterfallResult, build_id: str | None = None,
                          status: str = "pending_confirmation", level: int = 2) -> str:
    """What the user must confirm: forced features and manual edits, before/after.
    `level` is the heading level of the section title (1 for a standalone file)."""
    h = "#" * level
    out = [f"{h} Finalization review", ""]
    if build_id:
        state = ("**CONFIRMED**" if status == "confirmed" else
                 "**DRAFT, waiting for your confirmation**")
        out += [f"Build `{build_id}`: {state}.", ""]
    if res.forced:
        out += [f"{h}# Forced features", "",
                "Tried in the final model regardless of the earlier steps; discarded only if "
                "they still fail a finalization check inside the model.", "",
                _md_table(pd.DataFrame(res.forced)[["feature", "previously_dropped_at",
                                                    "previous_reason", "result", "reason"]])]
    else:
        out += ["No forced features.", ""]
    if res.edits:
        out += [f"{h}# Manual edits", ""]
        for e in res.edits:
            kind = "bins set by hand" if e["kind"] == "bins" else "WoE set by hand"
            mono = "monotonic" if e["monotonic_after"] else "**NOT monotonic**"
            used = "in the final model" if e["feature"] in res.selected else "not in the final model"
            out += [f"{h}## {e['feature']}: {kind} ({used})", "",
                    f"Edit: `{e['spec']}` · reason: {e['reason'] or '_none given_'} · "
                    f"after the edit the {'bad rate' if e['kind'] == 'bins' else 'WoE'} is {mono}.",
                    "", "Before:", "", _md_table(_bin_view(e["before"])),
                    "After:", "", _md_table(_bin_view(e["after"]))]
    else:
        out += ["No manual bin or WoE edits.", ""]
    out += ["Confirm with `POST /api/projects/{id}/finalization/confirm` "
            f'(`{{"build_id": "{build_id or "<id>"}"}}`). Changing the edits or forced '
            "features needs a rebuild before confirming.", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- report

def waterfall_svg(summary: pd.DataFrame) -> str:
    """Features remaining after each step: one horizontal bar per stage, anchored
    left, labelled with what was left and how many were dropped."""
    W, row_h, pad, label_w = 720, 52, 16, 150
    H = pad * 2 + 22 + len(summary) * row_h
    max_n = max(int(summary["n_in"].max()), 1)
    bar_max = W - 2 * pad - label_w
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
             f'height="{H}" role="img" aria-label="Features remaining after each waterfall step">',
             _STYLE, f'<rect class="bg" width="{W}" height="{H}"/>',
             f'<text class="t1" x="{pad}" y="{pad + 12}" font-size="14" font-weight="600">'
             f'Features remaining after each step</text>']
    for k, r in enumerate(summary.itertuples()):
        y = pad + 22 + k * row_h
        n_out, n_drop = int(r.n_out), int(r.n_dropped)
        bw = max(bar_max * n_out / max_n, 2)
        tip = (f"{r.step}: {r.criterion}. In {r.n_in}, dropped {n_drop}, left {n_out}")
        head = f"{r.order}. {r.step}" if r.order else r.step
        value = f"{n_out} left" + (f"  (−{n_drop})" if n_drop else "")
        bar_y = y + 26
        # bar with a 4px rounded data end, square at the baseline
        bar = (f'<path class="bar" d="M{pad} {bar_y} H{pad + bw - 4} '
               f'a4 4 0 0 1 4 4 V{bar_y + 10} a4 4 0 0 1 -4 4 H{pad} Z"/>') if bw > 4 else \
            f'<rect class="bar" x="{pad}" y="{bar_y}" width="{bw}" height="14"/>'
        parts.append(
            f'<g><title>{escape(tip)}</title>'
            f'<rect x="0" y="{y}" width="{W}" height="{row_h}" fill="transparent"/>'
            f'<text class="t1" x="{pad}" y="{y + 16}" font-size="12.5" font-weight="600">'
            f'{escape(head)}<tspan class="t2" font-weight="400" dx="8">'
            f'{escape(r.criterion)}</tspan></text>{bar}'
            f'<text class="t1" x="{pad + bw + 8}" y="{bar_y + 11}" font-size="12">'
            f'{escape(value)}</text></g>')
        if k:
            parts.append(f'<line class="rule" x1="{pad}" x2="{W - pad}" y1="{y + 0.5}" '
                         f'y2="{y + 0.5}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def report_markdown(res: WaterfallResult, performance: dict | None = None,
                    svg_name: str = "waterfall.svg", title: str = "Modelling waterfall report",
                    heatmap_name: str = "woe_correlation.svg", build_id: str | None = None,
                    corr_threshold: float | None = 0.7, data_section: str | None = None,
                    raw_corr: pd.DataFrame | None = None, raw_not_shown: list[str] | None = None,
                    raw_heatmap_name: str = "raw_correlation.svg") -> str:
    s = res.summary()
    counts = [(int(r.n_dropped), int(r.n_out)) for r in s.iloc[1:].itertuples()]
    out = [f"# {title}", "",
           f"Generated {datetime.now():%Y-%m-%d %H:%M}. "
           f"{len(res.features_in)} candidate features → **{len(res.selected)} in the final "
           f"model**.", "",
           f"![Features remaining after each step]({svg_name})", "",
           "## Summary", "", _md_table(s),
           "## Flow", "", "```mermaid", plan_mermaid(res.steps, counts), "```", ""]
    if data_section:
        out += [data_section, ""]

    for k, o in enumerate(res.outcomes, 1):
        out += [f"## {k}. {o.step.title}", "",
                f"Keeps a feature when: **{o.step.criterion}**. "
                f"{o.step.help}", "",
                f"In: {o.n_in} · dropped: {len(o.dropped)} · out: {o.n_out}", ""]
        if o.step.type == "correlation_cluster" and o.detail.get("clusters"):
            multi = [c for c in o.detail["clusters"] if len(c) > 1]
            out += ["Clusters with more than one member (first = kept, highest IV):", ""]
            out += [f"- {', '.join(c)}" for c in multi] or ["- none"]
            out.append("")
        if o.step.type in ("stepwise", "forward", "backward") and o.detail.get("steps"):
            out += ["Selection trace:", "",
                    _md_table(pd.DataFrame(o.detail["steps"])[
                        ["step", "action", "feature", "p_value", "aic", "reason"]])]
        if o.dropped:
            out += ["Dropped:", "", _md_table(pd.DataFrame(o.dropped))]

    out += ["## Final model", "", _md_table(res.model.summary().reset_index()), ""]
    if len(res.final_corr) > 1:
        c = res.final_corr
        off = c.where(~np.eye(len(c), dtype=bool)).abs()
        i, j = np.unravel_index(np.nanargmax(off.to_numpy()), off.shape)
        out += ["### WoE correlation of the final model", "",
                f"![WoE correlation heatmap]({heatmap_name})", "",
                f"Highest |r| between two model features: **{c.iloc[i, j]:.2f}** "
                f"({c.index[i]} × {c.columns[j]})"
                + (f"; clustering limit {corr_threshold:g}." if corr_threshold else "."), "",
                _md_table(c.round(3).reset_index(names="feature")), ""]
    if raw_corr is not None and len(raw_corr) > 1:
        out += ["### Raw-value correlation (reference only)", "",
                "Spearman rank correlation of the raw values of the final model's numeric "
                "features on the training sample (after outlier treatment, if any; missing "
                "values left out pair by pair). For reference: the model uses WoE, so the WoE "
                "matrix above is the one the clustering limit applies to.", "",
                f"![Raw-value correlation heatmap]({raw_heatmap_name})", "",
                _md_table(raw_corr.round(3).reset_index(names="feature"))]
        if raw_not_shown:
            out += [f"Not shown (categorical, no raw numeric value): {', '.join(raw_not_shown)}.",
                    ""]
    out.append(finalization_markdown(res, build_id))
    if performance:
        perf = pd.DataFrame(performance).T.reset_index(names="sample")
        out += ["## Performance", "", _md_table(perf[["sample", "n", "bad_rate", "auc",
                                                        "gini", "ks"]]), ""]
    nonmono = [f for f, b in res.binnings.items() if not b.is_monotonic]
    out += ["## Binning check", "",
            ("All selected features have strictly monotonic bad rates across their bins "
             "(missing bin excluded)." if not nonmono else
             f"**Not monotonic (manual bins):** {', '.join(nonmono)}"), ""]
    j = res.feature_journey()[["feature", "status", "dropped_at", "missing_rate",
                               "identical_rate", "reason"]]
    out += ["## Every feature", "", _md_table(j)]
    return "\n".join(out)
