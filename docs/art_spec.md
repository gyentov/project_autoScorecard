# Character art: brief, prompts and delivery spec

> **The art is now made by the project itself**: see `docs/character_art.md`
> (`scripts/art/make_art.py`). This page is the original brief for an outside artist
> or a by-hand image AI (`docs/art_prompts/`).

Give this file to the artist or image AI. The game loads art by **file name and
size**, so any art that follows section 5 can replace the placeholders from
`scripts/art/characters.py` without changing code.

Two sets are needed:

| Set | What it is | Where it shows | Count |
|---|---|---|---|
| **A. Standing art** | Large dating-sim / visual-novel portraits | Meeting scenes, dialogue box | 4 characters × 8 poses × 9 moods = 288 (minimum 4 × 17 = 68, see 5.4) |
| **B. Office sprites** | Small top-down pixel characters | The walkable office | 5 sprite sheets (4 team + Boss) |

---

## 1. The setting (context for the artist)

A small Japanese tech startup that builds credit-risk scorecards. It is a tidy,
warm office with a startup feel: plants, whiteboards, a vending machine, a coffee corner.
The four team members are **adult professional women (mid-20s to early 30s)**.
They are AI colleagues working for the player, who is called "Boss" (no gender).

**Tone:** friendly, competent, a little playful. The art is office-appropriate: no
fanservice, no school uniforms, no chibi in Set A.

## 2. Global style block (paste at the start of every Set A prompt)

```
Anime visual-novel character standing art, otome / dating-sim style, clean cel
shading with 2 tones per color plus soft highlight, crisp dark-brown lineart
(#4a3535), large expressive anime eyes with two white highlights, soft pink
blush when emotional, adult woman (mid-20s), office-appropriate clothing,
front-facing with a slight 3/4 turn allowed, framed from the top of the head to
mid-thigh, full character centered, plain flat background, no text, no logo
except where specified, no props except where specified, consistent proportions
(about 1 : 5.5 head-to-body at this crop), high resolution.
```

**Negative prompt** (for Stable Diffusion, NovelAI and similar):

```
child, loli, school uniform, chibi, nsfw, cleavage, revealing clothes, extra
fingers, deformed hands, multiple people, cropped head, background scenery,
text, watermark, signature, 3d render, photorealistic
```

## 3. The cast (character blocks)

Paste the character block after the style block. The hex colors match the
placeholders and the office UI, so keep them close.

### Laras — Partner (team lead, honest about costs and risks)
```
Laras: calm, warm, confident team lead. Long straight dark-brown hair
(#5a3a2e, highlights #7a5242) falling past the shoulders with two front locks
over the shoulders, side-swept bangs. Warm brown eyes (#7a4a2a). Navy tailored
blazer (#2f3b5c) with darker lapels, white blouse, two small gold buttons
(#c9a227). Elegant, composed posture.
```

### Bening — Prepper (data preparation, meticulous and cheerful)
```
Bening: meticulous, cheerful, slightly shy data specialist. Black chin-length
bob haircut (#26262e) with straight bangs and a small blue hair clip (#2a78d6)
on her left side. Blue-grey eyes (#3f5a7a). Soft beige open cardigan (#d9c4a1)
over a white top, blue lanyard with a white company ID card on her chest.
```

### Citra — Modeller (analytical, precise, dry humour)
```
Citra: analytical, precise, quietly confident modeller. Dark navy hair
(#22304d, highlights #34466e) in a high ponytail tied with a burgundy hair tie
(#8b1e3f), neat bangs. Rounded rectangular glasses with thin dark frames.
Blue eyes (#2f5f8f). Grey vest (#5f6673) over a white long-sleeve shirt,
slim burgundy necktie (#8b1e3f).
```

### Kirana — Deck builder (creative, energetic, presentation designer)
```
Kirana: creative, energetic, expressive presentation designer. Short messy
coral-orange hair (#d9735a, highlights #ec9a7f) with a thin braid on her
right side. Warm amber eyes (#a0522d). Mustard-yellow hoodie (#e8a33d) with
white drawstrings and a round white chest patch showing the letters "AS" in
navy (#2f3b5c), the company logo.
```

## 4. Workflow that keeps the characters consistent

Image AIs drift between generations. Do it in this order:

1. **Reference sheet first.** For each character, generate one model sheet:
   ```
   [STYLE BLOCK] [CHARACTER BLOCK] Character reference sheet: front view,
   neutral standing pose, arms relaxed at her sides, neutral calm expression,
   plus a small row of 4 face close-ups (happy, serious, surprised, worried).
   ```
   Pick the best one. It becomes the identity reference for every other image.
2. **Give the reference with every pose prompt.** Use your tool's reference
   feature: Midjourney `--cref <url> --cw 100` (or omni-reference), ChatGPT or
   Gemini image (upload the sheet and say "same character, same outfit, same
   art style"), or Stable Diffusion / NovelAI (IP-Adapter or a small LoRA
   trained on the sheet).
3. **Do the poses one at a time, then the moods.** First lock each pose with a
   neutral face. Then make the moods by editing or inpainting **only the face**
   on that pose image. This keeps the body identical across moods, and it is
   how real visual-novel art is made.
4. **Same framing every time** (section 5.2). Before removing backgrounds, put
   the images side by side and check that the head size and eye line match.

### Per-image prompt template

```
[STYLE BLOCK] [CHARACTER BLOCK]
Pose: <pose description from 4.1>.
Expression: <mood description from 4.2>.
Same character, outfit and art style as the reference image.
```

### 4.1 Poses (file prefix → description)

| Pose id | Description for the prompt |
|---|---|
| `neutral` | standing relaxed, both arms down at her sides, looking at the viewer |
| `explaining` | right hand raised near shoulder height with an open palm, as if explaining a point, other arm relaxed |
| `presenting` | arm extended to her right side (viewer's left), index finger pointing at an off-screen projector screen, body slightly turned toward it |
| `thinking` | one arm folded across the waist, the other hand touching the chin, eyes glancing up |
| `tablet` | holding a tablet with both hands at chest level, screen facing the viewer showing a simple line chart |
| `bowing` | polite Japanese apology bow of about 15 degrees, head slightly lowered, hands together in front of the thighs |
| `cheering` | both fists raised up near her head in a small victory gesture |
| `arms_crossed` | arms crossed over the chest, weight on one leg, confident |

### 4.2 Moods (file suffix → description)

| Mood id | Description for the prompt | Used when |
|---|---|---|
| `neutral` | calm neutral expression, soft closed-mouth smile | default line |
| `happy` | warm smile, eyes closed in happy arcs (^ ^), light blush | good news |
| `proud` | half-lidded confident eyes, small smug smile, light blush | presenting a good result |
| `thinking` | eyes looking up and to the side, mouth a small flat line | weighing options |
| `worried` | eyebrows raised in the middle, small wavy frown, a small sweat drop near the temple | risk or problem found |
| `apologetic` | eyes closed in downward arcs, apologetic eyebrows, small sad smile, blush, sweat drop | mistakes, delays, cost overruns |
| `excited` | wide sparkling eyes with star highlights, big open-mouth smile, blush | big wins, kickoff |
| `serious` | half-lidded focused eyes, eyebrows angled down toward the centre, mouth a straight line | warnings, decisions |
| `surprised` | wide eyes with small irises, eyebrows high, small round open mouth, blush | unexpected results |

## 5. Delivery spec (Set A: standing art)

### 5.1 Files
- Format: **PNG, RGBA, transparent background**, sRGB.
- Size: **600 × 900 px** (2:3 portrait). If you have a larger master, keep it,
  but deliver at exactly this size. A 1200 × 1800 @2x copy is welcome in
  `assets/characters/@2x/<id>/` with the same names.
- Path and name: `assets/characters/<id>/<pose>_<mood>.png`, all lowercase,
  `<id>` ∈ `laras, bening, citra, kirana`.
  Example: `assets/characters/citra/presenting_proud.png`.
- No shadows on the ground and no glow around the outline. Clean alpha edges,
  no leftover background fringe (check against both black and white).

### 5.2 Framing (so characters line up when they swap in the dialogue box)
- Character centered horizontally: the nose at **x ≈ 300**.
- Top of the hair at **y ≈ 40–60**. Eye line at **y ≈ 240–250**. Chin at **y ≈ 385–395**.
- The body is cropped by the bottom edge at mid-thigh (**y = 900**).
- Head width (including hair) ≈ **260–280 px**. All four characters have about
  the same height: in-world they are a similar height, and Kirana may be about 10 px shorter.
- Arms and hands may extend to x ≈ 20–580 (the presenting pose reaches far left).
  Nothing may be cut off at the left, right or top edges.
- `bowing`: the whole upper body tilts. Keep the hips in the same place.

### 5.3 Background removal
Most image AIs can't make true transparency. Generate on a **plain flat
light-grey (#e6e6e6) background**, then remove it (for example with
`rembg`, Photoshop "Remove background" or remove.bg) and clean the hair edges.
Don't use a green screen: it tints the hair edges.

### 5.4 Minimum set and fallback
288 images is a lot. The game will use this lookup order, so a partial set still works:

1. `<pose>_<mood>.png` (exact)
2. `neutral_<mood>.png` (the right face, default pose)
3. `<pose>_neutral.png` (the right pose, calm face)
4. `neutral_neutral.png`

**Minimum per character (17 files):** `neutral_<all 9 moods>` + `<all 8 poses>_neutral`.
**Recommended next:** the pairs the meeting scripts use most:
`presenting_proud`, `presenting_happy`, `explaining_neutral`, `explaining_serious`,
`tablet_happy`, `tablet_thinking`, `thinking_thinking`, `thinking_worried`,
`bowing_apologetic`, `cheering_excited`, `arms_crossed_serious`, `arms_crossed_proud`.

`assets/characters/manifest.json` is regenerated by the project from the files
that exist, so you don't need to write it.

---

## 6. Set B: office sprites (pixel art)

### 6.1 Style block
```
Top-down 3/4 view pixel art character sprite sheet in the style of cozy farming
/ life-sim games (Stardew Valley-like), 32x48 pixel frames, limited palette,
1-pixel dark outline (#3a2a2a), no anti-aliasing, no semi-transparent pixels,
cute but adult proportions (about 2.5 heads tall), transparent background.
```
Reuse each character's colors and silhouette details from section 3 (hair shape,
glasses, hair clip, lanyard, hoodie and so on). They must be recognisable at 32 px.

### 6.2 The Boss (player, sprites only, no standing art)
```
The Boss: gender-neutral young adult, short neat dark hair, smart-casual dark
green jacket (#2e7d4f) over a white t-shirt, dark trousers, simple and
readable at small size.
```

### 6.3 Sheet layout (same for all five)
- One PNG per character: `assets/sprites/<id>.png`, `<id>` ∈ `laras, bening, citra, kirana, boss`.
- Frame **32 × 48 px**. Grid of **4 columns × 12 rows** → sheet **128 × 576 px**.
  Feet touch the bottom of the frame, centered horizontally. Leave unused cells transparent.
- Delivered at 1× (the game scales it up with nearest-neighbour).

| Row | Animation | Frames | Notes |
|---|---|---|---|
| 0 | `walk_down` | 4 | facing the camera |
| 1 | `walk_left` | 4 | |
| 2 | `walk_right` | 4 | may mirror `walk_left`, but keep asymmetric details (braid, clip, ponytail) on the correct side |
| 3 | `walk_up` | 4 | back view |
| 4 | `idle_down` | 2 | frame 2 = blink |
| 5 | `sit_type` | 2 | seated, seen from behind/above, typing at a desk (desk not included) |
| 6 | `drink` | 2 | facing down, holding a cup or bottle; frame 2 = sipping (coffee, water) |
| 7 | `eat` | 2 | facing down, holding a snack or bento; frame 2 = bite |
| 8 | `tablet` | 2 | facing down, looking at a tablet or phone |
| 9 | `yawn` | 2 | sleepy stretch or yawn |
| 10 | `bow` | 2 | small bow (greeting or apology) |
| 11 | `cheer` | 2 | arms up |

For the Boss, rows 5–11 are optional: at minimum rows 0–4.

---

## 7. Acceptance checklist
- [ ] Each character looks like the same person across every pose and mood (face, hair, outfit, colors).
- [ ] 600 × 900 PNG, transparent, clean edges, framing per 5.2.
- [ ] File names exactly `<pose>_<mood>.png` from the lists above, lowercase.
- [ ] Office-appropriate, adult characters, no text except the "AS" hoodie logo.
- [ ] Sprite sheets 128 × 576, 32 × 48 frames, no semi-transparent pixels.
