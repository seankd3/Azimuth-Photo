"""Adopting a Lightroom catalog: a one-time read, not an integration.

`docs/CORE.md` says Lightroom's channel to Azimuth is the photograph, and that
is right for everything Lightroom has been told to write out. It is not right
for what it has *not*: measured across this machine's three catalogs, **1,872
photographs are turned in Lightroom, shown flat here, and carry no orientation
in the file at all**, because Save Metadata to Files was never run on them. A
principle that loses 1,872 of the owner's decisions is incomplete.

So this is deliberately shaped as an **adoption**, the same category as the
oplog adoption that preceded it, and not as the runtime coupling that was
removed:

* it runs when asked, files what it finds into the log, and is then done;
* nothing in the request path ever reads a `.lrcat`, so Adobe changing their
  schema breaks a future import and nothing else;
* running it twice writes nothing the second time.

**The safety rule, and it is the whole of the design.** A rotation can already
be in the file — a camera's own flip, which `rawpy` honours, or pixels an
application rewrote. Adopting on top of that turns a correct photograph a
second time. Measured on ten frames Lightroom calls turned: six have `flip=0`
and are genuinely flat here, two have `flip=5`/`flip=6` and are already
upright. So:

> **Adopt only where Azimuth is currently showing the photograph the other way
> round from Lightroom.**

That compares the two presentations rather than trusting either source, which
is why it cannot double-rotate. A half turn is invisible to it and is
deliberately not adopted — there is no way to verify one, and a wrong 180 is
worse than a missing one.

Read-only, always. A catalog may be open in Lightroom while this runs.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter

from model import decisions

# Lightroom's corner-lettering, as clockwise degrees.
TURN = {"AB": 0, "BC": 90, "CD": 180, "DA": 270}

# Where catalogs live on this machine. Backups hold copies of the same
# decisions at older times and would only ever re-file something staler.
ROOTS = (r"D:\Pictures", r"C:\Pictures", r"E:\Photos")
SKIP = ("backup", "lightroom settings")
DEPTH = 5

# Astrophotography is out of Azimuth's scope by standing instruction: not
# scanned, not indexed, not touched. It is the `Astro/` tree specifically —
# "AstroAwards" is the name of an event and is ordinary photography.
def _out_of_scope(folder: str) -> bool:
    low = folder.replace("\\", "/").lower()
    return "/astro/" in low or "astrophotography" in low


_PHOTOGRAPHS = """
    SELECT i.orientation,
           rf.absolutePath || fo.pathFromRoot AS folder,
           f.baseName || '.' || f.extension  AS name
    FROM Adobe_images i
    JOIN AgLibraryFile f       ON f.id_local  = i.rootFile
    JOIN AgLibraryFolder fo    ON fo.id_local = f.folder
    JOIN AgLibraryRootFolder rf ON rf.id_local = fo.rootFolder
"""


def catalogs(roots: tuple[str, ...] = ROOTS) -> list[str]:
    """Every `.lrcat` under these roots, newest first."""

    found: list[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not any(word in d.lower() for word in SKIP)]
            # Catalogs sit near the top of a drive; walking whole photo trees
            # looking for them took seven minutes and found nothing deeper.
            if folder[len(root):].count(os.sep) >= DEPTH:
                dirs[:] = []
            found += [os.path.join(folder, n) for n in names if n.lower().endswith(".lrcat")]
    return sorted(set(found), key=os.path.getmtime, reverse=True)


def turned(catalog: str) -> dict[str, int]:
    """Which photographs this catalog says are turned, by file name.

    Keyed on the name rather than the path because the photograph has usually
    been filed into the taxonomy since — `C:/Pictures/2025/…/SKDA3311.dng` is
    now `Raws/Digital/2025/…/SKDA3311.dng`, and the name is what both agree on.
    A name claimed by more than one photograph is dropped rather than guessed.
    """

    out: dict[str, int] = {}
    seen = Counter()
    try:
        conn = sqlite3.connect(f"file:{catalog}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(_PHOTOGRAPHS).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}

    for row in rows:
        if _out_of_scope(str(row["folder"] or "")):
            continue
        name = str(row["name"] or "").lower()
        if not name:
            continue
        seen[name] += 1
        degrees = TURN.get(str(row["orientation"] or "AB"), 0)
        # A quarter turn is the only kind this can verify, so it is the only
        # kind recorded. See the module docstring.
        if degrees in (90, 270):
            out[name] = degrees
    return {name: degrees for name, degrees in out.items() if seen[name] == 1}


def _on_disk(conn, tail: str) -> str | None:
    """The file behind a tail, on whichever attached drive has it."""

    from model import drives

    for drive in conn.execute("SELECT uuid FROM drives ORDER BY is_record, id"):
        path = drives.path_for(conn, drive["uuid"], tail)
        if path and os.path.exists(path):
            return path
    return None


def adopt(conn, roots: tuple[str, ...] = ROOTS, *, apply: bool = False) -> dict:
    """File Lightroom's rotations into the log. Reports without writing unless asked.

    Only photographs Azimuth currently shows the other way round are touched,
    and only where the answer would actually change — a restatement makes
    `history()` useless for seeing what changed.
    """

    found: list[str] = catalogs(roots)
    wanted: dict[str, int] = {}
    for catalog in reversed(found):
        # Oldest first so a newer catalog's answer wins on collision.
        wanted.update(turned(catalog))
    if not wanted:
        return {"catalogs": found, "turned": 0, "adopted": 0, "already upright": 0}

    import render
    from photo import location

    adopted = already = unchanged = unreadable = measured = 0
    for row in conn.execute(
        "SELECT id, tail, width, height, content_hash AS hash FROM images"
        " WHERE tail IS NOT NULL AND content_hash IS NOT NULL"
    ).fetchall():
        degrees = wanted.get(str(row["tail"]).split("/")[-1].lower())
        if not degrees:
            continue
        width, height = row["width"] or 0, row["height"] or 0
        if not width or not height:
            # The rule needs to know which way up this is shown, and 2,424 of
            # the photographs Lightroom turned have never had their dimensions
            # read. Read them now and keep them: it is a fact the grid needs
            # anyway, and measuring only the candidates keeps it bounded.
            source = location.locate(conn, row["id"]) if hasattr(location, "locate") else None
            source = source or _on_disk(conn, str(row["tail"]))
            if not source:
                unreadable += 1
                continue
            try:
                width, height = render.dimensions(source)
            except Exception:
                unreadable += 1
                continue
            measured += 1
            if apply:
                conn.execute("UPDATE images SET width = ?, height = ? WHERE id = ?",
                             (width, height, row["id"]))
        if height > width:
            # Already standing up — the turn is in the file, and saying it
            # again would lay the photograph on its side.
            already += 1
            continue
        if int(decisions.latest(conn, row["hash"], decisions.ROTATE) or 0) == degrees:
            unchanged += 1
            continue
        if apply:
            decisions.decide(conn, row["hash"], decisions.ROTATE, degrees)
        adopted += 1

    if apply:
        conn.commit()
    return {
        "catalogs": [os.path.basename(c) for c in found],
        "turned": len(wanted),
        "adopted": adopted,
        "already upright": already,
        "unchanged": unchanged,
        "measured": measured,
        "unreadable": unreadable,
        "applied": bool(apply),
    }
