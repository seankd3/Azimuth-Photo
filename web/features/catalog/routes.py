import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time as _time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import scanner
import settings
import thumbnails
from core import cache_events
from core.background import track_background_task
from core.path_groups import safe_commonpath, safe_relpath
from core.requests import json_object
from data.repositories import catalog as catalog_repository
from data.repositories import imports as import_repository
from features.catalog.folder_roots import quick_browse_roots
from features.catalog import metadata as catalog_metadata
from features.catalog import reveal as catalog_reveal


router = APIRouter()
log = logging.getLogger(__name__)


class RevealBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    source_id: int | None = Field(default=None, ge=1)


InvalidatePairing = Callable[..., None]
InvalidateCacheStatus = Callable[[], None]
DbPathProvider = Callable[[], str]
RecentActiveImagesProvider = Callable[..., Awaitable[list]]
AddOrRestoreSourceProvider = Callable[[str], Awaitable]
GetScanFolderProvider = Callable[[], Awaitable[str | None]]
GetCatalogSummaryProvider = Callable[[], Awaitable[dict]]
GetSourceProvider = Callable[[int], Awaitable]
RemoveSourceKeepDataProvider = Callable[[int], Awaitable[None]]
GetSourceImageIdsProvider = Callable[[int], Awaitable[list[int]]]
PurgeSourceCatalogDataProvider = Callable[[int], Awaitable[dict]]
GetCatalogImageCountsProvider = Callable[[], Awaitable[dict]]
_invalidate_pairing_cache: InvalidatePairing | None = None
_invalidate_cache_status_cache: InvalidateCacheStatus | None = None
_db_path: DbPathProvider | None = None
_get_recent_active_images: RecentActiveImagesProvider | None = None
_add_or_restore_source: AddOrRestoreSourceProvider | None = None
_get_scan_folder: GetScanFolderProvider | None = None
_get_catalog_summary: GetCatalogSummaryProvider | None = None
_get_source: GetSourceProvider | None = None
_remove_source_keep_data: RemoveSourceKeepDataProvider | None = None
_get_source_image_ids: GetSourceImageIdsProvider | None = None
_purge_source_catalog_data: PurgeSourceCatalogDataProvider | None = None
_get_catalog_image_counts: GetCatalogImageCountsProvider | None = None
_folders_cache: dict[int | None, dict] = {}
_folders_refreshing: set[int | None] = set()
_folders_cache_ttl_seconds = 300.0
_folder_tree_cache: dict = {"data": None, "expires": 0}
_folder_tree_cache_ttl_seconds = 60.0
_folder_tree_max_depth = 6


def configure(
    *,
    invalidate_pairing_cache: InvalidatePairing,
    invalidate_cache_status_cache: InvalidateCacheStatus,
    db_path: DbPathProvider,
    get_recent_active_images: RecentActiveImagesProvider,
    add_or_restore_source: AddOrRestoreSourceProvider,
    get_scan_folder: GetScanFolderProvider,
    get_catalog_summary: GetCatalogSummaryProvider,
    get_source: GetSourceProvider,
    remove_source_keep_data: RemoveSourceKeepDataProvider,
    get_source_image_ids: GetSourceImageIdsProvider,
    purge_source_catalog_data: PurgeSourceCatalogDataProvider,
    get_catalog_image_counts: GetCatalogImageCountsProvider,
) -> None:
    global _invalidate_pairing_cache, _invalidate_cache_status_cache
    global _db_path, _get_recent_active_images, _add_or_restore_source
    global _get_scan_folder, _get_catalog_summary, _get_source
    global _remove_source_keep_data, _get_source_image_ids
    global _purge_source_catalog_data, _get_catalog_image_counts
    _invalidate_pairing_cache = invalidate_pairing_cache
    _invalidate_cache_status_cache = invalidate_cache_status_cache
    _db_path = db_path
    _get_recent_active_images = get_recent_active_images
    _add_or_restore_source = add_or_restore_source
    _get_scan_folder = get_scan_folder
    _get_catalog_summary = get_catalog_summary
    _get_source = get_source
    _remove_source_keep_data = remove_source_keep_data
    _get_source_image_ids = get_source_image_ids
    _purge_source_catalog_data = purge_source_catalog_data
    _get_catalog_image_counts = get_catalog_image_counts


def _configured(provider):
    if provider is None:
        raise RuntimeError("Catalog routes are not configured")
    return provider


def _configured_db_path() -> str:
    return _configured(_db_path)()


def _invalidate_embedding_cache() -> None:
    try:
        import embed_cache
        embed_cache.invalidate()
    except Exception:
        pass


def invalidate_folders_cache() -> None:
    for cached in _folders_cache.values():
        cached["expires"] = 0
    _folder_tree_cache["expires"] = 0


def clear_folders_cache() -> None:
    _folders_cache.clear()
    _folders_refreshing.clear()
    _folder_tree_cache["data"] = None
    _folder_tree_cache["expires"] = 0


def _catalog_changed(*, matchups: bool = True, cache_status: bool = False) -> None:
    if _invalidate_pairing_cache is None:
        raise RuntimeError("Catalog routes are not configured")
    _invalidate_pairing_cache(matchups=matchups)
    invalidate_folders_cache()
    if cache_status:
        if _invalidate_cache_status_cache is None:
            raise RuntimeError("Catalog routes are not configured")
        _invalidate_cache_status_cache()
    _invalidate_embedding_cache()


async def scan_prefetch_on_batch(count):
    # Start prefetching thumbnails for early images.
    if count <= 200:
        images = await _configured(_get_recent_active_images)(limit=50)
        config = settings.get_settings()
        await thumbnails.prefetch_images(
            [dict(r) for r in images],
            "lg",
            limit=min(len(images), config["scan_prefetch_limit"]),
        )


async def _is_first_run_import() -> bool:
    if settings.get_settings().get("setup_completed"):
        return False
    catalog = await _configured(_get_catalog_summary)()
    return not catalog.get("sources") and int(catalog.get("stats", {}).get("total_images") or 0) == 0


def _start_first_run_pipeline() -> None:
    thumbnails.start_pregeneration()
    catalog_metadata.resume_catalog_metadata()


async def _run_scan(folder: str, source_id: int, *, first_run: bool = False) -> None:
    if first_run:
        _start_first_run_pipeline()
    await scanner.scan_folder(folder, source_id=source_id, on_batch=scan_prefetch_on_batch)
    # Invalidate read caches at COMPLETION too — _catalog_changed at scan start
    # is not enough: a grid query during the scan re-primes a stale empty
    # rankings response, so the first landing after an import shows 0 photos.
    _catalog_changed(matchups=True, cache_status=True)
    cache_events.invalidate_rankings_cache()
    import db as _db

    _db.invalidate_stats_cache()
    error = str(scanner.scan_state.get("error") or "").strip()
    if error:
        log.error(
            "worker=catalog_scan source_id=%s folder=%r failed: %s",
            source_id,
            folder,
            error,
        )


@router.post("/api/scan")
async def start_scan(request: Request):
    body, error = await json_object(request)
    if error:
        return error
    folder = body.get("folder", "")
    if not folder or not await asyncio.to_thread(os.path.isdir, folder):
        return JSONResponse({"error": "Invalid folder path"}, status_code=400)

    if not scanner.try_begin_scan():
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    try:
        first_run = await _is_first_run_import()
        source = await _configured(_add_or_restore_source)(folder)
    except Exception:
        scanner.release_scan_claim()
        raise
    track_background_task(_run_scan(source["path"], int(source["id"]), first_run=first_run))
    _catalog_changed(matchups=True)
    return {"status": "started", "folder": source["path"], "source_id": source["id"]}


@router.get("/api/scan/status")
async def scan_status():
    return scanner.scan_state


@router.get("/api/catalog/metadata/status")
async def catalog_metadata_status():
    return catalog_metadata.catalog_metadata_status()


@router.post("/api/catalog/metadata/start")
async def catalog_metadata_start():
    return {"ok": True, "metadata_status": catalog_metadata.resume_catalog_metadata()}


@router.post("/api/catalog/metadata/stop")
async def catalog_metadata_stop():
    return {"ok": True, "metadata_status": catalog_metadata.pause_catalog_metadata()}


@router.get("/api/scan/folder")
async def scan_folder():
    folder = await _configured(_get_scan_folder)()
    return {"folder": folder or ""}


def folder_picker_start(path: str = "") -> str:
    candidate = catalog_repository.normalize_source_path(path or os.path.expanduser("~"))
    if os.path.isdir(candidate):
        return candidate
    parent = os.path.dirname(candidate)
    while parent and parent != candidate:
        if os.path.isdir(parent):
            return parent
        candidate = parent
        parent = os.path.dirname(candidate)
    return os.path.expanduser("~")


def folder_picker_commands(initial: str) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    if shutil.which("zenity"):
        commands.append((
            "zenity",
            [
                "zenity",
                "--file-selection",
                "--directory",
                "--title=Select Catalog Folder",
                f"--filename={initial.rstrip(os.sep) + os.sep}",
            ],
        ))
    if shutil.which("kdialog"):
        commands.append((
            "kdialog",
            ["kdialog", "--title", "Select Catalog Folder", "--getexistingdirectory", initial],
        ))
    if shutil.which("yad"):
        commands.append((
            "yad",
            [
                "yad",
                "--file-selection",
                "--directory",
                "--title=Select Catalog Folder",
                f"--filename={initial.rstrip(os.sep) + os.sep}",
            ],
        ))
    if shutil.which("qarma"):
        commands.append((
            "qarma",
            [
                "qarma",
                "--file-selection",
                "--directory",
                "--title=Select Catalog Folder",
                f"--filename={initial.rstrip(os.sep) + os.sep}",
            ],
        ))
    commands.append((
        "tkinter",
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                "try:\n"
                "    import tkinter as tk\n"
                "    from tkinter import filedialog\n"
                "    root = tk.Tk()\n"
                "    root.withdraw()\n"
                "    try:\n"
                "        root.attributes('-topmost', True)\n"
                "    except Exception:\n"
                "        pass\n"
                "    path = filedialog.askdirectory(title='Choose your photos folder', initialdir=sys.argv[1], mustexist=True)\n"
                "    root.destroy()\n"
                "    if path:\n"
                "        print(path)\n"
                "        raise SystemExit(0)\n"
                "    raise SystemExit(1)\n"
                "except SystemExit:\n"
                "    raise\n"
                "except Exception as exc:\n"
                "    print(str(exc), file=sys.stderr)\n"
                "    raise SystemExit(2)\n"
            ),
            initial,
        ],
    ))
    return commands


def native_folder_picker_available() -> bool:
    if any(shutil.which(cmd) for cmd in ("zenity", "kdialog", "yad", "qarma")):
        return True
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def run_native_folder_picker(initial: str) -> dict:
    if not native_folder_picker_available():
        return {"ok": False, "cancelled": False, "error": "No native folder picker is available"}

    last_error = ""
    for tool_name, command in folder_picker_commands(initial):
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3600,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "cancelled": False, "error": "Folder picker timed out"}
        except Exception as exc:
            last_error = str(exc)
            continue

        selected = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        stderr = proc.stderr.strip()
        if proc.returncode == 0 and selected:
            selected_path = catalog_repository.normalize_source_path(selected)
            if os.path.isdir(selected_path):
                return {"ok": True, "path": selected_path, "tool": tool_name}
            last_error = "Selected path is not a folder"
            continue
        if proc.returncode in (1, 5) and not selected and not stderr:
            return {"ok": False, "cancelled": True, "tool": tool_name}
        last_error = stderr or f"{tool_name} exited with status {proc.returncode}"

    return {"ok": False, "cancelled": False, "error": last_error or "Folder picker failed"}


@router.get("/api/catalog/folder-picker")
async def api_catalog_folder_picker_status():
    tkinter_available = False
    try:
        import tkinter  # noqa: F401
        tkinter_available = True
    except Exception:
        pass
    return {
        "available": native_folder_picker_available(),
        "tools": [
            name
            for name, command in folder_picker_commands(os.path.expanduser("~"))
            if shutil.which(command[0]) and (name != "tkinter" or tkinter_available)
        ],
    }


@router.post("/api/catalog/select-folder")
async def api_catalog_select_folder(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    initial = folder_picker_start(body.get("path") or "")
    result = await asyncio.to_thread(run_native_folder_picker, initial)
    if not result.get("ok") and not result.get("cancelled"):
        return JSONResponse(result, status_code=503)
    return result


@router.get("/api/catalog/browse")
async def api_catalog_browse(path: str = ""):
    current = catalog_repository.normalize_source_path(path or os.path.expanduser("~"))
    return await asyncio.to_thread(_browse_dir, current)


def _browse_dir(current: str) -> dict:
    """Sync filesystem browse payload — run via asyncio.to_thread from the handler."""
    roots = quick_browse_roots()
    result = {
        "path": current,
        "parent": os.path.dirname(current.rstrip(os.sep)) or current,
        "exists": os.path.exists(current),
        "is_dir": os.path.isdir(current),
        "readable": os.access(current, os.R_OK | os.X_OK) if os.path.isdir(current) else False,
        "roots": roots,
        "entries": [],
        "error": "",
    }
    if not result["exists"]:
        result["error"] = "Path does not exist"
        return result
    if not result["is_dir"]:
        result["error"] = "Path is not a directory"
        return result
    if not result["readable"]:
        result["error"] = "Directory is not readable"
        return result

    try:
        entries = []
        with os.scandir(current) as scan:
            for entry in scan:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                    entry_path = catalog_repository.normalize_source_path(entry.path)
                    entries.append({
                        "name": entry.name,
                        "path": entry_path,
                        "readable": os.access(entry_path, os.R_OK | os.X_OK),
                    })
                except OSError:
                    continue
        result["entries"] = sorted(entries, key=lambda item: item["name"].lower())
    except PermissionError:
        result["readable"] = False
        result["error"] = "Directory is not readable"
    except OSError:
        log.exception("catalog folder browse failed path=%r", current)
        result["error"] = "Directory could not be read; check that the source is connected and accessible"
    return result


@router.get("/api/catalog")
async def api_catalog_summary():
    return await _configured(_get_catalog_summary)()


@router.post("/api/catalog/sources")
async def api_add_catalog_source(request: Request):
    body, error = await json_object(request)
    if error:
        return error
    folder = body.get("path") or body.get("folder") or ""
    scan = body.get("scan", True)
    if not folder or not os.path.isdir(catalog_repository.normalize_source_path(folder)):
        return JSONResponse({"error": "Invalid folder path"}, status_code=400)
    if scan and not scanner.try_begin_scan():
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    try:
        first_run = await _is_first_run_import()
        source = await _configured(_add_or_restore_source)(folder)
    except Exception:
        if scan:
            scanner.release_scan_claim()
        raise
    if scan:
        track_background_task(_run_scan(source["path"], int(source["id"]), first_run=first_run))
    _catalog_changed(matchups=True)
    return {
        "ok": True,
        "source": dict(source),
        "scan_started": bool(scan),
        "catalog": await _configured(_get_catalog_summary)(),
    }


@router.post("/api/catalog/sources/{source_id}/rescan")
async def api_rescan_catalog_source(source_id: int):
    source = await _configured(_get_source)(source_id)
    if not source:
        return JSONResponse({"error": "Source not found"}, status_code=404)
    if not os.path.isdir(source["path"]):
        return JSONResponse(
            {
                "error": "Source drive is offline",
                "detail": "Reconnect the source drive before rescanning; existing catalog entries were preserved.",
            },
            status_code=409,
        )
    if not scanner.try_begin_scan():
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    try:
        restored = await _configured(_add_or_restore_source)(source["path"])
    except Exception:
        scanner.release_scan_claim()
        raise
    track_background_task(_run_scan(restored["path"], int(restored["id"])))
    _catalog_changed(matchups=True)
    return {"ok": True, "source": dict(restored), "scan_started": True}


@router.post("/api/catalog/sources/{source_id}/remove")
async def api_remove_catalog_source(source_id: int, request: Request):
    body, error = await json_object(request)
    if error:
        return error
    mode = body.get("mode", "keep")
    source = await _configured(_get_source)(source_id)
    if not source:
        return JSONResponse({"error": "Source not found"}, status_code=404)
    if scanner.scan_state["scanning"] and scanner.scan_state.get("source_id") == source_id:
        return JSONResponse({"error": "Cannot remove a source while it is scanning"}, status_code=409)

    if mode == "keep":
        await _configured(_remove_source_keep_data)(source_id)
        action = {"kept_data": True, "images_deleted": 0, "comparisons_deleted": 0}
        _invalidate_embedding_cache()
    elif mode in ("delete", "purge"):
        image_ids = await _configured(_get_source_image_ids)(source_id)
        cache_result = thumbnails.purge_image_cache(image_ids)
        purge_result = await _configured(_purge_source_catalog_data)(source_id)
        action = {"kept_data": False, **purge_result, "cache": cache_result}
        _invalidate_embedding_cache()
    else:
        return JSONResponse({"error": "Invalid removal mode"}, status_code=400)

    if _invalidate_pairing_cache is None or _invalidate_cache_status_cache is None:
        raise RuntimeError("Catalog routes are not configured")
    _invalidate_pairing_cache(matchups=True)
    invalidate_folders_cache()
    _invalidate_cache_status_cache()
    return {"ok": True, "source_id": source_id, **action, "catalog": await _configured(_get_catalog_summary)()}


def add_folder_counts(
    folder_counts: dict[str, int],
    root: str,
    directory: str,
    count: int = 1,
    max_depth: int | None = None,
):
    root_prefix = root.rstrip(os.sep) + os.sep
    if directory == root:
        rel = "."
    elif root != os.sep and directory.startswith(root_prefix):
        rel = directory[len(root_prefix):]
    elif root == os.sep and directory.startswith(root_prefix):
        rel = directory[1:]
    else:
        rel = safe_relpath(directory, root)
        if rel is None:
            rel = directory
    # Folder keys are '/'-separated in every payload, on every platform —
    # the desktop JS splits on '/'. Rows may carry native or hub separators.
    rel = rel.replace("\\", "/")
    start = 0
    while True:
        idx = rel.find("/", start)
        if idx < 0:
            if max_depth is None or rel.count("/") <= max_depth:
                folder_counts[rel] = folder_counts.get(rel, 0) + count
            return
        key = rel[:idx]
        if max_depth is None or key.count("/") <= max_depth:
            folder_counts[key] = folder_counts.get(key, 0) + count
        start = idx + 1


def parent_directory(path: str) -> str:
    split_at = path.rfind(os.sep)
    return path[:split_at] if split_at >= 0 else ""


def is_filesystem_source(path: str) -> bool:
    return bool(path) and "://" not in path and os.path.isabs(path)


def build_source_level_folders_payload(sources: list[tuple[int, str, int]]) -> dict | None:
    source_paths = [path for _source_id, path, _active_count in sources if path]
    if len(source_paths) < 2:
        return None
    root = safe_commonpath(source_paths)
    folders = []
    for _source_id, source_path, active_image_count in sources:
        if not source_path:
            return None
        if root is None:
            # Multi-drive libraries: each source is its own top-level folder.
            folders.append({
                "path": source_path,
                "count": int(active_image_count or 0),
                "depth": 0,
            })
            continue
        rel = safe_relpath(source_path, root) or os.path.basename(source_path.rstrip(os.sep)) or "."
        folders.append({
            "path": rel.replace("\\", "/") if rel != "." else os.path.basename(source_path.rstrip(os.sep)) or ".",
            "count": int(active_image_count or 0),
            "depth": 0,
        })
    if root is None:
        return {"folders": sorted(folders, key=lambda item: item["path"]), "root": ""}
    return {"folders": sorted(folders, key=lambda item: item["path"]), "root": root}


def build_folders_payload(max_depth: int | None = None) -> dict:
    sources = [
        source
        for source in catalog_repository.folder_source_rows(_configured_db_path())
        if is_filesystem_source(source[1])
    ]
    if max_depth == 0:
        source_level = build_source_level_folders_payload(sources)
        if source_level is not None:
            return source_level
    active_source_ids = [
        source_id
        for source_id, _source_path, active_image_count in sources
        if int(active_image_count or 0) > 0
    ]
    paths_by_source = catalog_repository.folder_image_filepaths_by_source(_configured_db_path(), active_source_ids)
    directory_counts = {}
    fallback_dirs = []
    for source_id, source_path, _active_image_count in sources:
        for filepath in paths_by_source.get(int(source_id), []):
            directory = parent_directory(filepath or "")
            directory_counts[directory] = directory_counts.get(directory, 0) + 1
            if not source_path:
                fallback_dirs.append(directory)

    if not directory_counts:
        return {"folders": []}

    source_paths = [path for _source_id, path, _active_count in sources if path]
    if source_paths:
        root = safe_commonpath(source_paths)
        if root is None:
            # Multi-drive: fall back to source-level listing so pregen/UI never crash.
            source_level = build_source_level_folders_payload(sources)
            if source_level is not None:
                return source_level
            root = source_paths[0]
    else:
        root = safe_commonpath(fallback_dirs) or (fallback_dirs[0] if fallback_dirs else "")

    folder_counts = {}
    for directory, count in directory_counts.items():
        add_folder_counts(folder_counts, root, directory, count, max_depth=max_depth)

    folders = [{"path": k, "count": v, "depth": k.count("/")}
               for k, v in sorted(folder_counts.items())]
    return {"folders": folders, "root": root}


def parts_under_source(source_path: str, directory: str) -> list[str]:
    source_root = catalog_repository.normalize_source_path(source_path).rstrip(os.sep)
    current = catalog_repository.normalize_source_path(directory or source_path).rstrip(os.sep)
    if not source_root:
        return []
    if current == source_root:
        return []
    root_prefix = source_root + os.sep
    if current.startswith(root_prefix):
        rel = current[len(root_prefix):]
    else:
        rel = safe_relpath(current, source_root) or ""
    return [part for part in rel.split(os.sep) if part and part != "."]


def folder_path_for_parts(source_path: str, parts: list[str]) -> str:
    if not parts:
        return catalog_repository.normalize_source_path(source_path)
    return os.path.join(catalog_repository.normalize_source_path(source_path), *parts)


def make_folder_node(
    source_path: str,
    parts: list[str],
    *,
    source_id: int = 0,
    reveal_available: bool = True,
) -> dict:
    path = folder_path_for_parts(source_path, parts)
    return {
        "path": path,
        "name": os.path.basename(path.rstrip(os.sep)) or path,
        "source_id": source_id,
        "reveal_available": reveal_available,
        "count": 0,
        "total_count": 0,
        "children": [],
        "_children_by_name": {},
    }


def serialize_folder_node(node: dict) -> dict | None:
    if int(node.get("total_count") or 0) <= 0:
        return None
    children = []
    for child in sorted(
        node.get("_children_by_name", {}).values(),
        key=lambda item: (str(item.get("name") or "").lower(), str(item.get("path") or "")),
    ):
        serialized = serialize_folder_node(child)
        if serialized is not None:
            children.append(serialized)
    return {
        # API folder keys are '/' on every platform (reveal handles both).
        "path": str(node["path"]).replace("\\", "/"),
        "name": node["name"],
        "source_id": int(node.get("source_id") or 0),
        "reveal_available": bool(node.get("reveal_available", True)),
        "count": int(node.get("count") or 0),
        "total_count": int(node.get("total_count") or 0),
        "children": children,
    }


def build_folder_tree_payload_from_rows(
    sources: list[dict],
    directory_counts_by_source: dict[int, dict[str, int]],
    *,
    max_depth: int = _folder_tree_max_depth,
) -> dict:
    payload_sources = []
    capped_depth = max(0, min(int(max_depth), _folder_tree_max_depth))
    for source in sources:
        source_id = int(source.get("id") or 0)
        source_path = source.get("path") or ""
        reveal_available = catalog_reveal.source_has_local_folders(source_path)
        root = make_folder_node(
            source_path,
            [],
            source_id=source_id,
            reveal_available=reveal_available,
        )
        for directory, raw_count in directory_counts_by_source.get(source_id, {}).items():
            count = int(raw_count or 0)
            if count <= 0:
                continue
            parts = parts_under_source(source_path, directory)
            capped_parts = parts[:capped_depth]
            root["total_count"] += count
            if not parts:
                root["count"] += count
                continue

            node = root
            for depth in range(len(capped_parts)):
                name = capped_parts[depth]
                child = node["_children_by_name"].get(name)
                if child is None:
                    child = make_folder_node(
                        source_path,
                        capped_parts[:depth + 1],
                        source_id=source_id,
                        reveal_available=reveal_available,
                    )
                    node["_children_by_name"][name] = child
                child["total_count"] += count
                node = child
            if len(parts) <= capped_depth:
                node["count"] += count

        payload_sources.append({
            "id": source_id,
            "path": source_path if not reveal_available else catalog_repository.normalize_source_path(source_path),
            "display_name": source.get("display_name") or catalog_repository.source_display_name(source_path),
            "online": bool(source.get("online")),
            "reveal_available": reveal_available,
            "count": int(root["count"] or 0),
            "total_count": int(root["total_count"] or 0),
            "folders": [
                child
                for child in (
                    serialize_folder_node(node)
                    for node in sorted(
                        root["_children_by_name"].values(),
                        key=lambda item: (str(item.get("name") or "").lower(), str(item.get("path") or "")),
                    )
                )
                if child is not None
            ],
        })
    return {"sources": payload_sources}


def build_folder_tree_payload() -> dict:
    sources = catalog_repository.folder_tree_source_rows(_configured_db_path())
    active_source_ids = [
        int(source["id"])
        for source in sources
        if int(source.get("active_image_count") or 0) > 0
    ]
    directory_counts = catalog_repository.folder_directory_counts_by_source(_configured_db_path(), active_source_ids)
    return build_folder_tree_payload_from_rows(sources, directory_counts)


@router.get("/api/folders")
async def api_folders(max_depth: int | None = None):
    """Get folder tree with image counts."""
    normalized_max_depth = None
    if max_depth is not None:
        normalized_max_depth = max(0, min(int(max_depth), 10))
    cached = _folders_cache.get(normalized_max_depth)
    if cached and _time.time() < cached["expires"]:
        return cached["data"]
    if cached:
        if normalized_max_depth not in _folders_refreshing:
            _folders_refreshing.add(normalized_max_depth)

            async def _refresh_folders():
                try:
                    result = await asyncio.to_thread(build_folders_payload, normalized_max_depth)
                    _folders_cache[normalized_max_depth] = {
                        "data": result,
                        "expires": _time.time() + _folders_cache_ttl_seconds,
                    }
                except Exception:
                    log.exception("folders background refresh failed")
                finally:
                    _folders_refreshing.discard(normalized_max_depth)

            track_background_task(_refresh_folders())
        return cached["data"]
    counts = await _configured(_get_catalog_image_counts)()
    if int(counts.get("active_images") or 0) <= 0:
        result = {"folders": []}
        _folders_cache[normalized_max_depth] = {
            "data": result,
            "expires": _time.time() + _folders_cache_ttl_seconds,
        }
        return result

    result = await asyncio.to_thread(build_folders_payload, normalized_max_depth)
    _folders_cache[normalized_max_depth] = {
        "data": result,
        "expires": _time.time() + _folders_cache_ttl_seconds,
    }
    return result


@router.get("/api/folders/tree")
async def api_folders_tree():
    cached = _folder_tree_cache.get("data")
    if cached and _time.time() < _folder_tree_cache["expires"]:
        return cached

    counts = await _configured(_get_catalog_image_counts)()
    if int(counts.get("active_images") or 0) <= 0:
        result = {"sources": []}
    else:
        result = await asyncio.to_thread(build_folder_tree_payload)
    _folder_tree_cache["data"] = result
    _folder_tree_cache["expires"] = _time.time() + _folder_tree_cache_ttl_seconds
    return result


@router.post("/api/reveal")
async def api_reveal(body: RevealBody):
    """Open a catalog folder in the host OS file manager."""

    if not catalog_reveal.source_has_local_folders(body.path):
        return JSONResponse(
            {"ok": False, "error": "Reveal is only available for local folders"},
            status_code=400,
        )
    if body.source_id is not None:
        source = await catalog_repository.get_source(_configured_db_path(), body.source_id)
        if source is not None and not catalog_reveal.source_has_local_folders(source["path"]):
            return JSONResponse(
                {"ok": False, "error": "Reveal is only available for local folders"},
                status_code=400,
            )
    roots = await import_repository.catalog_source_paths(_configured_db_path())
    result = await asyncio.to_thread(catalog_reveal.reveal_folder, body.path, roots)
    if result.get("ok"):
        return {"ok": True}
    error = str(result.get("error") or "Could not open folder")
    lowered = error.lower()
    if "outside" in lowered:
        return JSONResponse({"ok": False, "error": error}, status_code=403)
    if "required" in lowered or "not a folder" in lowered:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    # Headless hosts / missing opener: soft failure so the UI can toast.
    return {"ok": False, "error": error}
