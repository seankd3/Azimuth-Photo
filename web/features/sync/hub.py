"""Hub-side manifest, upload, metadata, base, and hash-backfill behavior."""

from __future__ import annotations

import asyncio
import json
import ntpath
import os
import sys
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from core import runtime_paths
from core.source_files import inspect_source_file
from data import connection
from data.repositories import catalog as catalog_repository
from features.imports import taxonomy
from features.library import keywords
from features.sync import family_clock
from features.sync.develop_merge import preserve_local_rating
from features.sync.hashing import compute_content_hash, compute_full_hash
from features.sync.validation import validate_content_hash


MAX_CHUNK_BYTES = 32 * 1024 * 1024
BACKFILL_BATCH_SIZE = 100
BACKFILL_THROTTLE_SECONDS = 0.05
_UPLOAD_LOCKS: dict[str, asyncio.Lock] = {}
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

SYNC_DDL = """
CREATE TABLE IF NOT EXISTS sync_manifest_items (
    content_hash TEXT PRIMARY KEY,
    full_hash TEXT,
    bytes INTEGER NOT NULL,
    filename TEXT NOT NULL,
    date_taken TEXT,
    folder TEXT,
    placed_relpath TEXT
);
"""


def default_intake_root() -> Path:
    configured = os.environ.get("PHOTOARCHIVE_SYNC_INTAKE_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("PHOTOARCHIVE_SMOKE_MODE") == "1":
        return Path(tempfile.gettempdir()) / "photoarchive-sync-intake"
    resolved = runtime_paths.resolve_runtime_paths()
    if resolved.layout == "legacy":
        # Historic omarchy deployment keeps originals on the expansion drive.
        return Path("/mnt/expansion/Photos/_intake")
    return Path(resolved.data_dir) / "photos" / "_intake"


def default_raws_root(intake_root: Path | None = None) -> Path:
    configured = os.environ.get("PHOTOARCHIVE_SYNC_RAWS_DIR")
    if configured:
        return Path(configured).expanduser()
    intake = intake_root or default_intake_root()
    if intake == Path("/mnt/expansion/Photos/_intake"):
        return Path("/mnt/expansion/Photos/RAWS")
    return intake.parent / "RAWS"


def default_library_root(intake_root: Path | None = None, raws_root: Path | None = None) -> Path:
    return taxonomy.default_library_root(
        intake_root=intake_root or default_intake_root(),
        raws_root=raws_root or default_raws_root(intake_root),
    )


async def ensure_sync_schema(db_path: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        columns = await (await conn.execute("PRAGMA table_info(images)")).fetchall()
        if not any(row["name"] == "content_hash" for row in columns):
            await conn.execute("ALTER TABLE images ADD COLUMN content_hash TEXT DEFAULT NULL")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_content_hash "
            "ON images(content_hash) WHERE content_hash IS NOT NULL"
        )
        await conn.executescript(SYNC_DDL)
        await family_clock.migrate_legacy_states(conn)
        manifest_columns = await (await conn.execute(
            "PRAGMA table_info(sync_manifest_items)"
        )).fetchall()
        column_names = {row["name"] for row in manifest_columns}
        if "full_hash" not in column_names:
            await conn.execute("ALTER TABLE sync_manifest_items ADD COLUMN full_hash TEXT")
        if "folder" not in column_names:
            await conn.execute("ALTER TABLE sync_manifest_items ADD COLUMN folder TEXT")
        if "placed_relpath" not in column_names:
            await conn.execute("ALTER TABLE sync_manifest_items ADD COLUMN placed_relpath TEXT")
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


def _normalize_folder(value: Any) -> str | None:
    return taxonomy.normalize_folder_hint(value)


def _normalize_filename(value: Any) -> str:
    filename = str(value or "").strip()
    stem = filename.split(".", 1)[0].upper()
    if (
        not filename
        or len(filename) > 255
        or filename.rstrip(" .") != filename
        or any(ord(char) < 32 for char in filename)
        or ":" in filename
        or os.path.basename(filename) != filename
        or ntpath.basename(filename) != filename
        or stem in _WINDOWS_RESERVED_NAMES
        or filename in {".", ".."}
    ):
        raise ValueError("filename must be a portable base filename")
    return filename


def compute_placed_relpath(
    *,
    filename: str,
    date_taken: str | None,
    folder: str | None,
) -> str:
    """Deterministic library-relative destination for one sync identity.

    Computed once on first manifest sighting and reused for every retry so a
    mid-transfer timeout cannot place the same bytes under a second path.
    """

    year, day = _date_parts(date_taken) or (str(date.today().year), date.today().isoformat())
    folder_hint = str(folder).strip() if folder else None
    source_kind = "phone" if folder_hint == taxonomy.DEST_PERSONAL else None
    destination_name = taxonomy.route_destination(
        filename=filename,
        source_kind=source_kind,
        folder_hint=folder_hint,
    )
    return f"{destination_name}/{year}/{day}/{os.path.basename(filename)}"


async def manifest(db_path: str, items: Iterable[dict[str, Any]]) -> dict[str, list[Any]]:
    await ensure_sync_schema(db_path)
    normalized: list[tuple[str, str | None, int, str, str | None, str | None, str]] = []
    for item in items:
        content_hash = validate_content_hash(item.get("content_hash", ""))
        supplied_full_hash = item.get("full_hash")
        full_hash = validate_content_hash(supplied_full_hash) if supplied_full_hash else None
        byte_count = int(item.get("bytes", 0))
        filename = _normalize_filename(item.get("filename"))
        if byte_count < 0 or not filename:
            raise ValueError("manifest items require non-negative bytes and a filename")
        folder = _normalize_folder(item.get("folder"))
        date_taken = item.get("date_taken")
        placed_relpath = compute_placed_relpath(
            filename=filename, date_taken=date_taken, folder=folder
        )
        normalized.append(
            (content_hash, full_hash, byte_count, filename, date_taken, folder, placed_relpath)
        )

    conn = await connection.open_async(db_path)
    try:
        if normalized:
            await conn.executemany(
                "INSERT INTO sync_manifest_items("
                "content_hash, full_hash, bytes, filename, date_taken, folder, placed_relpath) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(content_hash) DO UPDATE SET "
                "full_hash=COALESCE(excluded.full_hash, sync_manifest_items.full_hash), "
                "bytes=excluded.bytes, filename=excluded.filename, date_taken=excluded.date_taken, "
                "folder=excluded.folder, "
                "placed_relpath=COALESCE(sync_manifest_items.placed_relpath, excluded.placed_relpath)",
                normalized,
            )
        hashes = [row[0] for row in normalized]
        known_by_hash: dict[str, int] = {}
        if hashes:
            placeholders = ",".join("?" for _ in hashes)
            rows = await (await conn.execute(
                f"SELECT content_hash, MIN(id) AS image_id FROM images "
                f"WHERE content_hash IN ({placeholders}) GROUP BY content_hash",
                hashes,
            )).fetchall()
            known_by_hash = {str(row["content_hash"]): int(row["image_id"]) for row in rows}
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)

    return {
        "missing": [content_hash for content_hash in hashes if content_hash not in known_by_hash],
        "known": [
            {"content_hash": content_hash, "image_id": known_by_hash[content_hash]}
            for content_hash in hashes
            if content_hash in known_by_hash
        ],
    }


async def have_content_hashes(db_path: str, content_hashes: Iterable[str]) -> list[str]:
    """Return requested identities backed by an active original on this hub now."""

    hashes = list(dict.fromkeys(validate_content_hash(value) for value in content_hashes))
    if not hashes:
        return []
    conn = await connection.open_async(db_path)
    try:
        placeholders = ",".join("?" for _ in hashes)
        rows = await (
            await conn.execute(
                f"SELECT i.content_hash, i.filepath, i.file_size, s.path AS source_path "
                f"FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE i.content_hash IN ({placeholders}) "
                "AND COALESCE(i.hub_remote, 0) = 0 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                hashes,
            )
        ).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    def identity_verified() -> set[str]:
        # "present" must mean the hub holds bytes of this exact identity RIGHT NOW,
        # not merely a non-empty file at the path. A satellite deletes its only local
        # copy on this answer, so a truncated/bit-rotted hub file must NOT qualify:
        # require the size to match and re-derive the content hash from the live bytes.
        present: set[str] = set()
        for row in rows:
            content_hash = str(row["content_hash"])
            if content_hash in present:
                continue
            filepath = str(row["filepath"] or "")
            state, file_stat = inspect_source_file(filepath, str(row["source_path"] or ""))
            if state != "available" or file_stat is None:
                continue
            expected_size = row["file_size"]
            if expected_size is not None and int(file_stat.st_size) != int(expected_size):
                continue
            try:
                if compute_content_hash(filepath) == content_hash:
                    present.add(content_hash)
            except OSError:
                continue
        return present

    present = await asyncio.to_thread(identity_verified)
    return [content_hash for content_hash in hashes if content_hash in present]


def upload_part_path(intake_root: Path, content_hash: str) -> Path:
    return intake_root / f"{validate_content_hash(content_hash)}.part"


def upload_offset_path(intake_root: Path, content_hash: str) -> Path:
    return intake_root / f"{validate_content_hash(content_hash)}.offset"


def _fsync_directory(path: Path) -> None:
    if sys.platform.startswith("win"):
        return  # Windows cannot open directories; os.replace is already durable-atomic on NTFS
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_committed_offset(offset_path: Path, offset: int) -> None:
    temporary = offset_path.with_suffix(".offset.tmp")
    with temporary.open("wb") as handle:
        handle.write(str(int(offset)).encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, offset_path)
    _fsync_directory(offset_path.parent)


def _committed_upload_offset(part: Path, offset_path: Path) -> int:
    if not part.exists():
        offset_path.unlink(missing_ok=True)
        return 0
    size = part.stat().st_size
    try:
        committed = int(offset_path.read_text(encoding="ascii"))
    except (FileNotFoundError, ValueError):
        committed = 0
    if committed < 0 or committed > size:
        committed = 0
    if size != committed:
        with part.open("r+b") as handle:
            handle.truncate(committed)
            handle.flush()
            os.fsync(handle.fileno())
    if not offset_path.exists() or offset_path.read_text(encoding="ascii") != str(committed):
        _write_committed_offset(offset_path, committed)
    return committed


async def upload_status(intake_root: Path, content_hash: str) -> dict[str, int]:
    part = upload_part_path(intake_root, content_hash)
    offset_path = upload_offset_path(intake_root, content_hash)
    offset = await asyncio.to_thread(_committed_upload_offset, part, offset_path)
    return {"offset": int(offset)}


def _date_parts(value: str | None) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw[:19] if "%H" in fmt else raw[:10], fmt)
            return parsed.strftime("%Y"), parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _discard_upload_temp(intake_root: Path, content_hash: str) -> None:
    upload_part_path(intake_root, content_hash).unlink(missing_ok=True)
    upload_offset_path(intake_root, content_hash).unlink(missing_ok=True)


async def _manifest_item(db_path: str, content_hash: str) -> dict[str, Any] | None:
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT content_hash, full_hash, bytes, filename, date_taken, folder, placed_relpath "
            "FROM sync_manifest_items WHERE content_hash = ?",
            (content_hash,),
        )).fetchone()
        return dict(row) if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _persist_placed_relpath(db_path: str, content_hash: str, placed_relpath: str) -> str:
    """Lock the first placement decision; later calls keep the original path."""

    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "UPDATE sync_manifest_items SET placed_relpath = COALESCE(placed_relpath, ?) "
            "WHERE content_hash = ?",
            (placed_relpath, content_hash),
        )
        row = await (await conn.execute(
            "SELECT placed_relpath FROM sync_manifest_items WHERE content_hash = ?",
            (content_hash,),
        )).fetchone()
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    if row is None or not row["placed_relpath"]:
        raise RuntimeError("Could not persist upload placement path")
    return str(row["placed_relpath"])


async def _known_image_id(db_path: str, content_hash: str) -> int | None:
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT id FROM images WHERE content_hash = ? ORDER BY id LIMIT 1", (content_hash,)
        )).fetchone()
        return int(row["id"]) if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


def _resolve_library_destination(
    raws_root: Path,
    placed_relpath: str,
) -> tuple[Path, Path]:
    """Return (absolute destination file, catalog source root) for a locked relpath."""

    library_root = taxonomy.library_root_from_raws(raws_root).resolve()
    relative = Path(str(placed_relpath).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("placed_relpath must be a safe library-relative path")
    destination = (library_root / relative).resolve()
    try:
        destination.relative_to(library_root)
    except ValueError as exc:
        raise ValueError("placed_relpath escapes the library root") from exc
    top = relative.parts[0]
    if top == taxonomy.DEST_RAWS:
        source_root = Path(raws_root)
    else:
        source_root = taxonomy.destination_source_root(library_root, top)
    return destination, source_root


async def _register_original(db_path: str, source_root: Path, path: Path, content_hash: str) -> int:
    stat = await asyncio.to_thread(path.stat)
    row = (path.name, str(path), path.suffix.lower(), int(stat.st_size), float(stat.st_mtime))
    # These are the catalog source + batch insert primitives used by the normal
    # importer and scanner, with an explicit DB path for isolated hub tests.
    source = await catalog_repository.add_or_restore_source(db_path, str(source_root))
    await catalog_repository.insert_images_batch(db_path, [row], int(source["id"]))
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("UPDATE images SET content_hash = ? WHERE filepath = ?", (content_hash, str(path)))
        row = await (await conn.execute("SELECT id FROM images WHERE filepath = ?", (str(path),))).fetchone()
        await conn.commit()
        if row is None:
            raise RuntimeError("Verified upload was not registered by the catalog importer")
        return int(row["id"])
    finally:
        await connection.close_async(conn, db_path=db_path)


async def append_upload_chunk(
    db_path: str,
    intake_root: Path,
    raws_root: Path,
    content_hash: str,
    *,
    offset: int,
    total_bytes: int,
    chunk: bytes,
) -> dict[str, Any]:
    normalized = validate_content_hash(content_hash)
    lock = _UPLOAD_LOCKS.setdefault(normalized, asyncio.Lock())
    async with lock:
        return await _append_upload_chunk_locked(
            db_path,
            intake_root,
            raws_root,
            normalized,
            offset=offset,
            total_bytes=total_bytes,
            chunk=chunk,
        )


async def _append_upload_chunk_locked(
    db_path: str,
    intake_root: Path,
    raws_root: Path,
    content_hash: str,
    *,
    offset: int,
    total_bytes: int,
    chunk: bytes,
) -> dict[str, Any]:
    content_hash = validate_content_hash(content_hash)
    if offset < 0 or total_bytes < 0 or len(chunk) > MAX_CHUNK_BYTES:
        raise ValueError("Invalid upload offset, total, or chunk size")
    await ensure_sync_schema(db_path)
    known = await _known_image_id(db_path, content_hash)
    if known is not None:
        _discard_upload_temp(intake_root, content_hash)
        return {"image_id": known}
    item = await _manifest_item(db_path, content_hash)
    if item is None:
        raise LookupError("Upload must be declared through the manifest first")
    if int(item["bytes"]) != total_bytes:
        raise ValueError("X-Total-Bytes does not match the manifest")
    if not item.get("full_hash"):
        raise ValueError("Manifest full_hash is required before upload finalization")

    intake_root.mkdir(parents=True, exist_ok=True)
    part = upload_part_path(intake_root, content_hash)
    offset_path = upload_offset_path(intake_root, content_hash)
    current = _committed_upload_offset(part, offset_path)
    if current != offset:
        raise FileExistsError(str(current))
    if offset + len(chunk) > total_bytes:
        raise ValueError("Chunk exceeds X-Total-Bytes")
    with part.open("ab") as handle:
        handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    current += len(chunk)
    _write_committed_offset(offset_path, current)
    if current < total_bytes:
        return {"offset": current}

    # Byte-verify BEFORE any library placement or catalog write. A full-size
    # .part with corrupt bytes (the satellite→hub timeout failure mode) must
    # never become an original.
    actual_hash = await asyncio.to_thread(compute_content_hash, part)
    if actual_hash != content_hash:
        _discard_upload_temp(intake_root, content_hash)
        raise ArithmeticError("Completed upload failed content hash verification")
    actual_full_hash = await asyncio.to_thread(compute_full_hash, part)
    if actual_full_hash != item["full_hash"]:
        _discard_upload_temp(intake_root, content_hash)
        raise ArithmeticError("Completed upload failed full-file hash verification")

    placed_relpath = str(item.get("placed_relpath") or "").strip()
    if not placed_relpath:
        placed_relpath = compute_placed_relpath(
            filename=str(item["filename"]),
            date_taken=item.get("date_taken"),
            folder=item.get("folder"),
        )
        placed_relpath = await _persist_placed_relpath(db_path, content_hash, placed_relpath)
    destination, destination_root = _resolve_library_destination(raws_root, placed_relpath)
    destination.parent.mkdir(parents=True, exist_ok=True)

    created_destination = False
    if destination.exists():
        existing_full = await asyncio.to_thread(compute_full_hash, destination)
        if existing_full != item["full_hash"]:
            _discard_upload_temp(intake_root, content_hash)
            raise ArithmeticError(
                "Upload destination already exists with different content; refusing to overwrite"
            )
        # Identical bytes already at the locked path — idempotent success.
    else:
        try:
            await asyncio.to_thread(os.link, part, destination)
        except OSError:
            await asyncio.to_thread(shutil.copy2, part, destination)
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            # Copy can silently diverge; prove the placed bytes before catalog commit.
            placed_full = await asyncio.to_thread(compute_full_hash, destination)
            if placed_full != item["full_hash"]:
                destination.unlink(missing_ok=True)
                _discard_upload_temp(intake_root, content_hash)
                raise ArithmeticError("Placed upload failed full-file hash verification")
        created_destination = True
    try:
        image_id = await _register_original(db_path, destination_root, destination, content_hash)
    except Exception:
        if created_destination:
            destination.unlink(missing_ok=True)
        raise
    _discard_upload_temp(intake_root, content_hash)
    return {"image_id": image_id}


def _timestamp(value: Any) -> str:
    return str(value or "").strip()


def _family_timestamp(item: dict[str, Any], family: str) -> str:
    if family == "develop":
        return _timestamp(item.get("develop_updated_at"))
    nested = item.get(family)
    if isinstance(nested, dict) and nested.get("updated_at"):
        return _timestamp(nested["updated_at"])
    return _timestamp(item.get(f"{family}_updated_at") or item.get("develop_updated_at") or item.get("updated_at"))


async def _state_key(conn, content_hash: str, family: str) -> tuple[float, str, int] | None:
    return await family_clock.state_key(conn, content_hash, family)


async def _record_state(conn, content_hash: str, family: str, updated_at: str) -> None:
    await family_clock.record_state(
        conn,
        content_hash,
        family,
        family_clock.legacy_key(updated_at),
    )


def _is_newer(incoming: tuple[float, str, int], existing: tuple[float, str, int] | None) -> bool:
    return existing is None or incoming[0] > existing[0]


async def _merge_scalar(conn, image_id: int, content_hash: str, item: dict[str, Any], family: str, column: str) -> tuple[bool, str]:
    updated_at = _family_timestamp(item, family)
    incoming = family_clock.legacy_key(updated_at)
    existing = await _state_key(conn, content_hash, family)
    if not _is_newer(incoming, existing):
        return False, "hub-newer-or-equal"
    await conn.execute(f"UPDATE images SET {column} = ? WHERE id = ?", (item[family], image_id))
    await _record_state(conn, content_hash, family, updated_at)
    return True, "applied"


async def _merge_rating(conn, image_id: int, content_hash: str, item: dict[str, Any]) -> tuple[bool, str]:
    updated_at = _family_timestamp(item, "rating")
    incoming = family_clock.legacy_key(updated_at)
    existing = await _state_key(conn, content_hash, "rating")
    if not _is_newer(incoming, existing):
        return False, "hub-newer-or-equal"
    columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(images)")).fetchall()}
    if "rating" in columns:
        await conn.execute("UPDATE images SET rating = ? WHERE id = ?", (item["rating"], image_id))
    else:
        row = await (await conn.execute(
            "SELECT origin FROM develop_settings WHERE image_id = ?", (image_id,)
        )).fetchone()
        settings = json.dumps({"_lr_rating": item["rating"]}, separators=(",", ":"))
        await conn.execute(
            "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, ?, '') "
            "ON CONFLICT(image_id) DO UPDATE SET settings=json_set("
            "CASE WHEN json_valid(develop_settings.settings) THEN "
            "  CASE WHEN json_type(develop_settings.settings) = 'object' "
            "    THEN develop_settings.settings ELSE '{}' END "
            "ELSE '{}' END, '$._lr_rating', ?)",
            (image_id, settings, row["origin"] if row else "sync", item["rating"]),
        )
    await _record_state(conn, content_hash, "rating", updated_at)
    return True, "applied"


async def _merge_develop(conn, image_id: int, content_hash: str, item: dict[str, Any]) -> tuple[bool, str]:
    updated_at = _family_timestamp(item, "develop")
    incoming = family_clock.legacy_key(updated_at)
    row = await (await conn.execute(
        "SELECT settings, updated_at FROM develop_settings WHERE image_id = ?", (image_id,)
    )).fetchone()
    row_key = family_clock.legacy_key(row["updated_at"]) if row else None
    state_key = await _state_key(conn, content_hash, "develop")
    existing = family_clock.newest_key(row_key, state_key)
    if not _is_newer(incoming, existing):
        return False, "hub-newer-or-equal"
    settings = preserve_local_rating(item["develop_settings"], row["settings"] if row else None)
    encoded = json.dumps(settings, separators=(",", ":"), sort_keys=True)
    await conn.execute(
        "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'sync', ?) "
        "ON CONFLICT(image_id) DO UPDATE SET settings=excluded.settings, origin='sync', updated_at=excluded.updated_at",
        (image_id, encoded, updated_at),
    )
    await _record_state(conn, content_hash, "develop", updated_at)
    return True, "applied"


async def _merge_iptc(conn, image_id: int, content_hash: str, item: dict[str, Any]) -> tuple[bool, str]:
    updated_at = _family_timestamp(item, "iptc")
    incoming = family_clock.legacy_key(updated_at)
    await keywords.ensure_schema(conn)
    state_key = await _state_key(conn, content_hash, "iptc")
    row_key = await family_clock.row_key(conn, image_id, "iptc")
    existing = family_clock.newest_key(state_key, row_key)
    if not _is_newer(incoming, existing):
        return False, "hub-newer-or-equal"
    iptc = item["iptc"] or {}
    await conn.execute(
        "INSERT INTO iptc_fields(image_id, title, caption, copyright, creator, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(image_id) DO UPDATE SET "
        "title=excluded.title, caption=excluded.caption, copyright=excluded.copyright, "
        "creator=excluded.creator, updated_at=excluded.updated_at",
        (
            image_id,
            str(iptc.get("title") or ""), str(iptc.get("caption") or ""),
            str(iptc.get("copyright") or ""), str(iptc.get("creator") or ""), updated_at,
        ),
    )
    await _record_state(conn, content_hash, "iptc", updated_at)
    return True, "applied"


async def merge_metadata(db_path: str, items: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    await ensure_sync_schema(db_path)
    results: list[dict[str, Any]] = []
    for item in items:
        content_hash = validate_content_hash(item.get("content_hash", ""))
        image_id = await _known_image_id(db_path, content_hash)
        if image_id is None:
            results.append({"content_hash": content_hash, "applied": [], "skipped": [{"family": "item", "reason": "unknown-content-hash"}]})
            continue
        result: dict[str, Any] = {"content_hash": content_hash, "image_id": image_id, "applied": [], "skipped": []}
        conn = await connection.open_async(db_path)
        try:
            families: list[tuple[str, Callable[..., Any]]] = []
            if "flag" in item and item["flag"] is not None:
                families.append(("flag", lambda: _merge_scalar(conn, image_id, content_hash, item, "flag", "flag")))
            if "develop_settings" in item and item["develop_settings"] is not None:
                families.append(("develop", lambda: _merge_develop(conn, image_id, content_hash, item)))
            if "rating" in item and item["rating"] is not None:
                families.append(("rating", lambda: _merge_rating(conn, image_id, content_hash, item)))
            if "iptc" in item and item["iptc"] is not None:
                families.append(("iptc", lambda: _merge_iptc(conn, image_id, content_hash, item)))
            for family, merger in families:
                applied, reason = await merger()
                (result["applied"] if applied else result["skipped"]).append(
                    family if applied else {"family": family, "reason": reason}
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await connection.close_async(conn, db_path=db_path)

        # Reuse the library's canonical hierarchy resolver and assignment path.
        for path in item.get("keywords") or []:
            keyword = await keywords.resolve_keyword_path(path)
            await keywords.assign_keyword([image_id], int(keyword["id"]), origin="sync")
        if item.get("keywords"):
            result["applied"].append("keywords")
        results.append(result)
    return {"items": results}


async def base_artifacts(db_path: str, content_hash: str) -> tuple[rawproc.BasePaths, dict[str, Any]]:
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until a hub base is requested

    await ensure_sync_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT id, filepath FROM images WHERE content_hash = ? ORDER BY id LIMIT 1",
            (validate_content_hash(content_hash),),
        )).fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)
    if row is None:
        raise LookupError("Unknown content hash")
    return await asyncio.to_thread(rawproc.ensure_base_cache, int(row["id"]), row["filepath"])


async def multipart_base_stream(paths: rawproc.BasePaths) -> AsyncIterator[bytes]:
    boundary = "photoarchive-pabase1"
    yield (
        f"--{boundary}\r\nContent-Disposition: attachment; name=\"base\"; filename=\"base.bin.gz\"\r\n"
        "Content-Type: application/octet-stream\r\nContent-Encoding: gzip\r\n\r\n"
    ).encode()
    with paths.binary.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            yield chunk
    yield (
        f"\r\n--{boundary}\r\nContent-Disposition: attachment; name=\"meta\"; filename=\"base.json\"\r\n"
        "Content-Type: application/json\r\n\r\n"
    ).encode()
    yield paths.metadata.read_bytes()
    yield f"\r\n--{boundary}--\r\n".encode()


async def hash_backfill_batch(
    db_path: str,
    *,
    after_id: int = 0,
    limit: int = BACKFILL_BATCH_SIZE,
) -> tuple[int, dict[str, int]]:
    await ensure_sync_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT id, filepath FROM images WHERE id > ? AND content_hash IS NULL ORDER BY id LIMIT ?",
            (after_id, limit),
        )).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)
    updates: list[tuple[str, int]] = []
    missing = 0
    for row in rows:
        try:
            digest = await asyncio.to_thread(compute_content_hash, row["filepath"])
        except OSError:
            missing += 1
            continue
        updates.append((digest, int(row["id"])))
    if updates:
        conn = await connection.open_async(db_path)
        try:
            await conn.executemany(
                "UPDATE images SET content_hash = ? WHERE id = ? AND content_hash IS NULL", updates
            )
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)
    cursor = int(rows[-1]["id"]) if rows else after_id
    return cursor, {"hashed": len(updates), "missing": missing}


async def run_hash_backfill(db_path: str, status: dict[str, Any]) -> None:
    status.update(state="running", started_at=time.time(), cursor=0, counts={"hashed": 0, "missing": 0}, error="")
    try:
        while True:
            cursor, changes = await hash_backfill_batch(db_path, after_id=int(status["cursor"]))
            if cursor == status["cursor"]:
                break
            status["cursor"] = cursor
            for field, count in changes.items():
                status["counts"][field] += count
            await asyncio.sleep(BACKFILL_THROTTLE_SECONDS)
        status.update(state="complete", finished_at=time.time())
    except Exception as exc:
        status.update(state="error", error=str(exc), finished_at=time.time())
