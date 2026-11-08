"""Generate icon_bankcredentials_master.png (1024px) -- the "Bank Credentials" icon.

The Desktop shortcut that stores or updates the bank login details the
westpac-login / anz-login / cba-login skills read from the computer's
credential vault (Windows Credential Manager / macOS Keychain), so the icon is a
safe: a gunmetal body on the family's blue tile, a round door with a rim, a
spoked combination dial, and the family's googly eyes on the door, one of them
eyeing the small golden key in the bottom corner. Same tile, sheen, shadow and
256px reference geometry as the other icons here (k = S / 256).

    python desktop_launchers/make_bankcredentials_icon.py    # writes icon_bankcredentials_master.png (1024)
"""
import math
import os
from PIL import Image, ImageDraw, ImageFilter

INK = (28, 30, 36)
SAFE_BODY = (96, 104, 118)
SAFE_EDGE = (62, 68, 80)
DOOR = (132, 140, 154)
DOOR_RIM = (176, 184, 198)
DIAL = (230, 234, 240)
DIAL_SPOKE = (70, 76, 88)
KEY = (240, 196, 72)
KEY_EDGE = (176, 132, 36)
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


def _dial(draw, cx, cy, r, k):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DIAL, outline=DIAL_SPOKE, width=max(2, int(3 * k)))
    for i in range(6):
        a = math.radians(i * 60)
        draw.line([cx + math.cos(a) * r * 0.25, cy + math.sin(a) * r * 0.25,
                   cx + math.cos(a) * r * 0.85, cy + math.sin(a) * r * 0.85],
                  fill=DIAL_SPOKE, width=max(2, int(4 * k)))
    hub = r * 0.22
    draw.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=DIAL_SPOKE)


def _key(draw, x, y, k):
    # A small old-fashioned key lying at an angle: bow (ring), shaft, two bits.
    bow = 11 * k
    draw.ellipse([x - bow, y - bow, x + bow, y + bow], fill=KEY, outline=KEY_EDGE, width=max(2, int(2 * k)))
    draw.ellipse([x - bow * 0.45, y - bow * 0.45, x + bow * 0.45, y + bow * 0.45], fill=SAFE_BODY)
    draw.line([x + bow * 0.8, y, x + 46 * k, y], fill=KEY, width=max(3, int(7 * k)))
    draw.rectangle([x + 34 * k, y, x + 39 * k, y + 9 * k], fill=KEY)
    draw.rectangle([x + 42 * k, y, x + 46 * k, y + 7 * k], fill=KEY)


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2], radius=40 * k, fill=(255, 255, 255, 26))
    canvas = Image.alpha_composite(canvas, sheen)

    # Safe body with a drop shadow, and two stubby feet.
    body = [44 * k, 40 * k, 212 * k, 204 * k]
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([body[0], body[1] + 8 * k, body[2], body[3] + 10 * k], radius=18 * k, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * k))
    canvas = Image.alpha_composite(canvas, shadow)
    d = ImageDraw.Draw(canvas)
    for fx in (62 * k, 170 * k):
        d.rounded_rectangle([fx, 200 * k, fx + 24 * k, 216 * k], radius=4 * k, fill=SAFE_EDGE)
    d.rounded_rectangle(body, radius=18 * k, fill=SAFE_BODY, outline=SAFE_EDGE, width=max(2, int(4 * k)))

    # Round door: rim, then face; hinge plates on the left edge.
    cx, cy, r = 128 * k, 122 * k, 64 * k
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOOR_RIM)
    d.ellipse([cx - r + 7 * k, cy - r + 7 * k, cx + r - 7 * k, cy + r - 7 * k], fill=DOOR, outline=SAFE_EDGE, width=max(2, int(3 * k)))
    for hy in (86 * k, 150 * k):
        d.rounded_rectangle([50 * k, hy, 66 * k, hy + 20 * k], radius=4 * k, fill=DOOR_RIM, outline=SAFE_EDGE, width=max(1, int(2 * k)))

    # Eyes on the upper door, the dial below them; the right eye watches the key.
    er = 16 * k
    _googly(d, cx - 24 * k, cy - 20 * k, er, (-0.2, -0.3))
    _googly(d, cx + 24 * k, cy - 18 * k, er, (0.45, 0.4))
    _dial(d, cx, cy + 26 * k, 22 * k, k)

    _key(d, 150 * k, 190 * k, k)
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_bankcredentials_master.png")
    render(1024).save(out)
    print("wrote", out)
