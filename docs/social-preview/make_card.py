from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG      = (9, 14, 12)       # deep green-black, matches site register
PANEL   = (13, 20, 17)
TEXT    = (240, 245, 242)   # near-white
GREEN   = (63, 220, 151)    # terminal green accent
MUTED   = (128, 140, 133)   # muted gray-green
BORDER  = (34, 48, 41)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

DV = "/usr/share/fonts/truetype/dejavu/"
f_eyebrow  = ImageFont.truetype(DV + "DejaVuSansMono.ttf", 24)
f_head     = ImageFont.truetype(DV + "DejaVuSans-Bold.ttf", 66)
f_chip     = ImageFont.truetype(DV + "DejaVuSansMono-Bold.ttf", 22)
f_foot     = ImageFont.truetype(DV + "DejaVuSansMono.ttf", 22)

M = 80  # left margin

# subtle top rule + eyebrow
d.text((M, 72), "KRISTIAN KOBESCAK · BOUNDED AGENT", font=f_eyebrow, fill=GREEN)
d.line([(M, 116), (W - M, 116)], fill=BORDER, width=2)

# headline, three lines
head_lines = ["The eval gate was committed", "to git before the agent", "was written."]
y = 160
for i, line in enumerate(head_lines):
    # accent the last line's final word area subtly: keep it simple, all white
    d.text((M, y), line, font=f_head, fill=TEXT)
    y += 82

# chips row
chips = ["4 TOOLS", "TURN CAP", "COST CEILING", "AUDIT LOG", "SYNTHETIC DATA"]
cx, cy = M, 468
pad_x, pad_y = 18, 10
for c in chips:
    bbox = d.textbbox((0, 0), c, font=f_chip)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    box = [cx, cy, cx + tw + 2 * pad_x, cy + th + 2 * pad_y + 6]
    d.rounded_rectangle(box, radius=6, fill=PANEL, outline=BORDER, width=2)
    d.text((cx + pad_x, cy + pad_y), c, font=f_chip, fill=GREEN)
    cx = box[2] + 14

# footer
d.line([(M, 556), (W - M, 556)], fill=BORDER, width=2)
d.text((M, 576), "github.com/kobescak-kristian/ai-claim-verification-agent", font=f_foot, fill=MUTED)
gate = "GATE: PASS"
bbox = d.textbbox((0, 0), gate, font=f_chip)
d.text((W - M - (bbox[2] - bbox[0]), 576), gate, font=f_chip, fill=GREEN)

out = "/home/claude/agent-social-preview.png"
img.save(out)
print("saved", out, img.size)
