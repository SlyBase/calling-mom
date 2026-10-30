"""Product icon for "calling mom" (1024x1024, rounded tile, RGBA).

Same language as banner v6: distressed black paper, ivory engraving of
Thetis reaching out, red disc behind her head, SlyBase-orange HUD lines and
one glitch band, thin ivory frame, grain and soft focus. Art: art_v4.png.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 2
N = 1024 * S
rng = np.random.default_rng(5)
RED = (205, 52, 34)
ORANGE = (237, 128, 36)
GLITCH = [(237, 128, 36), (237, 128, 36), (194, 97, 30), (246, 184, 83)]
IVORY = np.array([232, 221, 196], np.float32)
INK = np.array([13, 12, 11], np.float32)
CROP = (850, 10, 1490, 650)          # art_v4.png coords: Thetis head + hand
HEAD = (1060, 236)


def noise(scale, n=N):
    small = rng.random((max(2, n // scale),) * 2)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC)
    return np.asarray(img, np.float32) / 255


def blur(arr, r):
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255


# --- ground ---------------------------------------------------------------
bg = np.empty((N, N, 3), np.float32)
bg[:] = INK
bg += ((noise(160) - 0.5) * 16 + (noise(20) - 0.5) * 8)[..., None]
g = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
d = ImageDraw.Draw(g)
for v in range(0, N, 64 * S):
    d.line([(v, 0), (v, N)], fill=(34, 23, 14), width=1)
    d.line([(0, v), (N, v)], fill=(34, 23, 14), width=1)

k = N / (CROP[2] - CROP[0])
P = lambda x, y: ((x - CROP[0]) * k, (y - CROP[1]) * k)
hx, hy = P(*HEAD)
r = 250 * S
d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=RED)
for cx, cy in [(120, 880), (905, 120), (900, 900)]:               # crosshairs
    cx, cy = cx * S, cy * S
    d.line([(cx - 22 * S, cy), (cx + 22 * S, cy)], fill=ORANGE, width=2 * S)
    d.line([(cx, cy - 22 * S), (cx, cy + 22 * S)], fill=ORANGE, width=2 * S)
d.arc([N - 260 * S, 200 * S, N + 520 * S, 980 * S], 0, 360, fill=ORANGE, width=2 * S)
bg = np.asarray(g, np.float32).copy()
yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)
disc = np.hypot(xx - hx, yy - hy) < r
bg[disc] *= (0.78 + 0.3 * noise(5)[disc])[:, None]

# --- engraving ------------------------------------------------------------
art = Image.open("art_v4.png").convert("L").crop(CROP).resize((N, N), Image.BICUBIC)
lum = np.asarray(art, np.float32) / 255
mask = np.clip((blur(lum, 6 * S) - 0.24) / 0.1, 0, 1)
mask = blur(mask, 2 * S)[..., None]
fig = INK + (IVORY - INK) * (lum ** 0.85)[..., None]
img = np.maximum(bg, fig) * (1 - mask) + fig * mask

# --- one orange glitch band across the arm, off the face -----------------
blk = 22 * S
for j, (y0, x0, x1) in enumerate([(560, 260, 720), (582, 300, 640)]):
    y = y0 * S
    for x in range(x0 * S, x1 * S, blk):
        if rng.random() < 0.45:
            col = GLITCH[rng.integers(0, len(GLITCH))]
            img[y:y + blk, x:x + blk * rng.integers(1, 3)] = np.array(col) * (0.8 + 0.2 * rng.random())
ys = 610 * S
img[ys:ys + 10 * S] = np.roll(img[ys:ys + 10 * S], 30 * S, axis=1)

# --- frame, finish, rounded tile -----------------------------------------
t = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
d = ImageDraw.Draw(t)
for gap in (0, 10 * S):
    m = 70 * S + gap
    d.rectangle([m, m, N - m, N - m], outline=(190, 182, 164), width=2 * S)
t = t.resize((1024, 1024), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.7))
o = np.asarray(t, np.float32).copy()
o[..., 0] = np.roll(o[..., 0], 1, axis=1)
o[..., 2] = np.roll(o[..., 2], -1, axis=1)
o *= 1 - 0.035 * (np.arange(1024)[:, None, None] % 3 == 0)
o += rng.normal(0, 9, (1024, 1024, 1)) + rng.normal(0, 3, (1024, 1024, 3))
vy, vx = np.mgrid[0:1024, 0:1024].astype(np.float32) / 1024
o *= (1 - 0.3 * (np.hypot(vx - 0.5, vy - 0.5) * 1.5) ** 2)[..., None]
o = np.round(o / 5) * 5

alpha = Image.new("L", (N, N), 0)
ImageDraw.Draw(alpha).rounded_rectangle([0, 0, N - 1, N - 1], radius=228 * S, fill=255)
alpha = alpha.resize((1024, 1024), Image.LANCZOS)
icon = Image.fromarray(np.clip(o, 0, 255).astype(np.uint8)).convert("RGBA")
icon.putalpha(alpha)
icon.save("calling-mom-icon-v2.png", optimize=True)
icon.resize((128, 128), Image.LANCZOS).save("icon-preview-128.png")
print("ok")
