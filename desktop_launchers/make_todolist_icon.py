"""Generate icon_todolist_master.png (1024px) -- the TodoList clipboard icon.

This master is the cross-platform source of truth: the PIL one-liner in
README.md renders it to icon_todolist.ico for the Windows shortcut (and it is
.icns-ready should a macOS TodoList.app ever join rebuild.sh). The design is a
clipboard in the app's own palette -- light-blue paper (the #D6EBFF treeview),
a yellow clip (the #FFFFB3 headings), two ticked-off grey rows and one urgent
red row still pending (#CC0000 high-priority) -- with a coral googly-eyed
pencil swooping in to tick the last box, keeping the family gag started by the
CSV comma and the SelfBot robot.

Rendered natively at the target size (not upscaled) so every size stays crisp;
all geometry scales from a 256px reference via k = S / 256.

    python desktop_launchers/make_todolist_icon.py   # writes icon_todolist_master.png (1024)
"""
import math
import os
from PIL import Image, ImageDraw, ImageFilter

INK = (28, 30, 36)
PAPER = (255, 255, 255)
PAPER_TINT = (214, 235, 255)   # app treeview background
CLIP = (255, 236, 130)         # app heading yellow, deepened for contrast
CLIP_EDGE = (196, 168, 60)
DONE_BAR = (150, 156, 165)     # greyed-out completed rows
URGENT = (204, 0, 0)           # app High-priority red
TICK = (34, 158, 89)
CORAL = (235, 77, 61)          # family accent (CSV comma coral)
WOOD = (245, 205, 150)
ERASER = (247, 170, 185)
GRAD_TOP = (64, 122, 196)
GRAD_BOT = (26, 60, 116)


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


def _tick(draw, box, width, color=TICK):
    """A bold check mark filling the checkbox rect, overshooting the top-right
    corner the way a hand-drawn tick does."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    pts = [(x0 + 0.18 * w, y0 + 0.52 * h),
           (x0 + 0.42 * w, y0 + 0.78 * h),
           (x0 + 1.05 * w, y0 + 0.02 * h)]
    draw.line(pts, fill=color, width=width, joint="curve")
    r = width / 2
    for px, py in (pts[0], pts[-1]):
        draw.ellipse([px - r, py - r, px + r, py + r], fill=color)


def _pencil(S, k):
    """Coral googly-eyed pencil on its own layer, drawn horizontal with the
    tip at the left. Returns (layer, tip_apex_xy) so the caller can rotate it
    and still land the tip exactly where it should touch."""
    L, W = int(150 * k), int(34 * k)
    tip = int(34 * k)
    pad = int(26 * k)  # room for the rotation + eye overhang
    layer = Image.new("RGBA", (L + tip + 2 * pad, W + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ow = max(2, int(3 * k))
    x0, y0 = pad + tip, pad
    # body
    d.rectangle([x0, y0, x0 + L - 18 * k, y0 + W], fill=CORAL, outline=INK, width=ow)
    # eraser cap + ferrule band
    d.rounded_rectangle([x0 + L - 30 * k, y0, x0 + L, y0 + W], radius=10 * k,
                        fill=ERASER, outline=INK, width=ow)
    d.rectangle([x0 + L - 34 * k, y0, x0 + L - 24 * k, y0 + W], fill=(200, 205, 215),
                outline=INK, width=ow)
    # wood tip + graphite
    d.polygon([(x0, y0), (x0, y0 + W), (pad, y0 + W / 2)], fill=WOOD, outline=INK)
    d.polygon([(pad + tip * 0.38, y0 + W / 2 - tip * 0.19),
               (pad + tip * 0.38, y0 + W / 2 + tip * 0.19),
               (pad, y0 + W / 2)], fill=INK)
    # googly eyes near the tip end (the body's far half rotates off-canvas),
    # both peering at whatever the tip is up to
    er = W * 0.34
    ey = y0 + W * 0.28
    _googly(d, x0 + L * 0.14, ey, er, (-0.5, 0.35))
    _googly(d, x0 + L * 0.34, ey - er * 0.1, er, (-0.35, 0.55))
    return layer, (pad, y0 + W / 2)


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0),
                 _rounded_mask(S, int(56 * k)))
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2],
                                            radius=40 * k, fill=(255, 255, 255, 26))
    canvas = Image.alpha_composite(canvas, sheen)

    # clipboard: shadow, tinted paper, yellow clip
    board = [40 * k, 34 * k, 196 * k, 214 * k]
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [board[0], board[1] + 6 * k, board[2], board[3] + 7 * k],
        radius=16 * k, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * k))
    canvas = Image.alpha_composite(canvas, shadow)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(board, radius=16 * k, fill=PAPER)
    d.rounded_rectangle([board[0] + 8 * k, board[1] + 8 * k, board[2] - 8 * k, board[3] - 8 * k],
                        radius=10 * k, fill=PAPER_TINT)
    d.rounded_rectangle([92 * k, 20 * k, 144 * k, 48 * k], radius=10 * k,
                        fill=CLIP, outline=CLIP_EDGE, width=max(2, int(3 * k)))
    d.rounded_rectangle([104 * k, 12 * k, 132 * k, 28 * k], radius=8 * k,
                        fill=CLIP, outline=CLIP_EDGE, width=max(2, int(3 * k)))

    # three checklist rows: done, done, urgent-pending
    bw = 30 * k                      # checkbox side
    rows_y = (62 * k, 108 * k, 154 * k)
    bar_h = 18 * k
    ow = max(2, int(3 * k))
    for i, y in enumerate(rows_y):
        box = [56 * k, y, 56 * k + bw, y + bw]
        d.rounded_rectangle(box, radius=6 * k, fill=PAPER,
                            outline=INK if i == 2 else DONE_BAR, width=ow)
        bar_col = URGENT if i == 2 else DONE_BAR
        d.rounded_rectangle([98 * k, y + (bw - bar_h) / 2, 182 * k, y + (bw + bar_h) / 2],
                            radius=6 * k, fill=bar_col)
        if i < 2:
            _tick(d, box, max(3, int(8 * k)))
            # strikethrough over the finished row's text bar
            d.line([98 * k, y + bw / 2, 182 * k, y + bw / 2],
                   fill=(255, 255, 255, 200), width=max(2, int(4 * k)))

    # the googly pencil swoops in from the lower right to tick the urgent row:
    # PIL rotates counterclockwise about the layer center, so track where the
    # tip apex lands and paste so it touches just inside the empty checkbox
    angle = -48  # tip points up-left, body trails to the lower right
    layer, apex = _pencil(S, k)
    w0, h0 = layer.size
    rot = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    a = math.radians(angle)
    dx, dy = apex[0] - w0 / 2, apex[1] - h0 / 2
    rx = dx * math.cos(a) + dy * math.sin(a)
    ry = -dx * math.sin(a) + dy * math.cos(a)
    apex_rot = (rot.size[0] / 2 + rx, rot.size[1] / 2 + ry)
    tip_target = (78 * k, 178 * k)  # inside the urgent row's empty checkbox
    canvas.alpha_composite(rot, (int(tip_target[0] - apex_rot[0]),
                                 int(tip_target[1] - apex_rot[1])))
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_todolist_master.png")
    render(1024).save(out)
    print("wrote", out)
