"""Catalog-follows-renames repair for the three-root archive.

The owner renames archive roots on disk himself (Personal Photos → Snapshots,
RAWS → Raws, Film Scans → Raws/Film Scans, Exported Edits → Edits); the
catalog must then become true again without a single photo file being touched.
This module finds rows whose filepath no longer exists and re-points each at
its renamed or moved file — by relative path first, then by (basename, size),
then by content hash. A row is rewritten only when the match is proven: the
file's size and, when the row recorded one, its content hash must agree.

When the hub has already re-scanned a moved file into a fresh duplicate row,
the pair is merged: the elder row keeps its earned data (elo, comparisons,
picks, tags) and takes over the file; the fresh scan row is retired through
the standard catalog deletion path. Unmatched rows are reported, never
deleted. Dry-run by default; the filesystem is only ever read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from features.imports import taxonomy
from features.sync.hashing import compute_content_hash

REPORT_SAMPLE_LIMIT = 100

# Trees worth walking for moved files: the canonical roots plus any legacy
# tree that has not been renamed yet. Astrophotography is out of scope and is
# never walked; it is not in this list and is skipped defensively below.
_SEARCH_ROOTS = (
    *taxonomy.DESTINATIONS,
    taxonomy.DEST_VIDEO,
    *taxonomy.LEGACY_DESTINATIONS,
)
_SKIPPED_DIR_NAMES = frozenset({"Astrophotography"})


def _walk_library_files(library: Path) -> dict[tuple[str, int], list[Path]]:
    """(basename, size) → paths, across every existing destination tree."""
    index: dict[tuple[str, int], list[Path]] = {}
    seen_roots: set[Path] = set()
    for name in _SEARCH_ROOTS:
        root = library / Path(name).parts[0]
        if root in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                entry for entry in dirnames
                if entry not in _SKIPPED_DIR_NAMES and not entry.startswith(".")
            ]
            for filename in filenames:
                if filename.startswith("."):
                    continue  # dotfiles and .moving/.importing partials
                path = Path(dirpath) / filename
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                index.setdefault((filename, int(size)), []).append(path)
    return index


def _renamed_relative(rel_parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Apply the documented root renames to a library-relative path."""
    canonical = taxonomy.normalize_destination(rel_parts[0])
    if canonical == rel_parts[0]:
        return None
    return (*Path(canonical).parts, *rel_parts[1:])


def _proven(row: dict, candidate: Path, hash_cache: dict[Path, str | None]) -> bool:
    """A match is proven when size and any recorded content hash agree."""
    try:
        size = candidate.stat().st_size
    except OSError:
        return False
    if row["file_size"] is not None and int(size) != int(row["file_size"]):
        return False
    recorded = str(row["content_hash"] or "").strip()
    if recorded:
        return _candidate_hash(candidate, hash_cache) == recorded
    return True


def _candidate_hash(candidate: Path, hash_cache: dict[Path, str | None]) -> str | None:
    if candidate not in hash_cache:
        try:
            hash_cache[candidate] = compute_content_hash(candidate)
        except OSError:
            hash_cache[candidate] = None
    return hash_cache[candidate]


def _lineage_root(row: dict) -> int:
    """Virtual copies share their parent's file on purpose."""
    return int(row["vc_of"]) if row["vc_of"] is not None else int(row["id"])


async def relocate_catalog(
    db_path: str,
    library_root: Path | str,
    *,
    apply: bool = False,
    sample_limit: int = REPORT_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Re-point catalog rows at files the owner renamed or moved on disk.

    Dry-run unless ``apply=True``. Reads the filesystem, never writes to it.
    """
    from data import connection
    from data.repositories import catalog as catalog_repository

    library = Path(library_root).expanduser().resolve()
    conn = await connection.open_async(db_path)
    try:
        rows = [
            dict(row)
            for row in await (
                await conn.execute(
                    "SELECT id, filepath, filename, file_size, content_hash, vc_of "
                    "FROM images WHERE status != 'trashed' AND hub_remote = 0 ORDER BY id"
                )
            ).fetchall()
        ]
    finally:
        await connection.close_async(conn, db_path=db_path)

    by_path: dict[str, dict] = {}
    candidates: list[dict] = []
    for row in rows:
        filepath = str(row["filepath"] or "")
        if not filepath:
            continue
        try:
            rel_parts = Path(filepath).relative_to(library).parts
        except ValueError:
            continue  # outside this library — not this repair's business
        if os.path.exists(filepath):
            by_path[str(Path(filepath))] = row
        else:
            row["_rel_parts"] = rel_parts
            candidates.append(row)

    index: dict[tuple[str, int], list[Path]] | None = None
    hash_cache: dict[Path, str | None] = {}
    assigned: dict[str, int] = {}  # target path → lineage root that claimed it
    relocations: list[dict] = []
    merges: list[dict] = []
    unmatched: list[dict] = []
    matched_by = {"relative_path": 0, "basename_size": 0, "content_hash": 0}

    def unmatch(row: dict, reason: str) -> None:
        unmatched.append({"id": int(row["id"]), "filepath": str(row["filepath"]), "reason": reason})

    for row in candidates:
        target: Path | None = None
        method = ""

        renamed = _renamed_relative(row["_rel_parts"])
        if renamed is not None:
            probe = library.joinpath(*renamed)
            if probe.is_file() and _proven(row, probe, hash_cache):
                target, method = probe, "relative_path"

        if target is None:
            if index is None:
                index = _walk_library_files(library)
            options = (
                index.get((str(row["filename"]), int(row["file_size"])), [])
                if row["file_size"] is not None
                else []
            )
            if len(options) == 1:
                if _proven(row, options[0], hash_cache):
                    target, method = options[0], "basename_size"
                else:
                    unmatch(row, "candidate failed content-hash verification")
                    continue
            elif len(options) > 1:
                recorded = str(row["content_hash"] or "").strip()
                if not recorded:
                    unmatch(row, f"{len(options)} candidates, no recorded hash")
                    continue
                hashed = [
                    path for path in options
                    if _candidate_hash(path, hash_cache) == recorded
                ]
                if len(hashed) == 1:
                    target, method = hashed[0], "content_hash"
                else:
                    unmatch(row, f"{len(hashed) or len(options)} identical candidates")
                    continue

        if target is None:
            unmatch(row, "no matching file found")
            continue

        target_key = str(target)
        claimed_by = assigned.get(target_key)
        if claimed_by is not None and claimed_by != _lineage_root(row):
            unmatch(row, "file already claimed by another repaired row")
            continue

        claimant = by_path.get(target_key)
        if claimant is None or _lineage_root(claimant) == _lineage_root(row):
            relocations.append({
                "action": "relocate",
                "image_id": int(row["id"]),
                "from": str(row["filepath"]),
                "to": target_key,
                "matched_by": method,
            })
        elif int(claimant["id"]) > int(row["id"]) and row["vc_of"] is None and claimant["vc_of"] is None:
            # The dead-path row is the elder; the claimant is a fresh re-scan
            # of the moved file. Merge: elder keeps its earned data and takes
            # over the file, the fresh row retires.
            merges.append({
                "action": "merge",
                "image_id": int(row["id"]),
                "from": str(row["filepath"]),
                "to": target_key,
                "matched_by": method,
                "retired_image_id": int(claimant["id"]),
            })
        else:
            unmatch(row, f"file owned by elder row {int(claimant['id'])}")
            continue
        matched_by[method] += 1
        assigned[target_key] = _lineage_root(row)

    if apply and (relocations or merges):
        # Resolve catalog sources first — add_or_restore_source commits on its
        # own connection and must not interleave with the row-update transaction.
        source_ids: dict[str, int] = {}
        rebinds: list[tuple[str, int, int]] = []
        for action in (*relocations, *merges):
            top = Path(action["to"]).relative_to(library).parts[0]
            root = str(library / top) if top in taxonomy.DESTINATION_SET else str(library)
            if root not in source_ids:
                source = await catalog_repository.add_or_restore_source(db_path, root)
                source_ids[root] = int(source["id"])
            rebinds.append((action["to"], source_ids[root], action["image_id"]))

        for action in merges:
            # Retire the fresh row first (standard deletion path cleans every
            # dependent), then let the elder take over the file.
            await catalog_repository.delete_image_catalog_rows(
                db_path, [action["retired_image_id"]]
            )
        conn = await connection.open_async(db_path)
        try:
            await conn.executemany(
                "UPDATE images SET filepath = ?, source_id = ?, missing_at = NULL "
                "WHERE id = ?",
                rebinds,
            )
            await catalog_repository.update_source_counts_on_conn(conn)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)

    actions = [*relocations, *merges]
    return {
        "dry_run": not apply,
        "library_root": str(library),
        "rows_scanned": len(rows),
        "rows_missing_file": len(candidates),
        "relocated": len(relocations),
        "merged": len(merges),
        "matched_by": matched_by,
        "actions": actions[:sample_limit],
        "actions_total": len(actions),
        "unmatched": unmatched[:sample_limit],
        "unmatched_total": len(unmatched),
    }
