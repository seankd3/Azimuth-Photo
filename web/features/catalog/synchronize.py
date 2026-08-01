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


@dataclass
class Plan:
    """What one folder needs for the catalog to match it."""

    folder: str
    added: list[str] = field(default_factory=list)
    moved: list[tuple[int, str, str]] = field(default_factory=list)  # id, from, to
    gone: list[tuple[int, str]] = field(default_factory=list)        # id, path
    unchanged: int = 0

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


async def survey(db_path: str, folder: str) -> Plan:
    """Compare one folder tree against the catalog. Reads only."""

    folder = os.path.normpath(folder)
    plan = Plan(folder=folder)

    on_disk: dict[str, int] = {}
    for _filename, filepath, _ext, size, *_rest in scanner.walk_images(folder):
        on_disk[os.path.normpath(filepath)] = int(size or 0)

    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT id, filepath, file_size FROM images WHERE vc_of IS NULL AND status != 'trashed'"
        )).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    catalogued: dict[str, tuple[int, int | None]] = {}
    for row in rows:
        path = os.path.normpath(str(row["filepath"] or ""))
        if path and _within(path, folder):
            catalogued[path] = (int(row["id"]), row["file_size"])

    still_there = on_disk.keys() & catalogued.keys()
    plan.unchanged = len(still_there)

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
    return plan


async def apply(db_path: str, plan: Plan, *, source_id: int | None = None) -> dict:
    """Write a surveyed plan. Moves and losses only; adding is the scanner's job."""

    if plan.is_empty:
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
        for image_id, _path in plan.gone:
            await conn.execute(
                "UPDATE images SET missing_at = ? WHERE id = ? AND missing_at IS NULL",
                (now, image_id),
            )
        await catalog_repository.update_source_counts_on_conn(conn)
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
