"""Generate icon_costlog_master.png (1024px) -- the API Cost Log "gold coin" icon.

This master is the cross-platform source of truth: rebuild.sh renders it to .icns
for the macOS "API Cost Log.app", and the PIL one-liner in README.md renders it to
icon_costlog.ico for a future Windows shortcut. The design is a money-green card
with a rising cost bar-chart and a gold coin bearing a bold "$" under two googly
eyes -- "$" = spend, rising bars = cost-over-time, googly eyes match the
CSV/MyAgent/Heartbeat family.

Rendered natively at the target size (not upscaled) so every size stays crisp; all
geometry scales from a 256px reference via k = S / 256.

    python desktop_launchers/make_costlog_icon.py   # writes icon_costlog_master.png (1024)
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

GRAD_TOP = (52, 178, 110)     # money green, top
GRAD_BOT = (16, 92, 56)       # money green, bottom
COIN_TOP = (255, 216, 96)     # gold, light
COIN_BOT = (214, 150, 28)     # gold, dark
COIN_RIM = (150, 96, 12)      # dark gold rim
COIN_RING = (255, 234, 158)   # light inner ring
DOLLAR = (92, 58, 8)          # dark-brown "$" on the coin
INK = (28, 30, 36)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/ariblk.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Arial Black.ttf",
    "ariblk.ttf",
]


def _font(size):
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def _vgradient(size, top, bottom):
    col = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        col.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return col.resize((size, size))


def _googly(draw, cx, cy, r, look):
    ow = max(1, int(r / 5))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=INK, width=ow)
    pr = r * 0.5
    px, py = cx + look[0] * r, cy + look[1] * r
    draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=INK)
    hr = pr * 0.42
    hx, hy = px - pr * 0.32, py - pr * 0.32
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255))


def _glyph(ch, target_h, color):
    # Render large then downscale, so the "$" stays crisp at any S (no anchor= dep).
    fs = max(400, int(target_h * 1.8))
    f = _font(fs)
    tmp = Image.new("RGBA", (fs, int(fs * 1.6)), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((fs // 4, 0), ch, font=f, fill=color + (255,))
    g = tmp.crop(tmp.getbbox())
    w, h = g.size
    return g.resize((max(1, int(w * target_h / h)), target_h), Image.LANCZOS)


def _coin(canvas, cx, cy, r, k):
    box = int(2 * r)
    grad = _vgradient(box, COIN_TOP, COIN_BOT).convert("RGBA")
    cmask = Image.new("L", (box, box), 0)
    ImageDraw.Draw(cmask).ellipse([0, 0, box - 1, box - 1], fill=255)
    canvas.paste(grad, (int(cx - r), int(cy - r)), cmask)
    d = ImageDraw.Draw(canvas)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=COIN_RIM, width=max(2, int(7 * k)))
    ir = r * 0.84
    d.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], outline=COIN_RING, width=max(1, int(3 * k)))


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))

    # Top sheen, same family touch.
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2], radius=40 * k, fill=(255, 255, 255, 26))
    canvas = Image.alpha_composite(canvas, sheen)

    # Rising cost bars along the bottom (translucent white), behind the coin.
    bars = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bars)
    base = 224 * k
    for x, h in zip([30, 73, 116, 159, 202], [40, 66, 92, 120, 150]):
        x0 = x * k
        bd.rounded_rectangle([x0, base - h * k, x0 + 28 * k, base], radius=6 * k, fill=(255, 255, 255, 60))
    canvas = Image.alpha_composite(canvas, bars)

    # Drop shadow + gold coin, upper-centre.
    cx, cy, r = 128 * k, 110 * k, 70 * k
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([cx - r, cy - r + 7 * k, cx + r, cy + r + 9 * k], fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7 * k))
    canvas = Image.alpha_composite(canvas, shadow)
    _coin(canvas, cx, cy, r, k)

    # Big "$" under two googly eyes -> a friendly money face.
    g = _glyph("$", int(74 * k), DOLLAR)
    canvas.alpha_composite(g, (int(cx - g.width / 2), int(cy - g.height / 2 + 16 * k)))
    d = ImageDraw.Draw(canvas)
    er = 17 * k
    _googly(d, cx - 28 * k, cy - 26 * k, er, (0.15, -0.1))
    _googly(d, cx + 28 * k, cy - 26 * k, er, (-0.15, 0.25))
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_costlog_master.png")
    render(1024).save(out)
    print("wrote", out)
