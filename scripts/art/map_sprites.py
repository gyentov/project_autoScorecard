"""Office-map sprites made by gpt-image-2 from each character's reference.

    .venv/bin/python scripts/art/map_sprites.py make <id|all> [--yes]   # 1 image per character
    .venv/bin/python scripts/art/map_sprites.py cut <id|all>            # sheet -> game frames

`make` asks for ONE pixel-art turnaround per character (front, left, right, back;
standing on the top row, mid-stride on the bottom row), drawn from
assets/characters/_references/<id>.png so the sprite matches the standing art.
The Boss has no reference and is drawn from the description below.
Raw sheets go to assets/sprites/_raw/<id>.png (not committed).

`cut` finds the 8 figures, scales them to the game size and writes
assets/sprites/<id>.png: 4 columns (down, left, right, up) x 2 rows (stand, step)
of FW x FH frames, feet on the bottom edge. The browser animates walking by
alternating the rows, and everything else (sitting, drinking, cheering) with
small tweens and emote bubbles.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_art  # noqa: E402
from prompts import CAST, EYES, KEEP  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "sprites"
RAW = OUT / "_raw"
REFS = ROOT / "assets" / "characters" / "_references"
IDS = ["laras", "bening", "citra", "kirana", "boss"]
FW, FH = 48, 64                     # frame size in the game (map tiles are 32 px)
FIG_H = 60                          # figure height inside the frame

BOSS = ("the Boss: a young professional with short neat black hair and dark eyes, a dark "
        "green blazer over a plain white t-shirt, dark grey trousers and dark shoes")

PROMPT = (
    "Pixel-art game sprite sheet of {who}. Cute chibi proportions (big head, about 2.5 "
    "heads tall), in the style of cozy life-sim games like Stardew Valley: clean pixel "
    "art, crisp 1-pixel dark outlines, simple flat shading, limited palette, no "
    "anti-aliasing, no blur. {ref}"
    "Eight full-body figures of the SAME character, same size, evenly spaced on a 4 x 2 "
    "grid, all on one flat light-grey background (#e6e6e6). Top row, standing still: "
    "1) facing the viewer, 2) facing left (side view), 3) facing right (side view), "
    "4) seen from behind. Bottom row, the same four directions mid-stride while walking "
    "(one leg forward, arms swinging). Every figure shows the whole body from hair to "
    "shoes with space around it. No text, no labels, no grid lines, no shadows, no "
    "scenery, no other characters.")


SIT_PROMPT = (
    "Using the attached pixel-art sprite sheet as the exact style and character reference "
    "(same character, same chibi proportions, same colors, same pixel size and outline), draw "
    "FOUR new figures of this same character in SITTING poses, in one row, evenly spaced, all on "
    "one flat light-grey background (#e6e6e6): 1) sitting on a chair, seen from BEHIND (we see "
    "her back and hair; her shoulders and arms reach forward as if typing), but do NOT draw "
    "the chair, 2) kneeling on the floor in seiza, facing LEFT (side view), 3) kneeling on the "
    "floor in seiza, facing RIGHT (side view), 4) kneeling on the floor in seiza, seen from "
    "BEHIND. Same size scale as the standing figures in the reference. No furniture, no "
    "cushions, no text, no shadows, no other characters.")


def cmd_make_sit(a) -> None:
    ids = IDS if a.id == "all" else [a.id]
    est = make_art.EST[make_art.MODEL] * 1.3 * len(ids)
    print(f"{len(ids)} sitting sheet(s), about ${est:.2f}: {', '.join(ids)}")
    if not a.yes and input("go? [y/N] ").strip().lower() != "y":
        return
    total = 0.0
    for cid in ids:
        ref = RAW / f"{cid}.png"                       # the walking sheet: style + character
        out = RAW / f"{cid}_sit.png"
        cost = make_art.generate(SIT_PROMPT, ref, out, size=(1536, 1024))
        total += cost
        print(f"  {cid}: ${cost:.3f} -> {out.relative_to(ROOT)}")
    print(f"spent ${total:.3f}")


def prompt_for(cid: str) -> str:
    if cid == "boss":
        return PROMPT.format(who=BOSS, ref="")
    keep = KEEP.get(cid, "")
    ref = (f"Use the attached image as the design reference: keep her face, hair style and "
           f"color, {EYES[cid]} eyes, {keep}and outfit exactly, simplified into pixel art. ")
    return PROMPT.format(who=CAST[cid],
                         ref=ref)


def cmd_make(a) -> None:
    ids = IDS if a.id == "all" else [a.id]
    est = make_art.EST[make_art.MODEL] * 1.3 * len(ids)
    print(f"{len(ids)} image(s), about ${est:.2f}: {', '.join(ids)}")
    if not a.yes and input("go? [y/N] ").strip().lower() != "y":
        return
    total = 0.0
    for cid in ids:
        ref = REFS / f"{cid}.png"
        out = RAW / f"{cid}.png"
        cost = make_art.generate(prompt_for(cid), ref if ref.exists() else None, out,
                                 size=(1536, 1024))
        total += cost
        print(f"  {cid}: ${cost:.3f} -> {out.relative_to(ROOT)}")
    print(f"spent ${total:.3f}")


# --------------------------------------------------------------------------- cutting
def key_out(im: Image.Image, tol: float = 22) -> Image.Image:
    """Remove the flat background: background-colored pixels connected to the border.
    (A grey vest inside an outline is not connected to the border, so it stays.)"""
    from scipy import ndimage
    rgb = np.asarray(im.convert("RGB")).astype(int)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    near = np.sqrt(((rgb - bg) ** 2).sum(axis=2)) < tol
    lab, _ = ndimage.label(near)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    out = np.dstack([rgb, np.where(np.isin(lab, list(edge)), 0, 255)]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def figures(im: Image.Image) -> list[Image.Image]:
    """The 8 figures, in reading order, as RGBA crops with the background removed."""
    cut = key_out(im)
    a = np.asarray(cut)[..., 3] > 40
    h, w = a.shape
    rows = a.any(axis=1)
    # split into the two rows at the emptiest horizontal band near the middle
    mid = int(np.argmin(a[h // 3: 2 * h // 3].sum(axis=1))) + h // 3
    out = []
    for y0, y1 in ((0, mid), (mid, h)):
        band = a[y0:y1]
        cols = band.any(axis=0)
        runs, x = [], 0
        while x < w:                                  # column runs = figures
            if cols[x]:
                s = x
                while x < w and cols[x]:
                    x += 1
                runs.append((s, x))
            x += 1
        runs = [r_ for r_ in runs if r_[1] - r_[0] > w * 0.04]
        runs = sorted(sorted(runs, key=lambda r_: r_[0] - r_[1])[:4])   # the 4 widest
        for x0, x1 in runs:
            ys = np.nonzero(band[:, x0:x1].any(axis=1))[0]
            out.append(cut.crop((x0, y0 + ys[0], x1, y0 + ys[-1] + 1)))
    if len(out) != 8 or not rows.any():
        sys.exit(f"found {len(out)} figures, expected 8: check the raw sheet")
    return out


def to_frame(fig: Image.Image, scale: float) -> Image.Image:
    w, h = fig.size
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    small = fig.resize((nw, nh), Image.LANCZOS)
    arr = np.asarray(small).copy()
    arr[..., 3] = np.where(arr[..., 3] > 110, 255, 0)  # hard pixel edges
    small = Image.fromarray(arr, "RGBA")
    rgb = small.convert("RGB").quantize(colors=24, method=Image.Quantize.MEDIANCUT).convert("RGB")
    small = Image.merge("RGBA", (*rgb.split(), small.split()[3]))
    frame = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    frame.alpha_composite(small, ((FW - nw) // 2, FH - nh))
    return frame


def row_figures(im: Image.Image, n: int = 4) -> list[Image.Image]:
    """`n` figures standing in one row, left to right, background removed."""
    cut = key_out(im)
    a = np.asarray(cut)[..., 3] > 40
    cols = a.any(axis=0)
    runs, x, w = [], 0, a.shape[1]
    while x < w:
        if cols[x]:
            s0 = x
            while x < w and cols[x]:
                x += 1
            runs.append((s0, x))
        x += 1
    runs = sorted(sorted(runs, key=lambda r_: r_[0] - r_[1])[:n])
    out = []
    for x0, x1 in runs:
        ys = np.nonzero(a[:, x0:x1].any(axis=1))[0]
        out.append(cut.crop((x0, ys[0], x1, ys[-1] + 1)))
    if len(out) != n:
        sys.exit(f"found {len(out)} sitting figures, expected {n}")
    return out


def cmd_cut(a) -> None:
    ids = IDS if a.id == "all" else [a.id]
    for cid in ids:
        raw = RAW / f"{cid}.png"
        if not raw.exists():
            print(f"  {cid}: no raw sheet, run `make {cid}` first")
            continue
        figs = figures(Image.open(raw))
        scale = FIG_H / max(f.size[1] for f in figs)   # one scale for all 8: same size
        sit_raw = RAW / f"{cid}_sit.png"
        rows = 3 if sit_raw.exists() else 2
        sheet = Image.new("RGBA", (FW * 4, FH * rows), (0, 0, 0, 0))
        for k, f in enumerate(figs):
            sheet.alpha_composite(to_frame(f, scale), ((k % 4) * FW, (k // 4) * FH))
        if sit_raw.exists():                           # row 3: chair (from behind), seiza L, R, back
            # drawn from the walking sheet at the same pixel scale: the same scale fits
            for k, f in enumerate(row_figures(Image.open(sit_raw))):
                sheet.alpha_composite(to_frame(f, scale), (k * FW, 2 * FH))
        OUT.mkdir(parents=True, exist_ok=True)
        sheet.save(OUT / f"{cid}.png")
        print(f"  {cid}: {OUT / (cid + '.png')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("id")
    m.add_argument("--yes", action="store_true")
    c = sub.add_parser("cut")
    c.add_argument("id")
    ms = sub.add_parser("make-sit")
    ms.add_argument("id")
    ms.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    {"make": cmd_make, "cut": cmd_cut, "make-sit": cmd_make_sit}[a.cmd](a)


if __name__ == "__main__":
    main()
