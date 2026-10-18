"""Finish the downloaded character art: remove the grey background, size to 600x900, update the manifest.

    .venv/bin/python scripts/art/finish.py

Works on every PNG/JPG/WebP under assets/characters/<id>/. A file that is not yet a
600x900 transparent PNG is converted in place (saved as <name>.png). A copy of a
make_art.py image is then dropped (its source stays in _raw/); a hand-made original goes to
assets/characters/_originals/. Uses `rembg` if it is installed, otherwise a flood fill
of the flat grey background from the borders.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
CHARS = ROOT / "assets" / "characters"
W, H = 600, 900
KEY = (255, 0, 255)
_SESSION = None
POSES = ("neutral", "explaining", "presenting", "thinking", "tablet", "bowing", "cheering",
         "arms_crossed")
MOODS = ("neutral", "happy", "proud", "thinking", "worried", "apologetic", "excited",
         "serious", "surprised")


def done(p: Path) -> bool:
    if p.suffix.lower() != ".png":
        return False
    with Image.open(p) as im:
        return im.size == (W, H) and im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0


def remove_background(im: Image.Image) -> Image.Image:
    if im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0:     # already transparent
        return im
    try:
        from rembg import new_session, remove
        global _SESSION
        if _SESSION is None:
            _SESSION = new_session("isnet-anime")      # trained on anime characters
        return remove(im.convert("RGBA"), session=_SESSION)
    except ImportError:
        pass
    rgb = im.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(xy) for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    bg = sorted(corners)[1]                        # most corners are background

    def is_bg(px) -> bool:
        return sum(abs(a - b) for a, b in zip(px, bg)) < 40

    # flood only from border pixels that look like background, so clothes touching the
    # bottom edge are never erased
    seeds = [(x, y) for x in range(0, w, 4) for y in (0, h - 1)] + \
            [(x, y) for y in range(0, h, 4) for x in (0, w - 1)]
    for xy in seeds:
        px = rgb.getpixel(xy)
        if px != KEY and is_bg(px):
            ImageDraw.floodfill(rgb, xy, KEY, thresh=30)
    out = rgb.convert("RGBA")
    out.putdata([(0, 0, 0, 0) if px[:3] == KEY else px for px in out.getdata()])
    return out


def fit(im: Image.Image) -> Image.Image:
    """Scale to 900 px tall (keeps the AI's framing), then center on a 600-wide canvas."""
    w = round(im.width * H / im.height)
    im = im.resize((w, H), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((W - w) // 2, 0)) if w <= W else \
        canvas.alpha_composite(im.crop(((w - W) // 2, 0, (w - W) // 2 + W, H)))
    return canvas


def main() -> None:
    converted = 0
    for d in sorted(p for p in CHARS.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or done(p):
                continue
            with Image.open(p) as src:
                out = fit(remove_background(src))
            if (CHARS / "_raw" / d.name / p.name).exists():      # make_art keeps the source
                p.unlink()
            else:                                                # a hand-made file: keep it
                keep = CHARS / "_originals" / d.name / p.name
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), keep)
            out.save(p.with_suffix(".png"))
            converted += 1
            print(f"  finished {d.name}/{p.with_suffix('.png').name}")
    files = {d.name: sorted(f"{d.name}/{p.name}" for p in d.glob("*.png")
                            if not p.name.startswith("_"))
             for d in sorted(CHARS.iterdir()) if d.is_dir() and not d.name.startswith("_")}
    manifest = {"size": [W, H], "poses": list(POSES), "moods": list(MOODS), "files": files}
    (CHARS / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"converted {converted} image(s); manifest lists "
          f"{sum(len(v) for v in files.values())} file(s)")


if __name__ == "__main__":
    main()
