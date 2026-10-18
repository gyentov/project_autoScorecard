"""Generate the team's dating-sim standing art: one PNG per character x pose x mood.

    .venv/bin/python scripts/art/characters.py                 # everything
    .venv/bin/python scripts/art/characters.py --only citra --poses neutral,presenting

Output: assets/characters/<id>/<pose>_<mood>.png (600x900, transparent) and
assets/characters/manifest.json. The art is layered vector drawing (hair, body,
arms per pose, face per mood) rendered with cairosvg. Real illustrations can
replace any file later: same folder, same name, same size.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cairosvg

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "characters"
W, H = 600, 900
LINE = "#4a3535"
SKIN, SKIN_SHADE = "#fbe3d3", "#f1c9b5"

MOODS = ("neutral", "happy", "proud", "thinking", "worried", "apologetic", "excited",
         "serious", "surprised")
POSES = ("neutral", "explaining", "presenting", "thinking", "tablet", "bowing", "cheering",
         "arms_crossed")

# --------------------------------------------------------------------------- the cast

CAST = {
    "laras": {"hair": "#5a3a2e", "hair_hi": "#7a5242", "iris": "#7a4a2a", "style": "long",
              "outfit": "blazer", "main": "#2f3b5c", "inner": "#fdfdfb", "accent": "#c9a227"},
    "bening": {"hair": "#26262e", "hair_hi": "#40404c", "iris": "#3f5a7a", "style": "bob",
               "outfit": "cardigan", "main": "#d9c4a1", "inner": "#fdfdfb", "accent": "#2a78d6"},
    "citra": {"hair": "#22304d", "hair_hi": "#34466e", "iris": "#2f5f8f", "style": "ponytail",
              "outfit": "vest", "main": "#5f6673", "inner": "#f6f6f3", "accent": "#8b1e3f",
              "glasses": True},
    "kirana": {"hair": "#d9735a", "hair_hi": "#ec9a7f", "iris": "#a0522d", "style": "short",
               "outfit": "hoodie", "main": "#e8a33d", "inner": "#fff7e6", "accent": "#2f3b5c"},
}

# --------------------------------------------------------------------------- poses
# arm = (shoulder, elbow, wrist, hand style, drawn in front of the face?)
DOWN_L = ((186, 470), (160, 650), (172, 830), "fist", False)
DOWN_R = ((414, 470), (440, 650), (428, 830), "fist", False)

POSE_ARMS = {
    "neutral": [DOWN_L, DOWN_R],
    "explaining": [((186, 470), (128, 620), (166, 488), "open", False), DOWN_R],
    "presenting": [((186, 470), (112, 560), (36, 506), "point", False), DOWN_R],
    "thinking": [((186, 470), (214, 650), (392, 640), "fist", False),
                 ((414, 470), (452, 610), (338, 398), "chin", True)],
    "tablet": [((186, 470), (196, 650), (262, 612), "fist", False),
               ((414, 470), (404, 650), (338, 612), "fist", False)],
    "bowing": [((186, 470), (214, 660), (284, 708), "fist", False),
               ((414, 470), (386, 660), (316, 708), "fist", False)],
    "cheering": [((186, 470), (122, 430), (160, 318), "fist", True),
                 ((414, 470), (478, 430), (440, 318), "fist", True)],
    "arms_crossed": [((186, 470), (226, 610), (392, 588), "fist", False),
                     ((414, 470), (374, 628), (208, 606), "fist", False)],
}
HEAD_SHIFT = {"bowing": (0, 26, -4)}          # dx, dy, rotate (degrees)


# --------------------------------------------------------------------------- drawing helpers

def mirror(svg: str) -> str:
    return f'<g transform="translate(600,0) scale(-1,1)">{svg}</g>'


def eye(cx: float, cy: float, mood: str, iris: str) -> str:
    """One eye at (cx, cy); the right eye is the mirror of the left."""
    lash = (f'<path d="M{cx - 30},{cy - 6} Q{cx},{cy - 36} {cx + 28},{cy - 10}" fill="none" '
            f'stroke="{LINE}" stroke-width="7" stroke-linecap="round"/>')
    if mood in ("happy",):
        return (f'<path d="M{cx - 24},{cy + 4} Q{cx},{cy - 20} {cx + 24},{cy + 4}" fill="none" '
                f'stroke="{LINE}" stroke-width="7" stroke-linecap="round"/>')
    if mood == "apologetic":
        return (f'<path d="M{cx - 24},{cy - 6} Q{cx},{cy + 12} {cx + 24},{cy - 6}" fill="none" '
                f'stroke="{LINE}" stroke-width="7" stroke-linecap="round"/>')
    rx, ry = (28, 34) if mood == "surprised" else (26, 30)
    ir = (14, 19) if mood == "surprised" else (20, 26)
    dx, dy = (6, -7) if mood == "thinking" else (0, 2)
    parts = [f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#ffffff" '
             f'stroke="{LINE}" stroke-width="2.5"/>',
             f'<ellipse cx="{cx + dx}" cy="{cy + dy}" rx="{ir[0]}" ry="{ir[1]}" fill="{iris}"/>',
             f'<ellipse cx="{cx + dx}" cy="{cy + dy + 6}" rx="{ir[0] - 6}" ry="{ir[1] - 12}" '
             f'fill="#ffffff" opacity="0.18"/>',
             f'<ellipse cx="{cx + dx}" cy="{cy + dy}" rx="{ir[0] * 0.45:.1f}" '
             f'ry="{ir[1] * 0.5:.1f}" fill="#1d1b26"/>',
             f'<circle cx="{cx + dx - 8}" cy="{cy + dy - 10}" r="6.5" fill="#ffffff"/>',
             f'<circle cx="{cx + dx + 7}" cy="{cy + dy + 9}" r="3" fill="#ffffff"/>']
    if mood == "excited":                         # sparkle highlight
        sx, sy = cx + dx + 6, cy + dy - 6
        parts.append(f'<path d="M{sx},{sy - 11} L{sx + 3},{sy - 3} L{sx + 11},{sy} '
                     f'L{sx + 3},{sy + 3} L{sx},{sy + 11} L{sx - 3},{sy + 3} L{sx - 11},{sy} '
                     f'L{sx - 3},{sy - 3} Z" fill="#ffffff"/>')
    if mood in ("proud", "serious"):              # half-closed lid
        drop = 14 if mood == "proud" else 18
        parts.append(f'<path d="M{cx - 32},{cy - 40} L{cx + 32},{cy - 40} L{cx + 32},'
                     f'{cy - 30 + drop} Q{cx},{cy - 36 + drop} {cx - 32},{cy - 30 + drop} Z" '
                     f'fill="{SKIN}"/>')
        lash = (f'<path d="M{cx - 30},{cy - 26 + drop} Q{cx},{cy - 36 + drop} {cx + 28},'
                f'{cy - 28 + drop}" fill="none" stroke="{LINE}" stroke-width="7" '
                'stroke-linecap="round"/>')
    return "".join(parts) + lash


BROWS = {        # left brow; inner end is the right-hand point
    "neutral": "M228,222 Q254,212 280,220", "happy": "M228,214 Q254,202 280,212",
    "proud": "M228,216 Q254,208 280,218", "thinking": "M228,212 Q254,206 280,222",
    "worried": "M228,224 Q254,218 280,206", "apologetic": "M228,226 Q254,220 280,208",
    "excited": "M228,212 Q254,198 280,210", "serious": "M228,212 Q254,216 280,228",
    "surprised": "M228,204 Q254,190 280,202",
}
MOUTHS = {
    "neutral": '<path d="M286,322 Q300,332 314,322" fill="none" stroke="{l}" stroke-width="4" '
               'stroke-linecap="round"/>',
    "happy": '<path d="M280,316 Q300,346 320,316 Z" fill="#b8434f" stroke="{l}" '
             'stroke-width="3.5" stroke-linejoin="round"/><path d="M290,330 Q300,340 310,330" '
             'fill="#ec8a8a"/>',
    "proud": '<path d="M284,324 Q302,334 318,318" fill="none" stroke="{l}" stroke-width="4" '
             'stroke-linecap="round"/>',
    "thinking": '<path d="M292,326 L310,323" fill="none" stroke="{l}" stroke-width="4" '
                'stroke-linecap="round"/>',
    "worried": '<path d="M284,328 Q292,320 300,328 Q308,336 316,328" fill="none" stroke="{l}" '
               'stroke-width="4" stroke-linecap="round"/>',
    "apologetic": '<path d="M286,330 Q300,322 314,330" fill="none" stroke="{l}" '
                  'stroke-width="4" stroke-linecap="round"/>',
    "excited": '<path d="M276,314 Q300,354 324,314 Z" fill="#b8434f" stroke="{l}" '
               'stroke-width="3.5" stroke-linejoin="round"/><path d="M288,334 Q300,348 312,334" '
               'fill="#ec8a8a"/>',
    "serious": '<path d="M288,327 L312,327" fill="none" stroke="{l}" stroke-width="4" '
               'stroke-linecap="round"/>',
    "surprised": '<ellipse cx="300" cy="330" rx="9" ry="12" fill="#b8434f" stroke="{l}" '
                 'stroke-width="3.5"/>',
}


def face(mood: str, c: dict) -> str:
    left_eye = eye(255, 262, mood, c["iris"])
    brow = (f'<path d="{BROWS[mood]}" fill="none" stroke="{c["hair"]}" stroke-width="6" '
            'stroke-linecap="round"/>')
    parts = [left_eye, mirror(left_eye), brow, mirror(brow),
             f'<path d="M299,288 L295,300 L301,301" fill="none" stroke="{SKIN_SHADE}" '
             'stroke-width="3" stroke-linecap="round"/>',
             MOUTHS[mood].format(l=LINE)]
    if mood in ("happy", "excited", "apologetic", "proud", "surprised"):
        op = 0.3 if mood == "proud" else 0.5
        parts.append(f'<ellipse cx="226" cy="302" rx="22" ry="11" fill="#f49aa7" opacity="{op}"/>'
                     f'<ellipse cx="374" cy="302" rx="22" ry="11" fill="#f49aa7" opacity="{op}"/>')
    if mood in ("worried", "apologetic"):
        parts.append(f'<path d="M412,176 Q424,198 412,206 Q400,198 412,176 Z" fill="#bfe3ff" '
                     f'stroke="#6aa7d8" stroke-width="2.5"/>')
    if c.get("glasses"):
        g = (f'<rect x="219" y="232" width="72" height="58" rx="16" fill="#ffffff" '
             f'fill-opacity="0.12" stroke="{LINE}" stroke-width="4"/>')
        parts.append(g + mirror(g) + f'<path d="M291,256 Q300,250 309,256" fill="none" '
                     f'stroke="{LINE}" stroke-width="4"/>')
    return "".join(parts)


def head(c: dict) -> str:
    return (f'<ellipse cx="192" cy="268" rx="14" ry="22" fill="{SKIN}" stroke="{LINE}" '
            f'stroke-width="3"/><ellipse cx="408" cy="268" rx="14" ry="22" fill="{SKIN}" '
            f'stroke="{LINE}" stroke-width="3"/>'
            f'<path d="M196,226 Q194,322 252,362 Q300,388 348,362 Q406,322 404,226 '
            f'Q404,140 300,140 Q196,140 196,226 Z" fill="{SKIN}" stroke="{LINE}" '
            f'stroke-width="3.5"/>')


def neck() -> str:
    return (f'<path d="M272,352 L272,424 L328,424 L328,352 Z" fill="{SKIN}" stroke="{LINE}" '
            f'stroke-width="3"/><path d="M272,372 Q300,392 328,372 L328,388 Q300,404 272,388 Z" '
            f'fill="{SKIN_SHADE}"/>')


BANGS = {
    "long": "M186,250 C176,150 228,96 300,96 C372,96 424,150 414,250 L402,214 L392,246 "
            "L370,190 L352,236 L326,176 L304,224 L276,180 L262,232 L236,196 L222,244 L204,212 Z",
    "bob": "M184,262 C172,150 226,94 300,94 C374,94 428,150 416,262 L404,224 L388,238 "
           "L372,200 L350,226 L330,196 L310,224 L290,196 L268,226 L248,200 L228,236 L206,222 Z",
    "ponytail": "M188,248 C178,148 232,98 300,98 C368,98 422,148 412,248 L400,206 L384,236 "
                "L366,188 L346,214 L320,182 L300,210 L276,178 L254,224 L232,190 L212,236 Z",
    "short": "M184,246 C170,142 228,90 300,90 C372,90 430,142 416,246 L404,208 L390,232 "
             "L372,196 L356,224 L334,186 L314,214 L290,184 L270,216 L248,190 L226,232 L206,204 Z",
}


def hair_back(c: dict) -> str:
    h, st = c["hair"], c["style"]
    if st == "long":
        return (f'<path d="M190,200 C160,320 150,520 170,660 L430,660 C450,520 440,320 410,200 '
                f'Z" fill="{h}" stroke="{LINE}" stroke-width="3"/>')
    if st == "bob":
        return (f'<path d="M180,210 C160,300 168,380 196,400 L404,400 C432,380 440,300 420,210 '
                f'Z" fill="{h}" stroke="{LINE}" stroke-width="3"/>')
    if st == "ponytail":
        return (f'<path d="M380,140 C470,150 500,260 470,400 C455,470 430,520 440,560 '
                f'C400,520 410,430 420,360 C432,270 420,190 370,170 Z" fill="{h}" '
                f'stroke="{LINE}" stroke-width="3"/>'
                f'<path d="M196,210 C186,270 190,320 200,340 L400,340 C410,320 414,270 404,210 '
                f'Z" fill="{h}" stroke="{LINE}" stroke-width="3"/>')
    return (f'<path d="M186,210 C172,280 178,330 200,350 L400,350 C422,330 428,280 414,210 Z" '
            f'fill="{h}" stroke="{LINE}" stroke-width="3"/>')


def hair_front(c: dict) -> str:
    h, hi, st = c["hair"], c["hair_hi"], c["style"]
    out = [f'<path d="{BANGS[st]}" fill="{h}" stroke="{LINE}" stroke-width="3.5" '
           'stroke-linejoin="round"/>',
           f'<path d="M232,132 Q268,112 304,118" fill="none" stroke="{hi}" stroke-width="9" '
           'stroke-linecap="round" opacity="0.8"/>',
           f'<path d="M250,120 Q236,170 240,214 M300,104 Q296,160 300,206 M352,120 Q366,168 '
           f'362,212" fill="none" stroke="{LINE}" stroke-width="2.5" opacity="0.35"/>']
    if st == "long":            # locks over the shoulders
        lock = (f'<path d="M196,230 C180,340 178,460 196,560 C214,470 214,350 214,260 Z" '
                f'fill="{h}" stroke="{LINE}" stroke-width="3"/>')
        out += [lock, mirror(lock)]
    if st == "bob":             # hair clip
        out.append(f'<rect x="352" y="168" width="40" height="12" rx="6" fill="{c["accent"]}" '
                   f'stroke="{LINE}" stroke-width="2.5" transform="rotate(-18 372 174)"/>')
    if st == "ponytail":        # hair tie
        out.append(f'<ellipse cx="388" cy="150" rx="14" ry="10" fill="{c["accent"]}" '
                   f'stroke="{LINE}" stroke-width="2.5"/>')
    if st == "short":           # side braid
        out.append(f'<path d="M200,236 C190,290 196,330 206,360" fill="none" stroke="{h}" '
                   f'stroke-width="16" stroke-linecap="round"/><path d="M200,250 l10,8 '
                   f'M198,280 l10,8 M200,310 l10,8 M203,340 l9,7" stroke="{LINE}" '
                   f'stroke-width="2.5"/>')
    return "".join(out)


TORSO = ("M300,410 C230,410 178,424 162,470 L148,900 L452,900 L438,470 "
         "C422,424 370,410 300,410 Z")


def body(c: dict) -> str:
    m, inner, acc, o = c["main"], c["inner"], c["accent"], c["outfit"]
    out = []
    if o == "hoodie":           # hood behind the neck
        out.append(f'<path d="M218,430 C220,380 380,380 382,430 Z" fill="{m}" stroke="{LINE}" '
                   f'stroke-width="3"/>')
    out.append(f'<path d="{TORSO}" fill="{m}" stroke="{LINE}" stroke-width="3.5"/>'
               f'<path d="M362,430 C410,440 436,460 438,470 L452,900 L380,900 Z" '
               f'fill="#000000" opacity="0.10"/>')
    if o == "blazer":
        out.append(f'<path d="M300,416 L262,426 L300,560 L338,426 Z" fill="{inner}" '
                   f'stroke="{LINE}" stroke-width="3"/>'
                   f'<path d="M262,426 L300,560 L250,500 L240,440 Z" fill="#26304c" '
                   f'stroke="{LINE}" stroke-width="3"/><path d="M338,426 L300,560 L350,500 '
                   f'L360,440 Z" fill="#26304c" stroke="{LINE}" stroke-width="3"/>'
                   f'<circle cx="300" cy="610" r="7" fill="{acc}" stroke="{LINE}" '
                   f'stroke-width="2"/><circle cx="300" cy="680" r="7" fill="{acc}" '
                   f'stroke="{LINE}" stroke-width="2"/>')
    elif o == "cardigan":
        out.append(f'<path d="M300,414 L258,424 L272,900 L328,900 L342,424 Z" fill="{inner}" '
                   f'stroke="{LINE}" stroke-width="3"/><path d="M262,424 L300,452 L338,424 '
                   f'L320,440 L300,446 L280,440 Z" fill="#ffffff" stroke="{LINE}" '
                   f'stroke-width="2.5"/>'
                   f'<path d="M262,430 L300,640 L338,430" fill="none" stroke="{acc}" '
                   f'stroke-width="5"/><rect x="272" y="630" width="56" height="74" rx="6" '
                   f'fill="#ffffff" stroke="{LINE}" stroke-width="3"/><rect x="272" y="630" '
                   f'width="56" height="18" rx="6" fill="{acc}"/><circle cx="300" cy="672" '
                   f'r="11" fill="#d7dbe2"/>')
    elif o == "vest":
        out.append(f'<path d="M300,414 L258,424 L232,900 L368,900 L342,424 Z" fill="{inner}" '
                   f'stroke="{LINE}" stroke-width="3"/>'
                   f'<path d="M258,424 L300,470 L342,424 L322,442 L300,448 L278,442 Z" '
                   f'fill="#ffffff" stroke="{LINE}" stroke-width="2.5"/>'
                   f'<path d="M292,452 L300,470 L308,452 L304,560 L300,572 L296,560 Z" '
                   f'fill="{acc}" stroke="{LINE}" stroke-width="2"/>')
    else:                       # hoodie
        out.append(f'<path d="M262,424 Q300,470 338,424" fill="none" stroke="{LINE}" '
                   f'stroke-width="3"/><path d="M280,440 L276,540 M320,440 L324,540" '
                   f'stroke="{inner}" stroke-width="5" stroke-linecap="round"/>'
                   f'<circle cx="300" cy="640" r="36" fill="{inner}" stroke="{LINE}" '
                   f'stroke-width="3"/><text x="300" y="652" text-anchor="middle" '
                   f'font-family="sans-serif" font-weight="700" font-size="30" '
                   f'fill="{acc}">AS</text>')
    return "".join(out)


def arm(a, c: dict) -> str:
    (sx, sy), (ex, ey), (wx, wy), style, _ = a
    sleeve = c["main"] if c["outfit"] != "vest" else c["inner"]
    out = [f'<path d="M{sx},{sy} L{ex},{ey} L{wx},{wy}" fill="none" stroke="{LINE}" '
           'stroke-width="52" stroke-linecap="round" stroke-linejoin="round"/>',
           f'<path d="M{sx},{sy} L{ex},{ey} L{wx},{wy}" fill="none" stroke="{sleeve}" '
           'stroke-width="46" stroke-linecap="round" stroke-linejoin="round"/>',
           f'<path d="M{sx},{sy} L{ex},{ey} L{wx},{wy}" fill="none" stroke="#000000" '
           'stroke-opacity="0.08" stroke-width="18" stroke-linecap="round" '
           'stroke-linejoin="round" transform="translate(9,4)"/>']
    hx, hy = wx + (wx - ex) * 0.18, wy + (wy - ey) * 0.18
    if style == "point":
        out.append(f'<ellipse cx="{hx}" cy="{hy}" rx="22" ry="19" fill="{SKIN}" stroke="{LINE}" '
                   f'stroke-width="3"/><path d="M{hx - 14},{hy - 6} L{hx - 50},{hy - 16}" '
                   f'stroke="{LINE}" stroke-width="15" stroke-linecap="round"/><path '
                   f'd="M{hx - 14},{hy - 6} L{hx - 50},{hy - 16}" stroke="{SKIN}" '
                   f'stroke-width="10" stroke-linecap="round"/>')
    elif style == "open":
        out.append(f'<ellipse cx="{hx}" cy="{hy - 6}" rx="24" ry="30" fill="{SKIN}" '
                   f'stroke="{LINE}" stroke-width="3"/><path d="M{hx - 10},{hy - 30} '
                   f'L{hx - 10},{hy - 16} M{hx},{hy - 34} L{hx},{hy - 16} M{hx + 10},{hy - 30} '
                   f'L{hx + 10},{hy - 16}" stroke="{SKIN_SHADE}" stroke-width="3"/>')
    else:                       # fist / chin
        out.append(f'<circle cx="{hx}" cy="{hy}" r="24" fill="{SKIN}" stroke="{LINE}" '
                   f'stroke-width="3"/>')
    return "".join(out)


def character_svg(cid: str, pose: str, mood: str) -> str:
    c = CAST[cid]
    arms = POSE_ARMS[pose]
    dx, dy, rot = HEAD_SHIFT.get(pose, (0, 0, 0))
    tf = f'translate({dx},{dy}) rotate({rot} 300 360)'
    back_arms = "".join(arm(a, c) for a in arms if not a[4])
    front_arms = "".join(arm(a, c) for a in arms if a[4])
    tablet = ""
    if pose == "tablet":
        tablet = (f'<rect x="232" y="548" width="136" height="96" rx="10" fill="#2b2f3a" '
                  f'stroke="{LINE}" stroke-width="3"/><rect x="242" y="558" width="116" '
                  f'height="76" rx="5" fill="#86b6ef"/><path d="M254,620 L276,598 L296,610 '
                  f'L320,580 L346,592" fill="none" stroke="#ffffff" stroke-width="4"/>')
    slim = "translate(300,0) scale(0.86,1) translate(-300,0)"
    big = f"{tf} translate(300,372) scale(1.16) translate(-300,-372)"
    face_clip = ('<clipPath id="face"><path d="M196,226 Q194,322 252,362 Q300,388 348,362 '
                 'Q406,322 404,226 Q404,140 300,140 Q196,140 196,226 Z"/></clipPath>')
    bang_shadow = (f'<g clip-path="url(#face)"><path d="{BANGS[c["style"]]}" '
                   f'fill="{SKIN_SHADE}" transform="translate(0,12)"/></g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
            f'height="{H}"><defs>{face_clip}</defs>'
            f'<g transform="{big}">{hair_back(c)}</g>'
            f'<g transform="{slim}">{body(c)}{back_arms}{tablet}</g>'
            f'<g transform="{big}">{neck()}{head(c)}{bang_shadow}{face(mood, c)}'
            f'{hair_front(c)}</g><g transform="{slim}">{front_arms}</g></svg>')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated character ids")
    ap.add_argument("--poses", help="comma-separated poses")
    ap.add_argument("--moods", help="comma-separated moods")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    ids = args.only.split(",") if args.only else list(CAST)
    poses = args.poses.split(",") if args.poses else list(POSES)
    moods = args.moods.split(",") if args.moods else list(MOODS)
    manifest = {"size": [W, H], "poses": list(POSES), "moods": list(MOODS), "files": {}}
    for cid in ids:
        d = args.out / cid
        d.mkdir(parents=True, exist_ok=True)
        for pose in poses:
            for mood in moods:
                name = f"{pose}_{mood}.png"
                cairosvg.svg2png(bytestring=character_svg(cid, pose, mood).encode(),
                                 write_to=str(d / name), output_width=W, output_height=H)
                manifest["files"].setdefault(cid, []).append(f"{cid}/{name}")
    if not args.only:
        (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {sum(len(v) for v in manifest['files'].values())} images to {args.out}")


if __name__ == "__main__":
    main()
