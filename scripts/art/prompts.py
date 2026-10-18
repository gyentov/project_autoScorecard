"""Write copy-paste prompts for the character art, one per image, in the order to make them.

    .venv/bin/python scripts/art/prompts.py

Output: docs/art_prompts/<id>.md (standing art) and docs/art_prompts/sprites.md.
The full spec behind these prompts is docs/art_spec.md.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "art_prompts"
CUSTOM_MOODS = ROOT / "assets" / "characters" / "moods.json"

STYLE = ("Anime visual-novel character standing art, otome dating-sim style, clean cel shading "
         "with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult "
         "woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric "
         "texture, grain or noise, facing the viewer, framed from the "
         "top of the head to mid-thigh, character centered with space around her, plain flat "
         "light-grey background (#e6e6e6), no text, no other people, no scenery. "
         "Portrait image, 2:3 aspect ratio.")

CAST = {
    "laras": ("Laras, a calm, warm, confident team lead. Long straight dark-brown hair falling "
              "past the shoulders with two front locks over the shoulders, side-swept bangs, "
              "warm brown eyes. Navy tailored blazer with darker lapels, white blouse, two "
              "small gold buttons."),
    "bening": ("Bening, a meticulous, cheerful, slightly shy data specialist. Black chin-length "
               "bob haircut with straight bangs and a small blue hair clip on her left side, "
               "blue-grey eyes. Soft beige open cardigan over a white top, blue lanyard with a "
               "white company ID card on her chest."),
    "citra": ("Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a "
              "high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular "
              "glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve "
              "shirt, slim burgundy necktie."),
    "kirana": ("Kirana, a creative, energetic presentation designer. Short messy coral-orange "
               "hair with a thin braid on her right side, warm amber eyes. Mustard-yellow "
               "hoodie with white drawstrings and a round white chest patch showing the "
               "letters \"AS\" in navy."),
}

POSES = {
    "neutral": "standing relaxed, both arms down at her sides, looking at the viewer",
    "explaining": "one hand raised near shoulder height with an open palm, as if explaining a "
                  "point, the other arm relaxed",
    "presenting": "one arm extended to the viewer's left, index finger pointing at an "
                  "off-screen screen, body slightly turned toward it",
    "thinking": "one arm folded across the waist, the other hand touching her chin, eyes "
                "glancing up",
    "tablet": "holding a tablet with both hands at chest level, the screen facing the viewer "
              "showing a simple line chart",
    "bowing": "a polite Japanese apology bow of about 15 degrees, head slightly lowered, hands "
              "together in front of her thighs",
    "cheering": "both fists raised up near her head in a small victory gesture",
    "arms_crossed": "arms crossed over her chest, weight on one leg, confident",
}

MOODS = {
    "neutral": "a calm neutral expression with a soft closed-mouth smile",
    "happy": "a warm smile with eyes closed in happy arcs and a light blush",
    "proud": "half-lidded confident eyes, a small smug smile and a light blush",
    "thinking": "eyes looking up and to the side, mouth a small flat line",
    "worried": "eyebrows raised in the middle, a small wavy frown and a small sweat drop near "
               "the temple",
    "apologetic": "eyes closed in downward arcs, apologetic eyebrows, a small sad smile, a "
                  "blush and a sweat drop",
    "excited": "wide sparkling eyes with star highlights, a big open-mouth smile and a blush",
    "serious": "half-lidded focused eyes, eyebrows angled down toward the centre, mouth a "
               "straight line",
    "surprised": "wide eyes with small irises, eyebrows raised high, a small round open mouth "
                 "and a blush",
}

BASE_MOODS = tuple(MOODS)

# Emotions added later with `make_art.py emotion ... --desc` (shared by all characters).
if CUSTOM_MOODS.exists():
    MOODS.update(json.loads(CUSTOM_MOODS.read_text(encoding="utf-8")))

# Eye color, said again in every prompt: image models drift eye colors otherwise.
EYES = {"laras": "warm brown", "bening": "blue-grey", "citra": "blue", "kirana": "warm amber"}

EXTRA = ["presenting_proud", "presenting_happy", "explaining_serious", "tablet_happy",
         "tablet_thinking", "thinking_worried", "bowing_apologetic", "cheering_excited",
         "arms_crossed_serious", "arms_crossed_proud", "explaining_happy", "presenting_serious"]


def block(n: int, save_as: str, attach: str, prompt: str) -> str:
    return (f"### {n}. Save as `{save_as}`\n\n"
            f"**Attach:** {attach}\n\n```text\n{prompt}\n```\n")


EDIT = ("Edit the attached image. Change ONLY her facial expression to {mood}. Keep "
        "everything else exactly the same: hair, {keep}outfit, pose, framing, flat clean "
        "colors, art style and the plain grey background. Do not add anything that is not in "
        "the attached image.")
# what each character must keep when her face changes (and nobody else gets)
KEEP = {"laras": "", "bening": "the blue hair clip, the lanyard with ID card, ",
        "citra": "her rectangular dark-framed glasses, the burgundy hair tie, ",
        "kirana": "the thin side braid, the \"AS\" hoodie patch, "}


def reference_prompt(cid: str) -> str:
    return (f"{STYLE}\n\n{CAST[cid]}\n\nPose: {POSES['neutral']}. Expression: "
            f"{MOODS['neutral']}. This is her official character design; draw it clean "
            f"and complete.")


def pose_prompt(cid: str, pose: str) -> str:
    return (f"Same character, same outfit, same art style and same framing as the attached "
            f"reference image. Keep her {EYES[cid]} eyes with glossy highlights.\n\n{STYLE}\n\n"
            f"{CAST[cid]}\n\nPose: {POSES[pose]}. Expression: {MOODS['neutral']}.")


def face_prompt(cid: str, mood: str) -> str:
    return EDIT.format(mood=MOODS[mood], keep=KEEP[cid]) + \
        f" Her irises stay {EYES[cid]}."


def jobs(cid: str, extras: bool = True) -> list[dict]:
    """Every image after the reference: {name, attach, prompt, step, face}, in the order to make
    them. `attach` is the file name (same character folder) sent with the prompt; `face` means
    only the face may change (the API gets a face mask)."""
    out = []
    for pose in POSES:
        if pose != "neutral":
            out.append({"step": 2, "name": f"{pose}_neutral", "attach": "_reference",
                        "prompt": pose_prompt(cid, pose), "face": False})
    for mood in MOODS:
        if mood != "neutral" and (mood in BASE_MOODS or extras):
            out.append({"step": 3, "name": f"neutral_{mood}", "attach": "neutral_neutral",
                        "prompt": face_prompt(cid, mood), "face": True})
    if extras:
        for combo in EXTRA:
            pose = next(p for p in sorted(POSES, key=len, reverse=True)
                        if combo.startswith(p + "_"))
            out.append({"step": 4, "name": combo, "attach": f"{pose}_neutral",
                        "prompt": face_prompt(cid, combo[len(pose) + 1:]), "face": True})
    return out


def character_file(cid: str) -> str:
    d = f"assets/characters/{cid}"
    parts = [
        f"# {cid.capitalize()}: prompts in order\n",
        "Easiest: let the project make them (`scripts/art/make_art.py`, see "
        "docs/character_art.md). By hand: paste each prompt into ChatGPT or Gemini, attach what it "
        "says under **Attach**, save the result with the exact name shown (one chat per "
        "character), then run `.venv/bin/python scripts/art/finish.py`.\n",
        "## Step 1: reference sheet (do this first, pick the best of a few tries)\n",
        "Save this one twice: as `_reference.png` and as `neutral_neutral.png`.\n",
        block(1, f"{d}/_reference.png  +  {d}/neutral_neutral.png", "nothing",
              reference_prompt(cid)),
    ]
    heads = {2: "## Step 2: the other 7 poses (calm face)\n",
             3: "## Step 3: the 8 other faces (edit the neutral pose, change only the face)\n",
             4: "**Stop here if you like.** These 16 images are enough for the game. "
                "Missing combinations use the nearest image.\n\n"
                "## Step 4 (optional): extra combinations used most in meetings\n"}
    for n, j in enumerate(jobs(cid), 2):
        if j["step"] in heads:
            parts.append(heads.pop(j["step"]))
        parts.append(block(n, f"{d}/{j['name']}.png", f"`{j['attach']}.png`", j["prompt"]))
    return "\n".join(parts)


SPRITE_STYLE = (
    "Pixel art sprite sheet for a cozy top-down life-sim game (Stardew Valley style). "
    "A grid of 4 columns and 12 rows; every cell is one 32x48 pixel frame of the same "
    "character, feet at the bottom of the cell, centered. 1-pixel dark outline, limited "
    "palette, no anti-aliasing, transparent background. Rows from top to bottom: "
    "1 walking toward the camera (4 frames), 2 walking left (4), 3 walking right (4), "
    "4 walking away (4), 5 standing idle (2 frames, the second one blinking), "
    "6 sitting and typing seen from behind (2), 7 drinking from a cup (2), "
    "8 eating a snack (2), 9 looking at a tablet (2), 10 yawning (2), 11 a small bow (2), "
    "12 cheering with arms up (2). Empty cells stay transparent.")

SPRITE_CAST = dict(CAST, boss=(
    "The Boss, a gender-neutral young adult with short neat dark hair, a smart-casual dark "
    "green jacket over a white t-shirt and dark trousers."))


def sprites_file() -> str:
    parts = ["# Office sprites: prompts\n",
             "One prompt per character. Image AIs are bad at exact pixel grids, so treat the "
             "result as a draft: if a pixel-art tool (Aseprite, PixelLab, Piskel) is "
             "available, clean it up there to a **128 × 576** PNG. The game can run on the "
             "placeholder sprites until then.\n"]
    for n, (cid, desc) in enumerate(SPRITE_CAST.items(), 1):
        attach = "nothing" if cid == "boss" else f"`assets/characters/{cid}/_reference.png`"
        parts.append(block(n, f"assets/sprites/{cid}.png", attach,
                           f"{SPRITE_STYLE}\n\nCharacter: {desc} Small, cute but adult "
                           f"proportions (about 2.5 heads tall); keep her hair shape and "
                           f"outfit colors recognisable at this size."
                           .replace("her hair", "their hair" if cid == "boss" else "her hair")))
    return "\n".join(parts)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for cid in CAST:
        (OUT / f"{cid}.md").write_text(character_file(cid), encoding="utf-8")
    (OUT / "sprites.md").write_text(sprites_file(), encoding="utf-8")
    print(f"wrote prompts to {OUT}")


if __name__ == "__main__":
    main()
