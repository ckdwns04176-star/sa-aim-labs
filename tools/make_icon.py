#!/usr/bin/env python3
"""Render the SA AIM LABS app icon (assets/app.ico + ui/assets/app-256.png).

The mark is the app's own logo from the title bar (amber hexagon + cyan reticle),
not game artwork.  Drawn with Pillow only so no native Cairo is required.
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

here = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(here)

AMBER = (245, 158, 11)
AMBER_L = (253, 230, 138)
CYAN = (56, 189, 248)


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render(size: int = 1024) -> Image.Image:
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded-square background: vertical-ish diagonal gradient
    bg = Image.new("RGBA", (S, S))
    bd = ImageDraw.Draw(bg)
    for y in range(S):
        for_x = y / S
        bd.line([(0, y), (S, y)], fill=_lerp((18, 26, 41), (7, 11, 19), for_x) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [S * 0.03, S * 0.03, S * 0.97, S * 0.97], radius=S * 0.205, fill=255
    )
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # border
    d.rounded_rectangle(
        [S * 0.03, S * 0.03, S * 0.97, S * 0.97], radius=S * 0.205,
        outline=(148, 163, 184, 90), width=max(2, S // 200),
    )

    cx = cy = S / 2
    R = S * 0.305
    w = max(2, round(S * 0.021))
    pts = [
        (cx + R * math.sin(math.radians(60 * i)), cy - R * math.cos(math.radians(60 * i)))
        for i in range(6)
    ]
    # soft outer glow
    for k in range(6, 0, -1):
        d.line(pts + [pts[0]], fill=AMBER + (10,), width=w + k * 3, joint="curve")
    d.line(pts + [pts[0]], fill=AMBER, width=w, joint="curve")
    # top-left highlight
    d.line([pts[5], pts[0], pts[1]], fill=AMBER_L, width=max(2, w // 2), joint="curve")

    # reticle
    r_in = S * 0.108
    d.ellipse([cx - r_in, cy - r_in, cx + r_in, cy + r_in], outline=CYAN + (235,), width=max(2, S // 128))
    dot = S * 0.035
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=AMBER_L + (255,))

    tick = S * 0.052
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        x0, y0 = cx + dx * r_in * 1.28, cy + dy * r_in * 1.28
        d.line([(x0, y0), (x0 + dx * tick, y0 + dy * tick)], fill=CYAN + (220,), width=max(2, S // 160))
    return img


def main() -> int:
    big = render(1024)
    out_dir = os.path.join(ROOT, "assets")
    ui_dir = os.path.join(ROOT, "ui", "assets")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(ui_dir, exist_ok=True)
    ico = os.path.join(out_dir, "app.ico")
    big.save(ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(ui_dir, "app-256.png"))
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(out_dir, "app-512.png"))
    print("wrote", ico)
    print("wrote", os.path.join(ui_dir, "app-256.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
