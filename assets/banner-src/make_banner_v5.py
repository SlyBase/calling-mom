"""Banner for the Hermes plugin "calling mom", official-plugin banner style.

Reference: hermes-official-plugins/assets/*.jpg (1200x600): distressed black
paper with folds and worn edges, ivory engraving, red disc behind the head,
blue/yellow pixel glitch bars, thin blue HUD lines, heavy yellow uppercase
headline, grain and soft focus. Artwork art_v4.png (compose_v4.py from gen2b.png): own AI-generated engraving
(Thetis answers Achilles, Iliad I); Achilles drawn like a Greek statue.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 2
W, H = 1200 * S, 600 * S
rng = np.random.default_rng(11)
YELLOW = (246, 199, 32)
BLUE = (38, 72, 255)
RED = (205, 52, 34)
IVORY = np.array([232, 221, 196], np.float32)
INK = np.array([13, 12, 11], np.float32)
F = "fonts/"


def noise(scale, shape=(H, W)):
    """Smooth noise in 0..1 with feature size `scale` px."""
    small = rng.random((max(2, shape[0] // scale), max(2, shape[1] // scale)))
    img = Image.fromarray((small * 255).astype(np.uint8)).resize(shape[::-1], Image.BICUBIC)
    return np.asarray(img, np.float32) / 255


def font(name, px):
    return ImageFont.truetype(F + name, int(px * S))


# --- 1. Distressed paper ground -------------------------------------------
bg = np.empty((H, W, 3), np.float32)
bg[:] = INK
bg += ((noise(120) - 0.5) * 14 + (noise(18) - 0.5) * 8)[..., None]
ground = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
d = ImageDraw.Draw(ground)
for x in range(0, W, 46 * S):                                   # faint blue grid
    d.line([(x, 0), (x, H)], fill=(20, 26, 60), width=1)
for y in range(0, H, 46 * S):
    d.line([(0, y), (W, y)], fill=(20, 26, 60), width=1)
for x in (W // 3, 2 * W // 3):                                  # paper folds
    d.line([(x, 0), (x, H)], fill=(44, 42, 39), width=S)
    d.line([(x + S, 0), (x + S, H)], fill=(6, 6, 6), width=S)
for y in (H // 2 - 40 * S, 70 * S, H - 62 * S):
    d.line([(0, y), (W, y)], fill=(40, 38, 35), width=S)
bg = np.asarray(ground, np.float32).copy()

# worn, slightly torn edges
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
edge = np.minimum(np.minimum(xx, W - 1 - xx), np.minimum(yy, H - 1 - yy))
wear = np.clip((34 * S - edge + (noise(25) - 0.5) * 50 * S) / (14 * S), 0, 1)
speck = (noise(3) > 0.55).astype(np.float32) * wear
bg = bg * (1 - 0.55 * speck[..., None]) + np.array([92, 86, 76]) * 0.55 * speck[..., None]
torn = edge < (3 * S + noise(40) * 7 * S)
bg[torn] = 4

# --- 2. Red disc and blue HUD (behind the figures) -------------------------
ART, CROP = "art_v4.png", (720, 0, 2048, 1152)   # 2x source
k = H / 1152
ax = W - int((CROP[2] - CROP[0]) * k)                         # art x offset
P = lambda x, y: (ax + (x - CROP[0]) * k, y * k)              # gen1 -> banner
tx, ty = P(1060, 236)                                         # Thetis head                                          # Thetis head
hx, hy = P(1450, 436)                                         # Achilles head                                          # Achilles head

ground = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
d = ImageDraw.Draw(ground)
r = 114 * S
d.ellipse([tx - r, ty - r, tx + r, ty + r], fill=RED)
d.arc([-330 * S, H / 2 - 330 * S, 330 * S - 200 * S, H / 2 + 330 * S], 0, 360, fill=BLUE, width=S)
d.arc([W - 130 * S, H / 2 - 300 * S, W + 470 * S, H / 2 + 300 * S], 0, 360, fill=BLUE, width=S)
for cx, cy in [(70, 110), (520, 95), (1120, 90), (1135, 520), (610, 545), (95, 520)]:
    cx, cy = cx * S, cy * S
    d.line([(cx - 14 * S, cy), (cx + 14 * S, cy)], fill=BLUE, width=S)
    d.line([(cx, cy - 14 * S), (cx, cy + 14 * S)], fill=BLUE, width=S)
fx0, fy0 = P(760, 120)                                        # own thin double frame
fx1, fy1 = P(1990, 1080)
for g in (0, 5 * S):
    d.rectangle([fx0 + g, fy0 + g, fx1 - g, fy1 - g], outline=(190, 182, 164), width=S)
for i in range(7):                                            # the call: rings from Achilles
    rr = (60 + i * 26) * S
    d.arc([hx - rr, hy - rr, hx + rr, hy + rr], 175, 265, fill=BLUE, width=S)
bg = np.asarray(ground, np.float32).copy()
disc = np.hypot(xx - tx, yy - ty) < r                         # disc texture
bg[disc] *= (0.78 + 0.3 * noise(4)[disc])[:, None]

# --- 3. Engraving: ivory figures, opaque where hatching is dense ----------
art = Image.open(ART).convert("L").crop(CROP)
art = art.resize((W - ax, H), Image.BICUBIC)
lum = np.asarray(art, np.float32) / 255
mask = np.asarray(art.filter(ImageFilter.GaussianBlur(5 * S)), np.float32) / 255
mask = np.clip((mask - 0.24) / 0.1, 0, 1)
mask = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2 * S)), np.float32) / 255
fade = np.clip(np.arange(W - ax, dtype=np.float32) / (110 * S), 0, 1)[None, :] ** 1.4
lum *= fade
fig = INK + (IVORY - INK) * (lum ** 0.85)[..., None]
sepia = (noise(60, lum.shape) - 0.5)[..., None] * np.array([10, 6, 0])
fig += sepia * lum[..., None]
reg = bg[:, ax:]
m = (mask * fade)[..., None]
bg[:, ax:] = np.maximum(reg, fig) * (1 - m) + fig * m

# --- 4. Pixel glitch: shifted slices and blue/yellow block bars -----------
img = np.clip(bg, 0, 255)
for y0, h, dx in [(318, 6, -16), (452, 8, 20)]:
    ys, ye = int(y0 * S), int((y0 + h) * S)
    img[ys:ye, ax:] = np.roll(img[ys:ye, ax:], int(dx * S), axis=1)
blk = 11 * S
# SlyBase product oranges (orange, rust, a little amber), not Hermes blue/yellow
GLITCH = [(237, 128, 36), (237, 128, 36), (194, 97, 30), (246, 184, 83)]
# fewer, lighter bands, kept off the faces
for y0, rows, x0, x1 in [(300, 3, 500, 700), (440, 2, 780, 990)]:
    for j in range(rows):                                     # pixel block bands
        y = int(y0 * S) + j * blk
        for x in range(int(x0 * S), int(x1 * S), blk):
            if rng.random() < 0.42 - 0.08 * j:
                col = GLITCH[rng.integers(0, len(GLITCH))]
                img[y:y + blk, x:x + blk * rng.integers(1, 4)] = np.array(col) * (0.8 + 0.2 * rng.random())
    for _ in range(2):                                        # smeared streaks
        y = int((y0 + rng.random() * rows * 11) * S)
        x = int((x0 + rng.random() * (x1 - x0) * 0.7) * S)
        ln, hh = int(rng.integers(50, 170) * S), int(rng.integers(2, 6) * S)
        col = GLITCH[rng.integers(0, len(GLITCH))]
        img[y:y + hh, x:x + ln] = img[y:y + hh, x:x + ln] * 0.3 + np.array(col) * 0.7

# --- 5. Typography: heavy uppercase, rough and soft -----------------------
txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(txt)
L = 66 * S


def tracked(x, y, s, f, fill, track):
    for ch in s:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


tracked(L + 4 * S, 150 * S, "HERMES PLUGIN", font("ArchivoBlack.ttf", 21), (238, 232, 220), 6 * S)
head = Image.new("RGBA", (W, H), (0, 0, 0, 0))               # headline: own layer
hd = ImageDraw.Draw(head)
big = font("Anton.ttf", 132)
for i, line in enumerate(["CALLING", "MOM"]):
    y = 178 * S + i * 138 * S
    hd.text((L + 5 * S, y + 5 * S), line, font=big, fill=(120, 28, 18))  # misprint shadow
    hd.text((L, y), line, font=big, fill=YELLOW)

# flow: stuck -> second opinion -> stronger model
tag = font("ArchivoBlack.ttf", 15)
y = 488 * S
x = L
boxes = []
for word in ["STUCK", "SECOND-OPINION", "STRONGER MODEL"]:
    tw = d.textlength(word, font=tag)
    boxes.append((x, x + tw + 22 * S))
    x += tw + 22 * S + 46 * S
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
for (a0, a1), (b0, _) in zip(boxes, boxes[1:]):
    g.line([(a1, y + 14 * S), (b0, y + 14 * S)], fill=BLUE + (255,), width=5 * S)
glow = glow.filter(ImageFilter.GaussianBlur(5 * S))
txt = Image.alpha_composite(glow, txt)
d = ImageDraw.Draw(txt)
for (a0, a1), (b0, _) in zip(boxes, boxes[1:]):
    d.line([(a1, y + 14 * S), (b0, y + 14 * S)], fill=(150, 175, 255), width=2 * S)
    for px in (a1 + 6 * S, b0 - 6 * S):
        d.rectangle([px - 4 * S, y + 10 * S, px + 4 * S, y + 18 * S], fill=(235, 240, 255))
for (a0, a1), word in zip(boxes, ["STUCK", "SECOND-OPINION", "STRONGER MODEL"]):
    d.rectangle([a0, y, a1, y + 29 * S], fill=YELLOW)
    d.text((a0 + 11 * S, y + 5 * S), word, font=tag, fill=(14, 13, 12))

# headline: ink wear and slight soft focus (goes through the global blur too)
a = np.asarray(head, np.float32)
holes = (noise(2) > 0.9) | (noise(6) > 0.92)
a[..., 3] *= np.where(holes, 0.6, 1.0)
head = Image.fromarray(a.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.4 * S))
base = Image.fromarray(img.astype(np.uint8)).convert("RGBA")
out = Image.alpha_composite(base, head).convert("RGB")

# --- 6. Imperfection pass at final size ----------------------------------
out = out.resize((W // S, H // S), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.55))
# small type (label, tags) skips the blur: crisp, only grain + misregistration
small = txt.resize((W // S, H // S), Image.LANCZOS)
out = Image.alpha_composite(out.convert("RGBA"), small).convert("RGB")
o = np.asarray(out, np.float32).copy()
h, w = o.shape[:2]
o[..., 0] = np.roll(o[..., 0], 1, axis=1)                    # chromatic misregistration
o[..., 2] = np.roll(o[..., 2], -1, axis=1)
for y0, hh, dx in [(132, 3, 6), (391, 2, -5), (540, 4, 8)]:  # scanline tears
    o[y0:y0 + hh] = np.roll(o[y0:y0 + hh], dx, axis=1)
rows = np.arange(h, dtype=np.float32)[:, None, None]
o *= 1 - 0.035 * (rows % 3 == 0)                             # faint scanlines
o += rng.normal(0, 9, (h, w, 1)) + rng.normal(0, 3, (h, w, 3))  # film grain
vy, vx = np.mgrid[0:h, 0:w].astype(np.float32)
o *= (1 - 0.28 * (np.hypot((vx - w / 2) / w, (vy - h / 2) / h) * 1.6) ** 2)[..., None]
o = np.round(o / 5) * 5                                       # tonal banding
Image.fromarray(np.clip(o, 0, 255).astype(np.uint8)).save("calling-mom-banner-v5.jpg", quality=84)
print("ok")