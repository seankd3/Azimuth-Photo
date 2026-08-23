"""Develop: Lightroom's own settings, as decisions.

An edit is one decision (family ``develop``) whose value is the photograph's
crs settings — the exact keys and spellings Lightroom writes into a sidecar,
so import and export are transcription, never translation. The newest
authoritative decision is the photograph's current edit; history and undo are
the log, the same as stars and rotation.

The renderable part of the edit is projected to ``images.develop`` as a
canonical JSON fragment (today: the crop rectangle), which is what lets a
rendition's cache recipe be built per photograph *in SQL* — the tile of an
edited photograph is a different recipe because it is different pixels, and
the tile of an untouched one keeps the exact recipe it always had.

Sidecars are read on the sweep's rhythm: the walk already sees every ``.xmp``
beside a photograph, and a sidecar whose settings differ from the last
file-authored decision appends a new one. A sidecar is the ``file`` author,
like every fact read from one, and the owner's own in-app answer outranks
it — the same one rule the whole decision log lives by.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET

from model import decisions

FAMILY = decisions.DEVELOP
BY_FILE = decisions.FILE

# The crop rectangle, in Lightroom's spelling: unit coordinates of the
# oriented image. CropAngle rides along untouched; every one of the 1,138
# angle values in the real library is exactly 0, so rendering it waits for
# a measured fixture rather than a guessed dialect.
CROP_KEYS = ("CropLeft", "CropTop", "CropRight", "CropBottom")

_CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def sidecar_path(photo_path: str) -> str:
    """Lightroom's sidecar name: the photograph's stem plus ``.xmp``."""

    return os.path.splitext(photo_path)[0] + ".xmp"


def read_sidecar(path: str) -> dict[str, object] | None:
    """Every crs fact in one sidecar, keys spelled as Lightroom spells them.

    Scalars arrive as strings exactly as written (``"+0.50"`` stays
    ``"+0.50"`` — round-tripping is transcription). Array-valued settings
    (tone curves, point colors, looks) arrive as lists of their item
    strings. Returns None for a file that is not a crs sidecar.
    """

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    found: dict[str, object] = {}
    for description in root.iter(f"{{{_RDF}}}Description"):
        for name, value in description.attrib.items():
            if name.startswith(f"{{{_CRS}}}"):
                found[name[len(_CRS) + 2:]] = value
        for child in description:
            if not child.tag.startswith(f"{{{_CRS}}}"):
                continue
            key = child.tag[len(_CRS) + 2:]
            items = [li.text or "" for li in child.iter(f"{{{_RDF}}}li")]
            if items:
                found[key] = items
            elif child.text and child.text.strip():
                found[key] = child.text.strip()
    return found or None


def settings(conn, digest: str) -> dict:
    """The photograph's current edit — the newest authoritative decision."""

    said = decisions.latest(conn, str(digest), FAMILY)
    return said if isinstance(said, dict) else {}


def fragment(held: dict) -> str | None:
    """The renderable geometry of an edit, as the canonical recipe fragment.

    None when the edit changes no pixels a rendition shows — a full-frame
    crop is not an edit, and a photograph without one keeps the recipe (and
    the tiles) it always had.
    """

    try:
        crop = [round(float(held.get(key, default)), 6)
                for key, default in zip(CROP_KEYS, (0.0, 0.0, 1.0, 1.0))]
    except (TypeError, ValueError):
        return None
    left, top, right, bottom = crop
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        return None
    if crop == [0.0, 0.0, 1.0, 1.0]:
        return None
    return json.dumps(crop, separators=(",", ":"))


def project(conn, digest: str) -> str | None:
    """Write the photograph's current fragment to its rows' develop column.

    Returns the fragment. Every row of the identity takes it — a photo filed
    in two folders is one photograph, edited once.
    """

    held = fragment(settings(conn, digest))
    conn.execute(
        "UPDATE images SET develop = ? WHERE content_hash = ?", (held, str(digest)))
    return held


def reindex(conn) -> int:
    """Rebuild every develop column from the log — the boot-time answer to
    'the log is the truth and the column is an index over it'."""

    projected = 0
    conn.execute("UPDATE images SET develop = NULL WHERE develop IS NOT NULL")
    for row in conn.execute(
        "SELECT DISTINCT subject FROM decisions WHERE family = ?", (FAMILY,)):
        if project(conn, row["subject"]) is not None:
            projected += 1
    conn.commit()
    return projected


def edit(conn, digest: str, patch: dict) -> dict:
    """The owner changes an edit: the current settings plus this patch, as
    one appended decision. A None value removes its key."""

    held = dict(settings(conn, digest))
    for key, value in patch.items():
        if value is None:
            held.pop(key, None)
        else:
            held[key] = value
    decisions.decide(conn, str(digest), FAMILY, held)
    project(conn, digest)
    conn.commit()
    return held


def adopt(conn, root: str, sidecar_tails) -> int:
    """Read the sweep's sidecars into decisions.

    ``sidecar_tails`` is what the walk saw; each pairs to its photograph by
    stem. A sidecar whose settings differ from the last Lightroom-authored
    decision appends one — unchanged files append nothing, so re-sweeping
    is free.
    """

    tails = [str(tail) for tail in sidecar_tails or ()]
    if not tails:
        return 0
    stems: dict[str, str] = {}
    for row in conn.execute(
        "SELECT tail, content_hash FROM images"
        " WHERE tail IS NOT NULL AND content_hash IS NOT NULL"):
        stems[os.path.splitext(row["tail"])[0]] = row["content_hash"]
    adopted = 0
    for tail in tails:
        digest = stems.get(os.path.splitext(tail)[0])
        if digest is None:
            continue
        held = read_sidecar(os.path.join(root, tail.replace("/", os.sep)))
        if held is None:
            continue
        before = _last_adopted(conn, digest)
        if before == held:
            continue
        decisions.decide(conn, digest, FAMILY, held, by=BY_FILE)
        project(conn, digest)
        adopted += 1
    if adopted:
        conn.commit()
    return adopted


def _last_adopted(conn, digest: str) -> dict | None:
    row = conn.execute(
        "SELECT value FROM decisions WHERE subject = ? AND family = ? AND by = ?"
        " ORDER BY at DESC, id DESC LIMIT 1",
        (str(digest), FAMILY, BY_FILE),
    ).fetchone()
    said = decisions.loaded(row)
    return said if isinstance(said, dict) else None


def crop_of(fragment_json: str | None) -> tuple[float, float, float, float] | None:
    """A stored fragment back as (left, top, right, bottom), or None."""

    if not fragment_json:
        return None
    try:
        left, top, right, bottom = (float(v) for v in json.loads(fragment_json))
    except (TypeError, ValueError):
        return None
    return (left, top, right, bottom)


_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")


def spell(value: float) -> str:
    """A slider value in Lightroom's own spelling — signed, trimmed."""

    text = f"{value:+.6f}".rstrip("0").rstrip(".")
    return text if _NUMBER.match(text) else f"{value:+.2f}"
