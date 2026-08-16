"""Develop presets — SQLite storage, CRUD, and Lightroom .xmp import.

v22 adds ``develop_presets``. Schema ownership stays in ``data.schema``; this
module owns the DDL snippet + ensure helper that schema.py calls (see PATCH).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from features.develop import history

from features.develop.discovery import lightroom_preset_roots
from features.develop.importer import parse_xmp_text

_LOG = logging.getLogger(__name__)

DEVELOP_PRESETS_DDL = """
CREATE TABLE IF NOT EXISTS develop_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_develop_presets_folder
ON develop_presets(folder, name);
"""

LIGHTROOM_FOLDER = "Lightroom"

_import_report: dict[str, Any] = {
    "scanned": False,
    "roots_found": [],
    "xmp_found": 0,
    "imported": 0,
    "skipped": 0,
    "errors": 0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_settings(raw: str | None) -> dict[str, Any]:
    try:
        result = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _row_to_preset(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "name": data["name"],
        "folder": data["folder"] or "",
        "settings": _json_settings(data["settings"]),
        "created_at": data["created_at"],
    }


async def ensure_develop_presets(conn) -> None:
    """Create develop_presets if missing (idempotent; safe on every init)."""
    await conn.executescript(DEVELOP_PRESETS_DDL)


def import_report() -> dict[str, Any]:
    return dict(_import_report)


def discover_lr_preset_paths(roots: tuple[Path, ...] | None = None) -> list[Path]:
    """Return .xmp files under explicit or platform Lightroom preset folders."""
    search_roots = roots if roots is not None else tuple(Path(path) for path in lightroom_preset_roots())
    found: list[Path] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if filename.lower().endswith(".xmp"):
                    found.append(Path(dirpath) / filename)
    return found


def _preset_name_for_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    stem_parts = list(relative.with_suffix("").parts)
    return " / ".join(stem_parts) if stem_parts else path.stem


def import_lightroom_presets_sync(conn, *, roots: tuple[Path, ...] | None = None) -> dict[str, Any]:
    """Parse LR .xmp presets into folder ``Lightroom``. Idempotent by name+folder.

    Uses the shared XMP parser. Never raises for missing roots — reports only.
    """
    roots = roots if roots is not None else tuple(Path(path) for path in lightroom_preset_roots())
    report = {
        "scanned": True,
        "roots_found": [],
        "xmp_found": 0,
        "imported": 0,
        "skipped": 0,
        "errors": 0,
    }
    conn.executescript(DEVELOP_PRESETS_DDL)
    existing = {
        (row["folder"] or "", row["name"])
        for row in conn.execute("SELECT folder, name FROM develop_presets").fetchall()
    }
    for root in roots:
        if not root.is_dir():
            continue
        report["roots_found"].append(str(root))
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if not filename.lower().endswith(".xmp"):
                    continue
                path = Path(dirpath) / filename
                report["xmp_found"] += 1
                name = _preset_name_for_path(path, root)
                key = (LIGHTROOM_FOLDER, name)
                if key in existing:
                    report["skipped"] += 1
                    continue
                try:
                    settings = parse_xmp_text(path.read_bytes())
                except Exception:
                    _LOG.exception("Failed to parse LR preset %s", path)
                    report["errors"] += 1
                    continue
                if not settings:
                    report["skipped"] += 1
                    continue
                conn.execute(
                    "INSERT INTO develop_presets (name, folder, settings, created_at) VALUES (?, ?, ?, ?)",
                    (name, LIGHTROOM_FOLDER, json.dumps(settings, separators=(",", ":")), _now()),
                )
                existing.add(key)
                report["imported"] += 1
    conn.commit()
    _import_report.clear()
    _import_report.update(report)
    return report


async def import_lightroom_presets(conn, *, roots: tuple[Path, ...] | None = None) -> dict[str, Any]:
    """Async wrapper — runs the sync importer against an aiosqlite connection."""
    await ensure_develop_presets(conn)
    roots = roots if roots is not None else tuple(Path(path) for path in lightroom_preset_roots())
    # aiosqlite connections expose the same execute API; reuse sync logic via thread
    # would need a sync sqlite3 handle. Inline the async path instead.
    report = {
        "scanned": True,
        "roots_found": [],
        "xmp_found": 0,
        "imported": 0,
        "skipped": 0,
        "errors": 0,
    }
    cursor = await conn.execute("SELECT folder, name FROM develop_presets")
    existing = {(row["folder"] or "", row["name"]) for row in await cursor.fetchall()}
    for root in roots:
        if not root.is_dir():
            continue
        report["roots_found"].append(str(root))
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if not filename.lower().endswith(".xmp"):
                    continue
                path = Path(dirpath) / filename
                report["xmp_found"] += 1
                name = _preset_name_for_path(path, root)
                key = (LIGHTROOM_FOLDER, name)
                if key in existing:
                    report["skipped"] += 1
                    continue
                try:
                    settings = parse_xmp_text(path.read_bytes())
                except Exception:
                    _LOG.exception("Failed to parse LR preset %s", path)
                    report["errors"] += 1
                    continue
                if not settings:
                    report["skipped"] += 1
                    continue
                await conn.execute(
                    "INSERT INTO develop_presets (name, folder, settings, created_at) VALUES (?, ?, ?, ?)",
                    (name, LIGHTROOM_FOLDER, json.dumps(settings, separators=(",", ":")), _now()),
                )
                existing.add(key)
                report["imported"] += 1
    await conn.commit()
    _import_report.clear()
    _import_report.update(report)
    return report


async def list_presets(conn) -> list[dict[str, Any]]:
    await ensure_develop_presets(conn)
    cursor = await conn.execute(
        "SELECT id, name, folder, settings, created_at FROM develop_presets "
        "ORDER BY folder COLLATE NOCASE, name COLLATE NOCASE, id"
    )
    return [_row_to_preset(row) for row in await cursor.fetchall()]


async def get_preset(conn, preset_id: int) -> dict[str, Any] | None:
    await ensure_develop_presets(conn)
    cursor = await conn.execute(
        "SELECT id, name, folder, settings, created_at FROM develop_presets WHERE id = ?",
        (preset_id,),
    )
    row = await cursor.fetchone()
    return _row_to_preset(row) if row else None


async def create_preset(
    conn,
    *,
    name: str,
    settings: dict[str, Any],
    folder: str = "",
) -> dict[str, Any]:
    await ensure_develop_presets(conn)
    clean_name = (name or "").strip() or "Untitled"
    clean_folder = (folder or "").strip()
    encoded = json.dumps(settings if isinstance(settings, dict) else {}, separators=(",", ":"))
    cursor = await conn.execute(
        "INSERT INTO develop_presets (name, folder, settings, created_at) VALUES (?, ?, ?, ?)",
        (clean_name, clean_folder, encoded, _now()),
    )
    await conn.commit()
    return await get_preset(conn, int(cursor.lastrowid))


async def rename_preset(
    conn,
    preset_id: int,
    *,
    name: str | None = None,
    folder: str | None = None,
) -> dict[str, Any] | None:
    await ensure_develop_presets(conn)
    current = await get_preset(conn, preset_id)
    if current is None:
        return None
    next_name = current["name"] if name is None else ((name or "").strip() or current["name"])
    next_folder = current["folder"] if folder is None else (folder or "").strip()
    await conn.execute(
        "UPDATE develop_presets SET name = ?, folder = ? WHERE id = ?",
        (next_name, next_folder, preset_id),
    )
    await conn.commit()
    return await get_preset(conn, preset_id)


async def delete_preset(conn, preset_id: int) -> bool:
    await ensure_develop_presets(conn)
    cursor = await conn.execute("DELETE FROM develop_presets WHERE id = ?", (preset_id,))
    await conn.commit()
    return cursor.rowcount > 0


async def apply_preset_to_image(conn, preset_id: int, image_id: int) -> dict[str, Any] | None:
    """Merge full preset settings onto the image and append history ``Preset: name``.

    Stores FULL settings (spec §15); merge keeps geometry/WB keys the preset omits.
    """
    preset = await get_preset(conn, preset_id)
    if preset is None:
        return None
    cursor = await conn.execute(
        "SELECT settings, origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
        (image_id,),
    )
    existing = await cursor.fetchone()
    prior = _json_settings(existing["settings"]) if existing else {}
    merged = {**prior, **preset["settings"]}
    now = _now()
    encoded = json.dumps(merged, separators=(",", ":"))
    origin = (existing["origin"] if existing else None) or "user"
    xmp_path = existing["xmp_path"] if existing else None
    xmp_mtime = existing["xmp_mtime"] if existing else None
    if existing and origin == "xmp":
        await history.record_async(
            conn, image_id, json.dumps(prior, separators=(",", ":")), "Import from XMP"
        )
        origin = "user"
    await conn.execute(
        """
        INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(image_id) DO UPDATE SET
            settings = excluded.settings,
            origin = excluded.origin,
            xmp_path = excluded.xmp_path,
            xmp_mtime = excluded.xmp_mtime,
            updated_at = excluded.updated_at
        """,
        (image_id, encoded, origin, xmp_path, xmp_mtime, now),
    )
    label = f"Preset: {preset['name']}"
    await history.record_async(conn, image_id, encoded, label)
    await conn.commit()
    return {
        "preset": preset,
        "settings": merged,
        "origin": origin,
        "updated_at": now,
        "label": label,
    }
