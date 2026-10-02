"""Generate icon_mybackup_master.png (1024px) -- the MyBackup icon.

This master is the cross-platform source of truth: rebuild.sh renders it to
.icns for the macOS MyBackup.app, and the PIL one-liner in README.md renders it
to icon_mybackup.ico for the Windows shortcut. Deliberately the sober one in
the family (no googly eyes -- the user asked for a serious icon, 2026-10-03):
two drive slabs on a slate-blue tile, the FROM drive above with an idle light,
the TO drive below with a green "backed up" light, and one broad down-arrow
between them -- the one-way mirror MyBackup performs. At 16 px the arrow is a
stroke, but the two-slab stack still reads as a backup.

Rendered natively at the target size (not upscaled) so every size stays crisp;
all geometry scales from a 256px reference via k = S / 256.

    python desktop_launchers/make_mybackup_icon.py   # writes icon_mybackup_master.png (1024)
"""
import os
from PIL import Image, ImageDraw, ImageFilter

GRAD_TOP = (46, 78, 118)      # slate blue, top of the tile
GRAD_BOT = (22, 40, 66)
SLAB = (228, 234, 240)        # drive body
SLAB_LIP = (246, 249, 252)    # the lighter strip along a slab's top edge
SLAB_EDGE = (138, 154, 172)
GROOVE = (176, 189, 203)      # tray line + vents
ARROW = (244, 247, 250)
LED_IDLE = (70, 96, 130)      # the FROM drive: a quiet light
LED_OK = (58, 176, 108)       # the TO drive: backed up
LED_OK_RING = (24, 110, 64)

TOP_SLAB_Y = 44               # the two slabs' top edges, 256-px reference
BOTTOM_SLAB_Y = 162
SLAB_H = 50


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


def _soft_shadow(canvas, draw_fn, k, dy=5, blur=5, alpha=120):
    """Composite a blurred black copy of whatever draw_fn draws, shifted down."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), dy * k, (0, 0, 0, alpha))
    return Image.alpha_composite(canvas, layer.filter(ImageFilter.GaussianBlur(blur * k)))


def _slab(canvas, k, top, led, ring):
    """One drive slab: body with a lighter lip, a tray groove, three vents at
    the left and a status light at the right."""
    x0, x1 = 50 * k, 206 * k
    y0, y1 = top * k, (top + SLAB_H) * k
    r = 10 * k
    canvas = _soft_shadow(canvas, lambda d, dy, fill: d.rounded_rectangle([x0, y0 + dy, x1, y1 + dy], radius=r, fill=fill), k)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=SLAB, outline=SLAB_EDGE, width=max(1, int(2 * k)))
    d.rounded_rectangle([x0 + 3 * k, y0 + 3 * k, x1 - 3 * k, y0 + 13 * k], radius=6 * k, fill=SLAB_LIP)
    lw = max(1, int(3 * k))
    d.line([x0 + 14 * k, y0 + 34 * k, x1 - 36 * k, y0 + 34 * k], fill=GROOVE, width=lw)
    for i in range(3):
        vx = x0 + (16 + i * 9) * k
        d.line([vx, y0 + 19 * k, vx, y0 + 27 * k], fill=GROOVE, width=lw)
    cx, cy, lr = x1 - 20 * k, y0 + 25 * k, 6 * k
    d.ellipse([cx - lr, cy - lr, cx + lr, cy + lr], fill=led, outline=ring, width=max(1, int(1.5 * k)))
    hr = lr * 0.33
    hx, hy = cx - lr * 0.4, cy - lr * 0.4
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255))
    return canvas


def _arrow(canvas, k):
    """A broad down-arrow from under the FROM slab onto the TO slab's edge."""
    cx = 128 * k
    half_shaft = 11 * k
    top = (TOP_SLAB_Y + SLAB_H + 5) * k
    neck = (BOTTOM_SLAB_Y - 28) * k
    tip = (BOTTOM_SLAB_Y - 3) * k
    half_head = 30 * k

    def shapes(d, dy, fill):
        d.rounded_rectangle([cx - half_shaft, top + dy, cx + half_shaft, neck + 2 * k + dy], radius=4 * k, fill=fill)
        d.polygon([(cx - half_head, neck + dy), (cx + half_head, neck + dy), (cx, tip + dy)], fill=fill)

    canvas = _soft_shadow(canvas, shapes, k, dy=4, blur=4, alpha=130)
    shapes(ImageDraw.Draw(canvas), 0, ARROW)
    return canvas


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2], radius=40 * k, fill=(255, 255, 255, 22))
    canvas = Image.alpha_composite(canvas, sheen)

    canvas = _slab(canvas, k, TOP_SLAB_Y, LED_IDLE, SLAB_EDGE)
    canvas = _arrow(canvas, k)
    canvas = _slab(canvas, k, BOTTOM_SLAB_Y, LED_OK, LED_OK_RING)
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_mybackup_master.png")
    render(1024).save(out)
    print("wrote", out)
