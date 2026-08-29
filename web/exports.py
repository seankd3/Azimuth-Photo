"""An export is the photograph, rendered: a full-resolution JPEG of what
the library shows — the edit applied, the turn honoured — plus the facts a
JPEG should carry (when, what camera) so the file stands on its own.

Originals never leave through here; they never left the drives at all. And
the pixels come from the same one render path every tile uses, so an export
can never disagree with the screen.
"""

from __future__ import annotations

import os

import develop as developing
import render
from model import photos

QUALITY = 92


def jpeg(conn, photo_id: int, destination: str) -> str:
    """Render one photograph into `destination`. Returns the outcome word:
    exported / missing / failed."""

    row = conn.execute(
        "SELECT content_hash AS hash, tail, file_size, rotate, develop,"
        " date_taken, camera_make, camera_model FROM images WHERE id = ?",
        (int(photo_id),)).fetchone()
    if row is None or not row["hash"] or not row["tail"]:
        return "missing"
    source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
    if source is None:
        return "missing"

    stem = os.path.splitext(os.path.basename(row["tail"]))[0]
    target = os.path.join(destination, f"{stem}.jpg")
    suffix = 2
    while os.path.exists(target):
        target = os.path.join(destination, f"{stem}-{suffix}.jpg")
        suffix += 1

    from PIL import Image

    exif = Image.Exif()
    if row["date_taken"]:
        exif[0x9003] = row["date_taken"].replace("-", ":", 2)
    if row["camera_make"]:
        exif[0x010F] = row["camera_make"]
    if row["camera_model"]:
        exif[0x0110] = row["camera_model"]
    try:
        image = render.pixels(source, size=render.FULL, rotate=int(row["rotate"] or 0),
                              edit=developing.parts(row["develop"]) or None)
        image.save(target, "JPEG", quality=QUALITY, exif=exif.tobytes())
    except Exception:  # noqa: BLE001 — one bad frame must not end the batch
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        return "failed"
    return "exported"
