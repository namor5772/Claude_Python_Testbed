"""Generate icon_todolist_native_master.png (1024px) -- the TodoList (Native) icon.

The native C++ port's launcher shares TodoList's clipboard artwork so the two
Desktop items read as siblings, with one difference: a deep-blue "C++" badge
in the free top-left corner (the pencil owns the lower right, the clip the
top center), so a glance tells the compiled twin from the Python original.

    python desktop_launchers/make_todolist_native_icon.py
"""
import os

from PIL import ImageDraw, ImageFont

from make_todolist_icon import GRAD_BOT, render

BADGE_TEXT = "C++"
BADGE_FILL = tuple(int(c * 0.82) for c in GRAD_BOT)  # deepened gradient blue
BADGE_EDGE = (255, 255, 255)


def _badge_font(size):
    for cand in ("/System/Library/Fonts/Helvetica.ttc",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/Library/Fonts/Arial Bold.ttf"):
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size, index=1)  # 1 = bold face in the .ttc
            except OSError:
                try:
                    return ImageFont.truetype(cand, size)
                except OSError:
                    continue
    return ImageFont.load_default()


def render_native(S):
    k = S / 256.0
    canvas = render(S)
    d = ImageDraw.Draw(canvas)
    box = [14 * k, 14 * k, 112 * k, 62 * k]
    d.rounded_rectangle(box, radius=12 * k, fill=BADGE_FILL,
                        outline=BADGE_EDGE, width=max(2, int(3 * k)))
    font = _badge_font(int(34 * k))
    bb = d.textbbox((0, 0), BADGE_TEXT, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    cx = (box[0] + box[2]) / 2 - tw / 2 - bb[0]
    cy = (box[1] + box[3]) / 2 - th / 2 - bb[1]
    d.text((cx, cy), BADGE_TEXT, font=font, fill=(255, 255, 255))
    return canvas


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "icon_todolist_native_master.png")
    render_native(1024).save(out)
    print("wrote", out)
