"""Generate icon_basicform_master.png (1024px) -- the Basic Form (x64 MASM) icon.

A window -- white title bar with the three Windows caption glyphs, form-grey
client area -- whose two googly eyes peer down at its one blue-bordered
button, with a mouse pointer about to click it: the whole app in one glance.
It sits on a deep purple gradient (assembler: as low-level as the testbed
goes) with an "ASM" badge top-left in the style of the TodoList (Native)
"C++" badge, so the compiled Desktop items read as a family. The canvas,
gradient and googly-eye helpers come from make_todolist_icon.

Rendered natively at the target size (not upscaled) so every size stays crisp;
all geometry scales from a 256px reference via k = S / 256.

    python desktop_launchers/make_basicform_icon.py   # writes icon_basicform_master.png (1024)
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from make_todolist_icon import INK, _googly, _rounded_mask, _vgradient

GRAD_TOP = (104, 78, 178)
GRAD_BOT = (44, 28, 98)
TITLE_BAR = (255, 255, 255)
TITLE_TEXT = (150, 150, 158)   # a "title text" placeholder bar
CLIENT = (240, 240, 240)       # COLOR_BTNFACE, the form background the app uses
FRAME = (110, 110, 122)
SEPARATOR = (214, 214, 220)
BUTTON = (253, 253, 253)
ACCENT = (0, 103, 192)         # Windows 11 default-button border blue
BADGE_TEXT = "ASM"
BADGE_FILL = tuple(int(c * 0.82) for c in GRAD_BOT)
BADGE_EDGE = (255, 255, 255)


def _font(size):
    for cand in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/Library/Fonts/Arial Bold.ttf"):
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _centered_text(d, box, text, font, fill):
    bb = d.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((box[0] + box[2]) / 2 - tw / 2 - bb[0],
            (box[1] + box[3]) / 2 - th / 2 - bb[1]), text, font=font, fill=fill)


def _pointer(d, hot, k):
    """The classic white arrow pointer with its hotspot at `hot`."""
    u = 1.7 * k
    pts = [(0, 0), (0, 17), (4.5, 13.2), (7.6, 20), (10.6, 18.6), (7.6, 12.2), (13, 12.2)]
    d.polygon([(hot[0] + x * u, hot[1] + y * u) for x, y in pts],
              fill=(255, 255, 255), outline=INK, width=max(2, int(2.5 * k)))


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0),
                 _rounded_mask(S, int(56 * k)))
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2],
                                            radius=40 * k, fill=(255, 255, 255, 26))
    canvas = Image.alpha_composite(canvas, sheen)

    # the window: drop shadow, then a white frame whose lower part is the grey client
    win = [30 * k, 68 * k, 226 * k, 224 * k]
    x0, y0, x1, y1 = win
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x0, y0 + 6 * k, x1, y1 + 8 * k],
                                             radius=14 * k, fill=(0, 0, 0, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * k))
    canvas = Image.alpha_composite(canvas, shadow)
    d = ImageDraw.Draw(canvas)
    ow = max(2, int(3 * k))
    d.rounded_rectangle(win, radius=14 * k, fill=TITLE_BAR, outline=FRAME, width=ow)
    bar_h = 30 * k
    y_bar = y0 + bar_h
    # client area: rounded at the bottom, squared off under the title bar,
    # inset by the outline width so the frame stays intact
    d.rounded_rectangle([x0 + ow, y_bar, x1 - ow, y1 - ow], radius=12 * k, fill=CLIENT)
    d.rectangle([x0 + ow, y_bar, x1 - ow, y_bar + 14 * k], fill=CLIENT)
    d.line([x0 + ow, y_bar, x1 - ow, y_bar], fill=SEPARATOR, width=max(1, int(1.5 * k)))

    # title bar: a title-text placeholder and the minimise / maximise / close glyphs
    cy = y0 + bar_h / 2
    d.rounded_rectangle([x0 + 14 * k, cy - 3.5 * k, x0 + 72 * k, cy + 3.5 * k],
                        radius=3 * k, fill=TITLE_TEXT)
    gw = max(2, int(2.6 * k))
    for i, glyph in enumerate(("min", "max", "close")):
        cx = x1 - (3 - i) * 24 * k + 2 * k
        if glyph == "min":
            d.line([cx - 6 * k, cy, cx + 6 * k, cy], fill=INK, width=gw)
        elif glyph == "max":
            d.rectangle([cx - 6 * k, cy - 6 * k, cx + 6 * k, cy + 6 * k], outline=INK, width=gw)
        else:
            d.line([cx - 6 * k, cy - 6 * k, cx + 6 * k, cy + 6 * k], fill=INK, width=gw)
            d.line([cx - 6 * k, cy + 6 * k, cx + 6 * k, cy - 6 * k], fill=INK, width=gw)

    # the one button, Windows 11 style: white face, accent border, its caption
    btn = [x0 + 22 * k, y1 - 58 * k, x0 + 118 * k, y1 - 22 * k]
    d.rounded_rectangle(btn, radius=6 * k, fill=BUTTON, outline=ACCENT, width=max(2, int(3 * k)))
    _centered_text(d, btn, "Click me", _font(int(15 * k)), INK)

    # googly eyes in the client area, both peering down at the button
    er = 23 * k
    ey = y_bar + 46 * k
    _googly(d, x0 + 62 * k, ey, er, (0.12, 0.62))
    _googly(d, x0 + 118 * k, ey - 3 * k, er, (-0.28, 0.6))

    # a pointer hovering over the button's right end, about to click
    _pointer(d, (btn[2] - 18 * k, btn[1] + 10 * k), k)

    # the ASM badge, top-left, like the TodoList (Native) C++ badge
    box = [14 * k, 14 * k, 112 * k, 62 * k]
    d.rounded_rectangle(box, radius=12 * k, fill=BADGE_FILL,
                        outline=BADGE_EDGE, width=max(2, int(3 * k)))
    _centered_text(d, box, BADGE_TEXT, _font(int(34 * k)), (255, 255, 255))
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_basicform_master.png")
    render(1024).save(out)
    print("wrote", out)
