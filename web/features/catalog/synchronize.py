"""Make the catalog agree with what is actually in a folder.

The disk is authoritative. When the catalog and the folder disagree, the folder
is right and the catalog is re-derived — never hand-repaired. Lightroom calls
this Synchronize Folder and the shape is worth copying: survey the folder, say
plainly what was found, then apply it.

This exists so a folder rename stops being an operation. A photo is identified
by its content, not by its path, so a renamed or reorganised tree is not damage
to repair in four places — it is simply a folder that has not been synchronised
yet, and one pass makes the catalog true again.

Three things can be found, and nothing else:

    added    a file in the folder that the catalog does not have
    moved    a catalogued photo whose file is now somewhere else in the folder
    gone     a catalogued photo whose file is no longer in the folder

`survey` only reads. `apply` writes, and refuses to mark an implausible number
of photos gone — the same circuit breaker a scan uses, because "the drive was
unplugged" and "the owner deleted everything" look identical from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import scanner
from data import connection
from data.repositories import catalog as catalog_repository

# Astrophotography is out of scope and is never read, per AGENTS.md.
_SKIPPED_DIR_NAMES = frozenset({"Astrophotography"})

_FOLDER_STATE_DDL = """
CREATE TABLE IF NOT EXISTS folder_scan_state (
    directory TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    checked_at REAL NOT NULL
);
"""


@dataclass
class Plan:
    """What one folder needs for the catalog to match it."""

    folder: str
    added: list[str] = field(default_factory=list)
    moved: list[tuple[int, str, str]] = field(default_factory=list)  # id, from, to
    gone: list[tuple[int, str]] = field(default_factory=list)        # id, path
    unchanged: int = 0
    directories_seen: int = 0
    directories_read: int = 0
    # Every directory this survey saw, carried so applying can remember them.
    _directories: set[str] = field(default_factory=set, repr=False)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.moved or self.gone)

    def summary(self) -> dict:
        return {
            "folder": self.folder,
            "added": len(self.added),
            "moved": len(self.moved),
            "gone": len(self.gone),
            "unchanged": self.unchanged,
            "in_sync": self.is_empty,
            "directories_seen": self.directories_seen,
            "directories_read": self.directories_read,
        }

    def as_payload(self, sample: int = 20) -> dict:
        return {
            **self.summary(),
            "sample": {
                "added": self.added[:sample],
                "moved": [{"id": i, "from": a, "to": b} for i, a, b in self.moved[:sample]],
                "gone": [{"id": i, "path": p} for i, p in self.gone[:sample]],
            },
        }


def _within(path: str, folder: str) -> bool:
    prefix = os.path.join(folder, "")
    return path == folder or path.startswith(prefix)


def _key(path: str, size: int | None) -> tuple[str, int]:
    """Identify a photo by what it is, not where it sits."""

    return (os.path.basename(path).lower(), int(size or -1))


async def _directory_state(conn, folder: str) -> dict[str, float]:
    await conn.executescript(_FOLDER_STATE_DDL)
    rows = await (await conn.execute(
        "SELECT directory, mtime FROM folder_scan_state WHERE directory = ? OR directory GLOB ?",
        (folder, os.path.join(folder, "*")),
    )).fetchall()
    return {str(row["directory"]): float(row["mtime"]) for row in rows}


def _walk_directories(folder: str, known: dict[str, float]) -> tuple[set[str], set[str]]:
    """Return (every directory that exists, those whose contents may have changed).

    A directory's mtime moves whenever an entry inside it is added, removed or
    renamed. So a directory whose mtime is unchanged cannot have gained or lost a
    photo, and its files never need listing — the catalog is already the answer
    for it. That is the whole saving: on the real archive this is 1,714 stats in
    about two seconds, against roughly eleven minutes to stat every file.
    """

    seen: set[str] = set()
    changed: set[str] = set()
    for dirpath, dirnames, _files in os.walk(folder):
        dirnames[:] = [
            name for name in dirnames
            if not scanner.is_junk_directory(name) and name not in _SKIPPED_DIR_NAMES
        ]
        directory = os.path.normpath(dirpath)
        seen.add(directory)
        try:
            mtime = os.stat(directory).st_mtime
        except OSError:
            changed.add(directory)
            continue
        if known.get(directory) != mtime:
            changed.add(directory)
    return seen, changed


def _list_photos(directory: str) -> dict[str, int]:
    """The image files directly inside one directory, without descending."""

    found: dict[str, int] = {}
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_file() or scanner.is_junk_file(entry.name):
            continue
        if os.path.splitext(entry.name)[1].lower() not in scanner.SUPPORTED_EXTENSIONS:
            continue
        try:
            found[os.path.normpath(entry.path)] = int(entry.stat().st_size)
        except OSError:
            continue
    return found


async def survey(db_path: str, folder: str, *, full: bool = False) -> Plan:
    """Compare one folder tree against the catalog. Reads only.

    Only directories whose mtime moved since the last pass are listed. Pass
    ``full=True`` to ignore that memory and read every directory, which is what
    an explicit Synchronize Folder should do.
    """

    folder = os.path.normpath(folder)
    plan = Plan(folder=folder)

    conn = await connection.open_async(db_path)
    try:
        known = {} if full else await _directory_state(conn, folder)
        rows = await (await conn.execute(
            "SELECT id, filepath, file_size FROM images WHERE vc_of IS NULL AND status != 'trashed'"
        )).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    seen_dirs, changed_dirs = _walk_directories(folder, known)
    plan.directories_seen = len(seen_dirs)
    plan.directories_read = len(changed_dirs)

    on_disk: dict[str, int] = {}
    for directory in changed_dirs:
        on_disk.update(_list_photos(directory))

    catalogued: dict[str, tuple[int, int | None]] = {}
    for row in rows:
        path = os.path.normpath(str(row["filepath"] or ""))
        if not path or not _within(path, folder):
            continue
        parent = os.path.dirname(path)
        if parent in changed_dirs or parent not in seen_dirs:
            # Either the directory moved underneath this row, or it is gone.
            catalogued[path] = (int(row["id"]), row["file_size"])
        else:
            # Its directory is untouched, so the file is still exactly there.
            plan.unchanged += 1

    still_there = on_disk.keys() & catalogued.keys()
    plan.unchanged += len(still_there)

    # A photo the catalog has lost track of may simply have been moved inside
    # the folder, so match the leftovers to each other by what they are before
    # concluding anything is gone.
    lost = {p: catalogued[p] for p in catalogued.keys() - on_disk.keys()}
    fresh = {p: on_disk[p] for p in on_disk.keys() - catalogued.keys()}
    fresh_by_key: dict[tuple[str, int], list[str]] = {}
    for path, size in fresh.items():
        fresh_by_key.setdefault(_key(path, size), []).append(path)

    claimed: set[str] = set()
    for old_path, (image_id, size) in sorted(lost.items()):
        candidates = fresh_by_key.get(_key(old_path, size)) or []
        available = [p for p in candidates if p not in claimed]
        if len(available) == 1:
            claimed.add(available[0])
            plan.moved.append((image_id, old_path, available[0]))
        else:
            # No candidate, or more than one and no way to choose — say gone
            # rather than guess. A wrong move is harder to notice than a
            # missing photo.
            plan.gone.append((image_id, old_path))

    plan.added = sorted(p for p in fresh if p not in claimed)
    plan._directories = seen_dirs
    return plan


async def _remember_directories(conn, folder: str, directories: set[str]) -> None:
    """Record what each directory looked like, so the next pass can skip it.

    Only written after a plan is applied. Recording during the survey would let
    a pass that never applied its findings convince the next one that a changed
    directory was already reconciled.
    """

    import time

    await conn.executescript(_FOLDER_STATE_DDL)
    now = time.time()
    rows = []
    for directory in directories:
        try:
            rows.append((directory, os.stat(directory).st_mtime, now))
        except OSError:
            continue
    await conn.executemany(
        "INSERT INTO folder_scan_state(directory, mtime, checked_at) VALUES (?, ?, ?) "
        "ON CONFLICT(directory) DO UPDATE SET mtime = excluded.mtime, checked_at = excluded.checked_at",
        rows,
    )
    # A directory that no longer exists must not keep a remembered mtime, or a
    # folder recreated at the same path would look untouched.
    await conn.execute(
        "DELETE FROM folder_scan_state WHERE (directory = ? OR directory GLOB ?) "
        f"AND directory NOT IN ({','.join('?' * len(rows)) or 'NULL'})",
        (folder, os.path.join(folder, "*"), *[row[0] for row in rows]),
    )


async def apply(db_path: str, plan: Plan, *, source_id: int | None = None) -> dict:
    """Write a surveyed plan. Moves and losses only; adding is the scanner's job."""

    if plan.is_empty:
        conn = await connection.open_async(db_path)
        try:
            await _remember_directories(conn, plan.folder, plan._directories)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)
        return {**plan.summary(), "applied": False, "reason": "already in sync"}

    live = plan.unchanged + len(plan.moved) + len(plan.gone)
    if catalog_repository.would_mass_mark_missing(len(plan.gone), live):
        raise catalog_repository.StorageUnavailableDuringScan(
            f"Refusing to mark {len(plan.gone)} of {live} photos missing in {plan.folder}: "
            "that looks like unavailable storage rather than deleted photos."
        )

    import time

    now = time.time()
    conn = await connection.open_async(db_path)
    try:
        for image_id, _old, new_path in plan.moved:
            await conn.execute(
                "UPDATE images SET filepath = ?, missing_at = NULL WHERE id = ?",
                (new_path, image_id),
            )
            # A virtual copy shares its parent's file on purpose, so it has to
            # follow the move too — the same cascade the scanner's rematch does.
            await conn.execute(
                "UPDATE images SET filepath = ?, missing_at = NULL WHERE vc_of = ?",
                (new_path, image_id),
            )
        for image_id, _path in plan.gone:
            await conn.execute(
                "UPDATE images SET missing_at = ? WHERE id = ? AND missing_at IS NULL",
                (now, image_id),
            )
        await catalog_repository.update_source_counts_on_conn(conn)
        await _remember_directories(conn, plan.folder, plan._directories)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)

    return {
        **plan.summary(),
        "applied": True,
        "moved_applied": len(plan.moved),
        "gone_applied": len(plan.gone),
        "added_pending_scan": len(plan.added),
        "source_id": source_id,
    }
