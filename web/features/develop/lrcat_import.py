"""Read Lightroom catalogs without ever opening their working copies.

The catalog is copied to a temporary directory first because Lightroom keeps
its SQLite database live while it is open.  Imported catalog state is kept
deliberately narrow: picks, ratings, and real manual collections.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from data import connection
from data.repositories import collections as collections_repository


DEFAULT_CATALOG_ROOT = "/mnt/expansion/Photo Library Support/Lightroom/Catalogs"
CATALOG_YEARS = frozenset({"2020", "2021", "2022", "2023"})
_RAW_MARKER = "photos/raws/"
_RAW_TAIL = "raws/"
_VERSION_RE = re.compile(r"-v(\d+)(?:\D|$)", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_LOG = logging.getLogger(__name__)

_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "running": False,
    "catalogs": 0,
    "images": 0,
    "matched": 0,
    "errors": 0,
    "current": "",
    "results": [],
}


def import_status() -> dict[str, Any]:
    with _status_lock:
        result = dict(_status)
        result["results"] = list(_status["results"])
        return result


def begin_scan() -> bool:
    """Claim the LRCAT importer before its background task is scheduled."""

    with _status_lock:
        if _status["running"]:
            return False
        _status.update(running=True, catalogs=0, images=0, matched=0, errors=0, current="", results=[])
        return True


def finish_scan() -> None:
    with _status_lock:
        _status["running"] = False


def _set_current(catalog_path: str) -> None:
    with _status_lock:
        _status["current"] = catalog_path


def _record_result(result: dict[str, Any]) -> None:
    with _status_lock:
        _status["catalogs"] += 1
        _status["images"] += int(result.get("catalog_images", 0))
        _status["matched"] += int(result.get("matched", 0))
        _status["errors"] += int(result.get("errors", 0))
        _status["results"].append(result)


def catalog_paths(root: str = DEFAULT_CATALOG_ROOT) -> list[str]:
    """Return one highest-version catalog for every year directory."""

    candidates = sorted(Path(root).glob("**/*.lrcat")) if os.path.isdir(root) else []
    per_year: dict[str, tuple[int, str]] = {}
    for candidate in candidates:
        year = _catalog_year(str(candidate))
        if year not in CATALOG_YEARS:
            continue
        version_match = _VERSION_RE.search(candidate.name)
        version = int(version_match.group(1)) if version_match else 0
        current = per_year.get(year)
        if current is None or version > current[0] or (version == current[0] and str(candidate) > current[1]):
            per_year[year] = (version, str(candidate))
    return [per_year[year][1] for year in sorted(per_year)]


def _catalog_year(catalog_path: str) -> str | None:
    for part in reversed(Path(catalog_path).parts):
        match = _YEAR_RE.search(part)
        if match:
            return match.group(0)
    return None


@contextmanager
def _catalog_copy(catalog_path: str):
    """Yield a read-only connection to a temporary copy, including its WAL."""

    source = os.path.abspath(catalog_path)
    if not os.path.isfile(source):
        raise ValueError(f"Lightroom catalog does not exist: {source}")
    with tempfile.TemporaryDirectory(prefix="photoarchive-lrcat-") as directory:
        copied = os.path.join(directory, os.path.basename(source))
        shutil.copy2(source, copied)
        for suffix in ("-wal", "-shm"):
            sidecar = source + suffix
            if os.path.isfile(sidecar):
                shutil.copy2(sidecar, copied + suffix)
        uri = f"file:{quote(copied)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _slash_path(value: str | None) -> str:
    return str(value or "").replace("\\", "/").strip()


def reconstruct_path(root: str | None, folder: str | None, basename: str | None, extension: str | None) -> str:
    """Rebuild a catalog image path without interpreting Windows drive syntax."""

    name = str(basename or "")
    suffix = str(extension or "").lstrip(".")
    filename = name if not suffix else f"{name}.{suffix}"
    return posixpath.normpath(posixpath.join(_slash_path(root), _slash_path(folder), filename))


def _raw_suffix(path: str | None) -> str | None:
    normalized = _slash_path(path).casefold().lstrip("/")
    marker_at = normalized.rfind(_RAW_MARKER)
    if marker_at >= 0:
        return normalized[marker_at + len("photos/"):]
    marker_at = normalized.rfind(_RAW_TAIL)
    return normalized[marker_at:] if marker_at >= 0 else None


def _capture_date(value: object) -> str | None:
    match = re.search(r"(?:19|20)\d{2}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def _catalog_images(catalog: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return catalog.execute(
        """
        SELECT ai.id_local, ai.pick, ai.rating, ai.captureTime,
               root.absolutePath AS root_path, folder.pathFromRoot AS folder_path,
               file.baseName, file.extension
        FROM Adobe_images ai
        JOIN AgLibraryFile file ON file.id_local = ai.rootFile
        LEFT JOIN AgLibraryFolder folder ON folder.id_local = file.folder
        LEFT JOIN AgLibraryRootFolder root ON root.id_local = folder.rootFolder
        """
    )


def _library_maps(conn: sqlite3.Connection) -> tuple[dict[str, sqlite3.Row], dict[str, list[sqlite3.Row]], dict[tuple[str, str], list[sqlite3.Row]]]:
    rows = list(conn.execute("SELECT id, filepath, filename, date_taken, flag FROM images"))
    exact = {str(row["filepath"]): row for row in rows}
    suffixes: dict[str, list[sqlite3.Row]] = defaultdict(list)
    dated_names: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        suffix = _raw_suffix(row["filepath"])
        if suffix:
            suffixes[suffix].append(row)
        date = _capture_date(row["date_taken"])
        if date:
            dated_names[(str(row["filename"]).casefold(), date)].append(row)
    return exact, suffixes, dated_names


def _match_image(
    reconstructed: str,
    filename: str,
    capture_time: object,
    exact: dict[str, sqlite3.Row],
    suffixes: dict[str, list[sqlite3.Row]],
    dated_names: dict[tuple[str, str], list[sqlite3.Row]],
) -> tuple[sqlite3.Row | None, str | None]:
    direct = exact.get(reconstructed)
    if direct is not None:
        return direct, "exact"
    suffix = _raw_suffix(reconstructed)
    candidates = suffixes.get(suffix or "", [])
    if len(candidates) == 1:
        return candidates[0], "path_suffix"
    date = _capture_date(capture_time)
    candidates = dated_names.get((filename.casefold(), date or ""), []) if date else []
    if len(candidates) == 1:
        return candidates[0], "filename_date"
    return None, None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _store_rating(conn: sqlite3.Connection, image_id: int, rating: int, *, rating_column: bool) -> bool:
    if rating_column:
        cursor = conn.execute("UPDATE images SET rating = ? WHERE id = ? AND COALESCE(rating, -1) != ?", (rating, image_id, rating))
        return cursor.rowcount > 0
    row = conn.execute("SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)).fetchone()
    try:
        settings = json.loads(row["settings"]) if row else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        settings = {}
    if settings.get("_lr_rating") == rating:
        return False
    settings["_lr_rating"] = rating
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = json.dumps(settings, separators=(",", ":"), ensure_ascii=True)
    if row:
        conn.execute("UPDATE develop_settings SET settings = ?, updated_at = ? WHERE image_id = ?", (payload, now, image_id))
    else:
        conn.execute(
            "INSERT INTO develop_settings (image_id, settings, origin, updated_at) VALUES (?, ?, 'lrcat', ?)",
            (image_id, payload, now),
        )
    return True


def _rating_needs_update(conn: sqlite3.Connection, image_id: int, rating: int, *, rating_column: bool) -> bool:
    if rating_column:
        row = conn.execute("SELECT rating FROM images WHERE id = ?", (image_id,)).fetchone()
        return row is not None and row["rating"] != rating
    row = conn.execute("SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)).fetchone()
    if row is None:
        return True
    try:
        return json.loads(row["settings"]).get("_lr_rating") != rating
    except (TypeError, ValueError, json.JSONDecodeError):
        return True


def _catalog_keywords(catalog: sqlite3.Connection, matched_ids: set[int]) -> int:
    """Count keywords only: existing tag rows are caption-model-owned, not user tags."""

    count = 0
    rows = catalog.execute(
        """
        SELECT ki.image
        FROM AgLibraryKeywordImage ki
        JOIN AgLibraryKeyword keyword ON keyword.id_local = ki.tag
        """
    )
    for row in rows:
        if int(row["image"]) in matched_ids:
            count += 1
    return count


def _catalog_collections(catalog: sqlite3.Connection, matched_ids: dict[int, int], year: str) -> dict[str, list[int]]:
    collections: dict[str, list[int]] = defaultdict(list)
    rows = catalog.execute(
        """
        SELECT collection.name, ci.image
        FROM AgLibraryCollection collection
        LEFT JOIN AgLibraryCollectionImage ci ON ci.collection = collection.id_local
        WHERE collection.creationId = 'com.adobe.ag.library.collection'
        """
    )
    for row in rows:
        name = f"LR {year}/{row['name']}"
        collections.setdefault(name, [])
        image_id = matched_ids.get(int(row["image"])) if row["image"] is not None else None
        if image_id is not None:
            collections[name].append(image_id)
    return collections


async def _persist_collections(db_path: str, pending: dict[str, list[int]], *, dry_run: bool) -> tuple[int, int]:
    existing = {row["name"]: row for row in await collections_repository.list_collections(db_path)}
    created = 0
    updated = 0
    for name, image_ids in pending.items():
        unique_ids = list(dict.fromkeys(image_ids))
        if not unique_ids:
            continue
        collection = existing.get(name)
        if collection is None:
            if not dry_run:
                await collections_repository.create_collection(db_path, name=name, image_ids=unique_ids)
            created += 1
        else:
            if not dry_run:
                await collections_repository.add_images(db_path, int(collection["id"]), unique_ids)
            updated += 1
    return created, updated


def import_lrcat(catalog_path: str, db_path: str, dry_run: bool = False) -> dict[str, Any]:
    """Import one catalog, returning truthful match and write counts.

    Lightroom source files are copied before SQLite reads; photoArchive writes
    are entirely skipped for ``dry_run``.
    """

    year = _catalog_year(catalog_path)
    if year is None:
        raise ValueError(f"Could not infer a catalog year from: {catalog_path}")
    result: dict[str, Any] = {
        "catalog_path": os.path.abspath(catalog_path), "year": year, "dry_run": dry_run,
        "catalog_images": 0, "matched": 0, "match_rate": 0.0,
        "exact_matches": 0, "suffix_matches": 0, "filename_date_matches": 0,
        "picks_updated": 0, "picks_protected": 0, "ratings_updated": 0,
        "keywords_skipped": 0, "collections_created": 0, "collections_updated": 0,
        "collections_skipped_empty": 0, "errors": 0, "sample_paths": [],
    }
    library = connection.open_sync(db_path)
    try:
        exact, suffixes, dated_names = _library_maps(library)
        rating_column = _has_column(library, "images", "rating")
        matched_catalog_ids: dict[int, int] = {}
        with _catalog_copy(catalog_path) as catalog:
            for row in _catalog_images(catalog):
                result["catalog_images"] += 1
                reconstructed = reconstruct_path(row["root_path"], row["folder_path"], row["baseName"], row["extension"])
                if len(result["sample_paths"]) < 3:
                    result["sample_paths"].append(reconstructed)
                image, mode = _match_image(reconstructed, str(row["baseName"] or "") + ("." + str(row["extension"] or "").lstrip(".") if row["extension"] else ""), row["captureTime"], exact, suffixes, dated_names)
                if image is None:
                    continue
                catalog_id = int(row["id_local"])
                image_id = int(image["id"])
                matched_catalog_ids[catalog_id] = image_id
                result["matched"] += 1
                result[{"exact": "exact_matches", "path_suffix": "suffix_matches", "filename_date": "filename_date_matches"}[mode]] += 1
                pick = int(row["pick"] or 0)
                if pick in (1, -1):
                    target = "picked" if pick == 1 else "rejected"
                    if image["flag"] == "unflagged":
                        if not dry_run:
                            library.execute("UPDATE images SET flag = ? WHERE id = ? AND flag = 'unflagged'", (target, image_id))
                        result["picks_updated"] += 1
                    else:
                        result["picks_protected"] += 1
                rating = int(row["rating"] or 0)
                rating_changed = _rating_needs_update(library, image_id, rating, rating_column=rating_column)
                if rating_changed and not dry_run:
                    _store_rating(library, image_id, rating, rating_column=rating_column)
                if rating_changed:
                    result["ratings_updated"] += 1
            result["keywords_skipped"] = _catalog_keywords(catalog, set(matched_catalog_ids))
            pending_collections = _catalog_collections(catalog, matched_catalog_ids, year)
        result["collections_skipped_empty"] = sum(1 for ids in pending_collections.values() if not ids)
        result["match_rate"] = round(result["matched"] / result["catalog_images"], 6) if result["catalog_images"] else 0.0
        if not dry_run:
            library.commit()
    except Exception:
        library.rollback()
        result["errors"] += 1
        raise
    finally:
        connection.close_sync(library, db_path=db_path)
    created, updated = asyncio.run(_persist_collections(db_path, pending_collections, dry_run=dry_run))
    result["collections_created"] = created
    result["collections_updated"] = updated
    return result


def scan_catalogs(paths: Iterable[str], db_path: str, *, dry_run: bool = False, claimed: bool = False) -> list[dict[str, Any]]:
    """Run selected catalogs serially so each temporary copy is released quickly."""

    if not claimed and not begin_scan():
        return []
    results: list[dict[str, Any]] = []
    try:
        for path in paths:
            _set_current(path)
            try:
                result = import_lrcat(path, db_path, dry_run=dry_run)
            except Exception as exc:
                _LOG.exception("Lightroom catalog import failed: %s", path)
                result = {"catalog_path": path, "errors": 1, "error": str(exc)}
            results.append(result)
            _record_result(result)
        return results
    finally:
        finish_scan()
