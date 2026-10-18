# Citra: prompts in order

Easiest: let the project make them (`scripts/art/make_art.py`, see docs/character_art.md). By hand: paste each prompt into ChatGPT or Gemini, attach what it says under **Attach**, save the result with the exact name shown (one chat per character), then run `.venv/bin/python scripts/art/finish.py`.

## Step 1: reference sheet (do this first, pick the best of a few tries)

Save this one twice: as `_reference.png` and as `neutral_neutral.png`.

### 1. Save as `assets/characters/citra/_reference.png  +  assets/characters/citra/neutral_neutral.png`

**Attach:** nothing

```text
Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: standing relaxed, both arms down at her sides, looking at the viewer. Expression: a calm neutral expression with a soft closed-mouth smile. This is her official character design; draw it clean and complete.
```

## Step 2: the other 7 poses (calm face)

### 2. Save as `assets/characters/citra/explaining_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: one hand raised near shoulder height with an open palm, as if explaining a point, the other arm relaxed. Expression: a calm neutral expression with a soft closed-mouth smile.
```

### 3. Save as `assets/characters/citra/presenting_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: one arm extended to the viewer's left, index finger pointing at an off-screen screen, body slightly turned toward it. Expression: a calm neutral expression with a soft closed-mouth smile.
```

### 4. Save as `assets/characters/citra/thinking_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: one arm folded across the waist, the other hand touching her chin, eyes glancing up. Expression: a calm neutral expression with a soft closed-mouth smile.
```

### 5. Save as `assets/characters/citra/tablet_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: holding a tablet with both hands at chest level, the screen facing the viewer showing a simple line chart. Expression: a calm neutral expression with a soft closed-mouth smile.
```

### 6. Save as `assets/characters/citra/bowing_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: a polite Japanese apology bow of about 15 degrees, head slightly lowered, hands together in front of her thighs. Expression: a calm neutral expression with a soft closed-mouth smile.
```

### 7. Save as `assets/characters/citra/cheering_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: both fists raised up near her head in a small victory gesture. Expression: a calm neutral expression with a soft closed-mouth smile.
```

### 8. Save as `assets/characters/citra/arms_crossed_neutral.png`

**Attach:** `_reference.png`

```text
Same character, same outfit, same art style and same framing as the attached reference image. Keep her blue eyes with glossy highlights.

Anime visual-novel character standing art, otome dating-sim style, clean cel shading with soft highlights, crisp dark-brown lineart, large expressive anime eyes, adult woman in her mid-20s, office-appropriate clothing, flat clean colors with no fabric texture, grain or noise, facing the viewer, framed from the top of the head to mid-thigh, character centered with space around her, plain flat light-grey background (#e6e6e6), no text, no other people, no scenery. Portrait image, 2:3 aspect ratio.

Citra, an analytical, precise, quietly confident modeller. Dark navy hair in a high ponytail tied with a burgundy hair tie, neat bangs, rounded rectangular glasses with thin dark frames, blue eyes. Grey vest over a white long-sleeve shirt, slim burgundy necktie.

Pose: arms crossed over her chest, weight on one leg, confident. Expression: a calm neutral expression with a soft closed-mouth smile.
```

## Step 3: the 8 other faces (edit the neutral pose, change only the face)

### 9. Save as `assets/characters/citra/neutral_happy.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to a warm smile with eyes closed in happy arcs and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 10. Save as `assets/characters/citra/neutral_proud.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded confident eyes, a small smug smile and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 11. Save as `assets/characters/citra/neutral_thinking.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to eyes looking up and to the side, mouth a small flat line. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 12. Save as `assets/characters/citra/neutral_worried.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to eyebrows raised in the middle, a small wavy frown and a small sweat drop near the temple. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 13. Save as `assets/characters/citra/neutral_apologetic.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to eyes closed in downward arcs, apologetic eyebrows, a small sad smile, a blush and a sweat drop. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 14. Save as `assets/characters/citra/neutral_excited.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to wide sparkling eyes with star highlights, a big open-mouth smile and a blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 15. Save as `assets/characters/citra/neutral_serious.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded focused eyes, eyebrows angled down toward the centre, mouth a straight line. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 16. Save as `assets/characters/citra/neutral_surprised.png`

**Attach:** `neutral_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to wide eyes with small irises, eyebrows raised high, a small round open mouth and a blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

**Stop here if you like.** These 16 images are enough for the game. Missing combinations use the nearest image.

## Step 4 (optional): extra combinations used most in meetings

### 17. Save as `assets/characters/citra/presenting_proud.png`

**Attach:** `presenting_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded confident eyes, a small smug smile and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 18. Save as `assets/characters/citra/presenting_happy.png`

**Attach:** `presenting_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to a warm smile with eyes closed in happy arcs and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 19. Save as `assets/characters/citra/explaining_serious.png`

**Attach:** `explaining_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded focused eyes, eyebrows angled down toward the centre, mouth a straight line. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 20. Save as `assets/characters/citra/tablet_happy.png`

**Attach:** `tablet_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to a warm smile with eyes closed in happy arcs and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 21. Save as `assets/characters/citra/tablet_thinking.png`

**Attach:** `tablet_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to eyes looking up and to the side, mouth a small flat line. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 22. Save as `assets/characters/citra/thinking_worried.png`

**Attach:** `thinking_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to eyebrows raised in the middle, a small wavy frown and a small sweat drop near the temple. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 23. Save as `assets/characters/citra/bowing_apologetic.png`

**Attach:** `bowing_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to eyes closed in downward arcs, apologetic eyebrows, a small sad smile, a blush and a sweat drop. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 24. Save as `assets/characters/citra/cheering_excited.png`

**Attach:** `cheering_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to wide sparkling eyes with star highlights, a big open-mouth smile and a blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 25. Save as `assets/characters/citra/arms_crossed_serious.png`

**Attach:** `arms_crossed_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded focused eyes, eyebrows angled down toward the centre, mouth a straight line. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 26. Save as `assets/characters/citra/arms_crossed_proud.png`

**Attach:** `arms_crossed_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded confident eyes, a small smug smile and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 27. Save as `assets/characters/citra/explaining_happy.png`

**Attach:** `explaining_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to a warm smile with eyes closed in happy arcs and a light blush. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```

### 28. Save as `assets/characters/citra/presenting_serious.png`

**Attach:** `presenting_neutral.png`

```text
Edit the attached image. Change ONLY her facial expression to half-lidded focused eyes, eyebrows angled down toward the centre, mouth a straight line. Keep everything else exactly the same: hair, her rectangular dark-framed glasses, the burgundy hair tie, outfit, pose, framing, flat clean colors, art style and the plain grey background. Do not add anything that is not in the attached image. Her irises stay blue.
```
