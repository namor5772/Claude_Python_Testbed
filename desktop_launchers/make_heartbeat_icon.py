"""Generate icon_heartbeat_master.png (1024px) -- the Heartbeat Log "EKG monitor" icon.

This master is the cross-platform source of truth: rebuild.sh renders it to .icns
for the macOS "Heartbeat Log.app", and the PIL one-liner in README.md renders it
to icon_heartbeat.ico for a future Windows shortcut. The design is a dark
vital-signs monitor panel with a faint grid, a glowing green ECG pulse line, and a
small red googly-eyed heart at the left "emitting" the trace -- green pulse = the
system is alive, which is exactly what Heartbeat.py's liveness log proves, and the
googly eyes match the CSV/MyAgent family style.

Rendered natively at the target size (not upscaled) so every size stays crisp; all
geometry scales from a 256px reference via k = S / 256.

    python desktop_launchers/make_heartbeat_icon.py   # writes icon_heartbeat_master.png (1024)
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageChops

GRAD_TOP = (40, 56, 82)       # navy monitor body, top
GRAD_BOT = (14, 20, 34)       # navy monitor body, bottom
PANEL = (9, 15, 26)           # the dark "screen"
PANEL_BORDER = (74, 96, 124)
GRID = (60, 120, 100, 55)     # faint RGBA monitor grid
EKG = (54, 235, 140)          # vivid monitor green
HEART = (235, 77, 61)         # coral red (shared family accent colour)
INK = (28, 30, 36)


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


def _heart(draw, cx, cy, s, color):
    # Two lobes + a downward triangle -- crisp enough at icon sizes.
    rl = s * 0.52
    lx, ly = cx - s * 0.46, cy - s * 0.34
    rx, ry = cx + s * 0.46, cy - s * 0.34
    draw.ellipse([lx - rl, ly - rl, lx + rl, ly + rl], fill=color)
    draw.ellipse([rx - rl, ry - rl, rx + rl, ry + rl], fill=color)
    draw.polygon([(cx - s * 0.92, cy - s * 0.20), (cx + s * 0.92, cy - s * 0.20),
                  (cx, cy + s * 0.98)], fill=color)


def _ekg_points(x0, x1, base_y, amp):
    """Flat baseline with two compact PQRST complexes (ref units; up = -y)."""
    beat = [
        (0, 0), (10, 0), (13, -0.13), (16, 0), (22, 0),       # P wave
        (25, 0.20), (28, -1.0), (31, 0.40), (34, 0),          # QRS complex (R = full amp)
        (42, 0), (46, -0.30), (51, 0), (60, 0),               # T wave
    ]
    pts = [(x0, base_y)]
    s1 = x0 + 12
    pts += [(s1 + dx, base_y + dyf * amp) for dx, dyf in beat]
    s2 = s1 + 60 + 12
    pts += [(s2 + dx, base_y + dyf * amp) for dx, dyf in beat]
    pts.append((x1, base_y))
    return pts


def render(S):
    k = S / 256.0
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA"), (0, 0), _rounded_mask(S, int(56 * k)))

    # Top sheen, same family touch as the CSV icon.
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([10 * k, 8 * k, S - 10 * k, S / 2], radius=40 * k, fill=(255, 255, 255, 22))
    canvas = Image.alpha_composite(canvas, sheen)

    # Screen panel.
    panel = [28 * k, 52 * k, 228 * k, 204 * k]
    ImageDraw.Draw(canvas).rounded_rectangle(panel, radius=20 * k, fill=PANEL,
                                             outline=PANEL_BORDER, width=max(1, int(2 * k)))

    # Faint grid, clipped to the panel's rounded rect.
    grid = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = 16 * k
    x = panel[0] + step
    while x < panel[2]:
        gd.line([x, panel[1], x, panel[3]], fill=GRID, width=max(1, int(1 * k)))
        x += step
    y = panel[1] + step
    while y < panel[3]:
        gd.line([panel[0], y, panel[2], y], fill=GRID, width=max(1, int(1 * k)))
        y += step
    pm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(pm).rounded_rectangle(panel, radius=20 * k, fill=255)
    canvas.paste(grid, (0, 0), ImageChops.multiply(grid.split()[3], pm))

    # ECG trace: glow first (blurred fat line, doubled), then the crisp line.
    pts = [(x * k, y * k) for x, y in _ekg_points(74, 218, 128, 58)]
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=EKG + (255,), width=int(17 * k), joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(7 * k))
    canvas = Image.alpha_composite(canvas, glow)
    canvas = Image.alpha_composite(canvas, glow)
    ImageDraw.Draw(canvas).line(pts, fill=EKG + (255,), width=max(2, int(9 * k)), joint="curve")

    # Googly-eyed heart at the left, "emitting" the trace.
    d = ImageDraw.Draw(canvas)
    hcx, hcy, hs = 54 * k, 122 * k, 24 * k
    _heart(d, hcx, hcy, hs, HEART)
    er = hs * 0.34
    _googly(d, hcx - hs * 0.42, hcy - hs * 0.30, er, (0.15, -0.15))
    _googly(d, hcx + hs * 0.42, hcy - hs * 0.30, er, (-0.15, 0.30))
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_heartbeat_master.png")
    render(1024).save(out)
    print("wrote", out)
