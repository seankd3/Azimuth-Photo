"""Bringing photographs in: one destination rule, one verified copy each.

Import is `photos.put` per file plus one pure function from what a photograph
is to where it belongs, which is why it is not a subsystem. The owner decided
the taxonomy: three roots, by what a photograph *is* --

    Raws/Digital/YYYY/YYYY-MM-DD/<name>            a camera's own file
    Raws/Film Scans/YYYY/YYYY-MM-DD/<Roll>/<name>  a lab's scan, by roll
    Snapshots/YYYY/YYYY-MM-DD/<name>               a phone's, or anything casual
    Edits/YYYY/YYYY-MM-DD/<name>                   a finished picture

-- with the date the photograph's own, else the file's. Nothing is asked that
has a right answer; the kind comes from the source and is confirmed, and the
one question with no right answer (a folder of JPEGs) is the one asked.

What `bring` promises: the copy is complete and byte-verified before anything
else happens; a file already at its tail with the same bytes is simply
recorded; a different file at that tail gets the next free name, never
overwritten; a photograph the library already holds anywhere is skipped by
identity; the source is removed only after its copy is verified and recorded,
and only when asked; and a run stopped at any point is resumed by running it
again, because everything it did is in the catalog and on the disk.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from collections.abc import Callable, Iterable

from model import photos

RAWS = "raws"
FILM = "film"
SNAPSHOTS = "snapshots"
EDITS = "edits"
KINDS = (RAWS, FILM, SNAPSHOTS, EDITS)

ROOTS = {
    RAWS: "Raws/Digital",
    FILM: "Raws/Film Scans",
    SNAPSHOTS: "Snapshots",
    EDITS: "Edits",
}

# Extensions that are the camera's own file, which is what decides "raws"
# when the source does not say otherwise.
CAMERA = frozenset({".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2"})


def destination(kind: str, taken: str | None, name: str, *, roll: str = "") -> str:
    """The tail for one photograph. Pure: the same inputs always name the same place."""

    if kind not in ROOTS:
        raise ValueError(f"kind is one of {KINDS}")
    day = (taken or "")[:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError("a destination needs a date")
    parts = [ROOTS[kind], day[:4], day]
    if kind == FILM:
        if not roll:
            raise ValueError("a film scan needs a roll")
        parts.append(_safe_name(roll))
    parts.append(_safe_name(name))
    return "/".join(parts)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(name)).strip(" .")
    if not cleaned:
        raise ValueError("a name cannot be empty")
    return cleaned


def guess_kind(source_root: str, names: Iterable[str]) -> str | None:
    """What a source most likely holds, or None when only the owner can say.

    A card (a DCIM folder) is a camera's; a folder of camera files is a
    camera's; a folder that is only JPEGs could be a phone's, a lab's, or
    finished pictures, and is the one question asked.
    """

    root = os.path.abspath(source_root)
    if os.path.isdir(os.path.join(root, "DCIM")) or os.path.basename(root).upper() == "DCIM":
        return RAWS
    extensions = {os.path.splitext(name)[1].lower() for name in names}
    if extensions and extensions <= CAMERA:
        return RAWS
    return None


def scan(conn, source_root: str) -> list[dict]:
    """Every photograph under a source, with what the staged view shows: name,
    where it sits, size, its own date, and whether the library may already
    hold it (same name and size -- a suspicion, made exact by identity when
    the copy is made)."""

    root = os.path.abspath(source_root)
    seen: list[dict] = []
    known = {
        (str(row["filename"]), int(row["file_size"]))
        for row in conn.execute(
            "SELECT filename, file_size FROM images WHERE file_size IS NOT NULL")
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            try:
                entry = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(entry.st_mode) or entry.st_size <= 0 or not photos.supported(path):
                continue
            seen.append({
                "key": os.path.relpath(path, root).replace(os.sep, "/"),
                "path": path,
                "name": name,
                "size": int(entry.st_size),
                "taken": _taken(path, entry),
                "suspect": (name, int(entry.st_size)) in known,
            })
    return seen


def _taken(path: str, entry) -> str:
    """The photograph's own date, else the file's: always a date."""

    import metadata

    try:
        answer = metadata.read(path).get("date_taken")
    except Exception:  # noqa: BLE001 - an unreadable header is a file-time date
        answer = None
    if answer:
        return answer
    return dt.datetime.fromtimestamp(entry.st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def bring(
    conn,
    drive_uuid: str,
    kind: str,
    candidates: Iterable[dict],
    *,
    roll: str = "",
    clear_source: bool = False,
    skip_known: bool = True,
    progress: Callable[[dict], None] | None = None,
    stop: Callable[[], bool] = lambda: False,
) -> dict:
    """Copy each candidate to where the rule says, verify, record, and -- only
    then, only if asked -- remove the source. Returns what happened to each.

    `progress` is called after every file with the running tally; `stop` is
    asked before every file and ends the run cleanly between files, never
    leaving a source removed whose copy was not verified.
    """

    tally = {"brought": 0, "already": 0, "skipped": 0, "cleared": 0, "failed": 0,
             "bytes": 0, "done": 0, "total": 0, "stopped": False, "outcomes": []}
    wanted = list(candidates)
    tally["total"] = len(wanted)

    def note(outcome: str, candidate: dict, **extra) -> None:
        tally["outcomes"].append({"key": candidate["key"], "outcome": outcome, **extra})
        tally["done"] += 1
        if progress is not None:
            progress(dict(tally))

    for candidate in wanted:
        if stop():
            tally["stopped"] = True
            break
        source = candidate["path"]
        try:
            identity = photos.content_hash(source)
        except OSError as error:
            tally["failed"] += 1
            note("unreadable", candidate, error=str(error))
            continue
        # Held means a drive holds it, not merely that a row remembers it: a
        # photograph gone missing is brought back, not skipped.
        held = conn.execute(
            "SELECT 1 FROM images i JOIN copies c ON c.photo_id = i.id WHERE i.content_hash = ? LIMIT 1",
            (identity,),
        ).fetchone()
        if held and skip_known:
            tally["skipped"] += 1
            if clear_source:
                _clear(source, tally)
            note("already in the library", candidate)
            continue

        name = candidate["name"]
        outcome = None
        for attempt in range(1, 1000):
            try:
                tail = destination(kind, candidate["taken"], _numbered(name, attempt), roll=roll)
            except ValueError as error:
                outcome = {"outcome": f"no destination: {error}"}
                break
            outcome = photos.put(conn, source, drive_uuid, tail)
            if outcome["outcome"] != "different file at that tail":
                break
        if outcome is None or outcome["outcome"] not in ("written", "already there"):
            tally["failed"] += 1
            note(outcome["outcome"] if outcome else "no destination", candidate)
            continue
        conn.commit()
        if outcome["outcome"] == "written":
            tally["brought"] += 1
            tally["bytes"] += int(candidate["size"])
        else:
            tally["already"] += 1
        if clear_source:
            _clear(source, tally)
        note(outcome["outcome"], candidate, tail=outcome.get("tail") or tail)
    return tally


def _numbered(name: str, attempt: int) -> str:
    if attempt <= 1:
        return name
    stem, ext = os.path.splitext(name)
    return f"{stem}-{attempt}{ext}"


def _clear(source: str, tally: dict) -> None:
    # The copy is verified and recorded; only now may the source go, and only
    # the source -- the card's folders are left as they are.
    try:
        os.remove(source)
        tally["cleared"] += 1
    except OSError:
        pass


def cards() -> list[dict]:
    """Removable volumes with a DCIM folder -- a camera card that is here now.
    Windows only for the moment; elsewhere a card is a folder chosen by hand."""

    import sys

    found = []
    if not sys.platform.startswith("win"):
        return found
    import ctypes

    removable = 2  # DRIVE_REMOVABLE
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) != removable:
            continue
        dcim = os.path.join(root, "DCIM")
        if not os.path.isdir(dcim):
            continue
        photographs = 0
        size = 0
        for dirpath, _dirnames, filenames in os.walk(dcim):
            for name in filenames:
                path = os.path.join(dirpath, name)
                if photos.supported(path):
                    try:
                        size += os.path.getsize(path)
                        photographs += 1
                    except OSError:
                        continue
        found.append({"root": root, "label": _volume_label(root) or f"Card {letter}:",
                      "photos": photographs, "bytes": size})
    return found


def _volume_label(root: str) -> str:
    import ctypes

    buffer = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, buffer, 261, None, None, None, None, 0)
    return buffer.value if ok else ""
