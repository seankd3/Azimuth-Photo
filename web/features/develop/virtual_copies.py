"""Virtual-copy storage for Develop.

A virtual copy is a regular ``images`` row with ``vc_of`` pointing at its
source rendition.  That keeps existing image-facing features usable while the
library's representative/count queries exclude copies at their boundary.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from data.repositories import image_deletion
from data.repositories.common import chunked


VC_COLUMN = "vc_of"
VC_INDEX = "idx_images_vc_of"
ORIGINAL_FILEPATH_INDEX = "idx_images_original_filepath"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


async def ensure_virtual_copies(conn) -> None:
    """Add the additive virtual-copy column/index when this schema is opened.

    ``ALTER TABLE ... ADD COLUMN`` has no portable ``IF NOT EXISTS`` form in
    SQLite, so the schema owner calls this idempotent helper just as it calls
    the Develop presets helper.  The owner migration also removes the legacy
    unique filepath constraint: copies intentionally share that path.
    """
    cursor = await conn.execute("PRAGMA table_info(images)")
    columns = {row["name"] for row in await cursor.fetchall()}
    cursor = await conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'images'")
    table = await cursor.fetchone()
    table_sql = str(table["sql"] or "") if table else ""
    if re.search(r"\bfilepath\s+TEXT\s+NOT\s+NULL\s+UNIQUE\b", table_sql, re.IGNORECASE):
        await _rebuild_images_without_filepath_uniqueness(conn, table_sql, columns)
        cursor = await conn.execute("PRAGMA table_info(images)")
        columns = {row["name"] for row in await cursor.fetchall()}
    if VC_COLUMN not in columns:
        await conn.execute("ALTER TABLE images ADD COLUMN vc_of INTEGER REFERENCES images(id) ON DELETE CASCADE")
    await conn.execute(f"CREATE INDEX IF NOT EXISTS {VC_INDEX} ON images({VC_COLUMN})")
    await conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {ORIGINAL_FILEPATH_INDEX} "
        f"ON images(filepath) WHERE {VC_COLUMN} IS NULL"
    )


async def _rebuild_images_without_filepath_uniqueness(conn, table_sql: str, columns: set[str]) -> None:
    """One-time SQLite rebuild for the legacy ``filepath UNIQUE`` contract.

    SQLite cannot drop a table-level UNIQUE constraint.  Preserve every named
    images index/trigger, including the FTS triggers, then rebuild the external
    content index after the swap.  This runs before the schema owner's main
    transaction, because SQLite only permits changing foreign-key mode outside
    a transaction.
    """
    cursor = await conn.execute("PRAGMA foreign_keys")
    foreign_keys_were_on = bool((await cursor.fetchone())[0])
    cursor = await conn.execute(
        "SELECT type, sql FROM sqlite_master WHERE tbl_name = 'images' "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
    )
    image_objects = [row["sql"] for row in await cursor.fetchall()]
    replacement = re.sub(
        r"\bfilepath\s+TEXT\s+NOT\s+NULL\s+UNIQUE\b",
        "filepath TEXT NOT NULL",
        table_sql,
        flags=re.IGNORECASE,
    )
    replacement = re.sub(r"^CREATE TABLE(?: IF NOT EXISTS)?\s+images\b", "CREATE TABLE images_vc_rebuild", replacement, flags=re.IGNORECASE)
    if VC_COLUMN not in columns:
        head, tail = replacement.rsplit(")", 1)
        replacement = f"{head}, {VC_COLUMN} INTEGER REFERENCES images(id) ON DELETE CASCADE){tail}"
    copy_columns = [column for column in columns if column != VC_COLUMN]
    quoted = ", ".join(copy_columns)

    await conn.commit()
    await conn.execute("PRAGMA foreign_keys=OFF")
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute(replacement)
        await conn.execute(
            f"INSERT INTO images_vc_rebuild ({quoted}) SELECT {quoted} FROM images"
        )
        await conn.execute("DROP TABLE images")
        await conn.execute("ALTER TABLE images_vc_rebuild RENAME TO images")
        for object_sql in image_objects:
            await conn.execute(object_sql)
        try:
            await conn.execute("INSERT INTO images_metadata_fts(images_metadata_fts) VALUES('rebuild')")
        except Exception:
            # Lightweight test databases and databases predating FTS have no
            # external-content index to rebuild.
            pass
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        if foreign_keys_were_on:
            await conn.execute("PRAGMA foreign_keys=ON")


async def _image_row(conn, image_id: int):
    cursor = await conn.execute("SELECT * FROM images WHERE id = ?", (image_id,))
    return await cursor.fetchone()


async def _root_id(conn, image_id: int) -> int | None:
    row = await _image_row(conn, image_id)
    if row is None:
        return None
    return int(row[VC_COLUMN] or row["id"])


def _copy_columns(row: dict[str, Any]) -> list[str]:
    """All image metadata travels with a copy, except identity/lineage."""
    return [name for name in row if name not in {"id", VC_COLUMN}]


async def create_virtual_copy(conn, image_id: int) -> dict[str, Any] | None:
    """Clone an image rendition and its current Develop settings atomically."""
    source = await _image_row(conn, image_id)
    if source is None:
        return None
    source_data = dict(source)
    root_id = int(source_data[VC_COLUMN] or source_data["id"])
    columns = _copy_columns(source_data)
    placeholders = ", ".join("?" for _ in columns)
    values = [source_data[name] for name in columns]
    cursor = await conn.execute(
        f"INSERT INTO images ({', '.join(columns)}, {VC_COLUMN}) VALUES ({placeholders}, ?)",
        [*values, root_id],
    )
    copy_id = int(cursor.lastrowid)

    settings_cursor = await conn.execute(
        "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
    )
    settings_row = await settings_cursor.fetchone()
    encoded = settings_row["settings"] if settings_row is not None else "{}"
    master_settings_cursor = await conn.execute(
        "SELECT origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
        (root_id,),
    )
    master_settings = await master_settings_cursor.fetchone()
    origin = str(master_settings["origin"] or "user") if master_settings is not None else "user"
    xmp_path = master_settings["xmp_path"] if master_settings is not None else None
    xmp_mtime = master_settings["xmp_mtime"] if master_settings is not None else None
    baseline_cursor = await conn.execute(
        "SELECT settings FROM develop_history WHERE image_id = ? AND label = 'Import from XMP' "
        "ORDER BY id ASC LIMIT 1",
        (root_id,),
    )
    xmp_baseline = await baseline_cursor.fetchone()
    now = _now()
    await conn.execute(
        "INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (copy_id, encoded, origin, xmp_path, xmp_mtime, now),
    )
    await conn.execute(
        "INSERT INTO develop_history (image_id, settings, label, created_at) VALUES (?, ?, ?, ?)",
        (copy_id, encoded, "Virtual Copy", now),
    )
    if xmp_baseline is not None:
        await conn.execute(
            "INSERT INTO develop_history (image_id, settings, label, created_at) VALUES (?, ?, ?, ?)",
            (copy_id, xmp_baseline["settings"], "Import from XMP", now),
        )
    row = await _image_row(conn, copy_id)
    return image_to_virtual_copy(row)


def image_to_virtual_copy(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "vc_of": int(data[VC_COLUMN]),
        "filename": data.get("filename") or "",
        "filepath": data.get("filepath") or "",
        "created_at": data.get("created_at"),
    }


async def list_virtual_copies(conn, image_id: int) -> list[dict[str, Any]] | None:
    """List sibling copies for either the original or a selected copy."""
    root_id = await _root_id(conn, image_id)
    if root_id is None:
        return None
    cursor = await conn.execute(
        "SELECT * FROM images WHERE vc_of = ? ORDER BY id ASC", (root_id,)
    )
    return [image_to_virtual_copy(row) for row in await cursor.fetchall()]


async def delete_virtual_copy(conn, image_id: int) -> bool:
    """Delete only a copy; originals are never removable through this API."""
    row = await _image_row(conn, image_id)
    if row is None or row[VC_COLUMN] is None:
        return False
    expanded_ids = await image_deletion.expand_image_deletion_ids(conn, [image_id])
    await image_deletion.prepare_image_deletion(conn, expanded_ids)
    for ids in chunked(expanded_ids):
        placeholders = ", ".join("?" for _ in ids)
        await conn.execute(f"DELETE FROM images WHERE id IN ({placeholders})", ids)
    return True


async def is_virtual_copy_of(conn, image_id: int, copy_id: int) -> bool:
    """Return whether ``copy_id`` belongs to the root family for ``image_id``."""
    root_id = await _root_id(conn, image_id)
    copy = await _image_row(conn, copy_id)
    return bool(root_id is not None and copy is not None and copy[VC_COLUMN] == root_id)
