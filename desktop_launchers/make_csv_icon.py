"""Generate icon_csv_master.png (1024px) -- the CSV Editor "googly-comma" icon.

This master is the cross-platform source of truth: rebuild.sh renders it to
.icns for the macOS CSVEditor.app, and the PIL one-liner in README.md renders it
to icon_csv.ico for the Windows shortcut. The design is a clean spreadsheet grid
with a coral, mismatched-googly-eyed comma perched on the lower-right corner --
informative (grid + comma = comma-separated values) and a bit silly.

Rendered natively at the target size (not upscaled) so every size stays crisp;
all geometry scales from a 256px reference via k = S / 256.

    python desktop_launchers/make_csv_icon.py        # writes icon_csv_master.png (1024)
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

CORAL = (235, 77, 61)
INK = (28, 30, 36)
HEADER = (205, 233, 219)
GLINE = (208, 222, 214)
BAR = (206, 215, 226)
HLABEL = (120, 170, 142)
GRAD_TOP = (34, 158, 89)
GRAD_BOT = (18, 96, 56)

# Arial Black has the fat, round-headed comma the mascot needs; try the usual
# Windows and macOS locations so the master can be regenerated on either OS.
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


def _comma(target_h, color):
    # Render the glyph large then downscale to target_h, so it's crisp at any S.
    fs = max(400, int(target_h * 2.6))
    f = _font(fs)
    tmp = Image.new("RGBA", (fs, int(fs * 1.5)), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((fs // 4, 0), ",", font=f, fill=color + (255,))
    glyph = tmp.crop(tmp.getbbox())
    w, h = glyph.size
    return glyph.resize((max(1, int(w * target_h / h)), target_h), Image.LANCZOS)


def _googly(draw, cx, cy, r, look):
    ow = max(2, int(r / 5))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=INK, width=ow)
    pr = r * 0.5
    px, py = cx + look[0] * r, cy + look[1] * r
    draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=INK)
    hr = pr * 0.42
    hx, hy = px - pr * 0.32, py - pr * 0.32
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255))


def _grid(draw, rect, k):
    import itertools
    x0, y0, x1, y1 = rect
    cols, rows = 4, 5
    cw, rh = (x1 - x0) / cols, (y1 - y0) / rows
    lw = max(1, int(2 * k))
    draw.rounded_rectangle([x0, y0, x1, y0 + rh], radius=8 * k, fill=HEADER)
    draw.rectangle([x0, y0 + rh - 8 * k, x1, y0 + rh], fill=HEADER)
    for c in range(cols):
        hx = x0 + c * cw + cw * 0.2
        draw.rounded_rectangle([hx, y0 + rh * 0.34, hx + cw * 0.6, y0 + rh * 0.66], radius=3 * k, fill=HLABEL)
    for c in range(1, cols):
        draw.line([x0 + c * cw, y0, x0 + c * cw, y1], fill=GLINE, width=lw)
    for r in range(1, rows):
        draw.line([x0, y0 + r * rh, x1, y0 + r * rh], fill=GLINE, width=lw)
    fills = itertools.cycle([0.7, 0.45, 0.6, 0.3, 0.55, 0.4])
    for r in range(1, rows):
        for c in range(cols):
            frac = next(fills)
            bx = x0 + c * cw + cw * 0.16
            by = y0 + r * rh + rh * 0.34
            draw.rounded_rectangle([bx, by, bx + cw * frac, by + rh * 0.3], radius=3 * k, fill=BAR)


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2], radius=40 * k, fill=(255, 255, 255, 26))
    canvas = Image.alpha_composite(canvas, sheen)

    rect = [30 * k, 30 * k, 200 * k, 200 * k]
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([rect[0], rect[1] + 6 * k, rect[2], rect[3] + 7 * k], radius=16 * k, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * k))
    canvas = Image.alpha_composite(canvas, shadow)
    ImageDraw.Draw(canvas).rounded_rectangle(rect, radius=16 * k, fill=(255, 255, 255))

    _grid(ImageDraw.Draw(canvas), [44 * k, 44 * k, 186 * k, 186 * k], k)

    g = _comma(int(124 * k), CORAL)
    gw, gh = g.size
    px, py = int(150 * k), int(126 * k)
    canvas.alpha_composite(g, (px, py))
    d = ImageDraw.Draw(canvas)
    er = gw * 0.225
    ey = py + gh * 0.185
    _googly(d, px + gw * 0.33, ey, er, (-0.1, -0.4))      # left eye looks up
    _googly(d, px + gw * 0.75, ey + er * 0.15, er, (0.45, 0.35))  # right eye looks down-right
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_csv_master.png")
    render(1024).save(out)
    print("wrote", out)
