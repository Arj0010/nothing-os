#!/usr/bin/env python3
"""Generate a Nothing-OS wallpaper tinted to the current palette.

    genwall.py "#E5484D" [outfile]

Deep-black radial vignette, a faint accent core glow, a fine dot-matrix grid and
three concentric guide rings. The smooth gradient is rendered small and upscaled
(pure-Python per-pixel over 1920x1080 is far too slow, and numpy isn't installed);
the crisp elements are drawn afterwards at full resolution.
"""
from PIL import Image, ImageDraw
import math, os, sys

W, H = 1920, 1080
SW, SH = 240, 135                     # gradient render size, then upscaled
CX, CY = W / 2, H / 2


def accent_rgb(hexcol):
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def build(hexcol, out):
    ar, ag, ab = accent_rgb(hexcol)
    small = Image.new("RGB", (SW, SH), (0, 0, 0))
    px = small.load()
    scx, scy = SW / 2, SH / 2
    maxd = math.hypot(scx, scy)
    glow_r = 620 / W * SW               # same glow radius, in small-image units
    for y in range(SH):
        for x in range(SW):
            d = math.hypot(x - scx, y - scy) / maxd      # 0 centre .. 1 corner
            base = int(15 * (1 - d) ** 1.6)              # vignette lift
            g = max(0.0, 1 - (math.hypot(x - scx, y - scy) / glow_r)) ** 2.2
            px[x, y] = (min(base + int(ar * 0.12 * g), 255),
                        min(base + int(ag * 0.12 * g), 255),
                        min(base + int(ab * 0.12 * g), 255))
    img = small.resize((W, H), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")

    maxdf = math.hypot(CX, CY)
    step = 40                                            # fine dot-matrix grid
    for gy in range(step // 2, H, step):
        for gx in range(step // 2, W, step):
            d = math.hypot(gx - CX, gy - CY) / maxdf
            a = int(26 * (1 - d) ** 1.3)
            if a > 2:
                draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=(210, 210, 220, a))

    for rad in (300, 520, 760):                          # faint guide rings
        draw.ellipse([CX - rad, CY - rad, CX + rad, CY + rad],
                     outline=(ar, ag, ab, 14), width=1)

    img.save(out)
    return out


if __name__ == "__main__":
    col = sys.argv[1] if len(sys.argv) > 1 else "#D71921"
    dest = (sys.argv[2] if len(sys.argv) > 2
            else os.path.expanduser("~/.config/conky/nothing/wallpaper.png"))
    print("wrote", build(col, dest))
