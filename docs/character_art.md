# Character art guide

How the team's dating-sim standing art is made, and how to add a new emotion, pose or
fix later **without the characters drifting**. Everything here is one command away;
the script is `scripts/art/make_art.py`.

## 1. What exists

| | |
|---|---|
| Finished art | `assets/characters/<id>/<pose>_<mood>.png`: 600 × 900, transparent PNG |
| List the game reads | `assets/characters/manifest.json` (rebuilt automatically) |
| Chosen references (the "truth" for each face) | `assets/characters/_references/<id>.png` (committed) |
| Working files | `assets/characters/_raw/<id>/`: 1024 × 1536 on grey, what later edits start from (not committed) |
| Custom emotions | `assets/characters/moods.json` (created when you add one) |
| Cost log | `assets/characters/_raw/costs.jsonl`: every API call with its real cost |

**Base set per character (16 images):** the 8 poses with a calm face (`<pose>_neutral`)
plus the 9 moods in the default pose (`neutral_<mood>`).

**Fallback when a combination is missing:** the game tries `<pose>_<mood>` →
`neutral_<mood>` → `<pose>_neutral` → `neutral_neutral`. A scene never breaks for a
missing image; the right face or the right pose wins.

## 2. The cast sheet

Everything below is in `scripts/art/prompts.py` (`CAST`, `EYES`, `KEEP`), and every
prompt repeats it. If you change a character, change it there.

| | Laras | Bening | Citra | Kirana |
|---|---|---|---|---|
| Role | Partner | Prepper | Modeller | Deck builder |
| Reference | `_references/laras.png` | `_references/bening.png` | `_references/citra.png` | `_references/kirana.png` |
| Hair | long straight dark brown, side-swept bangs, two front locks | black chin-length bob, straight bangs | dark navy high ponytail, neat bangs | short messy coral-orange, thin braid on her right |
| Eyes (restated in every prompt) | warm brown | blue-grey | blue | warm amber |
| Outfit | navy blazer, white blouse, gold buttons, navy skirt | beige open cardigan, white top, dark skirt | grey vest, white shirt, burgundy tie, dark skirt | mustard hoodie with white "AS" patch, black skirt |
| Must keep (`KEEP`) | (none) | blue hair clip, lanyard with ID card | rectangular dark-framed glasses, burgundy hair tie | side braid, "AS" patch |
| Personality → favourite moods | calm, honest → neutral, serious, proud | cheerful, shy → happy, worried, apologetic | precise, dry → thinking, serious, proud | energetic → excited, happy, surprised |

## 3. Poses and moods

**Poses** (`POSES`): `neutral`, `explaining` (open palm), `presenting` (pointing at the
screen), `thinking` (hand on chin), `tablet` (holding a chart), `bowing` (apology),
`cheering` (fists up), `arms_crossed`.

**Moods** (`MOODS`), each a description of the face only:

| Mood | Face |
|---|---|
| `neutral` | calm, soft closed-mouth smile |
| `happy` | eyes closed in happy arcs, warm smile, light blush |
| `proud` | half-lidded confident eyes, small smug smile, light blush |
| `thinking` | eyes up and to the side, mouth a small flat line |
| `worried` | eyebrows raised in the middle, small wavy frown, sweat drop |
| `apologetic` | eyes closed in downward arcs, apologetic brows, small sad smile, blush, sweat drop |
| `excited` | wide sparkling eyes with star highlights, big open smile, blush |
| `serious` | half-lidded focused eyes, brows angled down, straight mouth |
| `surprised` | wide eyes with small irises, brows high, small round open mouth, blush |

## 4. Adding a new emotion

One command. It edits the matching `<pose>_neutral` image **with a face mask**, so only
the face changes and the hair, outfit, pose and framing stay pixel-identical.

```bash
# an existing mood in another pose (for one character, or all four)
.venv/bin/python scripts/art/make_art.py emotion citra presenting proud --yes
.venv/bin/python scripts/art/make_art.py emotion all tablet worried --yes

# a brand-new mood: describe the face once; it is saved to moods.json for everyone
.venv/bin/python scripts/art/make_art.py emotion bening neutral embarrassed \
    --desc "flushed cheeks, eyes glancing away to the side, a small awkward smile" --yes
```

Leave out `--yes` first to see how many images it would make and the cost.

**Writing a good `--desc`** (this is what keeps new emotions consistent with the old ones):
- Describe **only the face**, in this order: eyes → eyebrows → mouth → extras (blush,
  sweat drop, tears). Example: "eyes narrowed in a playful squint, one eyebrow raised, a
  lopsided grin".
- Don't mention the body, hands, clothes or accessories. The mask can't change them, and
  naming them invites the model to add things.
- Don't name an eye colour; the script adds each character's own.
- Use the same anime vocabulary as the table above ("happy arcs", "half-lidded",
  "sweat drop") so the new face matches the existing ones.
- One mood = one feeling. Make "smug" and "embarrassed" separately, not as one "smug but
  embarrassed".

## 5. Fixing a bad image

```bash
# make it again with an extra instruction; the old one goes to _raw/<id>/_old/
.venv/bin/python scripts/art/make_art.py redo citra neutral_excited \
    --prompt "She must keep her rectangular glasses." --yes
```

Check the result on `_raw/<id>/<name>.png` (grey background) or the finished file.

## 6. Adding a new pose

1. Add it to `POSES` in `scripts/art/prompts.py` with a one-line body description, e.g.
   `"waving": "one hand raised and waving at the viewer, the other arm relaxed"`.
2. Run `make_art.py make --yes`. It draws `<pose>_neutral` from each reference.
3. Add faces in that pose with `emotion` (section 4).

## 7. Why this stays consistent (the recipe)

| Rule | Why |
|---|---|
| Model `gpt-image-2`, medium quality, 1024 × 1536 | With a reference image it keeps the face, eyes and outfit; `gpt-image-1-mini` drifted (dull eyes, lost glasses, fabric noise) |
| Every pose starts from the character's **reference**, never from another pose | Errors don't add up from image to image |
| Every face change uses a **face mask** | Only the face is redrawn; the rest stays pixel-identical (measured mean change outside the mask: 3.6 out of 255) |
| The eye colour is restated in every prompt | Without it, eyes drifted to blue |
| `KEEP` names only **that** character's accessories, plus "do not add anything" | A shared list made Laras grow glasses and a hair clip |
| "Flat clean colors, no texture, grain or noise" in the style | Stops the grainy fabric look |
| Flat grey background, removed by `rembg` with the `isnet-anime` model | `gpt-image-2` can't draw transparent backgrounds, and a flood fill left grey patches between hair and body |

## 8. Cost (measured)

| What | Cost each |
|---|---|
| `gpt-image-2` pose or face edit (1024 × 1536, medium) | about $0.054 |
| Background removal and resizing | $0 (runs locally) |
| A new emotion for all four characters | about $0.22 |
| A new pose for all four (calm face only) | about $0.22 |

The total so far is in `costs.jsonl`. `make_art.py make` (without `--yes`) prints it.

## 9. Starting over with a new look

```bash
.venv/bin/python scripts/art/make_art.py refs --only kirana --n 2      # 2 candidates
.venv/bin/python scripts/art/make_art.py pick kirana 2                 # or --file path.png
```

`pick` replaces the reference (the old one goes to `_old/`). After that, existing images
no longer match it, so delete `_raw/<id>/*.png` (not the folders) and run `make --yes`
again.
