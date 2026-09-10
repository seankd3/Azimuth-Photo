"""Where the sharpness sits: the subject against the frame, as numbers.

Every classical focus measure confounds low texture with defocus and most of
them reward noise, so nothing here is an absolute score and nothing here is
a verdict. Two facts per photograph, one per face, all relative:

* a map of local variation over the 1,024 px tile in 32 px blocks, kept as
  its 50th, 75th and 90th percentiles -- the frame's own texture;
* for the largest face, the map inside its box against the frame's 75th
  percentile -- the subject ratio: above one the subject is sharper than
  its surroundings (a portrait against bokeh), below it the focus missed;
* for every face, Zhu & Milanfar's gradient-covariance measure on the face
  crop from the 4,096 px rendition (the one measure that penalises noise as
  well as blur), with the crop's size in pixels as its reliability gate.

The facts are read by the inspector and, later, weighted by the fit on the
owner's own rounds -- never blended into a score here. Research:
docs/taste-and-culling-research.md.
"""

from __future__ import annotations

import json

KEY = "zm2"          # the measure's version; a change re-owes every answer
BLOCK = 32           # the map's grain on the 1,024 px tile
FACE_LEAST = 48      # a face crop narrower than this says nothing reliable


def _grey(image, longest: int):
    import numpy as np

    held = image.copy()
    held.draft("L", (longest, longest))
    held.thumbnail((longest, longest))
    return np.asarray(held.convert("L"), dtype=np.float32)


def _mlv_map(grey):
    """Max local variation per pixel, then the 90th percentile per block."""

    import numpy as np

    pad = np.pad(grey, 1, mode="edge")
    centre = pad[1:-1, 1:-1]
    most = np.zeros_like(centre)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            shifted = pad[1 + dy:pad.shape[0] - 1 + dy, 1 + dx:pad.shape[1] - 1 + dx]
            np.maximum(most, np.abs(centre - shifted), out=most)
    rows = most.shape[0] // BLOCK
    cols = most.shape[1] // BLOCK
    if rows == 0 or cols == 0:
        return np.asarray([[float(np.percentile(most, 90))]])
    blocks = most[:rows * BLOCK, :cols * BLOCK].reshape(rows, BLOCK, cols, BLOCK).transpose(0, 2, 1, 3)
    return np.percentile(blocks.reshape(rows, cols, -1), 90, axis=2)


def _zhu_milanfar(grey) -> float:
    """Zhu & Milanfar's measure, per 8 px patch: s1 times the coherence of
    the patch's gradient covariance, high where an edge is sharp, low where
    it is blurred *and* where there is only noise. The crop's number is the
    90th percentile over its patches -- its sharpest structure -- since a
    face is mostly smooth skin and a whole-crop covariance is isotropic."""

    import numpy as np

    if grey.shape[0] < 8 or grey.shape[1] < 8:
        return 0.0
    gy, gx = np.gradient(grey)
    rows, cols = grey.shape[0] // 8, grey.shape[1] // 8

    def patched(field):
        return field[:rows * 8, :cols * 8].reshape(rows, 8, cols, 8).mean(axis=(1, 3))

    a = patched(gx * gx)
    b = patched(gx * gy)
    c = patched(gy * gy)
    half = (a + c) / 2.0
    root = np.sqrt(np.maximum(0.0, half * half - (a * c - b * b)))
    s1 = np.sqrt(np.maximum(0.0, half + root))
    s2 = np.sqrt(np.maximum(0.0, half - root))
    q = s1 * (s1 - s2) / (s1 + s2 + 1e-6)
    return round(float(np.percentile(q, 90)), 3)


def measure(path: str, boxes=()) -> dict:
    """The facts, from one rendition and the face boxes already found on
    it (fractions of the frame: x, y, w, h)."""

    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        grey = _grey(image, 1024)
        faces = []
        for box in boxes:
            x, y, w, h = (float(v) for v in box)
            left, top = int(x * width), int(y * height)
            right, bottom = int((x + w) * width), int((y + h) * height)
            if right - left < FACE_LEAST or bottom - top < FACE_LEAST:
                faces.append({"q": None, "px": min(right - left, bottom - top), "box": box})
                continue
            crop = np.asarray(image.crop((left, top, right, bottom)).convert("L"), dtype=np.float32)
            faces.append({"q": _zhu_milanfar(crop), "px": min(right - left, bottom - top), "box": box})
    amount = _mlv_map(grey)
    frame = {
        "p50": round(float(np.percentile(amount, 50)), 2),
        "p75": round(float(np.percentile(amount, 75)), 2),
        "p90": round(float(np.percentile(amount, 90)), 2),
    }
    subject = None
    if boxes:
        x, y, w, h = (float(v) for v in max(boxes, key=lambda b: float(b[2]) * float(b[3])))
        rows, cols = amount.shape
        r0, r1 = int(y * rows), max(int(y * rows) + 1, int((y + h) * rows))
        c0, c1 = int(x * cols), max(int(x * cols) + 1, int((x + w) * cols))
        inside = amount[r0:r1, c0:c1]
        if inside.size and frame["p75"] > 0:
            subject = round(float(np.percentile(inside, 75)) / frame["p75"], 3)
    return {"frame": frame, "faces": faces, "subject": subject}


def kind(tiles, faces_kind):
    """Sharpness as a cache capability: computed on the CPU from the loupe
    rendition when it exists (the eyes need the pixels) and the grid tile
    otherwise, with the faces the face pass already found."""

    import render
    from model import cache

    def compute(source, hash, model):
        path, boxes = source
        made = json.dumps(measure(path, boxes), separators=(",", ":"), sort_keys=True)
        return cache.Made(value=made, bytes=len(made.encode("utf-8")))

    def source(conn, row):
        import os

        import faces as facing

        loupe = tiles.path(row["hash"], render.LOUPE)
        path = loupe if os.path.isfile(loupe) else tiles.path(row["hash"], render.GRID)
        held = cache.get(conn, row["hash"], faces_kind, {"model": facing.KEY})
        boxes = []
        if held is not None and held.get("state") == cache.READY and held.get("value"):
            boxes, _scores, _matrix = facing.unpack(held["value"])
        return (path, boxes)

    return cache.Kind(
        name="sharpness",
        compute=compute,
        cost=0.2,
        params=("model",),
        ahead=lambda: ({"model": KEY},),
        evictable=False,
        wants=tiles.ready.sql,
        source=source,
    )


def of(conn, digest: str) -> dict | None:
    """The facts as last written, or None until the pass has been there."""

    row = conn.execute(
        "SELECT value FROM cache WHERE kind = 'sharpness' AND hash = ? AND state = 'ready'"
        " ORDER BY at DESC LIMIT 1", (digest,)).fetchone()
    if row is None or not row["value"]:
        return None
    held = row["value"]
    return json.loads(held if isinstance(held, str) else bytes(held).decode("utf-8"))
