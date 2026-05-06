from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

BG = (30, 64, 175, 255)
WHITE = (245, 245, 250, 255)
DARK = (30, 64, 175, 255)
EYE_GLOW = (125, 211, 252, 255)
AMBER = (251, 191, 36, 255)

d.rounded_rectangle((0, 0, S, S), radius=180, fill=BG)

highlight = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(highlight).rounded_rectangle(
    (40, 40, S - 40, S // 2 + 80), radius=160, fill=(255, 255, 255, 28)
)
img = Image.alpha_composite(img, highlight)
d = ImageDraw.Draw(img)

mx = S // 2
d.rectangle((mx - 18, 210, mx + 18, 360), fill=WHITE)
d.ellipse((mx - 64, 90, mx + 64, 210), fill=WHITE)
d.ellipse((mx - 28, 122, mx + 28, 178), fill=AMBER)

HL, HT, HR, HB = 200, 360, S - 200, S - 200
d.rounded_rectangle((HL, HT, HR, HB), radius=100, fill=WHITE)

ey = (HT + HB) // 2 - 50
for ex in (HL + 200, HR - 200):
    d.ellipse((ex - 100, ey - 100, ex + 100, ey + 100), fill=DARK)
    d.ellipse((ex - 60, ey - 60, ex + 60, ey + 60), fill=EYE_GLOW)
    d.ellipse((ex - 22, ey - 50, ex + 14, ey - 14), fill=WHITE)

my = HB - 165
for i in (-1, 0, 1):
    x = mx + i * 80
    d.rounded_rectangle((x - 18, my - 60, x + 18, my + 60), radius=10, fill=DARK)

img_256 = img.resize((256, 256), Image.LANCZOS)
img_256.save(
    "MyAgent.ico",
    format="ICO",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
img_256.save("MyAgent_preview.png", format="PNG")
print("Created MyAgent.ico and MyAgent_preview.png")
