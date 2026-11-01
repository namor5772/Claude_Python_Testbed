"""Generate icon_general_master.png (1024px) -- the "general" share folder icon.

The Desktop shortcut to <OneDrive>/MyAppShare/general -- the folder agent runs
save into (photos, bank exports, dockets) -- opens a plain folder in Explorer,
so its icon is a plain manila folder: a tab, a front flap, a small cloud on
the flap for the OneDrive share it lives in, and the family's googly eyes
peering out over the flap's edge. Same tile, sheen, shadow and 256px reference
geometry as the other icons here (k = S / 256).

    python desktop_launchers/make_general_icon.py    # writes icon_general_master.png (1024)
"""
import os
from PIL import Image, ImageDraw, ImageFilter

INK = (28, 30, 36)
FOLDER_BACK = (222, 170, 72)
FOLDER_FRONT = (246, 200, 96)
FOLDER_EDGE = (200, 148, 52)
CLOUD = (255, 255, 255)
GRAD_TOP = (52, 128, 214)
GRAD_BOT = (18, 70, 150)


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
    ow = max(2, int(r / 5))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=INK, width=ow)
    pr = r * 0.5
    px, py = cx + look[0] * r, cy + look[1] * r
    draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=INK)
    hr = pr * 0.42
    hx, hy = px - pr * 0.32, py - pr * 0.32
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255))


def _cloud(draw, cx, cy, w, color):
    # Three overlapping discs on a flat base: the OneDrive-ish cloud glyph.
    h = w * 0.55
    base = [cx - w / 2, cy - h * 0.1, cx + w / 2, cy + h / 2]
    draw.rounded_rectangle(base, radius=h * 0.3, fill=color)
    for dx, r in ((-0.22, 0.26), (0.05, 0.36), (0.3, 0.24)):
        rr = w * r
        x, y = cx + dx * w, cy - h * 0.1
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color)


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2], radius=40 * k, fill=(255, 255, 255, 26))
    canvas = Image.alpha_composite(canvas, sheen)

    # Folder back: body plus a tab at the top left.
    body = [32 * k, 70 * k, 224 * k, 206 * k]
    tab = [32 * k, 50 * k, 112 * k, 90 * k]
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([body[0], body[1] + 7 * k, body[2], body[3] + 8 * k], radius=14 * k, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * k))
    canvas = Image.alpha_composite(canvas, shadow)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(tab, radius=10 * k, fill=FOLDER_BACK)
    d.rounded_rectangle(body, radius=14 * k, fill=FOLDER_BACK)

    # Eyes sit on the back panel, peering over the front flap's top edge.
    er = 19 * k
    ey = 108 * k
    _googly(d, 100 * k, ey, er, (-0.15, -0.35))      # left eye looks up-left
    _googly(d, 152 * k, ey + 3 * k, er, (0.4, 0.3))  # right eye looks down-right

    # Front flap: slightly lighter, leaning back a touch so the eyes clear it.
    flap = [(28 * k, 118 * k), (228 * k, 118 * k), (216 * k, 206 * k), (40 * k, 206 * k)]
    d.polygon(flap, fill=FOLDER_FRONT)
    d.line([flap[0], flap[1]], fill=FOLDER_EDGE, width=max(2, int(3 * k)))

    _cloud(d, 128 * k, 166 * k, 74 * k, CLOUD)
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_general_master.png")
    render(1024).save(out)
    print("wrote", out)
