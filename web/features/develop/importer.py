"""Resumable XMP-sidecar import for Develop RAW sources.

This module deliberately stores all Camera Raw settings, including v1 settings
that the renderer does not yet implement (masks, lens corrections, etc.).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from typing import Any

import scanner
from data import connection
from data.repositories import catalog as catalog_repository
from features.develop.discovery import default_raw_import_root
from features.develop.xmp_write import parse_xmp_text

from photo import kind


DEFAULT_RAWS_ROOT = default_raw_import_root()
# Matches xmp_write._RAW_EXTENSIONS: any raw we write sidecars for must also
# have its Lightroom sidecars imported back.
RAW_EXTENSIONS = kind.RAW_FORMATS
_LOG = logging.getLogger(__name__)

_status_lock = threading.Lock()
_status: dict[str, int | bool | str] = {
    "running": False,
    "seen": 0,
    "imported": 0,
    "sidecars": 0,
    "skipped": 0,
    "errors": 0,
    "root": "",
}


def import_status() -> dict[str, int | bool | str]:
    """Return a point-in-time copy of the current import progress."""

    with _status_lock:
        return dict(_status)


def _begin_status(root: str) -> bool:
    with _status_lock:
        if _status["running"]:
            return False
        _status.update(
            running=True,
            seen=0,
            imported=0,
            sidecars=0,
            skipped=0,
            errors=0,
            root=root,
        )
        return True


def _finish_status() -> None:
    with _status_lock:
        _status["running"] = False


def finish_scan() -> None:
    """Release a route-level scan claim if its worker cannot start."""

    _finish_status()


def _increment(name: str, amount: int = 1) -> None:
    with _status_lock:
        _status[name] = int(_status[name]) + amount


def read_embedded_xmp(raw_path: str) -> bytes | None:
    """Extract the XMP packet embedded in a DNG/TIFF container (LR writes
    develop settings into DNGs directly; sidecars only exist for proprietary raws)."""
    start_tag, end_tag = b"<x:xmpmeta", b"</x:xmpmeta>"
    chunk_size = 1 << 22
    overlap = len(start_tag)
    buf = b""
    packet_start = -1
    collected = bytearray()
    with open(raw_path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return None
            buf = buf[-overlap:] + chunk if packet_start < 0 else chunk
            if packet_start < 0:
                idx = buf.find(start_tag)
                if idx < 0:
                    continue
                packet_start = idx
                collected.extend(buf[idx:])
            else:
                collected.extend(chunk)
            end = collected.find(end_tag)
            if end >= 0:
                return bytes(collected[: end + len(end_tag)])
            if len(collected) > (1 << 24):
                return None


def parse_xmp_file(xmp_path: str) -> dict[str, Any]:
    with open(xmp_path, "rb") as handle:
        return parse_xmp_text(handle.read())


def _iter_raw_rows(root: str) -> Iterator[tuple]:
    """Use the scanner's source-boundary-safe RAW enumeration."""

    for row in scanner.walk_images(root):
        if row[2].lower() in RAW_EXTENSIONS:
            yield row


def _ensure_source(conn, root: str):
    normalized = catalog_repository.normalize_source_path(root)
    display_name = catalog_repository.source_display_name(normalized)
    now = time.time()
    conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, created_at, last_scan_at, last_seen_at) "
        "VALUES (?, ?, 1, 1, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "display_name = excluded.display_name, included = 1, online = excluded.online, "
        "last_seen_at = excluded.last_seen_at, removed_at = NULL",
        (normalized, display_name, now, now, now),
    )
    return conn.execute("SELECT id FROM catalog_sources WHERE path = ?", (normalized,)).fetchone()


def _ensure_image(conn, source_id: int, row: tuple) -> tuple[int, bool]:
    filename, filepath, extension, size, modified_at = row[:5]
    cursor = conn.execute(
        "INSERT OR IGNORE INTO images "
        "(source_id, filename, filepath, status, file_ext, file_size, file_modified_at) "
        "VALUES (?, ?, ?, 'kept', ?, ?, ?)",
        (source_id, filename, filepath, extension, size, modified_at),
    )
    created = cursor.rowcount > 0
    conn.execute(
        "UPDATE images SET source_id = CASE WHEN source_id IS NULL THEN ? ELSE source_id END, "
        "filename = ?, file_ext = COALESCE(?, file_ext), file_size = COALESCE(?, file_size), "
        "file_modified_at = COALESCE(?, file_modified_at), missing_at = NULL "
        "WHERE filepath = ? AND vc_of IS NULL",
        (source_id, filename, extension, size, modified_at, filepath),
    )
    image = conn.execute(
        "SELECT id FROM images WHERE filepath = ? AND vc_of IS NULL",
        (filepath,),
    ).fetchone()
    if image is None:
        raise RuntimeError(f"could not register RAW: {filepath}")
    image_id = int(image["id"])
    catalog_repository.cascade_virtual_copy_missing_sync_on_conn(conn, [image_id])
    return image_id, created


def _needs_settings(conn, image_id: int, source_mtime: float) -> bool:
    existing = conn.execute(
        "SELECT origin, xmp_mtime FROM develop_settings WHERE image_id = ?", (image_id,)
    ).fetchone()
    if existing is None:
        return True
    if existing["origin"] == "user":
        return False
    return existing["xmp_mtime"] is None or float(existing["xmp_mtime"]) != source_mtime


def _write_settings(conn, image_id: int, xmp_path: str, xmp_mtime: float, settings: dict[str, Any]) -> bool:
    """Small local helper pending Develop store merge; never overwrite user work."""

    existing = conn.execute(
        "SELECT origin, xmp_mtime FROM develop_settings WHERE image_id = ?", (image_id,)
    ).fetchone()
    if existing and existing["origin"] == "user":
        return False
    if existing and existing["xmp_mtime"] is not None and float(existing["xmp_mtime"]) == xmp_mtime:
        return False
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at) "
        "VALUES (?, ?, 'xmp', ?, ?, ?) "
        "ON CONFLICT(image_id) DO UPDATE SET settings = excluded.settings, origin = 'xmp', "
        "xmp_path = excluded.xmp_path, xmp_mtime = excluded.xmp_mtime, updated_at = excluded.updated_at "
        "WHERE develop_settings.origin != 'user'",
        (image_id, json.dumps(settings, separators=(",", ":"), ensure_ascii=True), xmp_path, xmp_mtime, now),
    )
    return True


def _update_source_counts(conn, source_id: int) -> None:
    conn.execute(
        "UPDATE catalog_sources SET image_count = (SELECT COUNT(*) FROM images WHERE source_id = ?), "
        "active_image_count = (SELECT COUNT(*) FROM images WHERE source_id = ? "
        "AND status IN ('kept', 'maybe') AND missing_at IS NULL), last_scan_at = ?, last_seen_at = ? "
        "WHERE id = ?",
        (source_id, source_id, time.time(), time.time(), source_id),
    )


def begin_scan(root: str) -> str | None:
    """Claim a scan synchronously, so the HTTP response reports it as running."""

    resolved_root = catalog_repository.normalize_source_path(root or DEFAULT_RAWS_ROOT)
    if not os.path.isdir(resolved_root):
        raise ValueError(f"RAW root does not exist: {resolved_root}")
    return resolved_root if _begin_status(resolved_root) else None


def scan_raws(root: str, db_path: str, *, claimed: bool = False) -> dict[str, Any]:
    """Synchronously scan a RAW source; callers run this function in a thread."""

    resolved_root = catalog_repository.normalize_source_path(root or DEFAULT_RAWS_ROOT)
    if not os.path.isdir(resolved_root):
        raise ValueError(f"RAW root does not exist: {resolved_root}")
    if not claimed and not _begin_status(resolved_root):
        return {"started": False, "status": import_status(), "images": []}

    imported_images: list[dict[str, Any]] = []
    conn = None
    try:
        conn = connection.open_sync(db_path)
        source = _ensure_source(conn, resolved_root)
        source_id = int(source["id"])
        for index, row in enumerate(_iter_raw_rows(resolved_root), start=1):
            _increment("seen")
            try:
                image_id, created = _ensure_image(conn, source_id, row)
                if created:
                    _increment("imported")
                if len(imported_images) < 60:
                    imported_images.append({"id": image_id, "filepath": row[1]})
                xmp_path = os.path.splitext(row[1])[0] + ".xmp"
                if os.path.isfile(xmp_path):
                    xmp_mtime = float(os.path.getmtime(xmp_path))
                    settings = parse_xmp_text(open(xmp_path, "rb").read())
                elif row[1].lower().endswith((".dng", ".tif", ".tiff")):
                    xmp_mtime = float(os.path.getmtime(row[1]))
                    if not _needs_settings(conn, image_id, xmp_mtime):
                        _increment("skipped")
                        continue
                    packet = read_embedded_xmp(row[1])
                    if packet is None:
                        continue
                    xmp_path = row[1]
                    settings = parse_xmp_text(packet)
                else:
                    continue
                if not settings:
                    continue
                if _write_settings(conn, image_id, xmp_path, xmp_mtime, settings):
                    _increment("sidecars")
                else:
                    _increment("skipped")
            except Exception:
                _increment("errors")
                _LOG.exception("Develop import failed for %s", row[1])
            if index % 500 == 0:
                conn.commit()
                _LOG.info("Develop import: %s", import_status())
        _update_source_counts(conn, source_id)
        conn.commit()
        return {"started": True, "status": import_status(), "images": imported_images}
    finally:
        if conn is not None:
            connection.close_sync(conn, db_path=db_path)
        _finish_status()
