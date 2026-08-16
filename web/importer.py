"""Import: `identify` + `put`, over one pure function.

> `(date, kind, roll) -> tail`

That is the whole of it, and the reason importing stops being a subsystem. The
old one was 2,895 lines across staging, journals, batches, relocation and
routes, because it owned *moving files safely* as well as *deciding where they
go*. The first half is `photos.put` — write to a staging name, prove the bytes,
then rename — so what is left here is the decision.

**The shape came off the disk, not out of the old code.** Counted over 144,271
tails: 83,770 are `Raws/Digital/{YYYY}/{YYYY-MM-DD}/{file}` and the next
several thousand are the same with a project between the year and the day. So
that is the rule, and it is one f-string.

Three things in here were paid for and must not be re-derived:

* **Film scanners stamp every frame `2026-01-01`.** The EXIF date of a scan is
  the date of the *scan*, and using it files a 1998 wedding under this year.
  The roll's real day comes from the lab's own archive name.
* **An inferred date is stamped at noon**, never midnight, so that a timezone
  shift of a few hours cannot move it to the day before.
* **A collision gets a `-N` suffix and never an overwrite.** 5,460 tails in the
  archive carry one, so this is the normal case rather than the odd one.
"""

from __future__ import annotations

import datetime as dt
import os
import re

from model import photos
from photo import kind

# The four roots. Import may never mint a fifth: a stray top-level sibling is
# how an archive grows a folder nobody meant to create and nothing sweeps.
DIGITAL = "Raws/Digital"
FILM = "Raws/Film Scans"
EDITS = "Edits"
SNAPSHOTS = "Snapshots"
VIDEO = "Video"

FILM_EXTENSIONS = frozenset({".tif", ".tiff"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".m4v"})
PHONE_EXTENSIONS = frozenset({".heic", ".heif"})

# A lab writes the roll's date into the folder it hands back. This is the only
# trustworthy date a film scan has.
_DATE_IN_NAME = re.compile(r"(19|20)\d{2}[-_]?\d{2}[-_]?\d{2}")


def root_for(filename: str, *, source: str | None = None) -> str:
    """Which of the four roots a file belongs under.

    Provenance beats extension wherever the two disagree, because a phone
    shoots DNG and a camera does not shoot HEIC — deciding by name alone files
    a Pixel's raw beside a 45 MP studio frame.
    """

    if source in {"video", "export", "film", "phone"}:
        return {"video": VIDEO, "export": EDITS, "film": FILM, "phone": SNAPSHOTS}[source]

    extension = kind.extension(filename)
    if extension in VIDEO_EXTENSIONS:
        return VIDEO
    if extension in FILM_EXTENSIONS:
        return FILM
    if extension in PHONE_EXTENSIONS:
        return SNAPSHOTS
    return DIGITAL


def roll_date(name: str) -> dt.date | None:
    """The day a roll was shot, read from the lab's archive name.

    A film scan's EXIF says the day it was *scanned* — every frame of every
    roll stamped identically, `2026-01-01` in this archive — so the name is
    the only place the real date survives.
    """

    found = _DATE_IN_NAME.search(name or "")
    if not found:
        return None
    digits = re.sub(r"[^0-9]", "", found.group(0))
    try:
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def at_noon(day: dt.date) -> dt.datetime:
    """An inferred date, stamped where a timezone cannot move it.

    Midnight plus any westward shift is the previous day, which is how a shoot
    lands in the wrong folder and the wrong year.
    """

    return dt.datetime(day.year, day.month, day.day, 12, 0, 0)


def tail_for(filename: str, day: dt.date, *, source: str | None = None,
             roll: str | None = None) -> str:
    """Where this photograph belongs. The pure function the whole surface is.

    `{root}/{YYYY}/[{roll}/]{YYYY-MM-DD}/{filename}` — the shape 83,770 of the
    archive's own tails already have.
    """

    root = root_for(filename, source=source)
    parts = [root, f"{day.year:04d}"]
    if roll:
        parts.append(roll.strip("/ "))
    parts.append(day.isoformat())
    parts.append(os.path.basename(filename))
    return "/".join(parts)


def without_collision(conn, drive_uuid: str, tail: str) -> str:
    """The same tail, or the next free `-N` one.

    Suffixing rather than overwriting is not politeness, it is the only safe
    option: two cameras produce `IMG_0028.CR2` on the same day routinely, and
    the loser of an overwrite is off the card by the time anyone notices.
    """

    from model import drives

    stem, extension = os.path.splitext(tail)
    candidate, n = tail, 1
    while True:
        path = drives.path_for(conn, drive_uuid, candidate)
        if path is None or not os.path.exists(path):
            return candidate
        candidate = f"{stem}-{n}{extension}"
        n += 1


def import_file(conn, source_path: str, drive_uuid: str, *, day: dt.date | None = None,
                source: str | None = None, roll: str | None = None) -> dict:
    """Bring one file into the library. Decide the tail, then `put` it.

    Returns whatever `put` returned, plus the tail chosen — so a caller that
    wants to record the copy has everything without asking again.
    """

    if day is None:
        day = roll_date(roll or os.path.basename(source_path)) or dt.date.today()

    tail = without_collision(conn, drive_uuid, tail_for(source_path, day, source=source, roll=roll))
    result = photos.put(conn, source_path, drive_uuid, tail)
    result["tail"] = tail
    return result
