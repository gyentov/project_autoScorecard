"""Make the team's dating-sim standing art with an image API. Guide: docs/character_art.md.

    .venv/bin/python scripts/art/make_art.py make                 # shows count + cost only
    .venv/bin/python scripts/art/make_art.py make --yes           # the 15 base images each
    .venv/bin/python scripts/art/make_art.py make --extras --yes  # + the 12 meeting combos
    .venv/bin/python scripts/art/make_art.py emotion citra presenting embarrassed \
        --desc "flushed cheeks, eyes glancing away, a small awkward smile" --yes
    .venv/bin/python scripts/art/make_art.py redo citra neutral_happy --prompt "..." --yes
    .venv/bin/python scripts/art/make_art.py refs --only laras    # new reference candidates
    .venv/bin/python scripts/art/make_art.py pick laras --file path/to/chosen.png

Model: gpt-image-2 (OPENAI_API_KEY in .env), medium quality, 1024x1536, on a flat grey
background that finish.py removes. Each pose is drawn from the character's reference; each
emotion edits a pose image with a face mask, so only the face changes.

Raw images go to assets/characters/_raw/<id>/ (they are what later edits attach); finished
600x900 transparent copies go to assets/characters/<id>/. Existing images are never made
again, so a stopped run can just be restarted. Every call is logged with its cost (from the
API's token usage) in assets/characters/_raw/costs.jsonl.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finish  # noqa: E402
import prompts  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CHARS = ROOT / "assets" / "characters"
RAW = CHARS / "_raw"
REFS = CHARS / "_references"            # committed copies of the chosen references
OPENAI = "https://api.openai.com/v1/images/"
MODEL = "gpt-image-2"
SIZE = (1024, 1536)
GREY = (230, 230, 230)
# USD per token: (text in, image in, image out). Used to price each call from its usage.
TOKEN_PRICE = {"gpt-image-2": (5e-6, 8e-6, 30e-6), "gpt-image-1": (5e-6, 10e-6, 40e-6),
               "gpt-image-1-mini": (2e-6, 2.5e-6, 8e-6)}
EST = {"gpt-image-2": 0.055, "gpt-image-1": 0.07, "gpt-image-1-mini": 0.02}   # per image


# --------------------------------------------------------------------------- API

def api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("OPENAI_API_KEY is not set (.env)")
    return key


def on_grey(p: Path, size: tuple[int, int] = SIZE) -> bytes:
    """The image as PNG bytes, flattened on the grey background at `size`."""
    im = Image.open(p).convert("RGBA")
    if im.size != size:                  # e.g. a finished 600x900 file when no raw is left
        im = im.resize(size, Image.LANCZOS)
    bg = Image.new("RGBA", size, GREY + (255,))
    bg.alpha_composite(im)
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def face_mask(p: Path) -> bytes:
    """PNG mask for the edit API: transparent (= may change) over the face, opaque elsewhere.
    The face is found as the skin-colored area at the top of the figure."""
    a = np.asarray(Image.open(p).convert("RGB").resize(SIZE)).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    skin = (r > 200) & (g > 170) & (b > 140) & (r - b > 25) & (r - b < 90)
    skin[int(SIZE[1] * 0.5):] = False
    ys, xs = np.nonzero(skin)
    if len(ys) < 500:
        sys.exit(f"could not find the face in {p}")
    y0 = int(np.percentile(ys, 1))
    head = skin[y0:y0 + 380]                     # forehead to chin, not raised hands below
    hy, hx = np.nonzero(head)
    x0, x1 = int(np.percentile(hx, 3)), int(np.percentile(hx, 97))
    y1 = y0 + int(np.percentile(hy, 99))
    y1 = min(y1, y0 + int(1.3 * (x1 - x0)))      # stop at the chin, keep the collar
    m = np.full((SIZE[1], SIZE[0], 4), 255, np.uint8)
    m[max(0, y0 - 30):y1 + 10, max(0, x0 - 25):x1 + 25, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(m, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


def generate(prompt: str, attach: Path | None, out: Path, face: bool = False,
             model: str = MODEL, size: tuple[int, int] = SIZE) -> float:
    """One image -> `out` (opaque PNG on grey). Returns its cost in USD."""
    form = {"model": model, "prompt": prompt, "size": f"{size[0]}x{size[1]}",
            "quality": "medium", "background": "opaque", "output_format": "png"}
    headers = {"Authorization": f"Bearer {api_key()}"}
    for attempt in range(4):
        if attach is None:
            r = httpx.post(OPENAI + "generations", json=dict(form, n=1), headers=headers,
                           timeout=400)
        else:
            # a reference with the output's own shape is sent as is (sprite sheets)
            ref_size = size if Image.open(attach).size == size else SIZE
            files = [("image[]", (attach.name, on_grey(attach, ref_size), "image/png"))]
            if face:
                files.append(("mask", ("mask.png", face_mask(attach), "image/png")))
            r = httpx.post(OPENAI + "edits", data=form, files=files, headers=headers,
                           timeout=400)
        if (r.status_code == 429 or r.status_code >= 500) and attempt < 3 \
                and "insufficient_quota" not in r.text:
            time.sleep(20 * (attempt + 1))
            continue
        if r.status_code != 200:
            sys.exit(f"OpenAI error {r.status_code}: {r.text[:500]}")
        data = r.json()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
        u = data.get("usage", {})
        det = u.get("input_tokens_details", {})
        t_in, i_in, i_out = TOKEN_PRICE.get(model, (0, 0, 0))
        cost = (det.get("text_tokens", 0) * t_in + det.get("image_tokens", 0) * i_in
                + u.get("output_tokens", 0) * i_out) or EST.get(model, 0.06)
        with (RAW / "costs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model,
                                "file": str(out.relative_to(ROOT)), "usd_est": round(cost, 5),
                                "usage": u}) + "\n")
        return cost
    return 0.0


# --------------------------------------------------------------------------- helpers

def spent() -> float:
    log = RAW / "costs.jsonl"
    if not log.exists():
        return 0.0
    return sum(json.loads(ln)["usd_est"] for ln in log.read_text().splitlines() if ln.strip())


def ids(only: str | None) -> list[str]:
    return only.split(",") if only else list(prompts.CAST)


def source(cid: str, name: str) -> Path:
    """The raw image if it is still here, else the finished one (it gets upscaled)."""
    raw = RAW / cid / f"{name}.png"
    if raw.exists():
        return raw
    if name == "_reference" and (REFS / f"{cid}.png").exists():
        return REFS / f"{cid}.png"
    return CHARS / cid / f"{name}.png"


def backup(p: Path) -> None:
    if p.exists():
        old = p.parent / "_old" / f"{p.stem}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        old.parent.mkdir(exist_ok=True)
        shutil.move(str(p), old)


def run(plan: list[tuple[str, dict]], yes: bool) -> None:
    print(f"{len(plan)} image(s), ~${len(plan) * EST[MODEL]:.2f} with {MODEL} "
          f"(art spent so far ${spent():.2f})")
    if not plan:
        return
    if not yes:
        print("nothing generated; add --yes to go ahead")
        return
    for n, (cid, j) in enumerate(plan, 1):
        src = source(cid, j["attach"])
        if not src.exists():
            print(f"  [{n}/{len(plan)}] {cid}/{j['name']}: skipped, {j['attach']} is missing")
            continue
        cost = generate(j["prompt"], src, RAW / cid / f"{j['name']}.png", face=j["face"])
        print(f"  [{n}/{len(plan)}] {cid}/{j['name']}  ${cost:.3f}", flush=True)
    publish(sorted({cid for cid, _ in plan}))
    print(f"done; art spent so far ${spent():.2f}")


def publish(cids: list[str]) -> None:
    """Copy new raw images into assets/characters/<id>/ and let finish.py process them."""
    for cid in cids:
        for p in (RAW / cid).glob("*.png"):
            if p.name.startswith("_"):
                continue
            dst = CHARS / cid / p.name
            if not dst.exists() or dst.stat().st_mtime < p.stat().st_mtime:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p, dst)
    finish.main()


# --------------------------------------------------------------------------- commands

def cmd_make(a) -> None:
    plan = []
    for cid in ids(a.only):
        if not source(cid, "_reference").exists():
            print(f"  {cid}: skipped, no reference yet (refs + pick)")
            continue
        plan += [(cid, j) for j in prompts.jobs(cid, extras=a.extras)
                 if not (RAW / cid / f"{j['name']}.png").exists()]
    run(plan, a.yes)


def cmd_emotion(a) -> None:
    """A new <pose>_<mood> image, for one character or all (--all). A new mood needs --desc;
    it is saved to assets/characters/moods.json so every character can use it later."""
    if a.mood not in prompts.MOODS:
        if not a.desc:
            sys.exit(f"'{a.mood}' is a new mood: describe the face with --desc")
        custom = json.loads(prompts.CUSTOM_MOODS.read_text()) if prompts.CUSTOM_MOODS.exists() \
            else {}
        custom[a.mood] = a.desc
        prompts.CUSTOM_MOODS.write_text(json.dumps(custom, indent=2), encoding="utf-8")
        prompts.MOODS[a.mood] = a.desc
        print(f"saved new mood '{a.mood}' to {prompts.CUSTOM_MOODS.relative_to(ROOT)}")
    if a.pose not in prompts.POSES:
        sys.exit(f"unknown pose '{a.pose}'; poses: {', '.join(prompts.POSES)}")
    cids = list(prompts.CAST) if a.id == "all" else [a.id]
    plan = [(cid, {"name": f"{a.pose}_{a.mood}", "attach": f"{a.pose}_neutral", "face": True,
                   "prompt": prompts.face_prompt(cid, a.mood)}) for cid in cids
            if not (RAW / cid / f"{a.pose}_{a.mood}.png").exists()]
    run(plan, a.yes)


def cmd_redo(a) -> None:
    """Make listed images again (normal prompt + --prompt); the old raw goes to _old/."""
    jobs = {j["name"]: j for j in prompts.jobs(a.id)}
    plan = []
    for name in a.names:
        pose, mood = next((p, name[len(p) + 1:]) for p in sorted(prompts.POSES, key=len,
                                                                  reverse=True)
                          if name.startswith(p + "_"))
        j = jobs.get(name) or {"name": name, "attach": f"{pose}_neutral", "face": True,
                               "prompt": prompts.face_prompt(a.id, mood)}
        plan.append((a.id, dict(j, prompt=j["prompt"] + (" " + a.prompt if a.prompt else ""))))
    if a.yes:
        for _, j in plan:
            backup(RAW / a.id / f"{j['name']}.png")
    run(plan, a.yes)


def cmd_refs(a) -> None:
    """Reference candidates from text only, for a new look. Then `pick`."""
    for cid in ids(a.only):
        cdir = RAW / cid / "_candidates"
        k0 = max([int(p.stem.split("_")[1]) for p in cdir.glob("ref_*.png")] + [0])
        for k in range(k0 + 1, k0 + 1 + a.n):
            cost = generate(prompts.reference_prompt(cid), None, cdir / f"ref_{k}.png")
            print(f"  {cid} candidate {k}  ${cost:.3f}  -> {cdir / f'ref_{k}.png'}")


def cmd_pick(a) -> None:
    src = Path(a.file) if a.file else RAW / a.id / "_candidates" / f"ref_{a.k}.png"
    if not src.exists():
        sys.exit(f"no such image: {src}")
    (RAW / a.id).mkdir(parents=True, exist_ok=True)
    REFS.mkdir(parents=True, exist_ok=True)
    for name in ("_reference", "neutral_neutral"):
        backup(RAW / a.id / f"{name}.png")
        shutil.copy(src, RAW / a.id / f"{name}.png")
    shutil.copy(src, REFS / f"{a.id}.png")
    print(f"{a.id}: reference = {src}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--only")
    m.add_argument("--extras", action="store_true")
    m.add_argument("--yes", action="store_true")
    e = sub.add_parser("emotion")
    e.add_argument("id", help="character id, or 'all'")
    e.add_argument("pose")
    e.add_argument("mood")
    e.add_argument("--desc", help="face description, required for a new mood")
    e.add_argument("--yes", action="store_true")
    r = sub.add_parser("redo")
    r.add_argument("id")
    r.add_argument("names", nargs="+")
    r.add_argument("--prompt", default="")
    r.add_argument("--yes", action="store_true")
    f = sub.add_parser("refs")
    f.add_argument("--only")
    f.add_argument("--n", type=int, default=2)
    p = sub.add_parser("pick")
    p.add_argument("id")
    p.add_argument("k", type=int, nargs="?")
    p.add_argument("--file")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    {"make": cmd_make, "emotion": cmd_emotion, "redo": cmd_redo, "refs": cmd_refs,
     "pick": cmd_pick}[a.cmd](a)


if __name__ == "__main__":
    main()
