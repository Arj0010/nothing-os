#!/usr/bin/env python3
"""Generate a clean Nothing-OS static wallpaper: deep-black radial vignette,
a faint red core glow, a very fine dot-matrix grid, and a small Ndot wordmark."""
from PIL import Image, ImageDraw, ImageFont
import math, os

W, H = 1920, 1080
CX, CY = W/2, H/2
FONTS = os.path.expanduser("~/.local/share/fonts")

img = Image.new("RGB", (W, H), (0, 0, 0))
px = img.load()

maxd = math.hypot(CX, CY)
for y in range(H):
    for x in range(W):
        d = math.hypot(x-CX, y-CY) / maxd            # 0 center .. 1 corner
        # base lift near centre, fading to pure black at edges (vignette)
        base = int(15 * (1 - d) ** 1.6)
        # faint red core glow
        rg = max(0.0, 1 - (math.hypot(x-CX, y-CY) / 620))
        r = base + int(30 * rg ** 2.2)
        g = base + int(4 * rg ** 2.2)
        b = base + int(7 * rg ** 2.2)
        px[x, y] = (min(r, 255), min(g, 255), min(b, 255))

draw = ImageDraw.Draw(img, "RGBA")

# fine dot-matrix grid (subtle), fading out toward the edges
step = 40
for gy in range(step//2, H, step):
    for gx in range(step//2, W, step):
        d = math.hypot(gx-CX, gy-CY) / maxd
        a = int(26 * (1 - d) ** 1.3)                 # ~10% at centre -> 0 at edge
        if a > 2:
            draw.ellipse([gx-1, gy-1, gx+1, gy+1], fill=(210, 210, 220, a))

# a few concentric guide rings (very faint, static — the animation rides on top)
for rad in (300, 520, 760):
    draw.ellipse([CX-rad, CY-rad, CX+rad, CY+rad], outline=(215, 25, 33, 14), width=1)

out = os.path.expanduser("~/.config/conky/nothing/wallpaper.png")
img.save(out)
print("wrote", out, img.size)
