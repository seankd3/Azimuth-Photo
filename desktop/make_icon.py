"""Generate the Azimuth Photo app icon: dark rounded square, aperture-inspired mark."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# dark rounded-square plate
r = 180
d.rounded_rectangle([32, 32, S - 32, S - 32], radius=r, fill=(13, 15, 18, 255))

# aperture: 6 blades as arcs of offset circles, warm accent
cx, cy = S / 2, S / 2
R_outer = 330
R_inner = 128
blades = 6
accent = (255, 149, 43, 255)   # Azimuth orange
steel = (208, 214, 224, 255)

for i in range(blades):
    a0 = (i / blades) * 2 * math.pi
    a1 = a0 + 2 * math.pi / blades
    # blade polygon: outer arc segment swept toward inner circle with rotational offset
    pts = []
    steps = 24
    for t in range(steps + 1):
        a = a0 + (a1 - a0) * (t / steps)
        pts.append((cx + R_outer * math.cos(a), cy + R_outer * math.sin(a)))
    off = 2 * math.pi / blades * 0.62
    for t in range(steps + 1):
        a = a1 + off - (a1 - a0) * (t / steps)
        pts.append((cx + R_inner * math.cos(a), cy + R_inner * math.sin(a)))
    color = accent if i == 0 else steel
    d.polygon(pts, fill=color)

# center hole
d.ellipse([cx - R_inner + 34, cy - R_inner + 34, cx + R_inner - 34, cy + R_inner - 34], fill=(13, 15, 18, 255))

output = Path(__file__).with_name("app-icon.png")
img.save(output)
print(f"icon written to {output}")
