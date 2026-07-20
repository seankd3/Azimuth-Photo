#!/usr/bin/env python3
"""Resize period captures into web-ready plates for the devlog."""
import os
from PIL import Image
SB = r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog"
OUT = os.path.join(SB, "build", "assets", "screens")
os.makedirs(OUT, exist_ok=True)

JOBS = [
    ("captures/G1/photoranker.png",                 "01-photoranker-2023.jpg", 1680, 86),
    ("captures/M1/compare.png",                     "02-web-mosaic.jpg",       1680, 84),
    ("captures/M1/rankings.png",                    "02b-web-rankings.jpg",    1680, 84),
    ("captures/M2/library.png",                     "03-library-apr21.jpg",    1680, 84),
    ("assets/reference-screens-jul8/library-grid.jpg", "04-library-grid.jpg", 1680, 84),
    ("assets/reference-screens-jul8/loupe.jpg",        "05-loupe.jpg",        1680, 84),
    ("assets/reference-screens-jul8/loupe-lights-out.jpg","06-loupe-lights-out.jpg",1680,84),
    ("assets/reference-screens-jul8/refine-mosaic.jpg","07-refine-mosaic.jpg",1680, 84),
    ("assets/reference-screens-jul8/mobile-library.jpg","08-mobile.jpg",       900, 86),
    ("captures/M9/grid.png",                        "11-current-grid.jpg",     1680, 84),
    # develop/film added after interactive capture:
    ("captures/DEV/develop.png",                    "09-develop.jpg",          1680, 86),
    ("captures/DEV/film.png",                       "10-film.jpg",             1680, 86),
]

for src, name, w, q in JOBS:
    p = os.path.join(SB, src)
    if not os.path.exists(p):
        print("skip (missing):", src); continue
    im = Image.open(p).convert("RGB")
    if im.width > w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    im.save(os.path.join(OUT, name), "JPEG", quality=q, optimize=True, progressive=True)
    print(f"{name:28} {im.size}  {os.path.getsize(os.path.join(OUT,name))//1024}KB")
