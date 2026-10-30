"""Re-compose gen2b.png: Thetis larger, Achilles smaller, deeper shadows.

Output art_v4.png (2x of gen2b, 2048x1152, grayscale, no frame lines; the
banner script draws its own frame).
"""
import numpy as np
from PIL import Image, ImageFilter

Z = 2                                   # work at 2x of the 1024x576 source
src = Image.open("gen2b.png").convert("L").resize((1024 * Z, 576 * Z), Image.BICUBIC)
a = np.asarray(src, np.float32) / 255
yy, xx = np.mgrid[0:576 * Z, 0:1024 * Z].astype(np.float32) / Z   # source coords

# 1. remove the double frame lines (they would break when the layers scale)
for y0, y1 in [(60, 70), (533, 543)]:
    a[y0 * Z:y1 * Z, 375 * Z:1001 * Z] = 0
a[55 * Z:545 * Z, 370 * Z:393 * Z] = 0
a[55 * Z:360 * Z, 983 * Z:1003 * Z] = 0


def density(layer):
    """Opacity of a figure: dense hatching = solid, thin rays = see-through."""
    m = np.asarray(Image.fromarray((layer * 255).astype(np.uint8))
                   .filter(ImageFilter.GaussianBlur(6 * Z)), np.float32) / 255
    m = np.clip((m - 0.24) / 0.1, 0, 1)
    return np.asarray(Image.fromarray((m * 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(2 * Z)), np.float32) / 255


def scaled(layer, s, ax, ay):
    """Scale `layer` by s around anchor (ax, ay) in source coords."""
    img = Image.fromarray((layer * 255).astype(np.uint8))
    inv = 1 / s                          # output -> input affine
    img = img.transform(img.size, Image.AFFINE,
                        (inv, 0, ax * Z * (1 - inv), 0, inv, ay * Z * (1 - inv)),
                        resample=Image.BICUBIC)
    return np.asarray(img, np.float32) / 255


# 2. split along the gap between the two figures
hard = (((xx < 690) & (yy < 300)) | (xx < 615)).astype(np.float32)
side = np.asarray(Image.fromarray((hard * 255).astype(np.uint8))
                  .filter(ImageFilter.GaussianBlur(14 * Z)), np.float32) / 255
side = np.where(yy < 300, hard, side)                      # soft seam in the water only
T = scaled(a * side, 1.15, 470, 480)                       # goddess: bigger
T = np.roll(T, 35 * Z, axis=0)                             # ...and a bit lower
T[:35 * Z] = 0
A = scaled(a * (1 - side), 0.84, 640, 562)                 # hero: smaller, anchored at the seam
mT, mA = density(T), density(A)
out = np.maximum(T, A)                                     # rays: lighten
out = out * (1 - mT) + T * mT
out = out * (1 - mA) + A * mA                              # Achilles in front

# 3. more contrast and form shadows; light comes from the goddess
blur = np.asarray(Image.fromarray((out * 255).astype(np.uint8))
                  .filter(ImageFilter.GaussianBlur(18 * Z)), np.float32) / 255
out = out + 0.55 * (out - blur)                            # local contrast
out = np.clip((out - 0.5) * 1.22 + 0.5, 0, 1)              # global S-ish
tx, ty = 530, 118                                          # Thetis head (new)
light = np.exp(-((xx - tx) ** 2 + ((yy - ty) * 1.2) ** 2) / (2 * 330.0 ** 2))
out *= 0.5 + 0.6 * light                                   # far side falls dark
out *= 1 - 0.3 * np.clip((yy - 380) / 196, 0, 1)           # ground shadow
out[544 * Z:] = 0                                          # bottom frame rest
Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).save("art_v4.png")
print("ok")
