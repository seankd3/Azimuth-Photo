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

# Words a lab or a scanning tool writes into a scan's description when it
# knows the stock; a roll named after its stock beats a number.
STOCKS = ("portra", "ektar", "gold", "ultramax", "colorplus", "pro image", "tri-x", "t-max", "tmax",
          "hp5", "fp4", "delta", "pan f", "xp2", "superia", "provia", "velvia", "acros", "c200",
          "cinestill", "lomography", "fomapan", "kentmere", "rollei", "ektachrome", "vision3")
_DATE_IN_NAME = re.compile(r"(20\d\d|19\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")


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


def scan(conn, source_root: str, progress=None) -> list[dict]:
    """Every photograph under a source, with what the staged view shows: name,
    where it sits, size, its own date, and whether the library may already
    hold it -- a suspicion, made exact by identity when the copy is made.

    Suspicion matches same name and size, or same capture second and size:
    the import renames files to the date scheme, so a name is exactly the
    thing a previous import did not keep. The second pair is what lets a
    re-inserted card default to only the days not yet brought in.

    ``progress``, when given, hears the growing count as the walk reads --
    a card of thousands takes half a minute, and a count is the difference
    between "looking" and "looks dead".
    """

    root = os.path.abspath(source_root)
    seen: list[dict] = []
    known = {
        (str(row["filename"]), int(row["file_size"]))
        for row in conn.execute(
            "SELECT filename, file_size FROM images WHERE file_size IS NOT NULL")
    }
    known_when = {
        (str(row["date_taken"]), int(row["file_size"]))
        for row in conn.execute(
            "SELECT date_taken, file_size FROM images"
            " WHERE file_size IS NOT NULL AND date_taken IS NOT NULL")
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        group = os.path.relpath(dirpath, root).replace(os.sep, "/")
        group = "" if group == "." else group
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
            tags = _tags(path)
            taken = tags.get("date_taken")
            seen.append({
                "key": os.path.relpath(path, root).replace(os.sep, "/"),
                "path": path,
                "name": name,
                "group": group,
                "size": int(entry.st_size),
                "taken": taken or dt.datetime.fromtimestamp(entry.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                # A scan carries no capture date; a lab's folder or archive name
                # often carries the order's, which is the day the roll gets.
                "folder_date": _date_in(os.path.relpath(dirpath, root)) or _date_in(os.path.basename(root)),
                "stock": _stock(tags.get("description", "")),
                "suspect": (name, int(entry.st_size)) in known
                           or (taken, int(entry.st_size)) in known_when,
            })
            if progress is not None and len(seen) % 25 == 0:
                progress(len(seen))
    return seen


def _tags(path: str) -> dict:
    from photo import tags

    try:
        return tags.read(path, description=True)
    except Exception:  # noqa: BLE001 - an unreadable header is no tags
        return {}


def _date_in(text: str) -> str | None:
    found = _DATE_IN_NAME.search(text or "")
    return f"{found.group(1)}-{found.group(2)}-{found.group(3)}" if found else None


def _stock(description: str) -> str:
    """The stock as the lab wrote it: the part of the description, between
    separators, that names one -- "Kodak Portra 400" out of
    "Kodak Portra 400 - Noritsu HS-1800"."""

    for part in re.split(r"[,;|/]| - |\s{2,}", description or ""):
        lowered = part.lower()
        if any(stock in lowered for stock in STOCKS):
            return part.strip(" -_,.;:")
    return ""


def rolls(candidates: Iterable[dict]) -> dict[str, dict]:
    """Film scans come by the roll -- one folder each, usually. Each roll is
    named after its stock when a scan says so, else numbered 1, 2, 3 in the
    order the folders come; the owner may rename any of them."""

    groups: dict[str, dict] = {}
    for candidate in candidates:
        roll = groups.setdefault(candidate.get("group", ""), {"name": "", "count": 0, "stock": ""})
        roll["count"] += 1
        if not roll["stock"] and candidate.get("stock"):
            roll["stock"] = candidate["stock"]
    for number, (group, roll) in enumerate(groups.items(), start=1):
        roll["name"] = roll["stock"] or str(number)
    return groups


def bring(
    conn,
    drive_uuid: str,
    kind: str,
    candidates: Iterable[dict],
    *,
    roll: str = "",
    rolls_by_group: dict[str, str] | None = None,
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

    tally = {"hashes": [], "brought": 0, "already": 0, "skipped": 0, "cleared": 0, "failed": 0,
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
        this_roll = (rolls_by_group or {}).get(candidate.get("group", ""), "") or roll
        day = (candidate.get("folder_date") if kind == FILM else None) or candidate["taken"]
        for attempt in range(1, 1000):
            try:
                tail = destination(kind, day, _numbered(name, attempt), roll=this_roll)
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
        tally["hashes"].append(identity)
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
    """Volumes with a DCIM folder -- a camera card that is here now.

    The DCIM folder is the signal, not the bus: a CFexpress reader mounts
    as a *fixed* disk, so filtering on DRIVE_REMOVABLE hid the one card
    that matters most. Cheap on purpose (a drive type and one folder per
    letter), because it is asked every few seconds; what the card holds is
    counted when it is staged. Windows only for the moment; elsewhere a
    card is a folder chosen by hand."""

    import sys

    found = []
    if not sys.platform.startswith("win"):
        return found
    import ctypes

    removable, fixed = 2, 3
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) not in (removable, fixed):
            continue
        if not os.path.isdir(os.path.join(root, "DCIM")):
            continue
        found.append({"root": root, "label": _volume_label(root) or f"Card {letter}:"})
    return found


def _volume_label(root: str) -> str:
    import ctypes

    buffer = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, buffer, 261, None, None, None, None, 0)
    return buffer.value if ok else ""
