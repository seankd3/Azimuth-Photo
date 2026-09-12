"""The photographic facts the semantic embedding throws away.

A CLIP-family vector says *what* a photograph is and normalizes away *how it
looks* — palette, tonality, grain, where the sharpness sits. These are
exactly the qualities a taste model needs beside it and a person filters by,
and none of them needs a model: they are milliseconds of arithmetic over the
grid tile the library already keeps.

One cache kind, one small JSON of orthogonal numbers, and one derived word:
``look`` — ``color``, ``bw`` or ``sepia`` — because chroma statistics answer
that question outright. The facts stay separate numbers so every consumer
weights them itself; the word exists because "black & white" is vocabulary,
not a blend.
"""

from __future__ import annotations

import json

# The classification lines, in mean-chroma units over *lit* pixels
# (0..255 scale). Blackness says nothing about toning — a night scene's
# shadows are neutral in any photograph — so the look is judged where there
# is light to carry a tint. A digital B&W is chroma ~0 there; a sepia tone
# is modest chroma gathered on one warm hue; everything else is color.
GRAY = 3.0
TONED = 26.0
LIT = 30.0
WARM = (15.0, 95.0)


def measure(path: str) -> dict:
    """Every fact, from one tile file."""

    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        image.draft("RGB", (256, 256))
        small = np.asarray(image.convert("RGB"), dtype=np.float32)

    r, g, b = small[..., 0], small[..., 1], small[..., 2]
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    # Opponent chroma: cheap, and zero exactly when r=g=b.
    chroma = np.sqrt((r - g) ** 2 + (0.5 * (r + g) - b) ** 2)
    hue = np.degrees(np.arctan2(0.5 * (r + g) - b, r - g)) % 360.0

    lit = luminance > LIT
    seen_chroma = chroma[lit] if lit.any() else chroma
    seen_hue = hue[lit] if lit.any() else hue
    colored = seen_chroma > GRAY * 2
    if colored.any():
        # The circular mean of hue where there is color to speak of, and how
        # tightly it gathers: 1.0 is one hue everywhere (a toned photograph).
        radians = np.radians(seen_hue[colored])
        vector = complex(np.cos(radians).mean(), np.sin(radians).mean())
        hue_mode = float(np.degrees(np.arctan2(vector.imag, vector.real)) % 360.0)
        hue_focus = float(abs(vector))
    else:
        hue_mode, hue_focus = 0.0, 0.0

    tiny = luminance[::4, ::4]
    sharp = float(np.abs(np.diff(tiny, axis=0)).mean() + np.abs(np.diff(tiny, axis=1)).mean())

    chroma_mean = float(seen_chroma.mean())
    facts = {
        "lum_mean": round(float(luminance.mean()), 2),
        "lum_std": round(float(luminance.std()), 2),
        "clip_lo": round(float((luminance < 4).mean()), 4),
        "clip_hi": round(float((luminance > 251).mean()), 4),
        "chroma_mean": round(chroma_mean, 2),
        "chroma_p90": round(float(np.percentile(seen_chroma, 90)), 2),
        "hue_mode": round(hue_mode, 1),
        "hue_focus": round(hue_focus, 3),
        "sharp": round(sharp, 3),
    }
    facts["look"] = (
        "bw" if chroma_mean <= GRAY
        else "sepia" if (chroma_mean <= TONED and hue_focus >= 0.9
                         and WARM[0] <= hue_mode <= WARM[1])
        else "color")
    return facts


def kind(tiles):
    """The facts as a cache capability, computed from the grid tile —
    everywhere, on the CPU, and cheap enough never to think about."""

    import render
    from model import cache

    def compute(source, hash):
        made = json.dumps(measure(source), separators=(",", ":"), sort_keys=True)
        return cache.Made(value=made, bytes=len(made.encode("utf-8")))

    return cache.Kind(
        name="photostats",
        compute=compute,
        cost=0.03,
        wants=tiles.ready.sql,
        source=lambda conn, row: tiles.path(row["hash"], render.GRID),
    )
