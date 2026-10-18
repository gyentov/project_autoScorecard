"""Draw the office map (one screen, a modern corporate office with Japanese touches)
and its layout.

    .venv/bin/python scripts/art/office.py

Output:
    assets/office/office.png      896x480 pixel art (28 x 15 tiles of 32 px)
    assets/office/chair_back.png  desk-chair backrest, drawn IN FRONT of a seated person
    assets/office/layout.json     walkable grid, desks, seats, spots and animation anchors
The browser reads layout.json for pathfinding, for where everyone sits or stands and
for where to animate the work on each desk, so the picture and the logic always
agree. Replace office.png with hand-made art of the same size whenever you like;
keep layout.json in sync.

Style: Stardew-like 3/4 top-down view. Light comes from the top left; furniture has
a lit top face, a darker front face, a 1 px dark outline and a soft floor shadow.

Map (columns x rows):
    rows 0-2   back wall: meeting slats + projector | windows + whiteboard | pantry
    0-7 x 3-8  meeting room (glass walls, door at 8,6)
    9-20       work area: top desks Bening, Citra, Kirana; bottom desks Laras + Boss
    21-27x3-7  pantry, walled; a sliding door from the work area at 21,5
    0-7 x10-13 lounge (tatami nook, bookshelf)
    21-27x10-13 office front: reception counter, waiting sofa, entrance at 23-24,14
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "office"
T = 32
COLS, ROWS = 28, 15
W, H = COLS * T, ROWS * T
WALL_ROWS = 3
WB = WALL_ROWS * T                                       # y of the wall bottom
rng = random.Random(11)

# --------------------------------------------------------------------------- palette
INK = (46, 40, 48)
WALL, WALL_HI, WALL_LO = (238, 234, 226), (248, 245, 239), (218, 212, 202)
TRIM, TRIM_D = (124, 98, 78), (90, 70, 56)
SLAT, SLAT_D, SLAT_HI = (150, 112, 80), (118, 86, 60), (178, 138, 102)
PILLAR, PILLAR_D, PILLAR_HI = (206, 204, 206), (172, 170, 176), (226, 225, 228)
ASH, ASH_D, ASH_HI, ASH_SEAM = (222, 198, 160), (206, 180, 140), (232, 212, 178), (184, 158, 120)
MEET, MEET_A = (82, 96, 124), (76, 89, 116)
KITCHEN, KITCHEN_A, GROUT = (236, 232, 222), (224, 218, 206), (198, 192, 180)
OAK, OAK_D, OAK_HI = (196, 150, 102), (172, 128, 84), (212, 168, 120)
TATAMI, TATAMI_D, TATAMI_EDGE = (196, 198, 136), (178, 182, 118), (58, 76, 62)
DESK_TOP, DESK_FRONT = (244, 240, 232), (200, 192, 180)
WOOD_TOP, WOOD_FRONT = (168, 118, 80), (126, 86, 56)
METAL, METAL_D, METAL_HI = (178, 182, 190), (130, 134, 144), (216, 220, 226)
SCREEN_BG = (32, 40, 64)
GLASS, GLASS_HI, FRAME = (178, 214, 230), (228, 243, 249), (112, 122, 134)
LEAF, LEAF_D, LEAF_HI = (86, 156, 88), (54, 112, 68), (134, 198, 114)
POT, POT_D = (196, 118, 82), (150, 84, 60)
RED, NAVY, MUSTARD, TEAL, PINK, CYAN = ((212, 72, 72), (47, 59, 92), (232, 163, 61),
                                        (64, 168, 160), (255, 120, 190), (90, 220, 230))
WHITE = (250, 250, 248)
PAPER = (246, 244, 236)
SKY_TOP, SKY_LOW = (146, 194, 232), (208, 231, 245)
ACCENT = {"laras": (201, 162, 39), "bening": (42, 120, 214), "citra": (139, 30, 63),
          "kirana": (232, 163, 61), "boss": (46, 125, 79)}

img = Image.new("RGBA", (W, H), ASH + (255,))
d = ImageDraw.Draw(img)
solid = [[False] * COLS for _ in range(ROWS)]


def X(t: float) -> int:
    return int(round(t * T))


def r(x0, y0, x1, y1, c, outline=None):
    d.rectangle([x0, y0, x1, y1], fill=c, outline=outline)


def hline(x0, x1, y, c):
    d.line([x0, y, x1, y], fill=c)


def vline(x, y0, y1, c):
    d.line([x, y0, x, y1], fill=c)


def dark(c, k=0.8):
    return tuple(int(v * k) for v in c[:3])


def light(c, k=0.35):
    return tuple(int(v + (255 - v) * k) for v in c[:3])


def block(x, y, w=1, h=1):
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            solid[yy][xx] = True


def shadow(box, ellipse=False, alpha=56):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    (ld.ellipse if ellipse else ld.rectangle)(box, fill=(30, 24, 40, alpha))
    img.alpha_composite(layer)


def box3d(x0, y0, x1, y1, depth, top, front=None, hi=True, shade=True):
    """Furniture in 3/4 view: top face over a darker front face, outlined, with a shadow."""
    front = front or dark(top, 0.75)
    if shade:
        shadow((x0 + 3, y1 - 1, x1 + 4, y1 + 4))
    r(x0, y0, x1, y1 - depth, top)
    r(x0, y1 - depth + 1, x1, y1, front)
    if hi:
        hline(x0 + 1, x1 - 1, y0 + 1, light(top, 0.5))
    hline(x0, x1, y1 - depth, dark(top, 0.7))
    d.rectangle([x0, y0, x1, y1], outline=INK)


def plant(cx, base_y, size=1.0, pot=POT, tall=False):
    """A potted plant: pot with rim and lit leaf clusters (a small tree when `tall`)."""
    pw = int(10 * size)
    shadow((cx - pw - 1, base_y - 3, cx + pw + 4, base_y + 3), ellipse=True)
    ph = int(13 * size)
    r(cx - pw + 2, base_y - ph, cx + pw - 2, base_y, pot, INK)
    r(cx - pw + 3, base_y - ph + 2, cx - pw + 4, base_y - 2, light(pot, 0.3))
    r(cx - pw + 1, base_y - ph - 1, cx + pw - 1, base_y - ph + 2, dark(pot, 0.85), INK)
    top = base_y - ph
    if tall:                                              # trunk + crown
        r(cx - 1, top - 22, cx + 1, top, (110, 80, 56))
        top -= 22
    blobs = [(-7, -6, 8), (7, -7, 8), (0, -13, 9), (-5, -19, 7), (6, -18, 7), (0, -3, 7)]
    if tall:
        blobs += [(-9, -26, 7), (8, -27, 7), (0, -32, 8)]
    for dx, dy, rr in blobs:
        rr = int(rr * size)
        d.ellipse([cx + dx * size - rr, top + dy * size - rr, cx + dx * size + rr, top + dy * size + rr],
                  fill=LEAF_D, outline=INK)
    for dx, dy, rr in blobs:
        rr = int(rr * size) - 2
        d.ellipse([cx + dx * size - rr - 1, top + dy * size - rr - 1, cx + dx * size + rr - 1,
                   top + dy * size + rr - 2], fill=LEAF)
    for dx, dy, _ in blobs:
        d.point((cx + dx * size - 2, top + dy * size - 3), fill=LEAF_HI)
        d.point((cx + dx * size - 1, top + dy * size - 3), fill=LEAF_HI)
        d.point((cx + dx * size + 2, top + dy * size + 1), fill=dark(LEAF, 0.85))


def rug(x0, y0, x1, y1, base, border):
    shadow((x0 + 2, y0 + 2, x1 + 2, y1 + 2), alpha=26)
    r(x0, y0, x1, y1, base, INK)
    r(x0 + 3, y0 + 3, x1 - 3, y1 - 3, light(base, 0.08))
    d.rectangle([x0 + 5, y0 + 5, x1 - 5, y1 - 5], outline=border)
    for x in range(x0 + 1, x1, 3):                       # fringe
        d.point((x, y0 - 1), fill=light(border, 0.3))
        d.point((x, y1 + 1), fill=light(border, 0.3))


# =========================================================================== floors
# light ash planks for the work area and the office front: long boards, staggered seams
for ty in range(WALL_ROWS, ROWS):
    for row in range(4):
        y = ty * T + row * 8
        shift = ((ty * 4 + row) * 37) % 96
        for x0 in range(-shift, W, 96):
            tone = (ASH, ASH_HI, ASH, ASH_D)[(x0 // 96 + row + ty) % 4]
            r(max(0, x0), y, min(W - 1, x0 + 95), y + 7, tone)
            if 0 <= x0 < W:
                vline(x0, y, y + 7, ASH_SEAM)
            for k in range(3):                           # a little grain
                gx = x0 + 12 + k * 29 + (ty * 7 + row * 3) % 9
                if 0 <= gx < W:
                    hline(gx, min(W - 1, gx + 6), y + 3 + (k % 2) * 2, dark(tone, 0.95))
        hline(0, W - 1, y + 7, ASH_SEAM)

# meeting room: deep blue carpet with a woven texture, right up to the glass (col 8)
for ty in range(WALL_ROWS, 9):
    for tx in range(0, 9):
        x0, y0 = tx * T, ty * T
        right = x0 + (15 if tx == 8 else T - 1)                 # stops at the glass line
        r(x0, y0, right, y0 + T - 1, MEET)
        for j in range(0, T, 4):
            for k in range(0, right - x0, 4):
                if (j // 4 + k // 4) % 2 == 0:
                    d.point((x0 + k + 1, y0 + j + 1), fill=MEET_A)
# metal threshold where the carpet meets the wood, in the meeting-room doorway
r(X(8) + 14, X(6) + 1, X(8) + 18, X(7) - 2, METAL, INK)

# pantry: speckled terrazzo with brass strips, right up to its walls (from mid col 21)
TERRAZZO = (232, 228, 220)
r(X(21) + 16, WB, W - 1, X(8) - 1, TERRAZZO)
for y in range(WB, X(8)):
    for x in range(X(21) + 16, W):
        v = rng.random()
        if v < 0.05:
            d.point((x, y), fill=rng.choice([(196, 186, 172), (168, 160, 152), (214, 150, 128),
                                             (150, 160, 170), (250, 250, 248), (186, 196, 176)]))
        elif v < 0.07:
            d.point((x, y), fill=(222, 218, 210))
for x in range(X(22), W, 2 * T):
    vline(x, WB, X(8) - 1, (198, 166, 98))
for y in range(X(5), X(8), 2 * T):
    hline(X(21) + 16, W - 1, y, (198, 166, 98))

# office front: white marble with grey veins and a gold inlay border
for ty in range(10, 14):
    for tx in range(21, 28):
        x0, y0 = X(tx) + (16 if tx == 21 else 0), X(ty)     # col 21: the partition line
        r(x0, y0, X(tx) + T - 1, y0 + T - 1, (245, 242, 236) if (tx + ty) % 2 else (238, 235, 229))
        vx, vy = x0 + rng.randint(0, X(tx) + T - 1 - x0), y0
        for _ in range(40):                                   # a wandering vein
            vx += rng.choice((-1, 0, 1))
            vy += 1
            if x0 <= vx < X(tx) + T and vy < y0 + T:
                d.point((vx, vy), fill=rng.choice([(206, 202, 196), (218, 214, 208)]))
        hline(x0, X(tx) + T - 1, y0, (226, 222, 214))
        vline(x0, y0, y0 + T - 1, (226, 222, 214))
d.rectangle([X(21) + 22, X(10) + 3, W - 4, X(14) - 4], outline=(206, 170, 90))
d.rectangle([X(21) + 24, X(10) + 5, W - 6, X(14) - 6], outline=(226, 198, 130))

# lounge: oak boards
for ty in range(10, 14):
    for tx in range(0, 8):
        x0, y0 = tx * T, ty * T
        for k in range(4):
            yy = y0 + k * 8
            shade = OAK_HI if (tx * 3 + ty + k) % 5 == 0 else OAK
            r(x0, yy, x0 + T - 1, yy + 7, shade)
            hline(x0, x0 + T - 1, yy + 7, OAK_D)
            vline(x0 + (5 + 11 * ((tx + k + ty) % 3)) % T, yy, yy + 6, OAK_D)

# rugs: the work area runner, the office-front welcome rug
rug(X(12) + 6, X(10) + 6, X(20) - 6, X(13) - 4, (92, 110, 140), (214, 190, 120))
# the office front's round rug with the brand emblem
shadow((X(22) + 4, X(11) + 10, X(25) + 2, X(14) - 2), ellipse=True, alpha=24)
d.ellipse([X(22) + 2, X(11) + 6, X(25) - 2, X(14) - 6], fill=(36, 48, 84), outline=INK)
d.ellipse([X(22) + 6, X(11) + 10, X(25) - 6, X(14) - 10], outline=(214, 180, 100))
d.ellipse([X(23) + 8, X(12) + 4, X(24) + 8, X(12) + 18], outline=(214, 180, 100))

# =========================================================================== back wall
r(0, 0, W - 1, WB - 1, WALL)
r(0, 0, W - 1, 4, (80, 78, 90))                          # ceiling edge
hline(0, W - 1, 5, (118, 114, 128))
for x in range(0, W, 2):
    for y in range(8, WB - 14, 6):
        if rng.random() < 0.1:
            d.point((x + (y // 6) % 2, y), fill=WALL_LO)
r(0, WB - 14, W - 1, WB - 4, WALL_LO)                    # wainscot
hline(0, W - 1, WB - 15, WALL_HI)
for x in range(0, W, 16):
    vline(x, WB - 13, WB - 5, dark(WALL_LO, 0.94))
r(0, WB - 4, W - 1, WB - 1, TRIM_D)                      # skirting
hline(0, W - 1, WB - 4, TRIM)
for k in range(10):                                      # the wall's shadow on the floor
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle([0, WB + k, W - 1, WB + k], fill=(30, 24, 40, 70 - k * 7))
    img.alpha_composite(layer)

# meeting room wall: walnut acoustic slats + projector screen
r(0, 6, X(8) + 11, WB - 5, SLAT_D)                       # up to the glass partition
for x in range(2, X(8) + 9, 5):
    r(x, 7, x + 2, WB - 6, SLAT)
    vline(x, 7, WB - 6, SLAT_HI)
shadow((X(1) + 5, 16, X(7) + 2, 80), alpha=50)
r(X(0.9), 9, X(7.1), 14, (60, 60, 70), INK)
r(X(1), 14, X(7) - 1, 76, WHITE, INK)
r(X(1) + 3, 17, X(7) - 4, 73, (232, 240, 250))
for k, hgt in enumerate((12, 20, 16, 27, 23, 33, 30, 40)):
    bx = X(1.35) + k * 21
    r(bx, 70 - hgt, bx + 12, 70, (42, 120, 214))
    hline(bx, bx + 12, 70 - hgt, (110, 170, 240))
d.line([X(1.4) + 4, 48, X(2.6), 41, X(3.8), 45, X(5.2), 30, X(6.5), 24], fill=(232, 120, 60), width=2)

# where the partitions meet the back wall: the end of the meeting room's glass wall (an
# aluminium frame) and the end of the pantry wall (plaster). Nothing hangs on them.
r(X(8) + 12, 0, X(8) + 19, WB - 1, FRAME, INK)
vline(X(8) + 13, 1, WB - 2, (150, 160, 172))
r(X(21) + 10, 0, X(21) + 21, WB - 1, (214, 210, 204), INK)
r(X(21) + 10, 0, X(21) + 12, WB - 1, (232, 228, 222))
r(X(21) + 10, WB - 4, X(21) + 21, WB - 1, TRIM_D)


def window(x0, x1, y0=12, y1=WB - 22):
    """Tall window with a city view and a bamboo blind rolled half down."""
    r(x0, y0, x1, y1, FRAME, INK)
    for y in range(y0 + 2, y1 - 1):
        t = (y - y0) / (y1 - y0)
        hline(x0 + 2, x1 - 2, y, tuple(int(SKY_TOP[i] + (SKY_LOW[i] - SKY_TOP[i]) * t) for i in range(3)))
    d.ellipse([x0 + 8, y0 + 18, x0 + 22, y0 + 24], fill=(244, 248, 252))   # a cloud
    d.ellipse([x0 + 14, y0 + 15, x0 + 28, y0 + 23], fill=(244, 248, 252))
    sx = x0 + 2
    while sx < x1 - 2:
        bw, bh = rng.randint(5, 9), rng.randint(14, 38)
        col = rng.choice([(118, 138, 170), (134, 154, 186), (102, 122, 156)])
        r(sx, y1 - 1 - bh, min(sx + bw, x1 - 2), y1 - 2, col)
        for wy in range(y1 - bh + 2, y1 - 3, 4):
            for wx in range(sx + 1, min(sx + bw, x1 - 2), 3):
                if rng.random() < 0.5:
                    d.point((wx, wy), fill=(228, 238, 250))
        sx += bw + 1
    vline((x0 + x1) // 2, y0, y1, FRAME)
    hline(x0, x1, y0 + 26, FRAME)
    d.line([x0 + 4, y1 - 4, x0 + 14, y0 + 30], fill=GLASS_HI)
    r(x0 - 3, y1 + 1, x1 + 3, y1 + 4, WALL_HI, INK)       # sill
    r(x0 + 1, y0 + 1, x1 - 1, y0 + 13, (198, 166, 112))   # sudare blind
    for y in range(y0 + 2, y0 + 13, 2):
        hline(x0 + 1, x1 - 1, y, (170, 136, 86))
    hline(x0 + 1, x1 - 1, y0 + 13, INK)
    vline((x0 + x1) // 2 + 6, y0 + 13, y0 + 20, (120, 96, 70))   # pull cord


window(X(9) + 6, X(11) - 7)
window(X(19) + 6, X(21) - 7)

# whiteboard (the browser draws the live timeline dots on it)
WB_X0, WB_X1, WB_Y0, WB_Y1 = X(11) + 4, X(19) - 5, 12, 72
shadow((WB_X0 + 4, WB_Y0 + 3, WB_X1 + 3, WB_Y1 + 4), alpha=40)
r(WB_X0, WB_Y0, WB_X1, WB_Y1, METAL_D, INK)
r(WB_X0 + 2, WB_Y0 + 2, WB_X1 - 2, WB_Y1 - 2, WHITE)
hline(WB_X0 + 3, WB_X1 - 3, WB_Y0 + 3, (255, 255, 255))
r(WB_X0 + 2, WB_Y1 - 2, WB_X1 - 2, WB_Y1 + 3, METAL, INK)    # marker tray
for k, c in enumerate(((42, 120, 214), RED, (46, 125, 79))):
    r(WB_X0 + 10 + k * 9, WB_Y1, WB_X0 + 16 + k * 9, WB_Y1 + 1, c)
r(WB_X1 - 20, WB_Y1, WB_X1 - 8, WB_Y1 + 2, (90, 90, 100))
d.text((WB_X0 + 8, WB_Y0 + 5), "ROADMAP", fill=(150, 150, 166))
d.text((WB_X1 - 72, WB_Y0 + 5), "AutoScorecard", fill=(196, 196, 206))
for k, c in enumerate((MUSTARD, PINK, CYAN, MUSTARD)):     # sticky notes
    r(WB_X0 + 10 + k * 11, WB_Y0 + 34 + (k % 2) * 3, WB_X0 + 18 + k * 11, WB_Y0 + 42 + (k % 2) * 3, c)
DOTS = [WB_X0 + 70, WB_X1 - 16, WB_Y0 + 32]

# pantry wall: upper cabinets, subway-tile backsplash, neon logo, AC over the vending machine
r(X(22), 30, X(26) - 1, WB - 15, (246, 246, 242))
for y in range(30, WB - 15, 5):
    hline(X(22), X(26) - 1, y, (216, 216, 214))
    off = 0 if (y // 5) % 2 else 6
    for x in range(X(22) + off, X(26) - 1, 12):
        vline(x, y, y + 4, (216, 216, 214))
for k in range(4):                                       # upper cabinets
    x0 = X(22) + 2 + k * 31
    box3d(x0, 8, x0 + 28, 28, 3, (236, 232, 224), (210, 204, 194), shade=False)
    vline(x0 + 14, 10, 25, (200, 194, 184))
    r(x0 + 11, 20, x0 + 12, 23, METAL_D)
    r(x0 + 16, 20, x0 + 17, 23, METAL_D)
r(X(23) + 8, 40, X(25) - 8, 54, (30, 32, 44), INK)       # neon logo panel
d.ellipse([X(25) + 6, 33, X(25) + 26, 53], fill=WHITE, outline=INK, width=2)   # wall clock
vline(X(25) + 16, 37, 43, INK)
hline(X(25) + 16, X(25) + 21, 43, INK)
d.text((X(23) + 15, 41), "AS", fill=PINK)
d.text((X(23) + 29, 41), "lab", fill=CYAN)
r(X(26) + 6, 10, X(28) - 7, 27, WHITE, INK)              # wall air-con
hline(X(26) + 7, X(28) - 8, 11, (255, 255, 255))
r(X(26) + 8, 21, X(28) - 9, 24, (214, 214, 216))
for x in range(X(26) + 9, X(28) - 9, 3):
    vline(x, 22, 23, (176, 178, 186))
d.point((X(28) - 12, 15), fill=(120, 230, 140))
# the PANTRY sign: on the back wall, under the air-con, above the vending machine
r(X(26) + 8, 31, X(28) - 8, 43, (30, 32, 44), INK)
d.text((X(26) + 13, 31), "PANTRY", fill=(255, 210, 120))

block(0, 0, COLS, WALL_ROWS)

# =========================================================================== meeting room
for ty in range(WALL_ROWS, 10):                          # east glass wall, sliding door at y=6
    x0, y0 = X(8), X(ty)
    if ty == 6:
        hline(x0 + 12, x0 + 19, y0 + T - 2, METAL_D)
        continue
    r(x0 + 12, y0, x0 + 19, y0 + T - 1, GLASS)
    vline(x0 + 13, y0, y0 + T - 1, GLASS_HI)
    vline(x0 + 12, y0, y0 + T - 1, FRAME)
    vline(x0 + 19, y0, y0 + T - 1, FRAME)
    r(x0 + 12, y0 + 12, x0 + 19, y0 + 18, (226, 236, 240))
    block(8, ty)
r(X(8) + 11, X(6) - 3, X(8) + 20, X(6), FRAME, INK)
r(X(8) + 11, X(7) - 1, X(8) + 20, X(7) + 2, FRAME, INK)
for tx in range(0, 8):                                   # south glass wall, face-on
    x0, y0 = X(tx), X(9)
    shadow((x0, y0 + 27, x0 + T - 1, y0 + 31), alpha=40)
    r(x0, y0 + 2, x0 + T - 1, y0 + 26, GLASS)
    hline(x0, x0 + T - 1, y0 + 2, FRAME)
    hline(x0, x0 + T - 1, y0 + 1, INK)
    r(x0, y0 + 11, x0 + T - 1, y0 + 16, (226, 236, 240))
    for xx in range(x0 + 2, x0 + T, 4):
        d.point((xx, y0 + 13), fill=GLASS)
    d.line([x0 + 4, y0 + 24, x0 + 13, y0 + 5], fill=GLASS_HI)
    r(x0, y0 + 26, x0 + T - 1, y0 + 28, FRAME)
    hline(x0, x0 + T - 1, y0 + 28, INK)
    vline(x0 + T - 1, y0 + 2, y0 + 27, FRAME)
    block(tx, 9)
# the corner: the face-on glass runs on to meet the east wall's frame (no gap)
x0, y0 = X(8), X(9)
r(x0, y0 + 2, x0 + 19, y0 + 26, GLASS)
hline(x0, x0 + 19, y0 + 2, FRAME)
hline(x0, x0 + 19, y0 + 1, INK)
r(x0, y0 + 11, x0 + 19, y0 + 16, (226, 236, 240))
r(x0, y0 + 26, x0 + 19, y0 + 28, FRAME)
hline(x0, x0 + 19, y0 + 28, INK)
r(x0 + 12, y0 - 2, x0 + 19, y0 + 28, FRAME, INK)                 # corner post


def meeting_chair(cx, cy, facing):
    shadow((cx - 9, cy + 7, cx + 11, cy + 12), ellipse=True)
    r(cx - 8, cy - 3, cx + 8, cy + 8, (64, 70, 88), INK)
    hline(cx - 7, cx + 7, cy - 2, (92, 100, 122))
    if facing == "down":
        r(cx - 9, cy - 11, cx + 9, cy - 3, (52, 56, 72), INK)
        hline(cx - 8, cx + 8, cy - 10, (80, 86, 106))
    else:
        r(cx - 9, cy + 6, cx + 9, cy + 13, (52, 56, 72), INK)
        hline(cx - 8, cx + 8, cy + 7, (80, 86, 106))


for cx in range(2, 6):
    meeting_chair(X(cx) + 16, X(4) + 18, "down")
box3d(X(2) - 6, X(5) - 2, X(6) + 5, X(7) - 2, 8, WOOD_TOP, WOOD_FRONT)
hline(X(2) - 4, X(6) + 3, X(5) + 6, light(WOOD_TOP, 0.2))
hline(X(2) - 4, X(6) + 3, X(6) + 4, light(WOOD_TOP, 0.2))
r(X(3.8), X(5.6), X(4.6), X(6.15), (60, 60, 70), INK)        # projector
d.ellipse([X(4.6) - 4, X(5.75), X(4.6) + 2, X(5.75) + 6], fill=(200, 230, 255), outline=INK)
r(X(2.2), X(5.3), X(2.2) + 18, X(5.3) + 12, METAL_D, INK)  # laptop
r(X(2.2) + 2, X(5.3) + 2, X(2.2) + 16, X(5.3) + 8, SCREEN_BG)
for px_, py_ in ((5.4, 5.25), (5.7, 6.0), (3.0, 6.1)):        # papers
    r(X(px_), X(py_), X(px_) + 10, X(py_) + 13, PAPER, (180, 180, 186))
    hline(X(px_) + 2, X(px_) + 8, X(py_) + 4, (180, 180, 186))
d.ellipse([X(4.1), X(6.3), X(4.1) + 11, X(6.3) + 6], fill=(50, 50, 58), outline=INK)
block(2, 5, 4, 2)
for cx in range(2, 6):
    meeting_chair(X(cx) + 16, X(7) + 14, "up")
plant(X(0) + 16, X(9) - 4, 1.05, tall=True)
block(0, 8)
plant(X(7) + 16, X(4) - 2, 0.9)
block(7, 3)

# =========================================================================== desks
# Desks stand in joined rows like a real office: Bening, Citra and Kirana on one bench
# along the wall; Laras's desk joined to the Boss's executive desk below. Each desk is a
# 3/4-view box (lit top, apron, legs, a shadow on the floor) so it stands on the floor.
# who: (first tile x, tile y, width in tiles, which tile of the desk the chair is at)
DESKS = {"bening": (10, 3, 3, 0), "citra": (13, 3, 3, 0), "kirana": (16, 3, 3, 0),
         "laras": (13, 7, 3, 0), "boss": (16, 7, 4, 1)}
LAMINATE, LAMINATE_FRONT = (232, 216, 188), (190, 164, 128)
FX = {}                                                  # animation anchors for the browser
CHAIRS = []
TOP_Y, APRON_Y, FLOOR_Y = 18, 45, 56                     # desk rows: top face, apron, legs


def monitor(x, y, w=30, h=20, content="code"):
    r(x + w // 2 - 2, y + h, x + w // 2 + 2, y + h + 5, METAL_D, INK)
    r(x + w // 2 - 7, y + h + 4, x + w // 2 + 7, y + h + 6, METAL, INK)
    r(x, y, x + w, y + h, (40, 40, 48), INK)
    r(x + 2, y + 2, x + w - 2, y + h - 2, SCREEN_BG)
    if content == "table":                              # Bening: rows of data
        for k in range(4):
            hline(x + 4, x + w - 4, y + 4 + k * 4, (90, 110, 150))
            hline(x + 4, x + 10, y + 4 + k * 4, (120, 200, 140))
    elif content == "curve":                            # Citra: a score curve
        d.line([x + 4, y + h - 4, x + 9, y + h - 9, x + 15, y + 7, x + w - 4, y + 5], fill=(250, 140, 170))
        hline(x + 3, x + w - 3, y + h - 3, (90, 100, 130))
    elif content == "slides":                           # Kirana: a slide
        r(x + 4, y + 4, x + w - 4, y + h - 4, (240, 236, 228))
        r(x + 6, y + 6, x + 15, y + 8, MUSTARD)
        r(x + 6, y + 10, x + w - 6, y + 11, (150, 150, 160))
    elif content == "gantt":                            # Laras / Boss: the plan
        for k, (a, b_) in enumerate(((0, 8), (6, 14), (12, 20))):
            r(x + 4 + a, y + 4 + k * 4, x + 4 + b_, y + 5 + k * 4, (120, 180, 250))
    hline(x + 2, x + w - 2, y + 2, (70, 80, 110))
    return [x + 2, y + 2, w - 3, h - 3]


def chair_base(cx, y):
    """The parts of a desk chair BEHIND a seated person: seat and star base."""
    shadow((cx - 11, y + 22, cx + 13, y + 29), ellipse=True)
    r(cx - 1, y + 16, cx + 1, y + 24, METAL_D)
    hline(cx - 9, cx + 9, y + 25, METAL_D)
    for k in (-9, 0, 9):
        d.point((cx + k, y + 26), fill=INK)
    r(cx - 9, y + 8, cx + 9, y + 16, (70, 74, 94), INK)        # seat


def bench(tx, ty, tiles, top, front, seams=()):
    """A desk run seen in 3/4 view: lit top face, an apron, legs on the floor and the
    shadow under it. `seams` are tile x positions where two desks meet."""
    x0, x1, y0 = X(tx) + 1, X(tx + tiles) - 2, X(ty)
    shadow((x0 + 3, y0 + APRON_Y + 2, x1 + 5, y0 + 2 * T - 1), alpha=70)
    r(x0, y0 + TOP_Y, x1, y0 + APRON_Y - 1, top)                       # top face
    hline(x0 + 1, x1 - 1, y0 + TOP_Y + 1, light(top, 0.55))
    for k in range(y0 + TOP_Y + 6, y0 + APRON_Y - 2, 7):              # faint grain
        hline(x0 + 3, x1 - 3, k, dark(top, 0.97))
    hline(x0, x1, y0 + APRON_Y - 1, light(top, 0.3))                  # rounded front edge
    r(x0, y0 + APRON_Y, x1, y0 + FLOOR_Y, front)                      # apron
    hline(x0, x1, y0 + APRON_Y, dark(front, 0.8))
    d.rectangle([x0, y0 + TOP_Y, x1, y0 + FLOOR_Y], outline=INK)
    for lx in (x0 + 2, x1 - 4, *[X(s) - 2 for s in seams]):            # legs + feet
        r(lx, y0 + FLOOR_Y + 1, lx + 2, y0 + 2 * T - 3, METAL_D, None)
        hline(lx - 1, lx + 3, y0 + 2 * T - 2, INK)
    for sx in seams:                                                  # where two desks meet
        vline(X(sx) - 1, y0 + TOP_Y + 1, y0 + FLOOR_Y - 1, dark(top, 0.82))


def workstation(dx, dy, w, seat_off, who):
    """What sits on one desk: monitor, keyboard, mug, a name card and the owner's job
    props right of the PC; plus the chair below it."""
    x0, y0 = X(dx), X(dy)
    sx = X(dx + seat_off)                                  # the seat column (the sitter)
    acc = ACCENT[who]
    boss = who == "boss"
    # a drawer pedestal under the right end
    xr = X(dx + w) - 6
    r(xr - 22, y0 + APRON_Y + 1, xr, y0 + 2 * T - 2, dark(WOOD_FRONT if boss else LAMINATE_FRONT, 0.92), INK)
    for k in (y0 + APRON_Y + 5, y0 + APRON_Y + 11):
        hline(xr - 19, xr - 3, k, INK)
        r(xr - 13, k - 3, xr - 9, k - 2, METAL_HI)
    content = {"bening": "table", "citra": "curve", "kirana": "slides"}.get(who, "gantt")
    screen = monitor(sx + 36, y0 + 4, 32, 22, content)
    r(sx + 38, y0 + 34, sx + 64, y0 + 38, (80, 82, 92), INK)               # keyboard
    for k in range(sx + 40, sx + 63, 3):
        d.point((k, y0 + 35), fill=(150, 152, 162))
    r(sx + 67, y0 + 34, sx + 70, y0 + 38, (200, 200, 206), INK)             # mouse
    r(sx + 8, y0 + 22, sx + 14, y0 + 29, acc, INK)                         # mug
    vline(sx + 15, y0 + 24, y0 + 27, INK)
    hline(sx + 9, sx + 13, y0 + 23, light(acc, 0.5))
    r(sx + 18, y0 + 38, sx + 30, y0 + 42, WHITE, INK)                      # name card
    r(sx + 20, y0 + 39, sx + 23, y0 + 40, acc)
    px0, py0 = sx + 73, y0 + 18                                            # job props
    if who == "bening":                                                    # raw tray -> clean tray
        for k, messy in enumerate((True, False)):
            tx = px0 + k * 11
            r(tx, py0 + 4, tx + 9, py0 + 14, METAL_D, INK)
            for j in range(3):
                off = (rng.randint(-1, 1) if messy else 0)
                r(tx + 1 + off, py0 + 8 - j * 2, tx + 8 + off, py0 + 11 - j * 2, PAPER, (170, 170, 176))
            d.point((tx + 4, py0 + 13), fill=(240, 200, 120) if messy else (120, 220, 140))
        r(sx + 18, y0 + 30, sx + 28, y0 + 33, (42, 120, 214), INK)          # a USB data drive
        prop = [px0, py0, 22, 16]
    elif who == "citra":                                                   # calculator + stats books
        r(px0, py0 + 2, px0 + 9, py0 + 14, (54, 54, 64), INK)
        r(px0 + 1, py0 + 3, px0 + 8, py0 + 5, (170, 220, 170))
        for j in range(3):
            for k in range(3):
                d.point((px0 + 2 + k * 2, py0 + 8 + j * 2), fill=(200, 200, 210))
        for k, c in enumerate(((139, 30, 63), NAVY, (46, 125, 79))):
            r(px0 + 12, py0 + 10 - k * 4, px0 + 21, py0 + 13 - k * 4, c, INK)
        prop = [px0, py0, 22, 16]
    elif who == "kirana":                                                  # drawing tablet + swatches
        r(px0, py0 + 3, px0 + 18, py0 + 15, (40, 44, 56), INK)
        r(px0 + 2, py0 + 5, px0 + 16, py0 + 13, (70, 76, 96))
        d.line([px0 + 18, py0 + 2, px0 + 13, py0 + 9], fill=WHITE)          # stylus
        for k, c in enumerate((PINK, MUSTARD, CYAN, (120, 200, 120))):
            r(sx + 18 + k * 4, y0 + 30, sx + 21 + k * 4, y0 + 34, c, INK)
        prop = [px0 + 2, py0 + 5, 14, 8]
    elif who == "laras":                                                   # planner, phone, pen
        r(px0, py0 + 3, px0 + 12, py0 + 15, NAVY, INK)
        r(px0 + 2, py0 + 4, px0 + 11, py0 + 14, PAPER)
        for j in range(3):
            hline(px0 + 3, px0 + 10, py0 + 6 + j * 3, (180, 180, 190))
        r(px0 + 14, py0 + 5, px0 + 19, py0 + 14, (40, 40, 48), INK)
        r(sx + 20, y0 + 28, sx + 24, y0 + 34, (201, 162, 39), INK)          # gold pen holder
        prop = [px0 + 15, py0 + 6, 4, 4]
    else:                                                                  # Boss: inbox tray, ACC stamp
        r(px0, py0 + 4, px0 + 14, py0 + 14, METAL, INK)
        r(px0 + 2, py0 + 5, px0 + 12, py0 + 10, PAPER)
        r(px0 + 16, py0 + 9, px0 + 21, py0 + 13, RED, INK)
        r(px0 + 17, py0 + 3, px0 + 20, py0 + 9, (60, 46, 44), INK)
        plant(x0 + 14, y0 + 40, 0.5)
        r(x0 + 24, y0 + 30, x0 + 30, y0 + 40, (60, 64, 80), INK)           # desk lamp
        d.polygon([(x0 + 22, y0 + 22), (x0 + 32, y0 + 22), (x0 + 29, y0 + 16), (x0 + 25, y0 + 16)],
                  fill=(250, 236, 196), outline=INK)
        prop = [px0, py0 + 4, 14, 10]
    cx = sx + 16
    chair_base(cx, X(dy + 2) - 10)
    # backrest top-left y, and the feet line of the seat (its depth in the browser)
    CHAIRS.append({"x": cx, "y": X(dy + 2) - 12, "feet": X(dy + 2) + 31})
    FX[who] = {"screen": screen, "prop": prop}
    block(dx, dy, w, 2)


bench(10, 3, 9, LAMINATE, LAMINATE_FRONT, seams=(13, 16))         # the team bench
bench(13, 7, 3, LAMINATE, LAMINATE_FRONT)                           # Laras ...
bench(16, 7, 4, WOOD_TOP, WOOD_FRONT)                               # ... joined to the Boss
for who, (dx, dy, w, off) in DESKS.items():
    workstation(dx, dy, w, off, who)

# copier + binder shelf at the left end of the lower row
box3d(X(10) + 4, X(7) + 10, X(11) + 26, X(9) - 3, 22, (226, 226, 230), (196, 196, 202))
r(X(10) + 8, X(7) + 12, X(11) + 22, X(7) + 18, (70, 72, 84), INK)        # scanner lid
r(X(10) + 12, X(8) + 4, X(11) + 10, X(8) + 7, PAPER, INK)                # output tray
d.point((X(11) + 18, X(8) + 2), fill=(120, 230, 140))
box3d(X(12) + 2, X(7) - 4, X(13) - 3, X(9) - 3, 4, (140, 100, 70), (112, 78, 52), hi=False)
for shelf in range(3):
    yy = X(7) + shelf * 14
    x = X(12) + 4
    while x < X(13) - 5:
        bw = rng.randint(3, 5)
        c = rng.choice([RED, NAVY, MUSTARD, TEAL, (46, 125, 79), (230, 230, 226)])
        r(x, yy + 2, x + bw - 1, yy + 11, c)
        vline(x + bw - 1, yy + 2, yy + 11, dark(c, 0.7))
        x += bw
    hline(X(12) + 3, X(13) - 4, yy + 12, (90, 62, 42))
block(10, 7, 3, 2)

# the Boss's post box, just below the right end of the Boss desk (work-area side)
shadow((X(20) + 8, X(10) + 26, X(20) + 28, X(10) + 31), ellipse=True)
r(X(20) + 13, X(10) + 18, X(20) + 19, X(10) + 28, (60, 60, 66), INK)
r(X(20) + 6, X(10) - 6, X(20) + 26, X(10) + 19, RED, INK)
d.pieslice([X(20) + 6, X(10) - 13, X(20) + 26, X(10) + 1], 180, 360, fill=RED, outline=INK)
vline(X(20) + 8, X(10) - 5, X(10) + 17, light(RED, 0.35))
r(X(20) + 10, X(10) - 1, X(20) + 22, X(10) + 1, INK)
r(X(20) + 11, X(10) + 6, X(20) + 21, X(10) + 11, WHITE, INK)
block(20, 10)

# =========================================================================== pantry
# The pantry opens to the WORK AREA through a sliding door in its west wall (seen from
# above: the panel stands open along the wall), not to the office front. Its south wall
# is the office front's tall feature wall, drawn below with the reception.
wx = X(21)
PANTRY_DOOR = 5                                          # row of the door in the west wall
for ty in range(WALL_ROWS, 8):
    y0 = X(ty)
    if ty == PANTRY_DOOR:
        r(wx + 13, y0 + 5, wx + 18, y0 + T - 6, METAL_D)   # floor track / threshold
        vline(wx + 15, y0 + 5, y0 + T - 6, METAL_HI)
        for py in (y0, y0 + T - 5):                      # door posts
            r(wx + 9, py, wx + 22, py + 4, WOOD_FRONT, INK)
        continue
    r(wx + 10, y0, wx + 21, y0 + T - 1, (214, 210, 204))  # wall seen from above
    r(wx + 10, y0, wx + 12, y0 + T - 1, (232, 228, 222))
    vline(wx + 9, y0, y0 + T - 1, INK)
    vline(wx + 22, y0, y0 + T - 1, INK)
    block(21, ty)
shadow((wx + 23, WB, wx + 28, X(8)), alpha=36)
# the sliding panel, pushed open along the wall on the pantry side (wood, frosted pane)
shadow((wx + 25, X(PANTRY_DOOR) - 26, wx + 30, X(PANTRY_DOOR) + 4), alpha=40)
r(wx + 22, X(PANTRY_DOOR) - 28, wx + 27, X(PANTRY_DOOR) + 2, (170, 126, 86), INK)
r(wx + 24, X(PANTRY_DOOR) - 24, wx + 25, X(PANTRY_DOOR) - 2, (226, 236, 240))
d.point((wx + 23, X(PANTRY_DOOR) - 12), fill=(60, 46, 44))                   # handle

# counter run against the back wall
box3d(X(22) + 1, X(3) - 2, X(26) - 2, X(4) - 4, 12, (210, 212, 216), (234, 234, 230))
for k in range(4):
    x0 = X(22) + 3 + k * 31
    d.rectangle([x0, X(4) - 15, x0 + 27, X(4) - 6], outline=(190, 190, 192))
    r(x0 + 12, X(4) - 12, x0 + 15, X(4) - 11, METAL_D)
# water dispenser
r(X(22) + 7, X(3) - 22, X(22) + 24, X(3) + 12, (226, 232, 240), INK)
d.ellipse([X(22) + 9, X(3) - 34, X(22) + 22, X(3) - 20], fill=(150, 206, 244), outline=INK)
r(X(22) + 11, X(3) - 31, X(22) + 13, X(3) - 24, (210, 238, 255))
r(X(22) + 10, X(3) - 2, X(22) + 21, X(3) + 2, (60, 60, 70))
d.point((X(22) + 12, X(3) - 3), fill=RED)
d.point((X(22) + 19, X(3) - 3), fill=(42, 120, 214))
# coffee machine
r(X(23) + 5, X(3) - 20, X(23) + 26, X(3) + 10, (52, 52, 60), INK)
r(X(23) + 7, X(3) - 18, X(23) + 24, X(3) - 10, (80, 80, 92))
r(X(23) + 12, X(3) - 6, X(23) + 19, X(3) - 3, (30, 30, 34))
r(X(23) + 12, X(3) + 2, X(23) + 18, X(3) + 8, WHITE, INK)
d.point((X(23) + 9, X(3) - 14), fill=(120, 230, 140))
# microwave
r(X(24) + 2, X(3) - 14, X(24) + 30, X(3) + 8, (236, 236, 236), INK)
r(X(24) + 4, X(3) - 12, X(24) + 22, X(3) + 6, (40, 42, 50), INK)
hline(X(24) + 5, X(24) + 12, X(3) - 10, (90, 96, 110))
r(X(24) + 24, X(3) - 11, X(24) + 28, X(3) + 2, (200, 200, 206))
d.point((X(24) + 26, X(3) - 9), fill=TEAL)
# sink
r(X(25) + 4, X(3) + 1, X(25) + 26, X(3) + 10, METAL, INK)
r(X(25) + 6, X(3) + 3, X(25) + 24, X(3) + 8, METAL_D)
r(X(25) + 14, X(3) - 8, X(25) + 16, X(3) + 2, METAL_HI, INK)
# vending machine
shadow((X(26) + 4, X(4) - 4, X(28) + 2, X(4) + 3))
r(X(26) + 2, X(2) - 4, X(28) - 3, X(4) - 2, RED, INK)
r(X(26) + 4, X(2) - 2, X(26) + 6, X(4) - 4, light(RED, 0.3))
r(X(26) + 8, X(2) + 0, X(27) + 16, X(3) + 12, (220, 236, 248), INK)
for row_ in range(4):
    for k in range(5):
        c = [MUSTARD, TEAL, PINK, (110, 170, 230), (240, 240, 240)][(k + row_) % 5]
        x0 = X(26) + 11 + k * 8
        y0 = X(2) + 3 + row_ * 10
        r(x0, y0, x0 + 4, y0 + 7, c, INK)
        hline(x0 - 1, x0 + 6, y0 + 8, (150, 160, 176))
r(X(27) + 19, X(2) + 2, X(27) + 26, X(3) - 2, (60, 60, 70), INK)
r(X(27) + 21, X(2) + 5, X(27) + 24, X(2) + 7, (250, 230, 120))
r(X(26) + 8, X(3) + 16, X(27) + 16, X(3) + 24, (40, 40, 48), INK)
block(22, 3, 6, 1)
# table + stools
box3d(X(23) + 2, X(6) + 2, X(25) - 3, X(7) - 4, 7, (226, 196, 150), (184, 150, 108))
d.ellipse([X(23) + 10, X(6) + 7, X(23) + 18, X(6) + 13], fill=WHITE, outline=INK)
r(X(24) + 6, X(6) + 6, X(24) + 12, X(6) + 13, (120, 200, 160), INK)
block(23, 6, 2, 1)
for sx in (X(22) + 18, X(25) + 14):
    shadow((sx - 7, X(6) + 22, sx + 9, X(6) + 27), ellipse=True)
    vline(sx, X(6) + 18, X(6) + 25, METAL_D)
    d.ellipse([sx - 7, X(6) + 12, sx + 7, X(6) + 20], fill=(70, 58, 52), outline=INK)
    hline(sx - 4, sx + 3, X(6) + 14, (110, 92, 82))
plant(X(27) + 16, X(8) - 4, 0.95)
block(27, 7)

# =========================================================================== lounge
tx0, ty0, tx1, ty1 = 0, X(10) + 2, X(5) - 2, X(13) - 6
shadow((tx0, ty1, tx1 + 3, ty1 + 4))
r(tx0, ty0, tx1, ty1, (112, 82, 58), INK)
for (mx0, my0, mx1, my1) in ((2, ty0 + 3, X(2.5) - 1, X(11.5) - 1), (X(2.5) + 1, ty0 + 3, tx1 - 3, X(11.5) - 1),
                             (2, X(11.5) + 1, tx1 - 3, ty1 - 5)):
    r(mx0, my0, mx1, my1, TATAMI, TATAMI_EDGE)
    for yy in range(my0 + 3, my1 - 1, 3):
        hline(mx0 + 2, mx1 - 2, yy, TATAMI_D)
r(tx0, ty1 - 4, tx1, ty1, (90, 64, 46))
hline(tx0, tx1, ty1 - 4, INK)
# kotatsu: a low table with a futon quilt draped over its frame, a wooden top, mikan and tea
kx0, ky0, kx1, ky1 = X(2) - 14, X(11) + 2, X(3) + 14, X(12) + 4
shadow((kx0 + 2, ky1 - 4, kx1 + 4, ky1 + 3), alpha=60)
QUILT, QUILT_D = (176, 60, 62), (138, 40, 46)
d.rounded_rectangle([kx0, ky0, kx1, ky1], 6, fill=QUILT, outline=INK)          # the futon
r(kx0 + 2, ky1 - 8, kx1 - 2, ky1 - 2, QUILT_D)                                  # its front fall
for x in range(kx0 + 6, kx1 - 4, 8):                                            # asanoha dots
    for y in range(ky0 + 5, ky1 - 9, 8):
        d.point((x, y), fill=(232, 196, 160))
        d.point((x + 1, y + 1), fill=(232, 196, 160))
for x in range(kx0 + 4, kx1 - 3, 6):                                            # soft folds
    vline(x, ky1 - 7, ky1 - 3, (118, 32, 38))
tx_0, ty_0, tx_1, ty_1 = kx0 + 7, ky0 - 3, kx1 - 7, ky1 - 12                       # wooden top
r(tx_0, ty_1, tx_1, ty_1 + 3, (110, 74, 48), INK)                               # its edge
r(tx_0, ty_0, tx_1, ty_1, (196, 150, 104), INK)
hline(tx_0 + 1, tx_1 - 1, ty_0 + 1, (220, 180, 132))
d.ellipse([X(2) + 6, ty_0 + 3, X(2) + 22, ty_0 + 12], fill=(236, 228, 214), outline=INK)   # mikan bowl
for ox, oy in ((9, 4), (14, 3), (12, 6), (17, 5)):
    d.ellipse([X(2) + ox, ty_0 + oy, X(2) + ox + 4, ty_0 + oy + 4], fill=(246, 150, 40), outline=(180, 90, 20))
for cxk in (tx_0 + 4, tx_1 - 9):                                                # tea cups
    d.ellipse([cxk, ty_0 + 4, cxk + 5, ty_0 + 9], fill=(160, 206, 150), outline=INK)
block(2, 11)
# bookshelf, floor lamp, bonsai
# a low two-shelf bookcase, standing clear of the meeting room's glass wall behind it
box3d(X(6) + 2, X(10) + 1, X(8) - 4, X(11) - 2, 3, (120, 84, 56), (100, 70, 46), hi=False)
for shelf in range(2):
    yy = X(10) + 3 + shelf * 13
    hline(X(6) + 4, X(8) - 6, yy + 12, (84, 58, 40))
    x = X(6) + 5
    while x < X(8) - 8:
        bw = rng.randint(3, 5)
        c = rng.choice([RED, NAVY, MUSTARD, TEAL, (230, 230, 226), (120, 90, 150), (46, 125, 79)])
        bh = rng.randint(8, 11)
        r(x, yy + 12 - bh, x + bw - 1, yy + 11, c)
        vline(x + bw - 1, yy + 12 - bh, yy + 11, dark(c, 0.7))
        x += bw
block(6, 10, 2, 1)
shadow((X(6) + 8, X(13) + 24, X(6) + 26, X(13) + 30), ellipse=True)
vline(X(6) + 16, X(12) + 18, X(13) + 26, INK)
d.ellipse([X(6) + 10, X(13) + 24, X(6) + 22, X(13) + 28], fill=METAL_D, outline=INK)
d.polygon([(X(6) + 9, X(12) + 18), (X(6) + 23, X(12) + 18), (X(6) + 20, X(12) + 8), (X(6) + 12, X(12) + 8)],
          fill=(250, 236, 196), outline=INK)
block(6, 13)
box3d(X(5) + 6, X(13) + 14, X(5) + 26, X(13) + 26, 5, (90, 70, 56), (70, 54, 42))   # bonsai stand
plant(X(5) + 16, X(13) + 16, 0.6, pot=(70, 90, 120))
block(5, 13)

# =========================================================================== work area extras
plant(X(9) + 16, X(14) - 2, 1.0, tall=True)                 # tall ficus by the glass
block(9, 13)
plant(X(20) + 16, X(14) - 2, 1.0, tall=True)
block(20, 13)
# a round standing table on the runner rug, in 3/4 view: rim, pedestal, base, shadow
cx, ty_ = X(16), X(11) + 2
shadow((cx - 20, X(11) + 24, cx + 22, X(11) + 31), ellipse=True, alpha=60)
d.ellipse([cx - 11, X(11) + 24, cx + 11, X(11) + 30], fill=METAL_D, outline=INK)    # base
r(cx - 2, ty_ + 8, cx + 2, X(11) + 26, METAL, INK)                                  # pedestal
d.ellipse([cx - 24, ty_ - 6 + 4, cx + 24, ty_ + 12 + 4], fill=(150, 112, 78), outline=INK)  # rim
d.ellipse([cx - 24, ty_ - 6, cx + 24, ty_ + 12], fill=(222, 192, 150), outline=INK)  # top
d.arc([cx - 21, ty_ - 4, cx + 21, ty_ + 9], 200, 330, fill=(240, 218, 182))         # highlight
r(cx - 10, ty_ - 1, cx + 1, ty_ + 6, PAPER, (170, 170, 176))                         # notes
d.ellipse([cx + 6, ty_, cx + 12, ty_ + 5], fill=WHITE, outline=INK)                  # cup
block(15, 11, 2, 1)

# =========================================================================== office front
# A proper reception: a two-tile feature wall (walnut slats, a charcoal stone panel with
# the backlit logo, warm downlights), a marble-topped walnut counter, armchairs and a
# sofa, plants, and nothing that sends a guest into the pantry.
from PIL import ImageFont

fy0, fy1 = X(8) - 6, X(10) - 1                           # feature wall: top cap .. skirting
r(X(21), fy0, W - 1, fy0 + 5, (226, 222, 216), INK)       # wall top seen from above
r(X(21), fy0 + 6, W - 1, fy1 - 4, SLAT_D)
for x in range(X(21) + 1, W - 1, 5):                     # walnut slats
    r(x, fy0 + 7, x + 2, fy1 - 5, SLAT)
    vline(x, fy0 + 7, fy1 - 5, SLAT_HI)
sx0, sx1 = X(22) + 16, X(27) - 8                         # charcoal stone panel
r(sx0, fy0 + 9, sx1, fy1 - 8, (50, 50, 58), INK)
for k in range(6):
    hline(sx0 + 1, sx1 - 1, fy0 + 14 + k * 8, (58, 58, 66))
for gx in (sx0 - 2, sx1 + 1):                            # gold edges
    vline(gx, fy0 + 9, fy1 - 8, (214, 180, 100))
    vline(gx + 1, fy0 + 9, fy1 - 8, (176, 140, 70))
logo = Image.new("RGBA", (40, 12), (0, 0, 0, 0))          # crisp pixel logo, scaled 3x
ld_ = ImageDraw.Draw(logo)
bitmap = ImageFont.load_default_imagefont()
ld_.text((1, 0), "AS", font=bitmap, fill=PINK + (255,))
ld_.text((15, 0), "lab", font=bitmap, fill=CYAN + (255,))
logo = logo.crop(logo.getbbox()).resize((logo.getbbox()[2] * 3 - logo.getbbox()[0] * 3,
                                         (logo.getbbox()[3] - logo.getbbox()[1]) * 3), Image.NEAREST)
lx, ly = (sx0 + sx1) // 2 - logo.width // 2, fy0 + 20
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(glow).rounded_rectangle([lx - 8, ly - 6, lx + logo.width + 8, ly + logo.height + 6], 6,
                                        fill=(255, 140, 210, 46))
img.alpha_composite(glow)
img.alpha_composite(logo, (lx, ly))
d.text((sx0 + 26, ly + logo.height + 6), "AutoScorecard Lab", fill=(200, 196, 186))
r(X(21), fy1 - 3, W - 1, fy1, TRIM_D)                    # skirting
hline(X(21), W - 1, fy1, INK)
for cxl in (X(22) + 8, (sx0 + sx1) // 2, X(27) + 4):     # warm downlights washing the wall
    cone = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(cone).polygon([(cxl - 3, fy0 + 7), (cxl + 3, fy0 + 7), (cxl + 16, fy1 - 4),
                                  (cxl - 16, fy1 - 4)], fill=(255, 226, 160, 30))
    img.alpha_composite(cone)
    r(cxl - 3, fy0 + 6, cxl + 3, fy0 + 8, (255, 236, 190), INK)
block(21, 8, 7, 2)

# reception counter: walnut front with a gold band, white marble top
cx0, cx1 = X(23) + 2, X(27) - 3
shadow((cx0 + 3, X(11) - 4, cx1 + 5, X(11) + 3), alpha=70)
r(cx0, X(10) + 2, cx1, X(10) + 12, (246, 244, 240), INK)            # marble top
for k in range(5):
    d.line([cx0 + 8 + k * 24, X(10) + 3, cx0 + 14 + k * 24, X(10) + 11], fill=(214, 210, 204))
hline(cx0 + 1, cx1 - 1, X(10) + 3, WHITE)
r(cx0, X(10) + 13, cx1, X(11) - 3, (96, 64, 44), INK)                # walnut front
for x in range(cx0 + 4, cx1 - 2, 6):
    vline(x, X(10) + 15, X(11) - 5, (110, 76, 52))
hline(cx0, cx1, X(10) + 16, (214, 180, 100))                         # gold band
hline(cx0, cx1, X(10) + 17, (176, 140, 70))
r(X(25) - 12, X(10) + 20, X(25) + 12, X(10) + 27, (40, 30, 24), INK)  # name plate
d.text((X(25) - 10, X(10) + 18), "ASlab", fill=(214, 180, 100))
# on the counter: the back of a slim monitor, a gold bell, an orchid
r(cx0 + 12, X(10) - 8, cx0 + 34, X(10) + 6, (54, 56, 66), INK)
r(cx0 + 21, X(10) + 6, cx0 + 25, X(10) + 9, METAL_D)
d.pieslice([cx1 - 30, X(10) + 1, cx1 - 20, X(10) + 11], 180, 360, fill=(222, 186, 96), outline=INK)
hline(cx1 - 31, cx1 - 19, X(10) + 7, (160, 126, 60))
r(cx1 - 12, X(10) + 2, cx1 - 5, X(10) + 9, WHITE, INK)               # orchid pot
vline(cx1 - 9, X(10) - 14, X(10) + 2, (70, 120, 70))
for fx_, fy_ in ((-12, -14), (-6, -16), (-2, -11), (-8, -9)):
    d.ellipse([cx1 + fx_ - 3, X(10) + fy_ - 3, cx1 + fx_ + 3, X(10) + fy_ + 3], fill=(250, 236, 246), outline=(200, 120, 170))
block(23, 10, 4, 1)


def armchair(x0, y0, w=28, color=(150, 110, 80)):
    """A chair facing the viewer in 3/4 view: backrest behind, seat, arms, legs."""
    shadow((x0 + 2, y0 + 24, x0 + w + 4, y0 + 30), ellipse=True)
    r(x0 + 2, y0 - 6, x0 + w - 2, y0 + 10, dark(color, 0.85), INK)          # backrest
    hline(x0 + 3, x0 + w - 3, y0 - 5, light(color, 0.25))
    r(x0 + 4, y0 + 8, x0 + w - 4, y0 + 20, color, INK)                      # seat
    hline(x0 + 5, x0 + w - 5, y0 + 9, light(color, 0.35))
    for ax in (x0, x0 + w - 5):                                             # arms
        r(ax, y0 + 2, ax + 5, y0 + 20, dark(color, 0.75), INK)
    for lx_ in (x0 + 2, x0 + w - 4):                                        # legs
        r(lx_, y0 + 21, lx_ + 1, y0 + 25, (60, 44, 34))


# the partition between the office front and the office: frosted glass on an aluminium
# frame, and a security gate (glass doors + card reader) as the only way through
GATE = (11, 12)                                   # an automatic sliding door, two tiles wide
for ty in range(10, 14):
    x0, y0 = X(21), X(ty)
    if ty in GATE:                                # the opening: track on the floor only;
        r(x0 + 13, y0, x0 + 18, y0 + T - 1, (214, 214, 220))      # the leaves are drawn live
        vline(x0 + 15, y0, y0 + T - 1, METAL_D)
        continue
    r(x0 + 12, y0, x0 + 19, y0 + T - 1, GLASS)
    vline(x0 + 13, y0, y0 + T - 1, GLASS_HI)
    vline(x0 + 12, y0, y0 + T - 1, FRAME)
    vline(x0 + 19, y0, y0 + T - 1, FRAME)
    r(x0 + 12, y0 + 10, x0 + 19, y0 + 18, (226, 236, 240))               # frosted band
    block(21, ty)
for py in (X(GATE[0]) - 3, X(GATE[-1] + 1) - 1):                        # door frame ends
    r(X(21) + 10, py, X(21) + 21, py + 3, FRAME, INK)
r(X(21) + 23, X(GATE[0]) + 2, X(21) + 27, X(GATE[0]) + 11, (60, 64, 76), INK)   # card reader
d.point((X(21) + 25, X(GATE[0]) + 4), fill=(120, 230, 140))
r(X(21) + 24, X(GATE[0]) + 7, X(21) + 26, X(GATE[0]) + 8, (200, 204, 214))
r(X(21) + 22, X(10) + 8, X(21) + 30, X(10) + 14, (46, 125, 79), INK)     # "STAFF" plate
d.point((X(21) + 24, X(10) + 11), fill=WHITE)
d.point((X(21) + 26, X(10) + 11), fill=WHITE)
d.point((X(21) + 28, X(10) + 11), fill=WHITE)
r(X(21) + 11, X(10) - 2, X(21) + 20, X(10) + 1, FRAME, INK)              # meets the feature wall
# the waiting corner: a coffee table, and behind it a sofa against the front wall that
# faces the reception and the logo wall (seen from behind: the backrest is nearest to us)
box3d(X(25) + 6, X(12) + 4, X(27) + 26, X(12) + 18, 5, (36, 36, 42), (24, 24, 30))   # coffee table
r(X(26) + 4, X(12) + 6, X(26) + 16, X(12) + 10, (214, 180, 100))       # a design book
d.ellipse([X(27) + 4, X(12) + 5, X(27) + 12, X(12) + 10], fill=WHITE, outline=INK)   # a cup
block(25, 12, 3, 1)
shadow((X(25) + 4, X(13) + 26, X(28), X(13) + 31), alpha=60)
r(X(25) + 6, X(13) + 2, X(28) - 5, X(13) + 14, (70, 90, 130), INK)      # seat (facing up)
for k in range(3):
    r(X(25) + 8 + k * 25, X(13) + 4, X(25) + 28 + k * 25, X(13) + 10, (84, 106, 148))
r(X(25) + 4, X(13) + 12, X(28) - 3, X(13) + 26, (54, 70, 104), INK)     # backrest, nearest to us
hline(X(25) + 5, X(28) - 4, X(13) + 13, (74, 92, 128))
for ax in (X(25) + 2, X(28) - 8):
    r(ax, X(13) + 2, ax + 6, X(13) + 24, (46, 60, 90), INK)
block(25, 13, 3, 1)
plant(X(27) + 16, X(11) + 4, 1.0, pot=(46, 46, 52), tall=True)          # fiddle-leaf trees
block(27, 10, 1, 2)
# umbrella stand by the door (a rainy-season essential)
r(X(22) + 10, X(14) - 14, X(22) + 22, X(14) + 2, (60, 60, 66), INK)
for k, c in enumerate((RED, NAVY, (46, 125, 79))):
    vline(X(22) + 12 + k * 4, X(14) - 26, X(14) - 14, c)
    d.point((X(22) + 12 + k * 4, X(14) - 27), fill=INK)

# =========================================================================== front wall + entrance
for tx in range(COLS):
    x0, y0 = X(tx), X(14)
    if tx in (23, 24):
        continue
    r(x0, y0 + 8, x0 + T - 1, y0 + 13, (216, 210, 200))
    r(x0, y0 + 14, x0 + T - 1, y0 + T - 1, (150, 140, 132))
    hline(x0, x0 + T - 1, y0 + 7, INK)
    hline(x0, x0 + T - 1, y0 + 14, INK)
    for k in range(4, T, 8):
        vline(x0 + k, y0 + 16, y0 + T - 2, (136, 126, 118))
    block(tx, 14)
r(X(23), X(14) + 2, X(25) - 1, X(15) - 1, (40, 40, 48), INK)        # doormat
r(X(23) + 3, X(14) + 5, X(25) - 4, X(15) - 4, (56, 56, 66))
d.text((X(23) + 10, X(14) + 9), "WELCOME", fill=(214, 180, 100))
for x in (X(23) - 4, X(25) + 1):                                    # glass doors, gold frames
    r(x, X(14) - 2, x + 3, X(15) - 1, (214, 180, 100), INK)
    r(x + (4 if x < X(24) else -10), X(14) - 2, x + (10 if x < X(24) else -4), X(14) + 1, GLASS, FRAME)

# =========================================================================== chair backrest
# drawn by the browser IN FRONT of whoever sits, so a seated person shows from the waist up
CB_W, CB_H = 26, 22
cb = Image.new("RGBA", (CB_W, CB_H), (0, 0, 0, 0))
cd = ImageDraw.Draw(cb)
cd.rounded_rectangle([1, 0, CB_W - 2, CB_H - 7], 4, fill=(58, 62, 80), outline=INK)
cd.rectangle([4, 2, CB_W - 5, 4], fill=(86, 92, 114))
cd.rectangle([0, 9, 2, 14], fill=(40, 42, 54))
cd.rectangle([CB_W - 3, 9, CB_W - 1, 14], fill=(40, 42, 54))
cd.rectangle([CB_W // 2 - 1, CB_H - 7, CB_W // 2 + 1, CB_H - 1], fill=METAL_D)

# pieces drawn IN FRONT of people (none at the moment besides the chairs above)
FRONTS = []

# =========================================================================== layout
seat = {who: {"x": dx + off, "y": dy + 2, "face": "up"} for who, (dx, dy, w, off) in DESKS.items()}
layout = {
    "tile": T, "cols": COLS, "rows": ROWS,
    "solid": ["".join("#" if c else "." for c in row) for row in solid],
    # where each person works: the chair below the desk, facing up at the screen
    "desks": {who: s for who, s in seat.items() if who != "boss"},
    "boss_desk": seat["boss"],
    "drawer": {"tile": [*DESKS["boss"][:2], DESKS["boss"][2], 2]},   # click: the conversation log
    "mailbox": {"x": 20, "y": 11, "face": "up", "tile": [20, 10]},
    "whiteboard": {"x": 19, "y": 3, "face": "left", "tile": [11, 0, 8, 3], "dots": DOTS},
    "meeting": {
        "door": {"x": 8, "y": 6},
        "host": {"x": 6, "y": 4, "face": "left"},
        "seats": [{"x": x, "y": 7, "face": "up"} for x in range(2, 6)]
                 + [{"x": x, "y": 4, "face": "down"} for x in range(2, 6)],
        "area": [0, 3, 8, 6],
    },
    "pantry": {
        "dispenser": {"x": 22, "y": 4, "face": "up"},
        "coffee": {"x": 23, "y": 4, "face": "up"},
        "microwave": {"x": 24, "y": 4, "face": "up"},
        "vending": {"x": 26, "y": 4, "face": "up"},
        "table": [{"x": 22, "y": 6, "face": "right"}, {"x": 25, "y": 6, "face": "left"}],
    },
    # around the kotatsu: people sit on the floor with their legs under the futon
    "lounge": [{"x": 1, "y": 11, "face": "right", "floor": True},
               {"x": 3, "y": 11, "face": "left", "floor": True},
               {"x": 2, "y": 12, "face": "up", "floor": True}],
    "huddle": [{"x": 14, "y": 11, "face": "right"}, {"x": 17, "y": 11, "face": "left"}],
    # the office front: the Boss arrives at the door, Laras waits by reception
    "entrance": {"x": 23, "y": 14},
    # the automatic door between the office front and the office: two glass leaves along
    # the wall line (x px .. px+7) that slide apart when someone comes near
    "gate": {"x": 21, "rows": list(GATE), "px": X(21) + 12, "y0": X(GATE[0]), "y1": X(GATE[-1] + 1)},
    "greeter": {"x": 23, "y": 12, "face": "down"},
    "escort": {"x": 18, "y": 9, "face": "left"},           # where Laras stands at the Boss desk
    "chairs": CHAIRS, "chair_size": [CB_W, CB_H],
    "fronts": FRONTS,
    "desk_fx": FX,
}
OUT.mkdir(parents=True, exist_ok=True)
img.convert("RGB").save(OUT / "office.png")
cb.save(OUT / "chair_back.png")
(OUT / "layout.json").write_text(json.dumps(layout, indent=1), encoding="utf-8")
print(f"wrote {OUT / 'office.png'}, chair_back.png and layout.json")
