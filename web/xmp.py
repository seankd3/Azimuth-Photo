"""Sidecars: decisions made in another application.

Lightroom, darktable and Bridge all write an `.xmp` beside the photograph and
put the same three portable facts in it — a star rating, a colour label, an
orientation. Azimuth already has a place for exactly that kind of thing, so a
sidecar needs no importer, no mapping table and no sync state: **it is read,
and what it says is filed in the log like any other decision.**

Two rules make that safe, and they are the same two the log always had:

* **The owner's own decision wins.** A sidecar is filed with the time it was
  written, so `latest()` prefers whatever was decided most recently — and a
  star set here after a sidecar was written stays set. Nothing needs a
  precedence table because the log is already ordered.
* **Reading again is free.** The read is recorded as a cache entry keyed on the
  sidecar's modification time, so an unchanged sidecar is not re-read and a
  changed one is. That is the anti-join doing sync without a sync.

Deliberately only the portable fields. `crs:` develop settings are Adobe's
private edit format and this archive's sidecars are darktable's; pretending to
understand either would mean rendering someone else's edit wrongly, which is
worse than not rendering it. What the owner set — stars, labels, which way up —
transfers exactly.
"""

from __future__ import annotations

import os
import re

from model import decisions

# The three portable fields. Written by Lightroom, darktable and Bridge alike,
# and meaning the same thing in each.
_RATING = re.compile(r'xmp:Rating\s*=\s*"(-?\d+)"')
_LABEL = re.compile(r'xmp:Label\s*=\s*"([^"]*)"')
_ORIENT = re.compile(r'tiff:Orientation\s*=\s*"(\d+)"')

# EXIF orientation to the clockwise turn that puts it upright. 1 is upright,
# and the flipped states (2/4/5/7) have no rotation that fixes them, so they
# are left alone rather than half-corrected.
_TURN = {1: 0, 3: 180, 6: 90, 8: 270}


def sidecar_for(path: str) -> str | None:
    """The sidecar beside a photograph, whichever convention wrote it.

    Lightroom writes `IMG_1234.xmp` for a raw; darktable and Bridge write
    `IMG_1234.CR2.xmp`. Both are looked for because both turn up in one folder
    when a library has passed through more than one application.
    """

    for candidate in (f"{path}.xmp", f"{os.path.splitext(path)[0]}.xmp"):
        if os.path.exists(candidate):
            return candidate
    return None


def read(sidecar: str) -> dict:
    """The portable fields, or an empty dict if there is nothing we understand.

    Read as text rather than parsed as XML on purpose: these files carry vendor
    namespaces, embedded RDF bags and occasionally malformed entities, and a
    strict parser turns a photograph's rating into an exception. Three regexes
    cannot fail on a file they do not understand — they simply find nothing.
    """

    try:
        with open(sidecar, encoding="utf-8", errors="replace") as handle:
            text = handle.read(65536)
    except OSError:
        return {}

    out: dict = {}
    rating = _RATING.search(text)
    if rating:
        out["stars"] = max(0, min(5, int(rating.group(1))))
    label = _LABEL.search(text)
    if label and label.group(1).strip():
        out["label"] = label.group(1).strip()
    orient = _ORIENT.search(text)
    if orient and int(orient.group(1)) in _TURN:
        out["rotate"] = _TURN[int(orient.group(1))]
    return out


def adopt(conn, image_id: int, source: str) -> dict:
    """File a photograph's sidecar into the log. Returns what it found.

    Stamped with the sidecar's modification time, not with now, so the log
    orders it against the owner's own decisions correctly: an edit made here
    yesterday beats a sidecar written last week, and a sidecar written this
    morning beats a star set last year. That is the whole precedence rule, and
    it is just the log being a log.
    """

    sidecar = sidecar_for(source)
    if not sidecar:
        return {}
    found = read(sidecar)
    if not found:
        return {}

    row = conn.execute(
        "SELECT content_hash AS hash FROM images WHERE id = ?", (int(image_id),)
    ).fetchone()
    if row is None or not row["hash"]:
        return {}

    when = os.path.getmtime(sidecar)
    if "stars" in found:
        decisions.decide(conn, row["hash"], decisions.STAR, found["stars"], at=when)
    if "rotate" in found:
        decisions.decide(conn, row["hash"], decisions.ROTATE, found["rotate"], at=when)
    if "label" in found:
        decisions.decide(conn, row["hash"], "flag", found["label"], at=when)
    return found
