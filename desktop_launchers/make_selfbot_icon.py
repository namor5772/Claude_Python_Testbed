"""Generate icon_selfbot_master.png (1024px) -- the SelfBot "existential crisis" icon.

This master is the cross-platform source of truth: rebuild.sh renders it to .icns
for the macOS "SelfBot.app", and the PIL one-liner in README.md renders it to
icon_selfbot.ico for the Windows shortcut. The design expresses SelfBot's defining
trait -- it can run as two instances that chat with *themselves* (the
SelfBotInstanceMutex duo mode) -- as a self-referential crisis: an anxious,
cross-eyed googly robot (the MyAgent-family face, but sweating and dazed) whose
thought bubble contains a smaller copy of itself, whose thought bubble contains a
smaller copy of itself... a Droste recursion that never bottoms out. The googly
eyes tie it to the CSV/MyAgent/heartbeat family; the coral antenna ball and the
INK outlines are the shared family accents; the moody violet->indigo gradient is
the introspective odd-one-out among the green/navy siblings.

Rendered natively at the target size (not upscaled) so every size stays crisp; all
geometry scales from a 256px reference via k = S / 256 and each nested robot is
self-similar (every feature is a fraction of its own head width w).

    python desktop_launchers/make_selfbot_icon.py    # writes icon_selfbot_master.png (1024)
"""
import os
from PIL import Image, ImageDraw, ImageFilter

CORAL = (235, 77, 61)         # shared family accent (antenna ball)
INK = (28, 30, 36)            # outlines / pupils
GRAD_TOP = (99, 84, 172)      # introspective violet, top
GRAD_BOT = (41, 31, 74)       # deep indigo, bottom
HEAD = (228, 233, 245)        # cool silver-white robot face
HEAD_EDGE = (150, 161, 190)   # face outline + ears/antenna stalk
BUBBLE = (250, 251, 255)      # thought-cloud white
SWEAT = (86, 190, 240)        # light-blue bead of existential sweat
MAX_DEPTH = 2                 # 3 nested robots total (depths 0, 1, 2)


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
    # Family googly eye; `look` is the pupil offset as a fraction of r.
    ow = max(1, int(r / 5))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=INK, width=ow)
    pr = r * 0.5
    px, py = cx + look[0] * r, cy + look[1] * r
    draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=INK)
    hr = pr * 0.42
    hx, hy = px - pr * 0.32, py - pr * 0.32
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255))


def _sweat(draw, cx, cy, r):
    # An upward-pointing teardrop -- the classic anime "stressed" bead.
    draw.polygon([(cx - r * 0.55, cy - r * 0.1), (cx + r * 0.55, cy - r * 0.1),
                  (cx, cy - r * 1.7)], fill=SWEAT)
    draw.ellipse([cx - r, cy - r * 0.35, cx + r, cy + r * 1.3], fill=SWEAT)
    draw.ellipse([cx - r * 0.5, cy - r * 0.05, cx - r * 0.05, cy + r * 0.55],
                 fill=(220, 244, 255))  # tiny highlight


def _thought_cloud(draw, cx, cy, r):
    # Lumpy comic thought bubble = several overlapping white ellipses.
    for bx, by, br in [(-0.62, 0.12, 0.72), (0.0, -0.52, 0.80), (0.62, -0.02, 0.72),
                       (0.18, 0.58, 0.66), (-0.16, 0.02, 0.98)]:
        draw.ellipse([cx + bx * r - br * r, cy + by * r - br * r,
                      cx + bx * r + br * r, cy + by * r + br * r], fill=BUBBLE)


def _robot(draw, cx, cy, w, depth):
    """Draw a self-similar anxious robot centred at (cx, cy) with head width w,
    then -- until MAX_DEPTH -- a thought bubble up-right holding a smaller self."""
    h = w * 1.05
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    lw = max(1, int(w * 0.028))

    # Antenna (stalk + coral ball) -- the family's signature topper.
    a_top = y0 - w * 0.26
    ball = w * 0.10
    draw.line([cx, y0 + lw, cx, a_top + ball], fill=HEAD_EDGE, width=max(1, int(w * 0.05)))
    draw.ellipse([cx - ball, a_top - ball, cx + ball, a_top + ball],
                 fill=CORAL, outline=INK, width=max(1, int(w * 0.02)))

    # Ears / side nubs.
    ew, eh = w * 0.12, h * 0.26
    draw.rounded_rectangle([x0 - ew * 0.6, cy - eh / 2, x0 + ew * 0.4, cy + eh / 2],
                           radius=ew * 0.45, fill=HEAD_EDGE)
    draw.rounded_rectangle([x1 - ew * 0.4, cy - eh / 2, x1 + ew * 0.6, cy + eh / 2],
                           radius=ew * 0.45, fill=HEAD_EDGE)

    # Head.
    draw.rounded_rectangle([x0, y0, x1, y1], radius=w * 0.24,
                           fill=HEAD, outline=HEAD_EDGE, width=lw)

    # Cross-eyed, downcast googly eyes -- dazed + worried.
    ox, eyy, eyr = w * 0.23, cy - h * 0.04, w * 0.205
    _googly(draw, cx - ox, eyy, eyr, (0.52, 0.28))   # pupils converge inward + down
    _googly(draw, cx + ox, eyy, eyr, (-0.52, 0.28))

    if w > 58:  # fine detail only on the larger instances
        # Worried wavy mouth.
        my, amp, step = cy + h * 0.31, h * 0.035, w * 0.088
        pts, xx, up = [], cx - w * 0.19, True
        while xx <= cx + w * 0.19 + 1:
            pts.append((xx, my + (amp if up else -amp)))
            up = not up
            xx += step
        draw.line(pts, fill=INK, width=max(1, int(w * 0.028)), joint="curve")
        _sweat(draw, cx + w * 0.40, cy - h * 0.06, w * 0.075)
    else:
        draw.line([cx - w * 0.13, cy + h * 0.30, cx + w * 0.13, cy + h * 0.30],
                  fill=INK, width=max(1, int(w * 0.045)))

    # Recurse: a thought bubble up-right, a smaller self inside it.
    if depth < MAX_DEPTH:
        ccx, ccy = cx + w * 0.60, cy - h * 0.54
        child_w = w * 0.42
        # Puff tail: two shrinking circles from the head toward the cloud.
        tx0, ty0 = cx + w * 0.30, cy - h * 0.34
        for t, fr in ((0.34, 0.16), (0.63, 0.11)):
            px, py = tx0 + (ccx - tx0) * t, ty0 + (ccy - ty0) * t
            pr = w * fr
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=BUBBLE)
        _thought_cloud(draw, ccx, ccy, child_w * 0.95)
        _robot(draw, ccx, ccy, child_w, depth + 1)


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))

    # Top sheen -- shared family touch.
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2],
                                            radius=40 * k, fill=(255, 255, 255, 24))
    canvas = Image.alpha_composite(canvas, sheen)

    # Faint "?" watermark low in the gradient -- the unanswered question.
    wm = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wm)
    qx, qy, qr, qt = 66 * k, 150 * k, 34 * k, max(2, int(12 * k))
    wd.arc([qx - qr, qy - qr * 1.5, qx + qr, qy + qr * 0.2], start=150, end=390,
           fill=(255, 255, 255, 30), width=qt)
    wd.line([qx + qr * 0.32, qy + qr * 0.05, qx + qr * 0.02, qy + qr * 0.7],
            fill=(255, 255, 255, 30), width=qt)
    wd.ellipse([qx - qt, qy + qr * 1.0, qx + qt, qy + qr * 1.0 + 2 * qt],
               fill=(255, 255, 255, 30))
    canvas = Image.alpha_composite(canvas, wm.filter(ImageFilter.GaussianBlur(1.4 * k)))

    # Drop shadow under the main robot for a little lift.
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([48 * k, 196 * k, 168 * k, 232 * k], fill=(0, 0, 0, 120))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(9 * k)))

    _robot(ImageDraw.Draw(canvas), 100 * k, 150 * k, 150 * k, 0)
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_selfbot_master.png")
    render(1024).save(out)
    print("wrote", out)
