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

import asyncio
import logging
import os
from dataclasses import dataclass, field

import scanner
from data import connection
from data.repositories import catalog as catalog_repository

log = logging.getLogger(__name__)

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

    adopted = 0
    if plan.added and source_id is not None:
        adopted = await _adopt(db_path, plan.added, source_id)

    return {
        **plan.summary(),
        "applied": True,
        "moved_applied": len(plan.moved),
        "gone_applied": len(plan.gone),
        "adopted": adopted,
        "added_pending_scan": len(plan.added) - adopted,
        "source_id": source_id,
    }


async def _adopt(db_path: str, paths: list[str], source_id: int) -> int:
    """Catalog files that are in the folder but not in the library.

    What is in the three roots is what the owner has, so a file that appears
    there joins the library rather than waiting in a queue. It goes in through
    the same batch insert the scanner uses, so an adopted photo is
    indistinguishable from a scanned one.
    """

    rows = []
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        rows.append((
            os.path.basename(path),
            path,
            os.path.splitext(path)[1].lower(),
            int(stat.st_size),
            float(stat.st_mtime),
            None,   # orientation — the metadata worker fills this in
            None,   # aspect_ratio
        ))
    if not rows:
        return 0
    await catalog_repository.insert_images_batch(db_path, rows, source_id=source_id)
    return len(rows)


async def reconcile_source(db_path: str, source: dict, *, full: bool = False) -> dict:
    """Make one source's catalog rows match what is in its folder."""

    plan = await survey(db_path, str(source["path"]), full=full)
    return await apply(db_path, plan, source_id=int(source["id"]))


async def rebind_moved_source(db_path: str, source: dict, *, sample: int = 50) -> str | None:
    """A root that vanished has usually just been renamed. Find it and follow.

    This is the case that cost a day: the owner renames `Personal Photos` to
    `Snapshots` in a file manager and every path in the catalog is suddenly
    wrong. Rather than repair a hundred thousand rows, move the one row that
    says where the root is — every photo's path is derived from it.

    Proven, not guessed: a candidate only wins if the source's own photos are
    actually found underneath it at the same relative paths.
    """

    old_root = os.path.normpath(str(source["path"]))
    parent = os.path.dirname(old_root)
    if not parent or not os.path.isdir(parent):
        return None

    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT filepath FROM images WHERE source_id = ? AND vc_of IS NULL "
            "AND status != 'trashed' LIMIT ?",
            (int(source["id"]), sample),
        )).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    relatives = []
    for row in rows:
        path = os.path.normpath(str(row["filepath"] or ""))
        if path.startswith(os.path.join(old_root, "")):
            relatives.append(os.path.relpath(path, old_root))
    if not relatives:
        return None

    best: tuple[int, str] | None = None
    for entry in os.scandir(parent):
        if not entry.is_dir() or os.path.normpath(entry.path) == old_root:
            continue
        hits = sum(1 for rel in relatives if os.path.isfile(os.path.join(entry.path, rel)))
        if hits and (best is None or hits > best[0]):
            best = (hits, os.path.normpath(entry.path))

    # A clear majority of this source's photos must be there. A couple of
    # coincidental filename matches is not a renamed folder.
    if best is None or best[0] < max(1, int(len(relatives) * 0.8)):
        return None

    new_root = best[1]
    conn = await connection.open_async(db_path)
    try:
        held = await (await conn.execute(
            "SELECT id FROM catalog_sources WHERE path = ? AND id <> ?",
            (new_root, int(source["id"])),
        )).fetchone()
        if held is not None:
            # Something already claims that folder; leave both alone.
            return None
        await conn.execute(
            "UPDATE catalog_sources SET path = ?, display_name = ?, online = 1, "
            "included = 1, removed_at = NULL WHERE id = ?",
            (new_root, os.path.basename(new_root) or new_root, int(source["id"])),
        )
        # Every path under this root moves with it, in one indexed statement
        # rather than a hundred thousand individual repairs. Compared by prefix
        # length instead of GLOB so a bracket in a folder name cannot match
        # something else.
        old_prefix = os.path.join(old_root, "")
        new_prefix = os.path.join(new_root, "")
        await conn.execute(
            "UPDATE images SET filepath = ? || substr(filepath, ?) "
            "WHERE source_id = ? AND substr(filepath, 1, ?) = ?",
            (new_prefix, len(old_prefix) + 1, int(source["id"]), len(old_prefix), old_prefix),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)

    log.info("source %s followed a rename: %s -> %s", source["id"], old_root, new_root)
    return new_root


async def reconcile_once(db_path: str, *, full: bool = False) -> list[dict]:
    """Reconcile every source that is a real folder on this machine.

    A satellite's library is a mirror rather than a folder, so it has nothing to
    reconcile here — its `hub://` source is not a filesystem path and is skipped.
    """

    from features.catalog import reveal as catalog_reveal

    sources = await catalog_repository.get_catalog_sources(db_path)
    results = []
    for source in sources:
        path = str(source["path"] or "")
        if not int(source["included"] or 0) or not catalog_reveal.source_has_local_folders(path):
            continue
        if not os.path.isdir(path):
            # A root that is gone was usually renamed, not unplugged. Follow it
            # if its photos can be found; otherwise leave the source entirely,
            # because an unplugged drive is not an empty library.
            moved_to = await rebind_moved_source(db_path, source)
            if moved_to is None:
                continue
            source = {**dict(source), "path": moved_to}
        try:
            results.append(await reconcile_source(db_path, source, full=full))
        except catalog_repository.StorageUnavailableDuringScan as refused:
            log.warning("reconcile refused for %s: %s", path, refused)
        except Exception:
            log.exception("reconcile failed for %s", path)
    return results


def reconcile_interval_seconds(default: float = 300.0) -> float:
    """How often to look. Overridable so a check does not cost five minutes."""

    try:
        value = float(os.environ.get("AZIMUTH_RECONCILE_INTERVAL_SECONDS", "") or default)
    except ValueError:
        return default
    return max(5.0, value)


async def run_reconcile_worker(db_path_provider, *, interval_seconds: float | None = None) -> None:
    """Keep the catalog matching the folders, quietly and forever.

    Cheap by construction: a pass that finds nothing stats the directories and
    reads none of them. It still waits for a gap in interactive work first,
    because on a slow archive even stat-ing competes with browsing.
    """

    from core.background import wait_for_user_gap

    if interval_seconds is None:
        interval_seconds = reconcile_interval_seconds()
    while True:
        try:
            await wait_for_user_gap()
            results = await reconcile_once(db_path_provider())
            changed = [r for r in results if r.get("applied")]
            if changed:
                log.info("reconcile applied %s", changed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reconcile worker pass failed")
        await asyncio.sleep(interval_seconds)
