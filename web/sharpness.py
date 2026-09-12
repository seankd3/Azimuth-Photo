"""Where the sharpness sits: the subject against the frame, as numbers.

Every classical focus measure confounds low texture with defocus and most of
them reward noise, so nothing here is an absolute score and nothing here is
a verdict. Two facts per photograph, one per face, all relative:

* a map of local variation over the loupe rendition at 1,024 px in 32 px
  blocks, kept as its 50th, 75th and 90th percentiles -- the frame's own
  texture, and its noise: the map does not tell them apart, which is why
  it is only ever read as a ratio;
* for the largest face, the map inside its box against the frame's 75th
  percentile -- the subject ratio: above one the subject is sharper than
  its surroundings (a portrait against bokeh), below it the focus missed;
* for every face, Zhu & Milanfar's gradient-covariance measure on the face
  crop from the 4,096 px rendition (the one measure that penalises noise as
  well as blur), with the crop's size in pixels as its reliability gate;
* for every face large enough, each eye's openness and sharpness from the
  106 landmarks, and one word for the largest face's eyes.

Always the 4,096 px loupe rendition, never a stand-in: the same recipe must
be the same answer for every photograph, or the numbers cannot be compared
across the library.

The facts are read by the inspector and, later, weighted by the fit on the
owner's own rounds -- never blended into a score here. Research:
docs/taste-and-culling-research.md.
"""

from __future__ import annotations

import json

KEY = "zm4"          # the measure's version; a change re-owes every answer
RECIPE = json.dumps({"model": KEY}, separators=(",", ":"), sort_keys=True)
BLOCK = 32           # the map's grain on the 1,024 px tile
FACE_LEAST = 48      # a face crop narrower than this says nothing reliable
EYE_LEAST = 20       # an eye narrower than this cannot be read either way
# Openness is the eye contour's height over its width: an open eye reads
# about 0.3-0.5, a closed one about 0.1; between is "can't tell".
OPEN = 0.25
CLOSED = 0.15


def _grey(path: str, longest: int):
    """The frame at `longest`, decoded at that scale (a JPEG decodes at a
    quarter of its size for a quarter of the work when asked before it is
    loaded, which a copy of a loaded image cannot be)."""

    import numpy as np
    from PIL import Image

    with Image.open(path) as held:
        held.draft("L", (longest, longest))
        held.thumbnail((longest, longest))
        return np.asarray(held.convert("L"), dtype=np.float32)


def _clamped(left, top, right, bottom, width, height):
    """A box held inside the frame; a box off the frame's edge is measured
    on what is there, not on black padding, and a box wholly off it is
    empty."""

    x0, y0 = min(max(0, int(left)), int(width)), min(max(0, int(top)), int(height))
    x1, y1 = min(max(x0, int(right)), int(width)), min(max(y0, int(bottom)), int(height))
    return (x0, y0, x1, y1)


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


def openness(points) -> float:
    """An eye contour's height over its width."""

    import numpy as np

    pts = np.asarray(points, dtype=np.float32)
    width = float(pts[:, 0].max() - pts[:, 0].min())
    height = float(pts[:, 1].max() - pts[:, 1].min())
    return round(height / width, 3) if width > 0 else 0.0


def eyes_said(eyes) -> str | None:
    """Open, closed, or unsure -- from both eyes, never from one, and never
    from an eye too small to read (the tri-state the culling tools settled
    on, because a wrong "closed" costs a keeper)."""

    readable = [e for e in eyes if e.get("px", 0) >= EYE_LEAST and e.get("open") is not None]
    if len(readable) < 2:
        return "unsure" if eyes else None
    if all(e["open"] >= OPEN for e in readable):
        return "open"
    if all(e["open"] <= CLOSED for e in readable):
        return "closed"
    return "unsure"


def _eyes(image, frame_bgr, left, top, right, bottom):
    """Both eyes of one face: openness and sharpness from the 2d106
    contours, or nothing when the landmark model is not here."""

    import numpy as np

    import faces as facing

    marks = facing.landmarks(frame_bgr, (left, top, right, bottom))
    if marks is None:
        return []
    out = []
    for contour in (marks[facing.RIGHT_EYE], marks[facing.LEFT_EYE]):
        x0, y0 = contour.min(axis=0)
        x1, y1 = contour.max(axis=0)
        pad = 0.4 * max(x1 - x0, y1 - y0)
        box = _clamped(x0 - pad, y0 - pad, x1 + pad, y1 + pad, image.width, image.height)
        px = int(min(box[2] - box[0], box[3] - box[1]))
        q = None
        if px >= EYE_LEAST:
            crop = np.asarray(image.crop(box).convert("L"), dtype=np.float32)
            q = _zhu_milanfar(crop)
        out.append({"open": openness(contour), "q": q, "px": px})
    return out


def measure(path: str, boxes=()) -> dict:
    """The facts, from one rendition and the face boxes already found on
    it (fractions of the frame: x, y, w, h)."""

    import numpy as np
    from PIL import Image

    grey = _grey(path, 1024)
    with Image.open(path) as image:
        width, height = image.size
        faces = []
        frame_bgr = None
        for box in boxes:
            x, y, w, h = (float(v) for v in box)
            left, top, right, bottom = _clamped(x * width, y * height, (x + w) * width, (y + h) * height, width, height)
            if right - left < FACE_LEAST or bottom - top < FACE_LEAST:
                faces.append({"q": None, "px": min(right - left, bottom - top), "box": box, "eyes": []})
                continue
            crop = np.asarray(image.crop((left, top, right, bottom)).convert("L"), dtype=np.float32)
            if frame_bgr is None:
                # Contiguous, or OpenCV inside the landmark model throws on the
                # reversed channel stride (an unknown C++ exception, on real faces).
                frame_bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
            eyes = _eyes(image, frame_bgr, left, top, right, bottom)
            faces.append({"q": _zhu_milanfar(crop), "px": min(right - left, bottom - top), "box": box, "eyes": eyes})
    amount = _mlv_map(grey)
    frame = {
        "p50": round(float(np.percentile(amount, 50)), 2),
        "p75": round(float(np.percentile(amount, 75)), 2),
        "p90": round(float(np.percentile(amount, 90)), 2),
        # The rendition's own size: a small original is measured at its own
        # pixels, and the fit must know the scale a number was read at.
        "longest": int(max(width, height)),
    }
    subject = None
    if boxes:
        x, y, w, h = (float(v) for v in max(boxes, key=lambda b: float(b[2]) * float(b[3])))
        rows, cols = amount.shape
        r0 = max(0, int(y * rows))
        c0 = max(0, int(x * cols))
        r1, c1 = max(r0 + 1, int((y + h) * rows)), max(c0 + 1, int((x + w) * cols))
        inside = amount[r0:r1, c0:c1]
        if inside.size and frame["p75"] > 0:
            subject = round(float(np.percentile(inside, 75)) / frame["p75"], 3)
    # The largest face's eyes are the photograph's: one word, or none.
    largest = max(faces, key=lambda f: f["px"]) if faces else None
    return {"frame": frame, "faces": faces, "subject": subject,
            "eyes": eyes_said(largest["eyes"]) if largest and largest.get("eyes") else None}


def kind(tiles, faces_kind):
    """Sharpness as a cache capability: computed on the CPU from the loupe
    rendition once it exists (the eyes need the pixels; a stand-in would be
    a different answer under the same recipe), with the faces the face pass
    already found, on a machine that has the landmark model."""

    import faces as facing
    import render
    from model import cache

    def compute(source, hash, model):
        path, boxes = source
        made = json.dumps(measure(path, boxes), separators=(",", ":"), sort_keys=True)
        return cache.Made(value=made, bytes=len(made.encode("utf-8")))

    def source(conn, row):
        import os

        import faces as facing

        path = tiles.path(row["hash"], render.LOUPE)
        if not os.path.isfile(path):
            return None   # the row says ready but the file is gone: owed, not failed
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
        wants=tiles.made(tiles.loupe).sql,
        here=facing.ready,
        source=source,
    )


def tidy(conn) -> int:
    """The rows of a measure no longer asked for, gone: never evicted and
    never read, they would sit in the catalog for good."""

    gone = conn.execute("DELETE FROM cache WHERE kind = 'sharpness' AND recipe != ?", (RECIPE,)).rowcount
    # A failed row is re-owed: the measure that failed on a real face (a
    # non-contiguous frame handed to the landmark model) is fixed, and a
    # failure is never a fact worth keeping about a photograph.
    gone += conn.execute("DELETE FROM cache WHERE kind = 'sharpness' AND state = 'failed'").rowcount
    return gone


def of(conn, digest: str) -> dict | None:
    """The facts as last written, or None until the pass has been there."""

    row = conn.execute(
        "SELECT value FROM cache WHERE kind = 'sharpness' AND hash = ? AND recipe = ? AND state = 'ready'",
        (digest, RECIPE)).fetchone()
    if row is None or not row["value"]:
        return None
    held = row["value"]
    return json.loads(held if isinstance(held, str) else bytes(held).decode("utf-8"))
