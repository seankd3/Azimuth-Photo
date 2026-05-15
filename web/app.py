import asyncio
import copy
import csv
import heapq
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import ai_models
import db
import elo_propagation
import helpers as app_helpers
import pairing
import photo_metadata
import resource_governor
import scanner
import settings
import thumbnails

from starlette.middleware.gzip import GZipMiddleware


class SelectiveGZipMiddleware:
    """Compress text/JSON responses without spending CPU on JPEG/full images."""

    def __init__(self, app, minimum_size: int = 1000):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path") or ""
            if path.startswith("/api/thumb/") or path.startswith("/api/full/"):
                await self.app(scope, receive, send)
                return
        await self.gzip(scope, receive, send)


class StaticCacheHeadersMiddleware:
    """Let browsers reuse static JS/CSS briefly while still revalidating soon."""

    def __init__(self, app, max_age: int = 300):
        self.app = app
        self.cache_control = f"public, max-age={max_age}, stale-while-revalidate=3600"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not (scope.get("path") or "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_with_cache_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(name.lower() == b"cache-control" for name, _value in headers):
                    headers.append((b"cache-control", self.cache_control.encode("ascii")))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache_headers)


app = FastAPI(title="photoArchive")
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)
app.add_middleware(StaticCacheHeadersMiddleware, max_age=86400)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

INTERACTION_CACHE_WARMUP_DELAY_SECONDS = 0.05
_BROWSER_IMAGE_EXTENSIONS = thumbnails.BROWSER_ORIGINAL_EXTENSIONS
_IDLE_ACTIVITY_EXCLUDED_PATHS = {
    "/api/ai/status",
    "/api/cache/status",
    "/api/cache/pregen/status",
    "/api/dev/status",
    "/api/scan/status",
    "/api/settings",
    "/api/ui/settings",
}
_STARTED_AT = time.time()
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _track_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


async def _json_object(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None, JSONResponse({"error": "Malformed JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return None, JSONResponse({"error": "JSON body must be an object"}, status_code=400)
    return body, None


def _ranking_signal_count(image: dict) -> int:
    return app_helpers.ranking_signal_count(image)


def _has_ranking_signal(image: dict) -> bool:
    return app_helpers.has_ranking_signal(image)


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).strip()
    except Exception:
        return None


_GIT_COMMIT = _git_commit()
_STATIC_VERSION: str | None = None


def _static_version() -> str:
    global _STATIC_VERSION
    if _STATIC_VERSION is not None:
        return _STATIC_VERSION
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    try:
        mtimes = [
            os.path.getmtime(os.path.join(static_dir, filename))
            for filename in ("app.js", "style.css")
        ]
        _STATIC_VERSION = str(int(max(mtimes)))
    except OSError:
        _STATIC_VERSION = str(int(_STARTED_AT))
    return _STATIC_VERSION


def _template_context(request: Request) -> dict:
    return {"request": request, "static_version": _static_version()}


def _warm_templates() -> None:
    for template_name in ("settings.html", "library.html", "compare.html"):
        templates.env.get_template(template_name)


@app.middleware("http")
async def track_idle_activity(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/static") and path not in _IDLE_ACTIVITY_EXCLUDED_PATHS:
        thumbnails.note_user_activity()
    return await call_next(request)


@app.on_event("startup")
async def startup():
    await db.init_db()
    thumbnails.configure(settings.load_settings())

    async def _warm_embed_cache():
        try:
            import embed_cache
            await embed_cache.get_matrix()
        except Exception:
            pass

    async def _cleanup_stale_cache_temps_when_quiet():
        await asyncio.to_thread(thumbnails.cleanup_stale_cache_temps)

    async def _warm_common_filter_caches():
        options = await db.get_filter_options()
        file_types = [
            str(item.get("ext") or "")
            for item in (options.get("file_types") or [])[:3]
            if item.get("ext")
        ]
        await asyncio.gather(
            *(
                api_rankings(limit=60, file_type=file_type)
                for file_type in file_types
            ),
            *(
                api_rankings(limit=60, q=file_type)
                for file_type in file_types
            ),
            *(
                db.get_date_groups(
                    file_type=file_type,
                    visible_thumb_size="sm",
                    cache_root=_cache_root(),
                )
                for file_type in file_types
            ),
            return_exceptions=True,
        )

    async def _warm_light_startup_caches():
        await asyncio.sleep(0.1)
        await asyncio.gather(
            db.get_catalog_image_counts(),
            db.get_stats(),
            db.get_ai_status_counts(),
            db.get_filter_options(),
            build_ai_status(),
            db.get_date_groups(visible_thumb_size="sm", cache_root=_cache_root()),
            api_rankings(limit=60),
            mosaic_next(n=12, strategy="explore"),
            api_folders(max_depth=0),
            api_folders(max_depth=1),
            api_folders(max_depth=2),
            api_map_markers(),
            api_settings(),
            db.get_visible_orientation_pairing_pool_counts("md", _cache_root(), "landscape"),
            db.get_visible_orientation_pairing_pool_counts("md", _cache_root(), "portrait"),
            db.get_visible_orientation_pairing_pool_counts("sm", _cache_root(), "landscape"),
            db.get_visible_orientation_pairing_pool_counts("sm", _cache_root(), "portrait"),
            _warm_common_filter_caches(),
            _warm_filtered_visible_ranked_candidates(
                "md",
                limit=_FILTERED_SWISS_PAIR_WINDOW,
                orientation="landscape",
                warm_matchups=True,
            ),
            _warm_filtered_visible_ranked_candidates(
                "md",
                limit=_FILTERED_SWISS_PAIR_WINDOW,
                orientation="portrait",
                warm_matchups=True,
            ),
            _warm_filtered_visible_ranked_candidates(
                "sm",
                limit=_FILTERED_MOSAIC_WINDOW,
                orientation="landscape",
            ),
            _warm_filtered_visible_ranked_candidates(
                "sm",
                limit=_FILTERED_MOSAIC_WINDOW,
                orientation="portrait",
            ),
            asyncio.to_thread(_warm_templates),
            return_exceptions=True,
        )

    async def _wait_for_background_window(min_idle_seconds: float = 60.0):
        while True:
            idle_seconds = thumbnails.get_idle_seconds()
            decision = resource_governor.get_background_decision(idle_seconds)
            if idle_seconds >= min_idle_seconds and decision.can_start_heavy_work:
                return
            await asyncio.sleep(max(1.0, decision.sleep_seconds or 1.0))

    _track_background_task(_warm_light_startup_caches())

    async def _warm_priority_interaction_caches():
        await asyncio.sleep(0.5)
        await _wait_for_background_window()
        await asyncio.gather(
            db.get_stats(),
            db.get_filter_options(),
            _default_visible_pairing_candidates(
                "md",
                limit=_SWISS_PAIR_WINDOW,
                include_card_metadata=True,
            ),
            _default_visible_pairing_candidates(
                "sm",
                copy_rows=True,
                limit=_MOSAIC_EXPLORE_WINDOW,
                order="cache",
            ),
            _default_visible_pairing_candidates(
                "sm",
                limit=_MOSAIC_DIVERSE_WINDOW,
                order="least_compared",
                include_card_metadata=False,
            ),
            _get_visible_past_matchups("md"),
            api_folders(max_depth=1),
            api_rankings(limit=50),
            api_rankings(limit=50, sort="resolution"),
            api_settings(),
            return_exceptions=True,
        )
        await mosaic_next(n=12, strategy="explore")
        await mosaic_next(n=12, strategy="diverse")
        await compare_next(n=5)

    _track_background_task(_warm_priority_interaction_caches())

    async def _start_background_after_ready(coro_factory, delay: float = 5.0):
        await asyncio.sleep(delay)
        await coro_factory()

    _track_background_task(_start_background_after_ready(thumbnails.run_prefetch_worker))
    _track_background_task(_start_background_after_ready(_cleanup_stale_cache_temps_when_quiet, delay=20.0))
    _track_background_task(_start_background_after_ready(classify_orientations_background))
    _track_background_task(_start_background_after_ready(scan_metadata_background))
    try:
        import embedding_worker
        if settings.get_settings().get("defer_ai_on_startup", True):
            embedding_worker.pause_embedding_worker("AI work deferred by startup setting.")
        _track_background_task(_start_background_after_ready(embedding_worker.run_embedding_worker))
        _track_background_task(_start_background_after_ready(embedding_worker.run_deep_search_worker, delay=15.0))
    except ImportError:
        pass  # AI features disabled — missing dependencies

    async def _warm_interaction_caches():
        await asyncio.sleep(INTERACTION_CACHE_WARMUP_DELAY_SECONDS)
        await _wait_for_background_window()
        await asyncio.gather(
            db.get_ai_status_counts(),
            db.get_visible_orientation_pairing_pool_counts("md", _cache_root(), "landscape"),
            db.get_visible_orientation_pairing_pool_counts("md", _cache_root(), "portrait"),
            db.get_visible_orientation_pairing_pool_counts("sm", _cache_root(), "landscape"),
            db.get_visible_orientation_pairing_pool_counts("sm", _cache_root(), "portrait"),
            _warm_filtered_visible_ranked_candidates(
                "md",
                limit=_FILTERED_SWISS_PAIR_WINDOW,
                orientation="landscape",
                warm_matchups=True,
            ),
            _warm_filtered_visible_ranked_candidates(
                "md",
                limit=_FILTERED_SWISS_PAIR_WINDOW,
                orientation="portrait",
                warm_matchups=True,
            ),
            _warm_filtered_visible_ranked_candidates(
                "sm",
                limit=_FILTERED_MOSAIC_WINDOW,
                orientation="landscape",
            ),
            _warm_filtered_visible_ranked_candidates(
                "sm",
                limit=_FILTERED_MOSAIC_WINDOW,
                orientation="portrait",
            ),
            build_cache_status(ahead=0),
            db.get_catalog_summary(),
            api_date_groups(),
            api_map_markers(),
            api_rankings(limit=50, sort="newest"),
            api_rankings(limit=50, sort="camera"),
            return_exceptions=True,
        )
        await asyncio.gather(
            mosaic_next(n=6, orientation="landscape"),
            mosaic_next(n=12, strategy="diverse"),
            mosaic_next(n=12, strategy="diverse", orientation="landscape"),
            mosaic_next(n=12, strategy="diverse", orientation="portrait"),
            compare_next(n=5, mode="topn"),
            return_exceptions=True,
        )
    _track_background_task(_warm_interaction_caches())

    async def _warm_embed_cache_when_quiet():
        await asyncio.sleep(120.0)
        await _wait_for_background_window()
        await _warm_embed_cache()
    _track_background_task(_warm_embed_cache_when_quiet())


async def classify_orientations_background():
    """Continuously classify unclassified images by reading just the image header."""
    from PIL import Image as PILImage
    loop = asyncio.get_event_loop()

    def _classify_batch(rows):
        results = []
        for row in rows:
            try:
                img = PILImage.open(row["filepath"])
                w, h = img.size
                img.close()
                orient = "landscape" if w >= h else "portrait"
                ar = round(w / h, 4) if h > 0 else 1.5
                results.append((orient, ar, row["id"]))
            except Exception:
                results.append(("landscape", 1.5, row["id"]))
        return results

    while True:
        try:
            decision = resource_governor.get_background_decision(thumbnails.get_idle_seconds())
            if decision.pause:
                await asyncio.sleep(decision.sleep_seconds)
                continue

            batch_limit = max(10, min(200, int(200 * max(decision.intensity, 0.1))))
            rows = await db.get_unclassified_images(limit=batch_limit)
            if not rows:
                await asyncio.sleep(5)
                continue
            results = await loop.run_in_executor(None, _classify_batch, rows)
            if results:
                await db.batch_set_orientations(results)
            await asyncio.sleep(max(0.05, decision.embedding_pause_seconds))
        except Exception as e:
            print(f"Orientation classifier error: {e}")
            await asyncio.sleep(5)


def _metadata_update_tuple(image_id: int, metadata: dict):
    width = metadata.get("width")
    height = metadata.get("height")
    orientation = None
    aspect_ratio = None
    if width and height:
        try:
            width_num = int(width)
            height_num = int(height)
            if height_num > 0:
                orientation = "landscape" if width_num >= height_num else "portrait"
                aspect_ratio = round(width_num / height_num, 4)
        except Exception:
            pass

    return (
        metadata.get("date_taken") or None,
        metadata.get("camera_make") or None,
        metadata.get("camera_model") or None,
        metadata.get("lens") or None,
        metadata.get("file_ext") or None,
        metadata.get("file_size"),
        metadata.get("file_modified_at"),
        width,
        height,
        time.time(),
        photo_metadata.METADATA_EXTRACTOR_VERSION,
        orientation,
        aspect_ratio,
        metadata.get("latitude"),
        metadata.get("longitude"),
        image_id,
    )


async def scan_metadata_background():
    """Backfill EXIF/file metadata used for library filters and sorts."""
    loop = asyncio.get_event_loop()

    def _extract_batch(rows):
        updates = []
        for row in rows:
            metadata = photo_metadata.extract_image_metadata(row["filepath"])
            updates.append(_metadata_update_tuple(row["id"], metadata))
        return updates

    while True:
        try:
            decision = resource_governor.get_background_decision(thumbnails.get_idle_seconds())
            if decision.pause:
                await asyncio.sleep(decision.sleep_seconds)
                continue

            batch_limit = max(10, min(100, int(100 * max(decision.intensity, 0.1))))
            rows = await db.get_images_needing_metadata(
                limit=batch_limit,
                metadata_version=photo_metadata.METADATA_EXTRACTOR_VERSION,
            )
            if not rows:
                await asyncio.sleep(10)
                continue
            updates = await loop.run_in_executor(None, _extract_batch, rows)
            await db.batch_update_metadata(updates)
            await asyncio.sleep(max(0.05, decision.embedding_pause_seconds))
        except Exception as e:
            print(f"Metadata scanner error: {e}")
            await asyncio.sleep(10)


@app.on_event("shutdown")
async def shutdown():
    thumbnails.stop_prefetch()
    tasks = list(_BACKGROUND_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _BACKGROUND_TASKS.clear()


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "settings.html", _template_context(request))


@app.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    return templates.TemplateResponse(request, "compare.html", _template_context(request))


@app.get("/rankings", response_class=HTMLResponse)
async def rankings_page(request: Request):
    return templates.TemplateResponse(request, "library.html", _template_context(request))


@app.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    return templates.TemplateResponse(request, "library.html", _template_context(request))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", _template_context(request))


@app.get("/catalog", response_class=HTMLResponse)
async def catalog_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", _template_context(request))


@app.get("/api/dev/status")
async def dev_status():
    """Lightweight process/version probe for local server management."""
    return {
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "uptime_seconds": round(time.time() - _STARTED_AT, 3),
        "git_commit": _GIT_COMMIT,
        "cwd": os.getcwd(),
    }


# --- Scan API ---

@app.post("/api/scan")
async def start_scan(request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    folder = body.get("folder", "")
    if not folder or not os.path.isdir(folder):
        return JSONResponse({"error": "Invalid folder path"}, status_code=400)

    if scanner.scan_state["scanning"]:
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    async def on_batch(count):
        # Start prefetching thumbnails for early images.
        if count <= 200:
            images = await db.get_recent_active_images(limit=50)
            config = settings.get_settings()
            await thumbnails.prefetch_images(
                [dict(r) for r in images],
                "lg",
                limit=min(len(images), config["scan_prefetch_limit"]),
            )

    source = await db.add_or_restore_source(folder)
    asyncio.create_task(scanner.scan_folder(source["path"], source_id=source["id"], on_batch=on_batch))
    _invalidate_pairing_cache(matchups=True)
    _invalidate_folders_cache()
    try:
        import embed_cache
        embed_cache.invalidate()
    except Exception:
        pass
    return {"status": "started", "folder": source["path"], "source_id": source["id"]}


@app.get("/api/scan/status")
async def scan_status():
    return scanner.scan_state


@app.get("/api/scan/folder")
async def scan_folder():
    folder = await db.get_scan_folder()
    return {"folder": folder or ""}


async def _scan_prefetch_on_batch(count):
    # Start prefetching thumbnails for early images.
    if count <= 200:
        images = await db.get_recent_active_images(limit=50)
        config = settings.get_settings()
        await thumbnails.prefetch_images(
            [dict(r) for r in images],
            "lg",
            limit=min(len(images), config["scan_prefetch_limit"]),
        )


def _quick_browse_roots() -> list[dict]:
    home = os.path.expanduser("~")
    candidates = [
        ("Home", home),
        ("Pictures", os.path.join(home, "Pictures")),
        ("Media", "/media"),
        ("Mounts", "/mnt"),
        ("Run Media", os.path.join("/run/media", os.getenv("USER", ""))),
        ("Volumes", "/Volumes"),
    ]
    roots = []
    seen = set()
    for label, path in candidates:
        normalized = db.normalize_source_path(path)
        if normalized in seen or not os.path.isdir(normalized):
            continue
        seen.add(normalized)
        roots.append({"label": label, "path": normalized})
    return roots


def _folder_picker_start(path: str = "") -> str:
    candidate = db.normalize_source_path(path or os.path.expanduser("~"))
    if os.path.isdir(candidate):
        return candidate
    parent = os.path.dirname(candidate)
    while parent and parent != candidate:
        if os.path.isdir(parent):
            return parent
        candidate = parent
        parent = os.path.dirname(candidate)
    return os.path.expanduser("~")


def _folder_picker_commands(initial: str) -> list[tuple[str, list[str]]]:
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
                "    path = filedialog.askdirectory(title='Select Catalog Folder', initialdir=sys.argv[1], mustexist=True)\n"
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


def _native_folder_picker_available() -> bool:
    if any(shutil.which(cmd) for cmd in ("zenity", "kdialog", "yad", "qarma")):
        return True
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def _run_native_folder_picker(initial: str) -> dict:
    if not _native_folder_picker_available():
        return {"ok": False, "cancelled": False, "error": "No native folder picker is available"}

    last_error = ""
    for tool_name, command in _folder_picker_commands(initial):
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
            selected_path = db.normalize_source_path(selected)
            if os.path.isdir(selected_path):
                return {"ok": True, "path": selected_path, "tool": tool_name}
            last_error = "Selected path is not a folder"
            continue
        if proc.returncode in (1, 5) and not selected and not stderr:
            return {"ok": False, "cancelled": True, "tool": tool_name}
        last_error = stderr or f"{tool_name} exited with status {proc.returncode}"

    return {"ok": False, "cancelled": False, "error": last_error or "Folder picker failed"}


@app.get("/api/catalog/folder-picker")
async def api_catalog_folder_picker_status():
    tkinter_available = False
    try:
        import tkinter  # noqa: F401
        tkinter_available = True
    except Exception:
        pass
    return {
        "available": _native_folder_picker_available(),
        "tools": [
            name
            for name, command in _folder_picker_commands(os.path.expanduser("~"))
            if shutil.which(command[0]) and (name != "tkinter" or tkinter_available)
        ],
    }


@app.post("/api/catalog/select-folder")
async def api_catalog_select_folder(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    initial = _folder_picker_start(body.get("path") or "")
    result = await asyncio.to_thread(_run_native_folder_picker, initial)
    if not result.get("ok") and not result.get("cancelled"):
        return JSONResponse(result, status_code=503)
    return result


@app.get("/api/catalog/browse")
async def api_catalog_browse(path: str = ""):
    current = db.normalize_source_path(path or os.path.expanduser("~"))
    roots = _quick_browse_roots()
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
                    entry_path = db.normalize_source_path(entry.path)
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
    except OSError as exc:
        result["error"] = str(exc)
    return result


@app.get("/api/catalog")
async def api_catalog_summary():
    return await db.get_catalog_summary()


@app.post("/api/catalog/sources")
async def api_add_catalog_source(request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    folder = body.get("path") or body.get("folder") or ""
    scan = body.get("scan", True)
    if not folder or not os.path.isdir(db.normalize_source_path(folder)):
        return JSONResponse({"error": "Invalid folder path"}, status_code=400)
    if scan and scanner.scan_state["scanning"]:
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    source = await db.add_or_restore_source(folder)
    if scan:
        asyncio.create_task(scanner.scan_folder(source["path"], source_id=source["id"], on_batch=_scan_prefetch_on_batch))
    _invalidate_pairing_cache(matchups=True)
    _invalidate_folders_cache()
    try:
        import embed_cache
        embed_cache.invalidate()
    except Exception:
        pass
    return {
        "ok": True,
        "source": dict(source),
        "scan_started": bool(scan),
        "catalog": await db.get_catalog_summary(),
    }


@app.post("/api/catalog/sources/{source_id}/rescan")
async def api_rescan_catalog_source(source_id: int):
    source = await db.get_source(source_id)
    if not source:
        return JSONResponse({"error": "Source not found"}, status_code=404)
    if not os.path.isdir(source["path"]):
        return JSONResponse({"error": "Source folder is offline"}, status_code=400)
    if scanner.scan_state["scanning"]:
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    restored = await db.add_or_restore_source(source["path"])
    asyncio.create_task(scanner.scan_folder(restored["path"], source_id=restored["id"], on_batch=_scan_prefetch_on_batch))
    _invalidate_pairing_cache(matchups=True)
    _invalidate_folders_cache()
    try:
        import embed_cache
        embed_cache.invalidate()
    except Exception:
        pass
    return {"ok": True, "source": dict(restored), "scan_started": True}


@app.post("/api/catalog/sources/{source_id}/remove")
async def api_remove_catalog_source(source_id: int, request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    mode = body.get("mode", "keep")
    source = await db.get_source(source_id)
    if not source:
        return JSONResponse({"error": "Source not found"}, status_code=404)
    if scanner.scan_state["scanning"] and scanner.scan_state.get("source_id") == source_id:
        return JSONResponse({"error": "Cannot remove a source while it is scanning"}, status_code=409)

    if mode == "keep":
        await db.remove_source_keep_data(source_id)
        action = {"kept_data": True, "images_deleted": 0, "comparisons_deleted": 0}
        try:
            import embed_cache
            embed_cache.invalidate()
        except Exception:
            pass
    elif mode in ("delete", "purge"):
        image_ids = await db.get_source_image_ids(source_id)
        cache_result = thumbnails.purge_image_cache(image_ids)
        purge_result = await db.purge_source_catalog_data(source_id)
        action = {"kept_data": False, **purge_result, "cache": cache_result}
        try:
            import embed_cache
            embed_cache.invalidate()
        except Exception:
            pass
    else:
        return JSONResponse({"error": "Invalid removal mode"}, status_code=400)

    _invalidate_pairing_cache(matchups=True)
    _invalidate_folders_cache()
    _invalidate_cache_status_cache()
    return {"ok": True, "source_id": source_id, **action, "catalog": await db.get_catalog_summary()}


# --- Thumbnail ---

@app.get("/api/thumb/{size}/{image_id}")
async def serve_thumbnail(request: Request, size: str, image_id: int, cached: bool = False):
    if size not in thumbnails.SIZES:
        return JSONResponse({"error": "Invalid size"}, status_code=400)

    def cache_headers(signature: str) -> dict:
        return {
            "Cache-Control": (
                f"public, max-age={thumbnails.BROWSER_CACHE_MAX_AGE}, "
                f"stale-while-revalidate={thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
            ),
            "ETag": f'"{signature}"',
        }

    # Fast path: check memory cache, then SSD disk cache — no DB lookup or HDD stat.
    # The cached signature is strong enough for browser revalidation and avoids
    # the old "size-id" ETag that could mask regenerated thumbnails.
    request_etag = request.headers.get("if-none-match")
    entry = thumbnails._memory_get_entry_fast(size, image_id)
    if entry is None:
        path_entry = thumbnails.fast_disk_path_entry(size, image_id)
        if path_entry is not None:
            signature, path = path_entry
            headers = cache_headers(signature)
            if request_etag == headers["ETag"]:
                return Response(status_code=304, headers=headers)
            return FileResponse(path, media_type="image/jpeg", headers=headers)
        entry = await asyncio.get_event_loop().run_in_executor(
            None,
            thumbnails.fast_disk_read_entry,
            size,
            image_id,
            None,
        )
        if entry is not None:
            signature, data = entry
            thumbnails._memory_put(size, image_id, signature, data)
    if entry is not None:
        signature, data = entry
        headers = cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(content=data, media_type="image/jpeg", headers=headers)
    if cached:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    # Slow path: need to generate from source — requires DB lookup for filepath
    image = await db.get_image_by_id(image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    data = await thumbnails.get_thumbnail(image["filepath"], size, image_id)
    if not data:
        return JSONResponse({"error": "Thumbnail generation failed"}, status_code=500)

    headers = thumbnails.response_headers(image["filepath"], size, image_id)
    return Response(content=data, media_type="image/jpeg", headers=headers)


@app.get("/api/full/{image_id}")
async def serve_full_image(request: Request, image_id: int, background_tasks: BackgroundTasks, cached: bool = False):
    request_etag = request.headers.get("if-none-match")
    if cached:
        entry = thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, image_id)
        if entry is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        signature, path = entry
        headers = {
            "Cache-Control": (
                f"public, max-age={thumbnails.BROWSER_CACHE_MAX_AGE}, "
                f"stale-while-revalidate={thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
            ),
            "ETag": f'"{signature}"',
        }
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, headers=headers)

    full_entry = thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, image_id)
    if full_entry is not None:
        signature, path = full_entry
        headers = {
            "Cache-Control": (
                f"public, max-age={thumbnails.BROWSER_CACHE_MAX_AGE}, "
                f"stale-while-revalidate={thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
            ),
            "ETag": f'"{signature}"',
        }
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, headers=headers)

    image = await db.get_image_by_id(image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    ext = os.path.splitext(image["filepath"])[1].lower()
    if ext not in _BROWSER_IMAGE_EXTENSIONS:
        headers = thumbnails.response_headers(image["filepath"], "lg", image_id)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)

        data = await thumbnails.get_thumbnail(image["filepath"], "lg", image_id)
        if not data:
            return JSONResponse({"error": "Preview generation failed"}, status_code=500)
        return Response(content=data, media_type="image/jpeg", headers=headers)

    headers = thumbnails.response_headers(image["filepath"], thumbnails.FULL_TIER, image_id)
    if request_etag == headers["ETag"]:
        return Response(status_code=304, headers=headers)

    path = thumbnails.get_cached_full_image_path(image["filepath"], image_id)
    if path is None:
        path = image["filepath"]
        background_tasks.add_task(thumbnails.schedule_full_image_cache, image["filepath"], image_id)

    if not path or not os.path.exists(path):
        return JSONResponse({"error": "Full image unavailable"}, status_code=404)

    return FileResponse(path, headers=headers)


def _image_media_status_payload(image_id: int) -> dict:
    tiers = {}
    for size in thumbnails.THUMB_TIERS:
        cached = thumbnails.has_cached_fast(size, image_id)
        tiers[size] = {
            "cached": cached,
            "url": f"/api/thumb/{size}/{image_id}",
            "cached_url": f"/api/thumb/{size}/{image_id}?cached=1",
        }

    full_cached = thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, image_id) is not None
    tiers[thumbnails.FULL_TIER] = {
        "cached": full_cached,
        "url": f"/api/full/{image_id}",
        "cached_url": f"/api/full/{image_id}?cached=1",
    }
    best_cached = next(
        (tier for tier in (thumbnails.FULL_TIER, "lg", "md", "sm") if tiers.get(tier, {}).get("cached")),
        None,
    )
    return {"id": image_id, "tiers": tiers, "best_cached": best_cached}


@app.get("/api/image/{image_id}/media-status")
async def image_media_status(image_id: int):
    return await asyncio.to_thread(_image_media_status_payload, image_id)


@app.post("/api/images/media-status")
async def images_media_status(request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    raw_ids = body.get("ids", [])
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    ids = []
    seen = set()
    for value in raw_ids:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        ids.append(image_id)
        if len(ids) >= 96:
            break
    statuses = await asyncio.to_thread(
        lambda: [_image_media_status_payload(image_id) for image_id in ids]
    )
    return {"statuses": statuses}


@app.post("/api/images/warm")
async def warm_images(request: Request):
    """Mark current/nearby images as hot and schedule SSD cache warming."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    tier_requests = body.get("tiers") or {}
    requested: dict[str, list[int]] = {}
    all_ids: set[int] = set()

    for tier, values in tier_requests.items():
        if tier not in thumbnails.ALL_TIERS:
            continue
        ids = []
        seen_for_tier = set()
        values_iter = values if isinstance(values, (list, tuple, set)) else [values]
        for value in values_iter or []:
            try:
                image_id = int(value)
            except (TypeError, ValueError):
                continue
            if image_id <= 0 or image_id in seen_for_tier:
                continue
            seen_for_tier.add(image_id)
            ids.append(image_id)
            all_ids.add(image_id)
        if ids:
            requested[tier] = ids[:96]

    if not requested or not all_ids:
        return {"scheduled": {}, "images": 0}

    try:
        rows_by_id = await db.get_active_images_by_ids(list(all_ids))
    except (sqlite3.OperationalError, OSError) as exc:
        print(f"Warm image lookup skipped: {exc}")
        return {"scheduled": {tier: 0 for tier in requested}, "images": len(all_ids)}
    except Exception as exc:
        print(f"Warm image lookup skipped: {exc}")
        return {"scheduled": {tier: 0 for tier in requested}, "images": len(all_ids)}

    scheduled = {}
    for tier in list(requested.keys()):
        if tier not in thumbnails.THUMB_TIERS:
            continue
        cached_ids = await _cached_image_ids(requested[tier], tier)
        if not cached_ids:
            continue
        requested[tier] = [image_id for image_id in requested[tier] if image_id not in cached_ids]
        if not requested[tier]:
            scheduled[tier] = 0

    for tier, ids in requested.items():
        rows = [rows_by_id[image_id] for image_id in ids if image_id in rows_by_id]
        if not rows:
            scheduled[tier] = 0
            continue
        if tier in thumbnails.THUMB_TIERS:
            try:
                scheduled[tier] = await thumbnails.prefetch_images(
                    rows,
                    tier,
                    limit=len(rows),
                    hot=True,
                )
            except (sqlite3.OperationalError, OSError) as exc:
                print(f"Warm {tier} skipped: {exc}")
                scheduled[tier] = 0
            except Exception as exc:
                print(f"Warm {tier} skipped: {exc}")
                scheduled[tier] = 0
        elif tier == thumbnails.FULL_TIER:
            count = 0
            for row in rows[:12]:
                ext = os.path.splitext(row["filepath"])[1].lower()
                if ext not in _BROWSER_IMAGE_EXTENSIONS:
                    continue
                try:
                    await thumbnails.schedule_full_image_cache(row["filepath"], row["id"], hot=True)
                    count += 1
                except (sqlite3.OperationalError, OSError) as exc:
                    print(f"Warm full image {row['id']} skipped: {exc}")
                except Exception as exc:
                    print(f"Warm full image {row['id']} skipped: {exc}")
            scheduled[tier] = count

    return {"scheduled": scheduled, "images": len(all_ids)}


# --- Cache Status ---

_cache_status_cache: dict[tuple[int], dict] = {}
_cache_status_refreshing: set[tuple[int]] = set()
_cache_status_cache_ttl_seconds = 30.0
_cache_status_ahead_limit = 5000
_browser_original_count_cache = {"value": None, "bytes": 0, "expires": 0.0}
_browser_original_count_cache_ttl_seconds = 30.0


def _invalidate_cache_status_cache():
    _cache_status_cache.clear()
    _cache_status_refreshing.clear()
    _browser_original_count_cache["value"] = None
    _browser_original_count_cache["bytes"] = 0
    _browser_original_count_cache["expires"] = 0.0
    _expire_settings_response_cache()


async def _browser_original_summary() -> dict:
    now = time.monotonic()
    cached = _browser_original_count_cache.get("value")
    if cached is not None and float(_browser_original_count_cache.get("expires") or 0) > now:
        return {
            "count": int(cached),
            "bytes": int(_browser_original_count_cache.get("bytes") or 0),
        }

    browser_exts = tuple(sorted(thumbnails.BROWSER_ORIGINAL_EXTENSIONS))
    counts = await db.get_catalog_image_counts()
    all_catalog_images_active = (
        int(counts.get("active_images") or 0) > 0
        and int(counts.get("active_images") or 0) == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    conn = await db.get_db()
    try:
        placeholders = ",".join("?" for _ in browser_exts)
        if all_catalog_images_active:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(i.file_size), 0) AS bytes FROM images i "
                "WHERE i.missing_at IS NULL "
                f"AND i.file_ext IN ({placeholders})",
                browser_exts,
            )
        else:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(i.file_size), 0) AS bytes FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 AND i.missing_at IS NULL "
                f"AND i.file_ext IN ({placeholders})",
                browser_exts,
            )
        row = await cursor.fetchone()
        total = int(row["count"] or 0)
        total_bytes = int(row["bytes"] or 0)

        for condition in ("i.file_ext IS NULL", "i.file_ext = ''"):
            if all_catalog_images_active:
                cursor = await conn.execute(
                    "SELECT i.filepath, i.file_size FROM images i "
                    "WHERE i.missing_at IS NULL "
                    f"AND {condition}"
                )
            else:
                cursor = await conn.execute(
                    "SELECT i.filepath, i.file_size FROM images i "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    "WHERE s.included = 1 AND i.missing_at IS NULL "
                    f"AND {condition}"
                )
            rows = await cursor.fetchall()
            for row in rows:
                if thumbnails.is_browser_displayable_original(row["filepath"]):
                    total += 1
                    total_bytes += int(row["file_size"] or 0)
        _browser_original_count_cache["value"] = total
        _browser_original_count_cache["bytes"] = total_bytes
        _browser_original_count_cache["expires"] = time.monotonic() + _browser_original_count_cache_ttl_seconds
        return {"count": total, "bytes": total_bytes}
    finally:
        await conn.close()


async def _browser_original_count() -> int:
    return int((await _browser_original_summary())["count"])


def _cache_recommendations(
    cache: dict,
    eligible_images: int,
    total_images: int,
    browser_original_images: int,
    estimates: dict | None = None,
) -> dict:
    estimates = estimates or thumbnails.cache_archive_estimates()
    tiers = {}
    for tier_name in thumbnails.ALL_TIERS:
        avg_bytes = int(estimates.get("avg_bytes", {}).get(tier_name) or thumbnails.estimated_tier_bytes(tier_name))
        target_count = browser_original_images if tier_name == thumbnails.FULL_TIER else eligible_images
        full_archive_bytes = int(estimates.get("needed_bytes", {}).get(tier_name) or 0)
        if full_archive_bytes <= 0:
            full_archive_bytes = avg_bytes * max(0, int(target_count))
        budget_bytes = int(cache.get("disk", {}).get("tiers", {}).get(tier_name, {}).get("budget_bytes") or 0)
        estimated_cached = int(budget_bytes / avg_bytes) if avg_bytes > 0 else 0
        tiers[tier_name] = {
            "avg_bytes": avg_bytes,
            "sample_count": int(estimates.get("sample_count", {}).get(tier_name) or 0),
            "full_archive_bytes": full_archive_bytes,
            "budget_bytes": budget_bytes,
            "estimated_cached": min(target_count, estimated_cached),
            "coverage_pct": round((budget_bytes / full_archive_bytes) * 100, 1) if full_archive_bytes > 0 else 0.0,
        }

    return {
        "eligible_images": eligible_images,
        "total_images": total_images,
        "browser_original_images": browser_original_images,
        "budget": thumbnails.cache_budget_config(),
        "tiers": tiers,
    }


def _cache_archive_estimates_from_status(
    cache: dict,
    active_images: int,
    total_images: int,
    browser_original_images: int = 0,
    browser_original_bytes: int = 0,
) -> dict:
    avg_bytes = {}
    sample_count = {}
    for tier_name in thumbnails.ALL_TIERS:
        tier = cache.get("disk", {}).get("tiers", {}).get(tier_name, {})
        count = int(tier.get("current_count") or tier.get("count") or 0)
        bytes_used = int(tier.get("current_bytes") or tier.get("bytes") or 0)
        avg_bytes[tier_name] = (
            max(1, int(bytes_used / count))
            if count > 0 and bytes_used > 0
            else thumbnails.estimated_tier_bytes(tier_name)
        )
        sample_count[tier_name] = count

    needed_bytes = {
        tier_name: avg_bytes[tier_name] * max(0, int(active_images))
        for tier_name in thumbnails.THUMB_TIERS
    }
    if browser_original_bytes > 0:
        avg_bytes[thumbnails.FULL_TIER] = max(
            1,
            int(browser_original_bytes / max(1, int(browser_original_images or 0))),
        )
        needed_bytes[thumbnails.FULL_TIER] = int(browser_original_bytes)
    else:
        needed_bytes[thumbnails.FULL_TIER] = (
            avg_bytes[thumbnails.FULL_TIER] * max(0, int(total_images))
        )
    return {
        "active_images": max(0, int(active_images)),
        "total_images": max(0, int(total_images)),
        "avg_bytes": avg_bytes,
        "sample_count": sample_count,
        "needed_bytes": needed_bytes,
    }


def _copy_dict_of_dicts(value: dict | None) -> dict:
    return {
        key: dict(item) if isinstance(item, dict) else item
        for key, item in (value or {}).items()
    }


def _system_resource_status(cache_root: str) -> dict:
    disk_path = cache_root or os.getcwd()
    try:
        os.makedirs(disk_path, exist_ok=True)
    except OSError:
        disk_path = os.path.dirname(disk_path) or os.getcwd()
    try:
        disk_usage = shutil.disk_usage(disk_path)
        disk = {
            "path": disk_path,
            "total_bytes": int(disk_usage.total),
            "used_bytes": int(disk_usage.used),
            "free_bytes": int(disk_usage.free),
            "free_pct": round((disk_usage.free / disk_usage.total) * 100, 1) if disk_usage.total > 0 else 0.0,
        }
    except OSError:
        disk = {
            "path": disk_path,
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "free_pct": 0.0,
        }

    meminfo = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, raw_value = line.split(":", 1)
                parts = raw_value.strip().split()
                if parts:
                    meminfo[key] = int(parts[0]) * 1024
    except OSError:
        pass
    total = int(meminfo.get("MemTotal") or 0)
    available = int(meminfo.get("MemAvailable") or 0)
    swap_total = int(meminfo.get("SwapTotal") or 0)
    swap_free = int(meminfo.get("SwapFree") or 0)
    memory = {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "available_pct": round((available / total) * 100, 1) if total > 0 else 0.0,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "swap_used_pct": round(((swap_total - swap_free) / swap_total) * 100, 1) if swap_total > 0 else 0.0,
    }
    return {"disk": disk, "memory": memory}


def _copy_cache_status_response(status: dict) -> dict:
    copied = dict(status)
    if isinstance(status.get("memory"), dict):
        copied["memory"] = dict(status["memory"])
    if isinstance(status.get("disk"), dict):
        disk = dict(status["disk"])
        disk["tiers"] = _copy_dict_of_dicts(disk.get("tiers"))
        copied["disk"] = disk
    if isinstance(status.get("thumbnail_config"), dict):
        copied["thumbnail_config"] = dict(status["thumbnail_config"])
    if isinstance(status.get("recommendations"), dict):
        recommendations = dict(status["recommendations"])
        if isinstance(recommendations.get("budget"), dict):
            recommendations["budget"] = dict(recommendations["budget"])
        recommendations["tiers"] = _copy_dict_of_dicts(recommendations.get("tiers"))
        copied["recommendations"] = recommendations
    if isinstance(status.get("pregen"), dict):
        pregen = dict(status["pregen"])
        pregen["phases"] = _copy_dict_of_dicts(pregen.get("phases"))
        for key in ("preview", "originals"):
            if isinstance(pregen.get(key), dict):
                pregen[key] = dict(pregen[key])
        copied["pregen"] = pregen
    if isinstance(status.get("governor"), dict):
        copied["governor"] = dict(status["governor"])
    if isinstance(status.get("system_resources"), dict):
        resources = dict(status["system_resources"])
        if isinstance(resources.get("disk"), dict):
            resources["disk"] = dict(resources["disk"])
        if isinstance(resources.get("memory"), dict):
            resources["memory"] = dict(resources["memory"])
        copied["system_resources"] = resources
    return copied


def _cache_status_ttl(result: dict) -> float:
    pregen = result.get("pregen") or {}
    if pregen.get("state") == "running":
        return 2.0

    preview_remaining = int((pregen.get("preview") or {}).get("remaining") or 0)
    original_remaining = int((pregen.get("originals") or {}).get("remaining") or 0)
    warming_enabled = bool(pregen.get("enabled")) and not bool(pregen.get("manual_pause"))
    if warming_enabled and (preview_remaining > 0 or original_remaining > 0):
        return 1.0

    return _cache_status_cache_ttl_seconds


async def build_cache_status(ahead: int = 100, *, force: bool = False):
    ahead = _clamp_int(ahead, 0, 0, _cache_status_ahead_limit)
    cache_key = (ahead,)
    now = time.monotonic()
    if not force:
        cached = _cache_status_cache.get(cache_key)
        if cached and cached["expires"] > now:
            return _copy_cache_status_response(cached["data"])
        if cached and cached.get("data") is not None:
            if _cache_status_ttl(cached["data"]) > 1.0:
                if cache_key not in _cache_status_refreshing:
                    _cache_status_refreshing.add(cache_key)

                    async def _refresh_cache_status():
                        try:
                            await build_cache_status(ahead=ahead, force=True)
                        except Exception:
                            pass
                        finally:
                            _cache_status_refreshing.discard(cache_key)

                    asyncio.create_task(_refresh_cache_status())
                return _copy_cache_status_response(cached["data"])

    counts = await db.get_catalog_image_counts()

    if int(counts.get("active_images") or 0) > 0:
        browser_original_summary, cache = await asyncio.gather(
            _browser_original_summary(),
            asyncio.to_thread(thumbnails.cache_stats),
        )
        browser_original_total = int(browser_original_summary["count"])
        browser_original_bytes = int(browser_original_summary["bytes"])
    else:
        cache = await asyncio.to_thread(thumbnails.cache_stats)
        browser_original_total = 0
        browser_original_bytes = 0
    active_total = int(counts.get("active_images") or 0)
    total_images = int(counts.get("total_catalog_images") or 0)
    archive_estimates = _cache_archive_estimates_from_status(
        cache,
        active_total,
        total_images,
        browser_original_total,
        browser_original_bytes,
    )

    memory = cache["memory"]
    disk = cache["disk"]
    memory["utilization_pct"] = round(
        (memory["used_bytes"] / memory["limit_bytes"]) * 100,
        1,
    ) if memory["limit_bytes"] > 0 else 0.0
    disk["utilization_pct"] = round(
        (disk["used_bytes"] / disk["limit_bytes"]) * 100,
        1,
    ) if disk["limit_bytes"] > 0 else 0.0

    for tier_name, tier in disk["tiers"].items():
        progress_total = active_total if tier_name in thumbnails.THUMB_TIERS else browser_original_total
        progress_count = (
            tier.get("current_count", 0)
            if tier.get("replacement_mode")
            else tier.get("count", 0)
        )
        tier["progress_total"] = progress_total
        tier["progress_count"] = progress_count
        tier["progress_pct"] = round((progress_count / progress_total) * 100, 1) if progress_total > 0 else 0.0
        tier["utilization_pct"] = round(
            (tier["bytes"] / tier["budget_bytes"]) * 100,
            1,
        ) if tier["budget_bytes"] > 0 else 0.0

    result = {
        **cache,
        "eligible_images": active_total,
        "browser_original_images": browser_original_total,
        "recommendations": _cache_recommendations(
            cache,
            active_total,
            total_images,
            browser_original_total,
            archive_estimates,
        ),
        "pregen": thumbnails.get_pregen_status(
            active_total,
            cache,
            browser_original_total,
            archive_estimates,
        ),
        "system_resources": _system_resource_status(_cache_root()),
        "governor": resource_governor.get_background_decision(
            thumbnails.get_idle_seconds()
        ).to_dict(),
    }

    if ahead > 0:
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "WITH ahead_images AS ("
                "  SELECT i.id FROM images i "
                "  JOIN catalog_sources s ON s.id = i.source_id "
                "  WHERE s.included = 1 AND i.missing_at IS NULL "
                "  ORDER BY i.id LIMIT ?"
                ") "
                "SELECT COUNT(a.id) AS total, COUNT(c.image_id) AS cached "
                "FROM ahead_images a "
                "LEFT JOIN cache_entries c "
                "  ON c.image_id = a.id AND c.cache_root = ? AND c.size = ?",
                (ahead, _cache_root(), "lg"),
            )
            row = await cursor.fetchone()
            total = int(row["total"] or 0)
            cached = int(row["cached"] or 0)
        finally:
            await conn.close()

        result["total"] = total
        result["cached"] = cached
    else:
        result["total"] = 0
        result["cached"] = 0
    result["window"] = ahead
    cache_ttl = _cache_status_ttl(result)
    _cache_status_cache[cache_key] = {
        "data": _copy_cache_status_response(result),
        "expires": time.monotonic() + cache_ttl,
    }
    return result


@app.get("/api/cache/status")
async def cache_status(ahead: int = 0):
    return await build_cache_status(ahead=ahead)


@app.post("/api/cache/pregen/start")
async def cache_pregen_start():
    thumbnails.start_pregeneration()
    _invalidate_cache_status_cache()
    return {"ok": True, "cache": await build_cache_status(ahead=0, force=True)}


@app.post("/api/cache/pregen/stop")
async def cache_pregen_stop():
    thumbnails.stop_pregeneration()
    _invalidate_cache_status_cache()
    return {"ok": True, "cache": await build_cache_status(ahead=0, force=True)}


@app.get("/api/cache/pregen/status")
async def cache_pregen_status():
    return (await build_cache_status(ahead=0)).get("pregen", {})


@app.post("/api/ai/embeddings/pause")
async def api_pause_embeddings():
    try:
        import embedding_worker
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)
    embedding_worker.pause_embedding_worker()
    _invalidate_ai_status_response_cache()
    _invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status(force=True)}


@app.post("/api/ai/embeddings/resume")
async def api_resume_embeddings():
    try:
        import embedding_worker
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)
    embedding_worker.resume_embedding_worker()
    _invalidate_ai_status_response_cache()
    _invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status(force=True)}


# --- Settings API ---

@app.get("/api/settings")
async def api_settings():
    global _settings_response_refreshing
    cached = _settings_response_cache.get("data")
    if cached is not None and float(_settings_response_cache.get("expires") or 0) > time.monotonic():
        return _copy_settings_response(cached)
    if cached is not None:
        if not _settings_response_refreshing:
            _settings_response_refreshing = True

            async def _refresh_settings_response():
                global _settings_response_refreshing
                try:
                    response = await _build_settings_response()
                    _settings_response_cache["data"] = _copy_settings_response(response)
                    _settings_response_cache["expires"] = time.monotonic() + _settings_response_cache_ttl_seconds
                finally:
                    _settings_response_refreshing = False

            _track_background_task(_refresh_settings_response())
        return _copy_settings_response(cached)

    response = await _build_settings_response()
    _settings_response_cache["data"] = _copy_settings_response(response)
    _settings_response_cache["expires"] = time.monotonic() + _settings_response_cache_ttl_seconds
    return response


async def _build_settings_response():
    model_status = ai_models.get_model_status()
    cache_status_task = asyncio.create_task(build_cache_status(ahead=0))
    ai_status_task = asyncio.create_task(build_ai_status(model_status=model_status))
    catalog_task = asyncio.create_task(db.get_catalog_light_summary())
    cache_status, ai_status, catalog = await asyncio.gather(
        cache_status_task,
        ai_status_task,
        catalog_task,
    )
    response = {
        "settings": settings.get_settings(),
        "cache_stats": cache_status,
        "model_status": model_status,
        "ai_status": ai_status,
        "catalog": catalog,
        **settings.settings_metadata(),
    }
    return response


@app.get("/api/ui/settings")
async def api_ui_settings():
    config = settings.get_settings()
    return {
        "settings": {
            "show_loupe_cache_status": bool(config.get("show_loupe_cache_status", True)),
        }
    }


@app.post("/api/image/{image_id}/flag")
async def api_set_image_flag(image_id: int, request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    flag = body.get("flag", "unflagged")
    if flag not in ("picked", "unflagged", "rejected"):
        return JSONResponse({"error": "Invalid flag"}, status_code=400)

    image = await db.get_image_by_id(image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    await db.set_image_flag(image_id, flag)
    _invalidate_pairing_cache()
    return {"ok": True, "id": image_id, "flag": flag}


@app.post("/api/images/flag")
async def api_batch_set_flag(request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    flag = body.get("flag", "unflagged")
    image_ids = body.get("image_ids", [])
    if flag not in ("picked", "unflagged", "rejected"):
        return JSONResponse({"error": "Invalid flag"}, status_code=400)
    if not image_ids or not isinstance(image_ids, list):
        return JSONResponse({"error": "image_ids must be a non-empty list"}, status_code=400)

    normalized_ids = []
    seen_ids = set()
    for value in image_ids:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        normalized_ids.append(image_id)

    if not normalized_ids:
        return JSONResponse({"error": "No valid image ids"}, status_code=400)

    count = await db.batch_set_image_flags(normalized_ids, flag)
    _invalidate_pairing_cache()
    return {"ok": True, "count": count, "flag": flag}


@app.post("/api/settings")
async def api_save_settings(request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    current = settings.get_settings()
    saved = settings.save_settings(body)
    await db.sync_deep_search_terms(saved.get("deep_search_terms") or [])
    model_changed = any(
        current.get(field) != saved.get(field)
        for field in ("embed_model_id", "embed_model_revision", "embed_model_dir", "embed_model_dim")
    )
    search_runtime_changed = (
        model_changed
        or float(current.get("search_similarity_threshold", 0.35))
        != float(saved.get("search_similarity_threshold", 0.35))
    )
    thumbnail_changed = any(
        int(current.get(field, 0)) != int(saved.get(field, 0))
        for field in ("thumb_size_sm", "thumb_size_md", "thumb_size_lg", "thumb_quality")
    )
    replace_thumbnail_cache = (
        thumbnail_changed
        and str(body.get("thumbnail_cache_policy", "keep")).strip().lower() == "replace"
    )
    thumbnails.configure({**saved, "_replace_thumbnail_cache": replace_thumbnail_cache})
    if current.get("defer_ai_on_startup") != saved.get("defer_ai_on_startup"):
        try:
            import embedding_worker
            if saved.get("defer_ai_on_startup"):
                embedding_worker.pause_embedding_worker("AI work deferred by startup setting.")
            else:
                embedding_worker.resume_embedding_worker()
        except Exception:
            pass
    if model_changed:
        try:
            import embedding_worker
            embedding_worker._text_cache.clear()
        except Exception:
            pass
        _invalidate_vector_derived_caches()
    if search_runtime_changed:
        _invalidate_rankings_cache()
    _invalidate_cache_status_cache()
    _invalidate_ai_status_response_cache()
    _invalidate_settings_response_cache()
    return {
        "ok": True,
        "settings": saved,
        "cache_stats": await build_cache_status(ahead=0, force=True),
        "model_status": ai_models.get_model_status(),
        "ai_status": await build_ai_status(),
        "catalog": await db.get_catalog_summary(),
    }


@app.post("/api/settings/reset")
async def api_reset_settings():
    saved = settings.reset_settings()
    await db.sync_deep_search_terms(saved.get("deep_search_terms") or [])
    thumbnails.configure(saved)
    try:
        import embed_cache
        import embedding_worker
        embed_cache.invalidate()
        embedding_worker._text_cache.clear()
    except Exception:
        pass
    _invalidate_rankings_cache()
    _invalidate_cache_status_cache()
    _invalidate_ai_status_response_cache()
    _invalidate_settings_response_cache()
    return {
        "ok": True,
        "settings": saved,
        "cache_stats": await build_cache_status(ahead=0, force=True),
        "model_status": ai_models.get_model_status(),
        "ai_status": await build_ai_status(),
        "catalog": await db.get_catalog_summary(),
    }


@app.post("/api/cache/clear")
async def api_clear_thumbnail_cache():
    result = thumbnails.clear_cache()
    if result.get("refused"):
        return JSONResponse({"ok": False, **result}, status_code=400)
    _invalidate_cache_status_cache()
    return {
        "ok": True,
        **result,
        "cache_stats": await build_cache_status(ahead=0, force=True),
        "ai_status": await build_ai_status(),
    }


@app.post("/api/ai/model/install")
async def api_install_ai_model(role: str = "fast"):
    selected_role = "deep" if str(role or "").lower() == "deep" else "fast"
    install_config = (
        settings.deep_search_embedding_config()
        if selected_role == "deep"
        else settings.fast_search_embedding_config()
    )
    state = ai_models.start_model_install(install_config)
    active_install_dir = str(state.get("model_dir") or "")
    requested_install_dir = str(install_config["model_dir"] or "")
    if (
        state.get("running")
        and active_install_dir
        and requested_install_dir
        and active_install_dir != requested_install_dir
    ):
        return JSONResponse(
            {
                "ok": False,
                "role": selected_role,
                "error": f"Another model install is already running: {state.get('model_id') or active_install_dir}",
                "install": state,
                "model_status": ai_models.get_model_status(install_config),
                "ai_status": await build_ai_status(force=True),
            },
            status_code=409,
        )
    return {
        "ok": True,
        "role": selected_role,
        "install": state,
        "model_status": ai_models.get_model_status(install_config),
        "ai_status": await build_ai_status(force=True),
    }


# --- Mosaic Ranking API ---

# Cache for active pairing images. The default compare/mosaic path uses the
# smaller visible-candidate cache below; this broader cache is for filtered
# paths and is invalidated when ratings change.
_pairing_cache = {"data": None, "valid": False}
_matchups_cache = {"data": None, "valid": False}
_visible_matchups_cache: dict[str, dict] = {}
_visible_pairing_candidates_cache: dict[str, dict] = {}
_visible_pairing_candidates_refreshing: set[str] = set()
_visible_pairing_candidates_generation = 0
_rankings_response_cache: dict[tuple, dict] = {}
_text_search_resolution_cache: dict[tuple, dict] = {}
_deep_search_query_record_cache: dict[str, float] = {}
_interaction_response_cache: dict[tuple, dict] = {}
_settings_response_cache: dict[str, dict | float | None] = {"data": None, "expires": 0}
_settings_response_refreshing = False
_ai_status_response_cache: dict[str, dict | tuple | float | None] = {"data": None, "key": None, "expires": 0}
_visible_pairing_candidates_cache_ttl_seconds = 15.0
_patched_pairing_candidates_ttl_seconds = 15.0
# Rankings are invalidated explicitly by rating/flag/catalog changes. Keep the
# idle TTL long so returning to the app does not pay a cold rebuild tax.
_rankings_response_cache_ttl_seconds = 1800.0
_text_search_resolution_cache_ttl_seconds = 300.0
_deep_search_query_record_cache_ttl_seconds = 300.0
_interaction_response_cache_ttl_seconds = 600.0
_settings_response_cache_ttl_seconds = 10.0
_ai_status_response_cache_ttl_seconds = 5.0
_thumbnail_prefetch_inflight: set[str] = set()
_thumbnail_memory_warm_inflight: set[str] = set()
_SWISS_PAIR_WINDOW = 512
_FILTERED_SWISS_PAIR_WINDOW = 256
_FILTERED_MOSAIC_WINDOW = 192
_MOSAIC_EXPLORE_WINDOW = 768
_MOSAIC_DIVERSE_WINDOW = 1536

async def _get_pairing_images():
    """Cached wrapper — invalidated by mosaic_pick and submit_comparison."""
    if _pairing_cache["valid"] and _pairing_cache["data"] is not None:
        return _pairing_cache["data"]
    rows = await db.get_active_images_for_pairing()
    _pairing_cache["data"] = rows
    _pairing_cache["valid"] = True
    return rows

def _invalidate_pairing_cache(*, matchups: bool = False):
    global _visible_pairing_candidates_generation
    _pairing_cache["valid"] = False
    _visible_pairing_candidates_cache.clear()
    _visible_pairing_candidates_refreshing.clear()
    _visible_pairing_candidates_generation += 1
    _invalidate_rankings_cache()
    _invalidate_interaction_response_cache()
    if matchups:
        _matchups_cache["valid"] = False
        _visible_matchups_cache.clear()


def _invalidate_rankings_cache():
    _rankings_response_cache.clear()
    _text_search_resolution_cache.clear()
    _deep_search_query_record_cache.clear()


def _deep_search_query_embedding_stored(_model_key: str, _query: str):
    _invalidate_rankings_cache()
    _invalidate_ai_status_response_cache()


def _invalidate_vector_derived_caches():
    _duplicates_cache.update({"key": None, "data": None})
    _collections_cache.update({"key": None, "data": None})
    elo_propagation.invalidate_prediction_cache()
    try:
        import embed_cache
        embed_cache.invalidate()
    except Exception:
        pass


def _embedding_batch_stored(_model_key: str, _image_ids: list[int]):
    _invalidate_rankings_cache()
    _invalidate_ai_status_response_cache()
    _invalidate_vector_derived_caches()


db.register_embedding_batch_listener(_embedding_batch_stored)
db.register_deep_search_query_embedding_listener(_deep_search_query_embedding_stored)


def _invalidate_interaction_response_cache():
    _interaction_response_cache.clear()


def _copy_interaction_response(response: dict) -> dict:
    copied = dict(response)
    if isinstance(response.get("images"), list):
        copied["images"] = [dict(image) for image in response["images"]]
    if isinstance(response.get("pairs"), list):
        copied["pairs"] = [
            {
                "left": dict(pair.get("left") or {}),
                "right": dict(pair.get("right") or {}),
            }
            for pair in response["pairs"]
        ]
    if isinstance(response.get("stats"), dict):
        copied["stats"] = dict(response["stats"])
    return copied


def _copy_rankings_response(response: dict) -> dict:
    copied = dict(response)
    copied["images"] = list(response.get("images") or [])
    return copied


def _cache_rankings_response(cache_key, response: dict) -> None:
    _rankings_response_cache[cache_key] = {
        "data": _copy_rankings_response(response),
        "json": json.dumps(response, separators=(",", ":")).encode("utf-8"),
        "expires": time.monotonic() + _rankings_response_cache_ttl_seconds,
    }


def _invalidate_settings_response_cache():
    global _settings_response_refreshing
    _settings_response_cache["data"] = None
    _settings_response_cache["expires"] = 0
    _settings_response_refreshing = False


def _expire_settings_response_cache():
    global _settings_response_refreshing
    _settings_response_cache["expires"] = 0
    _settings_response_refreshing = False


def _invalidate_ai_status_response_cache():
    _ai_status_response_cache["data"] = None
    _ai_status_response_cache["key"] = None
    _ai_status_response_cache["expires"] = 0


def _copy_settings_response(response: dict) -> dict:
    copied = dict(response)
    for key in ("settings", "model_status", "catalog", "defaults"):
        if isinstance(response.get(key), dict):
            copied[key] = dict(response[key])
    if isinstance(response.get("ai_status"), dict):
        copied["ai_status"] = _copy_ai_status_response(response["ai_status"])
    if isinstance(response.get("cache_stats"), dict):
        copied["cache_stats"] = _copy_cache_status_response(response["cache_stats"])
    if isinstance(response.get("catalog"), dict):
        catalog = dict(response["catalog"])
        catalog["sources"] = [dict(source) for source in catalog.get("sources") or []]
        if isinstance(catalog.get("stats"), dict):
            catalog["stats"] = dict(catalog["stats"])
        copied["catalog"] = catalog
    if isinstance(response.get("cache_profiles"), list):
        copied["cache_profiles"] = list(response["cache_profiles"])
    if isinstance(response.get("background_work_modes"), list):
        copied["background_work_modes"] = [
            dict(mode) for mode in response["background_work_modes"]
        ]
    if isinstance(response.get("embedding_model_presets"), list):
        copied["embedding_model_presets"] = [
            dict(preset) for preset in response["embedding_model_presets"]
        ]
    return copied


def _ai_model_status_cache_key(model_status: dict) -> tuple:
    install = model_status.get("install") or {}
    return (
        bool(model_status.get("installed")),
        str(model_status.get("model_id") or ""),
        str(model_status.get("model_dir") or ""),
        int(model_status.get("dimension") or 0),
        str(model_status.get("model_key") or ""),
        bool(install.get("running")),
        str(install.get("status") or ""),
        str(install.get("message") or ""),
    )


def _copy_ai_status_response(response: dict) -> dict:
    copied = dict(response)
    if isinstance(response.get("last_batch_stage_seconds"), dict):
        copied["last_batch_stage_seconds"] = dict(response["last_batch_stage_seconds"])
    if isinstance(response.get("governor"), dict):
        copied["governor"] = dict(response["governor"])
    if isinstance(response.get("deep_search"), dict):
        copied["deep_search"] = copy.deepcopy(response["deep_search"])
    if isinstance(response.get("embedding_indexes"), dict):
        copied["embedding_indexes"] = copy.deepcopy(response["embedding_indexes"])
    return copied


async def _get_past_matchups():
    if _matchups_cache["valid"] and _matchups_cache["data"] is not None:
        return _matchups_cache["data"]
    matchups = await db.get_past_matchups()
    _matchups_cache["data"] = matchups
    _matchups_cache["valid"] = True
    return matchups


async def _get_visible_past_matchups(size: str):
    cache_root = _cache_root()
    cache_key = f"{db.DB_PATH}:{cache_root}:{size}"
    cached = _visible_matchups_cache.get(cache_key)
    if cached is not None:
        return cached["data"]
    matchups = await db.get_visible_past_matchups(size, cache_root)
    _visible_matchups_cache[cache_key] = {"data": matchups}
    return matchups


async def _get_past_matchups_for_candidate_ids(size: str, image_ids: list[int]):
    unique_ids = tuple(dict.fromkeys(int(image_id) for image_id in image_ids or [] if int(image_id) > 0))
    if len(unique_ids) < 2:
        return set()
    cache_key = f"{db.DB_PATH}:{_cache_root()}:{size}:candidates:{len(unique_ids)}:{hash(unique_ids)}"
    cached = _visible_matchups_cache.get(cache_key)
    if cached is not None:
        return cached["data"]
    matchups = await db.get_past_matchups_for_image_ids(list(unique_ids))
    _visible_matchups_cache[cache_key] = {"data": matchups}
    return matchups


def _add_past_matchups(pairs: list[tuple[int, int]]):
    _invalidate_interaction_response_cache()
    normalized_pairs = [(min(a, b), max(a, b)) for a, b in pairs]
    if _matchups_cache["valid"] and _matchups_cache["data"] is not None:
        _matchups_cache["data"].update(normalized_pairs)
    for cached in _visible_matchups_cache.values():
        cached["data"].update(normalized_pairs)


def _patch_pairing_cache(updates: list[tuple[int, float, int]]):
    _invalidate_rankings_cache()
    _invalidate_interaction_response_cache()
    update_map = {
        int(image_id): (float(elo), int(comparison_delta))
        for image_id, elo, comparison_delta in updates
    }
    if not update_map:
        _visible_pairing_candidates_cache.clear()
        _pairing_cache["valid"] = False
        return

    def _patched_rows(rows):
        patched = []
        changed = False
        for row in rows:
            try:
                image_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                patched.append(row)
                continue
            update = update_map.get(image_id)
            if update is None:
                patched.append(row)
                continue
            new_elo, comparison_delta = update
            row_dict = dict(row)
            row_dict["elo"] = new_elo
            row_dict["comparisons"] = int(row_dict.get("comparisons") or 0) + comparison_delta
            patched.append(row_dict)
            changed = True
        return patched, changed

    if _pairing_cache["valid"] and _pairing_cache["data"] is not None:
        patched, changed = _patched_rows(_pairing_cache["data"])
        if changed:
            _pairing_cache["data"] = patched
        else:
            _pairing_cache["valid"] = False

    now = time.monotonic()
    for cache_key, cached in list(_visible_pairing_candidates_cache.items()):
        rows = cached.get("data")
        if rows is None:
            continue
        cached_ids = cached.get("id_set")
        if cached_ids is not None and cached_ids.isdisjoint(update_map):
            continue
        patched, changed = _patched_rows(rows)
        if not changed:
            continue
        cached["data"] = patched
        cached["id_set"] = {int(row["id"]) for row in patched}
        cached["expires"] = now + _patched_pairing_candidates_ttl_seconds


def _schedule_pairing_propagation(coro):
    async def _runner():
        try:
            await coro
        except Exception as exc:
            print(f"Elo propagation error: {exc}")
        finally:
            _invalidate_pairing_cache()

    asyncio.create_task(_runner())


def _top_indices_desc(values, limit: int, exclude_index: int | None = None):
    import numpy as np

    if limit <= 0 or len(values) == 0:
        return []
    if exclude_index is not None:
        values = values.copy()
        values[exclude_index] = -np.inf

    limit = min(limit, len(values))
    if len(values) <= limit:
        return np.argsort(values)[::-1]

    candidates = np.argpartition(values, -limit)[-limit:]
    return candidates[np.argsort(values[candidates])[::-1]]


def _camera_label(image: dict) -> str:
    return app_helpers.camera_label(image)


def _metadata_payload(image: dict) -> dict:
    return app_helpers.metadata_payload(image)


def _visibility_counts(total_images: int, visible_images: int) -> dict:
    return app_helpers.visibility_counts(total_images, visible_images)


def _interaction_pool_stats(total_images: int, visible_images: int) -> dict:
    total = max(0, int(total_images or 0))
    visible = max(0, int(visible_images or 0))
    return {
        "total_images": total,
        "active_images": total,
        "kept": total,
        "maybe": 0,
        "filtered_pool": visible,
        "filtered_pool_visible": visible,
        "filtered_pool_total": total,
    }


def _cache_root() -> str:
    return thumbnails.SSD_CACHE_DIR


def _schedule_thumbnail_prefetch(rows, size: str, limit: int):
    if not rows or limit <= 0:
        return
    if size in _thumbnail_prefetch_inflight:
        return
    _thumbnail_prefetch_inflight.add(size)

    async def _run_prefetch():
        try:
            await thumbnails.prefetch_images(rows, size, limit=limit)
        except Exception:
            pass
        finally:
            _thumbnail_prefetch_inflight.discard(size)

    asyncio.create_task(_run_prefetch())


def _schedule_cached_thumbnail_memory_warm(
    rows,
    size: str,
    limit: int,
    *,
    active_min_warm: int = 1,
):
    if size not in ("sm", "md", "lg") or not rows or limit <= 0:
        return
    image_ids = []
    for row in rows[:limit]:
        try:
            image_ids.append(int(row["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not image_ids:
        return
    key = f"{size}:{','.join(str(image_id) for image_id in image_ids)}"
    if not key or any(existing.startswith(f"{size}:") for existing in _thumbnail_memory_warm_inflight):
        return
    _thumbnail_memory_warm_inflight.add(key)

    def _warm():
        warmed = 0
        min_before_yield = max(1, min(int(active_min_warm or 1), limit))
        for image_id in image_ids:
            if warmed >= limit:
                break
            if warmed >= min_before_yield and thumbnails.get_idle_seconds() < 15.0:
                break
            if thumbnails._memory_get_entry_fast(size, image_id) is not None:
                continue
            if thumbnails.fast_disk_read_entry(size, image_id, populate_memory=True) is not None:
                warmed += 1

    async def _run_warm():
        try:
            await asyncio.to_thread(_warm)
        except Exception:
            pass
        finally:
            _thumbnail_memory_warm_inflight.discard(key)

    asyncio.create_task(_run_warm())


def _schedule_result_thumbnail_memory_warm(rows, *, sm_limit: int = 48, md_limit: int = 12, lg_limit: int = 12):
    if not rows:
        return
    row_count = len(rows)
    _schedule_cached_thumbnail_memory_warm(
        rows, "sm", limit=min(row_count, sm_limit), active_min_warm=12
    )
    _schedule_cached_thumbnail_memory_warm(
        rows, "md", limit=min(row_count, md_limit), active_min_warm=6
    )
    _schedule_cached_thumbnail_memory_warm(
        rows, "lg", limit=min(row_count, lg_limit), active_min_warm=6
    )


def _compare_response_rows(response: dict) -> list[dict]:
    rows = []
    for pair in response.get("pairs") or ():
        left = pair.get("left") if isinstance(pair, dict) else None
        right = pair.get("right") if isinstance(pair, dict) else None
        if isinstance(left, dict):
            rows.append(left)
        if isinstance(right, dict):
            rows.append(right)
    return rows


def _chunks(values: list[int], size: int = 900):
    yield from app_helpers._chunks(values, size)


async def _cached_image_ids(image_ids, size: str) -> set[int]:
    return await app_helpers.cached_image_ids(image_ids, size, _cache_root())


async def _filter_visible_candidates(candidates: list[dict], size: str) -> list[dict]:
    return await app_helpers.filter_visible_candidates(candidates, size, _cache_root())


async def _hydrate_active_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    try:
        rows[0]["created_at"]
        return rows
    except (KeyError, IndexError, TypeError):
        pass
    by_id = await db.get_active_images_by_ids([row["id"] for row in rows])
    return [by_id.get(row["id"], row) for row in rows]


async def _default_visible_pairing_candidates(
    size: str,
    *,
    copy_rows: bool = False,
    limit: int | None = None,
    order: str = "elo",
    include_card_metadata: bool | None = None,
) -> list[dict]:
    cache_root = _cache_root()
    normalized_limit = int(limit or 0)
    if include_card_metadata is None:
        include_card_metadata = size != "md"
    cache_key = f"{db.DB_PATH}:{cache_root}:{size}:{normalized_limit}:{order}:{int(include_card_metadata)}"
    now = time.monotonic()
    cached = _visible_pairing_candidates_cache.get(cache_key)
    if cached and cached["expires"] > now:
        rows = cached["data"]
    elif cached and cached.get("data") is not None:
        rows = cached["data"]
        if cache_key not in _visible_pairing_candidates_refreshing:
            _visible_pairing_candidates_refreshing.add(cache_key)
            refresh_generation = _visible_pairing_candidates_generation

            async def _refresh_visible_pairing_candidates():
                try:
                    refreshed = await db.get_visible_images_for_pairing(
                        size,
                        cache_root,
                        include_card_metadata=include_card_metadata,
                        limit=normalized_limit or None,
                        order=order,
                    )
                    if refresh_generation != _visible_pairing_candidates_generation:
                        return
                    _visible_pairing_candidates_cache[cache_key] = {
                        "data": refreshed,
                        "id_set": {int(row["id"]) for row in refreshed},
                        "expires": time.monotonic() + _visible_pairing_candidates_cache_ttl_seconds,
                    }
                except Exception:
                    pass
                finally:
                    _visible_pairing_candidates_refreshing.discard(cache_key)

            asyncio.create_task(_refresh_visible_pairing_candidates())
    else:
        rows = await db.get_visible_images_for_pairing(
            size,
            cache_root,
            include_card_metadata=include_card_metadata,
            limit=normalized_limit or None,
            order=order,
        )
        _visible_pairing_candidates_cache[cache_key] = {
            "data": rows,
            "id_set": {int(row["id"]) for row in rows},
            "expires": time.monotonic() + _visible_pairing_candidates_cache_ttl_seconds,
        }
    if copy_rows:
        return [dict(row) for row in rows]
    return rows


async def _filtered_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
) -> tuple[list[dict], int, int]:
    cache_root = _cache_root()
    cache_key = (
        f"filtered:{db.DB_PATH}:{cache_root}:{size}:{max(1, int(limit))}:"
        f"{orientation}:{compared}:{int(min_stars or 0)}:{folder}:{flag}:"
        f"{date_taken}:{file_type}:{camera}:{lens}"
    )
    now = time.monotonic()
    cached = _visible_pairing_candidates_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return (
            cached["data"],
            int(cached.get("filtered_total") or len(cached["data"])),
            int(cached.get("visible_count") or len(cached["data"])),
        )
    if cached and cached.get("data") is not None:
        if cache_key not in _visible_pairing_candidates_refreshing:
            _visible_pairing_candidates_refreshing.add(cache_key)
            refresh_generation = _visible_pairing_candidates_generation

            async def _refresh_filtered_visible_ranked_candidates():
                try:
                    refreshed_rows, refreshed_total, refreshed_visible = (
                        await _load_filtered_visible_ranked_candidates(
                            size,
                            limit=limit,
                            orientation=orientation,
                            compared=compared,
                            min_stars=min_stars,
                            folder=folder,
                            flag=flag,
                            date_taken=date_taken,
                            file_type=file_type,
                            camera=camera,
                            lens=lens,
                        )
                    )
                    if refresh_generation != _visible_pairing_candidates_generation:
                        return
                    _visible_pairing_candidates_cache[cache_key] = {
                        "data": refreshed_rows,
                        "id_set": {int(row["id"]) for row in refreshed_rows},
                        "filtered_total": int(refreshed_total),
                        "visible_count": int(refreshed_visible),
                        "expires": time.monotonic() + _visible_pairing_candidates_cache_ttl_seconds,
                    }
                except Exception:
                    pass
                finally:
                    _visible_pairing_candidates_refreshing.discard(cache_key)

            asyncio.create_task(_refresh_filtered_visible_ranked_candidates())
        return (
            cached["data"],
            int(cached.get("filtered_total") or len(cached["data"])),
            int(cached.get("visible_count") or len(cached["data"])),
        )

    result_rows, filtered_total, visible_count = await _load_filtered_visible_ranked_candidates(
        size,
        limit=limit,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
    )
    _visible_pairing_candidates_cache[cache_key] = {
        "data": result_rows,
        "id_set": {int(row["id"]) for row in result_rows},
        "filtered_total": int(filtered_total),
        "visible_count": int(visible_count),
        "expires": time.monotonic() + _visible_pairing_candidates_cache_ttl_seconds,
    }
    return result_rows, int(filtered_total), int(visible_count)


async def _load_filtered_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
) -> tuple[list[dict], int, int]:
    cache_root = _cache_root()
    normalized_limit = max(1, int(limit))
    orientation_only = bool(orientation) and not any(
        (
            compared,
            int(min_stars or 0),
            folder,
            flag,
            date_taken,
            file_type,
            camera,
            lens,
        )
    )
    if orientation_only:
        counts_task = asyncio.create_task(
            db.get_visible_orientation_pairing_pool_counts(size, cache_root, orientation)
        )
    else:
        filtered_total_task = asyncio.create_task(
            db.count_rankings(
                orientation=orientation,
                compared=compared,
                min_stars=min_stars,
                folder=folder,
                flag=flag,
                date_taken=date_taken,
                file_type=file_type,
                camera=camera,
                lens=lens,
            )
        )
        visible_count_task = asyncio.create_task(
            db.count_rankings(
                orientation=orientation,
                compared=compared,
                min_stars=min_stars,
                folder=folder,
                flag=flag,
                date_taken=date_taken,
                file_type=file_type,
                camera=camera,
                lens=lens,
                visible_thumb_size=size,
                cache_root=cache_root,
            )
        )
    rows = await db.get_rankings(
        limit=normalized_limit,
        offset=0,
        sort="elo",
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        visible_thumb_size=size,
        cache_root=cache_root,
    )
    result_rows = [dict(row) for row in rows]
    if orientation_only:
        counts = await counts_task
        filtered_total = int(counts.get("active_images") or 0)
        visible_count = int(counts.get("visible_images") or 0)
    else:
        filtered_total = await filtered_total_task
        visible_count = await visible_count_task
    return result_rows, int(filtered_total), int(visible_count)


async def _search_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
    search: dict,
    exclude_ids: set[int] | None = None,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
) -> tuple[list[dict], int, int]:
    cache_root = _cache_root()
    exclude_ids = exclude_ids or set()
    fetch_limit = max(1, int(limit)) + min(len(exclude_ids), 200)
    id_filter = search.get("id_filter")
    text_query = search.get("text_query") or ""
    exact_counts = not (text_query and id_filter is None)
    if exact_counts:
        total_task = asyncio.create_task(
            db.count_rankings(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens,
                id_filter=id_filter,
                text_query=text_query,
            )
        )
        visible_task = asyncio.create_task(
            db.count_rankings(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens,
                id_filter=id_filter,
                visible_thumb_size=size,
                cache_root=cache_root,
                text_query=text_query,
            )
        )
    else:
        total_task = None
        visible_task = None
    rows = await db.get_rankings(
        limit=fetch_limit,
        offset=0,
        sort="elo",
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        id_filter=id_filter,
        visible_thumb_size=size,
        cache_root=cache_root,
        text_query=text_query,
    )
    result_rows = [
        dict(row)
        for row in rows
        if int(row["id"]) not in exclude_ids
    ][:max(1, int(limit))]
    if exact_counts:
        return result_rows, await total_task, await visible_task
    visible_count = len(result_rows)
    return result_rows, visible_count, visible_count


async def _warm_filtered_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
    orientation: str,
    warm_matchups: bool = False,
) -> None:
    try:
        rows, _filtered_total, _visible_count = await _filtered_visible_ranked_candidates(
            size,
            limit=limit,
            orientation=orientation,
        )
        if warm_matchups:
            await _get_past_matchups_for_candidate_ids(size, [row["id"] for row in rows])
    except Exception:
        pass


async def _visible_ranked_images(ranked_ids: list[int], limit: int, size: str = "sm") -> list[dict]:
    return await app_helpers.visible_ranked_images(ranked_ids, limit, size, _cache_root())


async def _count_visible_ranked_ids(ranked_ids: list[int], size: str = "sm") -> int:
    return await app_helpers.count_visible_ranked_ids(ranked_ids, size, _cache_root())


async def _visible_embedding_page(
    image_ids,
    similarities,
    limit: int,
    size: str = "sm",
    *,
    exclude_id: int | None = None,
    model_key: str | None = None,
) -> tuple[list[dict], int, int]:
    cached_ids = await db.get_cached_image_id_set(size, _cache_root())
    if not cached_ids:
        total = max(0, len(image_ids) - (1 if exclude_id is not None else 0))
        return [], 0, total

    id_to_idx = {}
    try:
        import embed_cache
        id_to_idx = embed_cache.get_index(model_key)
    except Exception:
        id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}
    if not id_to_idx:
        id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}

    visible_pairs = []
    for image_id in cached_ids:
        image_id = int(image_id)
        if exclude_id is not None and image_id == exclude_id:
            continue
        idx = id_to_idx.get(image_id)
        if idx is None:
            continue
        visible_pairs.append((image_id, float(similarities[idx])))

    visible_count = len(visible_pairs)
    if len(visible_pairs) > limit:
        visible_pairs = heapq.nlargest(limit, visible_pairs, key=lambda item: item[1])
    else:
        visible_pairs.sort(key=lambda item: item[1], reverse=True)
    selected_ids = [image_id for image_id, _score in visible_pairs[:limit]]

    rows_by_id = await db.get_active_images_by_ids(selected_ids)
    visible_rows = []
    for image_id in selected_ids:
        row = rows_by_id.get(image_id)
        if row is not None:
            visible_rows.append(row)
    total = max(0, len(image_ids) - (1 if exclude_id is not None else 0))
    return visible_rows, visible_count, total


def _filter_by_metadata(
    images: list[dict],
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
) -> list[dict]:
    return app_helpers.filter_by_metadata(images, date_taken, file_type, camera, lens)


async def _record_deep_search_query(query: str):
    normalized_query = _normalize_search_query(query)
    if not normalized_query:
        return
    extension_query = normalized_query.lower().lstrip(".")
    if extension_query in db.IMAGE_EXTENSION_SEARCH_TERMS:
        return
    cache_key = normalized_query.casefold()
    now = time.monotonic()
    if _deep_search_query_record_cache.get(cache_key, 0.0) > now:
        return
    try:
        await db.record_deep_search_query(normalized_query)
        _deep_search_query_record_cache[cache_key] = (
            now + _deep_search_query_record_cache_ttl_seconds
        )
        _invalidate_ai_status_response_cache()
        _invalidate_settings_response_cache()
    except Exception:
        pass


def _normalize_search_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    max_length = int(getattr(settings, "MAX_DEEP_SEARCH_TERM_LENGTH", 160))
    return normalized[:max_length].strip()


async def _resolve_cached_deep_search(query: str) -> dict | None:
    normalized_query = _normalize_search_query(query)
    if not normalized_query:
        return None
    try:
        import embed_cache
        import embedding_worker

        deep_config = settings.deep_search_embedding_config()
        blob = await db.get_deep_search_query_embedding(normalized_query, deep_config["model_key"])
        if blob is None:
            return None
        text_vec = embedding_worker.blob_to_vec(blob)
        image_ids, matrix = await embed_cache.get_matrix(deep_config["model_key"])
        if image_ids is None or matrix is None or matrix.shape[1] != text_vec.shape[0]:
            return None
        similarities = matrix @ text_vec
        threshold = settings.get_settings().get("search_similarity_threshold", 0.35)
        import numpy as np
        matching_indices = np.flatnonzero(similarities >= threshold)
        scores = {
            int(image_ids[int(i)]): float(similarities[int(i)])
            for i in matching_indices
        }
        return {
            "id_filter": set(scores.keys()),
            "scores": scores,
            "image_ids": image_ids,
            "similarities": similarities,
            "search_mode": "deep_embedding",
            "deep_model_key": deep_config["model_key"],
        }
    except Exception:
        return None


def _encode_text_with_config(encoder, query: str, config: dict):
    try:
        import inspect

        if len(inspect.signature(encoder).parameters) < 2:
            return encoder(query)
    except (TypeError, ValueError):
        pass
    return encoder(query, config)


async def _resolve_text_search(q: str, *, deep: bool = False) -> dict:
    """Resolve a text query into either embedding IDs or metadata fallback text."""
    normalized_query = _normalize_search_query(q)
    deep_requested = bool(deep)
    cache_key = (normalized_query.casefold(), deep_requested)
    result = {
        "active": bool(normalized_query),
        "id_filter": None,
        "scores": {},
        "text_query": "",
        "search_mode": "",
        "ai_unavailable": False,
        "deep_requested": deep_requested,
        "deep_search_cached": False,
        "fallback_reason": "",
    }
    if not normalized_query:
        return result

    extension_query = normalized_query.lower().lstrip(".")
    cached = _text_search_resolution_cache.get(cache_key)
    if cached and cached["expires"] > time.monotonic():
        return dict(cached["data"])

    if extension_query not in db.IMAGE_EXTENSION_SEARCH_TERMS:
        await _record_deep_search_query(normalized_query)

    if extension_query in db.IMAGE_EXTENSION_SEARCH_TERMS:
        result.update({
            "text_query": normalized_query,
            "search_mode": "metadata",
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    deep_search = await _resolve_cached_deep_search(normalized_query)
    if deep_search is not None:
        result.update({
            "id_filter": deep_search["id_filter"],
            "scores": deep_search["scores"],
            "search_mode": deep_search["search_mode"],
            "deep_search_cached": True,
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    if deep_requested:
        result.update({
            "text_query": normalized_query,
            "search_mode": "metadata",
            "ai_unavailable": True,
            "fallback_reason": "deep_search_not_cached",
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    try:
        import embedding_worker
        import embed_cache

        import importlib.util
        default_loader = (
            getattr(embedding_worker.ensure_model_loaded_for_search, "__module__", "")
            == "embedding_worker"
        )
        if default_loader and importlib.util.find_spec("torch") is None:
            raise RuntimeError("torch is not installed")

        fast_config = settings.fast_search_embedding_config()
        text_vec = await asyncio.get_event_loop().run_in_executor(
            None,
            _encode_text_with_config,
            embedding_worker.encode_text,
            normalized_query,
            fast_config,
        )
        if text_vec is None and await embedding_worker.ensure_model_loaded_for_search():
            text_vec = await asyncio.get_event_loop().run_in_executor(
                None,
                _encode_text_with_config,
                embedding_worker.encode_text,
                normalized_query,
                fast_config,
            )
        if text_vec is not None:
            image_ids, matrix = await embed_cache.get_matrix()
            if image_ids is not None:
                config = settings.get_settings()
                threshold = config.get("search_similarity_threshold", 0.35)
                similarities = matrix @ text_vec
                import numpy as np
                matching_indices = np.flatnonzero(similarities >= threshold)
                scores = {
                    int(image_ids[int(i)]): float(similarities[int(i)])
                    for i in matching_indices
                }
                result.update({
                    "id_filter": set(scores.keys()),
                    "scores": scores,
                    "search_mode": "embedding",
                })
                _text_search_resolution_cache[cache_key] = {
                    "data": dict(result),
                    "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
                }
                return result
    except Exception:
        pass

    result.update({
        "text_query": normalized_query,
        "search_mode": "metadata",
        "ai_unavailable": True,
    })
    if extension_query not in db.IMAGE_EXTENSION_SEARCH_TERMS:
        metadata_ids = await db.metadata_search_image_ids(normalized_query)
        if metadata_ids is not None:
            result["id_filter"] = metadata_ids
    _text_search_resolution_cache[cache_key] = {
        "data": dict(result),
        "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
    }
    return result


def _metadata_text_match(image: dict, query: str) -> bool:
    needle = (query or "").strip().lower()
    if not needle:
        return True
    fields = (
        "filename",
        "filepath",
        "date_taken",
        "camera_make",
        "camera_model",
        "lens",
        "file_ext",
    )
    return any(needle in str(image.get(field) or "").lower() for field in fields)


def _apply_text_search_constraint(candidates: list[dict], search: dict) -> list[dict]:
    if not search.get("active"):
        return candidates
    id_filter = search.get("id_filter")
    if id_filter is not None:
        search_ids = {int(image_id) for image_id in id_filter}
        return [c for c in candidates if int(c.get("id") or 0) in search_ids]
    text_query = search.get("text_query") or ""
    return [c for c in candidates if _metadata_text_match(c, text_query)]


def _has_candidate_filters(
    *,
    exclude_ids: set[int] | None = None,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
    search: dict | None = None,
) -> bool:
    return bool(
        exclude_ids
        or orientation
        or compared
        or min_stars > 0
        or folder
        or flag
        or date_taken
        or file_type
        or camera
        or lens
        or (search and search.get("active"))
    )


async def _diverse_sample(candidates: list[dict], count: int) -> list[dict]:
    """Select images that maximize visual diversity using embedding distance."""
    import random
    if len(candidates) <= count:
        return candidates

    try:
        import numpy as np
        import embed_cache

        try:
            deep_key = elo_propagation.compare_embedding_model_key()
            image_ids, matrix = await embed_cache.get_matrix(deep_key)
            id_to_idx = embed_cache.get_index(deep_key)
        except Exception:
            image_ids, matrix = None, None
            id_to_idx = {}

        if image_ids is None:
            image_ids, matrix = embed_cache.get_warm_matrix()
            if image_ids is None:
                return random.sample(candidates, min(count, len(candidates)))
            id_to_idx = embed_cache.get_index()

        # Bound the expensive per-candidate work for large libraries. The final
        # diversity pool is only 500 images, so a 5k search window keeps the same
        # broad random/exploratory behavior without building arrays over 100k+ rows.
        SEARCH_POOL = max(5000, count * 40)
        search_candidates = candidates
        if len(candidates) > SEARCH_POOL:
            search_candidates = random.sample(candidates, SEARCH_POOL)

        # Filter candidates to those with embeddings, get their matrix indices
        cand_indices = []  # index into matrix
        cand_items = []    # corresponding candidate dicts
        without_emb = []
        for c in search_candidates:
            idx = id_to_idx.get(c["id"])
            if idx is not None:
                cand_indices.append(idx)
                cand_items.append(c)
            else:
                without_emb.append(c)

        # If embeddings are sparse, fall back to scanning the full candidate set
        # so the function still returns enough images instead of letting the cap
        # change behavior for partially embedded libraries.
        if len(cand_items) < count and search_candidates is not candidates:
            seen = {c["id"] for c in search_candidates}
            for c in candidates:
                if c["id"] in seen:
                    continue
                idx = id_to_idx.get(c["id"])
                if idx is not None:
                    cand_indices.append(idx)
                    cand_items.append(c)
                else:
                    without_emb.append(c)
                if len(cand_items) >= count:
                    break

        if len(cand_items) < count:
            sample = list(cand_items)
            remaining = count - len(sample)
            if without_emb and remaining > 0:
                sample.extend(random.sample(without_emb, min(remaining, len(without_emb))))
            return sample

        # Subsample a random pool — skip building the full candidate matrix.
        # Use comparison-count strata instead of np.random.choice(..., p=weights):
        # it keeps the least-compared bias, but avoids normalizing/probability
        # sampling across every candidate.
        POOL = min(max(count * 16, 96), 192, len(cand_items))
        comp_counts = np.fromiter(
            (c["comparisons"] for c in cand_items),
            dtype=np.float32,
            count=len(cand_items),
        )

        if len(cand_items) > POOL:
            bucket_defs = (
                comp_counts == 0,
                (comp_counts > 0) & (comp_counts <= 2),
                (comp_counts > 2) & (comp_counts <= 5),
                (comp_counts > 5) & (comp_counts <= 10),
                comp_counts > 10,
            )
            bucket_indices = [np.flatnonzero(mask) for mask in bucket_defs]
            bucket_weights = np.array(
                [
                    float((1.0 / (comp_counts[idx] + 1.0)).sum()) if len(idx) else 0.0
                    for idx in bucket_indices
                ],
                dtype=np.float64,
            )

            if bucket_weights.sum() > 0:
                raw_quotas = bucket_weights / bucket_weights.sum() * POOL
                quotas = np.minimum(
                    np.floor(raw_quotas).astype(int),
                    [len(idx) for idx in bucket_indices],
                )
                remaining = POOL - int(quotas.sum())
                fractions = raw_quotas - np.floor(raw_quotas)
                for bucket in np.argsort(fractions)[::-1]:
                    if remaining <= 0:
                        break
                    capacity = len(bucket_indices[bucket]) - quotas[bucket]
                    if capacity <= 0:
                        continue
                    take = min(remaining, capacity)
                    quotas[bucket] += take
                    remaining -= take

                selected_idx = []
                for idx, quota in zip(bucket_indices, quotas):
                    if quota <= 0:
                        continue
                    selected_idx.extend(random.sample(idx.tolist(), int(quota)))

                if len(selected_idx) < POOL:
                    selected_set = set(selected_idx)
                    remaining_idx = [i for i in range(len(cand_items)) if i not in selected_set]
                    selected_idx.extend(random.sample(remaining_idx, POOL - len(selected_idx)))
                pool_idx = np.array(selected_idx, dtype=np.intp)
                np.random.shuffle(pool_idx)
            else:
                pool_idx = np.array(random.sample(range(len(cand_items)), POOL), dtype=np.intp)
        else:
            pool_idx = np.arange(len(cand_items))

        # Build matrix only for the pool (500 x 2048 instead of 20k x 2048)
        pool_matrix_idx = np.fromiter(
            (cand_indices[int(i)] for i in pool_idx),
            dtype=np.intp,
            count=len(pool_idx),
        )
        pool_matrix = matrix[pool_matrix_idx]
        pool_items = [cand_items[int(i)] for i in pool_idx]
        pool_bias = 1.0 / (comp_counts[pool_idx] + 1.0)

        # Random seed for variety
        first = random.randrange(len(pool_items))
        selected = [first]

        # Greedy farthest-point with comparison-count bias
        max_sim = pool_matrix @ pool_matrix[first]

        for _ in range(count - 1):
            max_sim[selected[-1]] = 999.0
            score = max_sim - pool_bias * 0.15
            next_pick = int(np.argmin(score))
            selected.append(next_pick)
            new_sims = pool_matrix @ pool_matrix[next_pick]
            np.maximum(max_sim, new_sims, out=max_sim)

        return [pool_items[i] for i in selected]

    except Exception:
        # Fallback to random if embeddings unavailable
        import random
        return random.sample(candidates, min(count, len(candidates)))


@app.get("/api/mosaic/next")
async def mosaic_next(
    n: int = 12, exclude: str = "", strategy: str = "explore", grid_elo: float = 0,
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: str = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    q: str = "", deep: bool = False,
):
    """Get active images for mosaic ranking with configurable sampling strategy."""
    exclude_ids = set()
    if exclude:
        exclude_ids = {int(x) for x in exclude.split(",") if x.strip().isdigit()}
    search = await _resolve_text_search(q, deep=deep)
    has_filters = _has_candidate_filters(
        exclude_ids=exclude_ids,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        search=search,
    )
    default_pool_only = not _has_candidate_filters(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        search=search,
    )
    response_cache_key = None
    if default_pool_only and not exclude_ids and strategy in {"explore", "diverse"}:
        response_cache_key = (
            "mosaic_next",
            db.DB_PATH,
            _cache_root(),
            int(n),
            strategy,
        )
        cached_response = _interaction_response_cache.get(response_cache_key)
        if cached_response and cached_response["expires"] > time.monotonic():
            response = _copy_interaction_response(cached_response["data"])
            _schedule_cached_thumbnail_memory_warm(response.get("images") or [], "sm", limit=min(max(1, n), 48))
            return response
    if default_pool_only and strategy != "top":
        counts_task = asyncio.create_task(db.get_visible_pairing_pool_counts("sm", _cache_root()))
        if strategy == "explore":
            candidates = await _default_visible_pairing_candidates(
                "sm",
                limit=max(_MOSAIC_EXPLORE_WINDOW, n * 80),
                order="cache",
            )
        elif strategy == "diverse":
            candidates = await _default_visible_pairing_candidates(
                "sm",
                limit=max(_MOSAIC_DIVERSE_WINDOW, n * 120),
                order="least_compared",
                include_card_metadata=False,
            )
        else:
            candidates = await _default_visible_pairing_candidates("sm")
        if exclude_ids:
            candidates = [row for row in candidates if int(row["id"]) not in exclude_ids]
        counts = await counts_task
        visible_count = int(counts.get("visible_images") or 0)
        filtered_total = int(counts.get("active_images") or 0)
        stats = _interaction_pool_stats(filtered_total, visible_count)
    elif strategy != "top" and not search.get("active"):
        stats = None
        candidates, filtered_total, visible_count = await _filtered_visible_ranked_candidates(
            "sm",
            limit=max(_FILTERED_MOSAIC_WINDOW, n * 40),
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        if exclude_ids:
            candidates = [row for row in candidates if int(row["id"]) not in exclude_ids]
            filtered_total = max(0, int(filtered_total) - len(exclude_ids))
            visible_count = max(0, int(visible_count) - len(exclude_ids))
    elif strategy != "top" and search.get("active"):
        stats = None
        candidates, filtered_total, visible_count = await _search_visible_ranked_candidates(
            "sm",
            limit=max(_FILTERED_MOSAIC_WINDOW, n * 40),
            search=search,
            exclude_ids=exclude_ids,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
    else:
        stats = None
        if strategy == "top":
            images = await db.get_top_images(limit=50)
        else:
            images = await _get_pairing_images()
        candidates = app_helpers.filter_compare_mosaic_candidates(
            images,
            exclude_ids=exclude_ids,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        candidates = _apply_text_search_constraint(candidates, search)
        filtered_total = len(candidates)
        candidates = await _filter_visible_candidates(candidates, "sm")
        visible_count = len(candidates)

    if len(candidates) < 2 and default_pool_only and strategy == "explore" and visible_count > len(candidates):
        candidates = await _default_visible_pairing_candidates("sm")
        if exclude_ids:
            candidates = [row for row in candidates if int(row["id"]) not in exclude_ids]
        visible_count = len(candidates)

    if len(candidates) < 2:
        stats = stats or _interaction_pool_stats(filtered_total, visible_count)
        stats["filtered_pool"] = visible_count
        stats["filtered_pool_visible"] = visible_count
        stats["filtered_pool_total"] = filtered_total
        response = {
            "images": [],
            **_visibility_counts(filtered_total, visible_count),
            "total_kept": filtered_total,
            "stats": stats,
            "search_mode": search["search_mode"],
            "ai_unavailable": search["ai_unavailable"],
            "deep_requested": search.get("deep_requested", False),
            "deep_search_cached": search.get("deep_search_cached", False),
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if response_cache_key is not None:
            _interaction_response_cache[response_cache_key] = {
                "data": _copy_interaction_response(response),
                "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
            }
        return response

    import random
    count = min(n, len(candidates))

    def effective_elo(img):
        # Hook for predicted ranking can live here without mutating the shared candidate cache.
        return img["elo"] or 1200.0

    if strategy == "diverse":
        # Maximize visual diversity: pick images that are most dissimilar from each other
        sample = await _diverse_sample(candidates, count)
    else:
        if strategy == "explore":
            # Favor least-compared images
            weights = [1.0 / (img["comparisons"] + 1) for img in candidates]
        elif strategy == "compete" and grid_elo > 0:
            # Favor images with effective Elo close to the grid average
            weights = [1.0 / (abs(effective_elo(img) - grid_elo) + 50) for img in candidates]
        elif strategy == "top":
            # Favor highest-rated within the top 50
            weights = [effective_elo(img) for img in candidates]
        else:
            # Random — uniform
            weights = [1.0 for _ in candidates]

        draw_count = min(len(candidates), max(count * 4, count))
        sample = []
        seen_ids = set()
        for img in random.choices(candidates, weights=weights, k=draw_count):
            image_id = img["id"]
            if image_id in seen_ids:
                continue
            sample.append(img)
            seen_ids.add(image_id)
            if len(sample) >= count:
                break
        if len(sample) < count:
            remaining = [img for img in candidates if img["id"] not in seen_ids]
            sample.extend(random.sample(remaining, min(count - len(sample), len(remaining))))

    sample_elo_by_id = {img["id"]: effective_elo(img) for img in sample}
    hydrated_sample = await _hydrate_active_rows(sample)
    result = [
        app_helpers.image_card(img, "sm", elo_value=sample_elo_by_id.get(img["id"], img["elo"]))
        for img in hydrated_sample
    ]

    if sample:
        config = settings.get_settings()
        _schedule_thumbnail_prefetch(
            hydrated_sample,
            "md",
            limit=min(len(sample), config["mosaic_prefetch_limit"]),
        )
        _schedule_cached_thumbnail_memory_warm(result, "sm", limit=min(len(result), 48))

    stats = stats or _interaction_pool_stats(filtered_total, visible_count)
    stats["filtered_pool"] = visible_count
    stats["filtered_pool_visible"] = visible_count
    stats["filtered_pool_total"] = filtered_total
    response = {
        "images": result,
        **_visibility_counts(filtered_total, visible_count),
        "total_kept": filtered_total,
        "stats": stats,
        "search_mode": search["search_mode"],
        "ai_unavailable": search["ai_unavailable"],
        "deep_requested": search.get("deep_requested", False),
        "deep_search_cached": search.get("deep_search_cached", False),
        "fallback_reason": search.get("fallback_reason", ""),
    }
    if response_cache_key is not None:
        _interaction_response_cache[response_cache_key] = {
            "data": _copy_interaction_response(response),
            "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
        }
    return response
@app.post("/api/mosaic/pick")
async def mosaic_pick(request: Request):
    """
    User picked the best image from the visible mosaic.
    Body: { "winner_id": int, "loser_ids": [int, ...] }
    K=12 per pair.
    """
    body, error = await _json_object(request)
    if error:
        return error
    picked_id = _positive_int(body.get("winner_id"))
    raw_other_ids = body.get("loser_ids", [])

    if picked_id is None or not isinstance(raw_other_ids, list) or not raw_other_ids:
        return JSONResponse({"error": "Need winner_id and loser_ids"}, status_code=400)

    other_ids = []
    seen_losers = set()
    for value in raw_other_ids:
        loser_id = _positive_int(value)
        if loser_id is None:
            return JSONResponse({"error": "loser_ids must contain valid image ids"}, status_code=400)
        if loser_id == picked_id:
            return JSONResponse({"error": "Winner cannot also be a loser"}, status_code=400)
        if loser_id in seen_losers:
            return JSONResponse({"error": "Duplicate loser_ids are not allowed"}, status_code=400)
        seen_losers.add(loser_id)
        other_ids.append(loser_id)

    action_id = uuid.uuid4().hex
    result = await db.record_active_mosaic_pick(picked_id, other_ids, action_id)
    if not result.get("ok"):
        return JSONResponse(
            {
                "error": "Images must exist in an active online catalog",
                "image_ids": result.get("missing_ids", []),
            },
            status_code=400,
        )

    picked_elo = result["new_elo"]
    pairs_recorded = result["pairs_recorded"]
    loser_updates = result["loser_updates"]

    if pairs_recorded:
        _patch_pairing_cache(
            [(picked_id, picked_elo, pairs_recorded)]
            + [(image_id, new_elo, 1) for image_id, new_elo in loser_updates]
        )
    _add_past_matchups([(picked_id, loser_id) for loser_id in other_ids])

    # Fire-and-forget: propagate Elo to similar images via embeddings
    _schedule_pairing_propagation(
        elo_propagation.propagate_mosaic(picked_id, other_ids, k=12.0, action_id=action_id)
    )

    return {
        "ok": True,
        "new_elo": round(picked_elo, 1),
        "pairs_recorded": pairs_recorded,
        "action_id": action_id,
    }


@app.get("/api/propagation/last")
async def propagation_last():
    """Return the number of images affected by the last Elo propagation."""
    return {"count": elo_propagation.last_propagation_count}


@app.post("/api/propagation/predict")
async def propagation_predict(request: Request):
    """Precompute propagation counts for each possible winner in a grid."""
    body, error = await _json_object(request)
    if error:
        return error
    grid_ids = body.get("grid_ids", [])
    if not grid_ids:
        return {"counts": {}}
    counts = await elo_propagation.predict_propagation(grid_ids)
    return {"counts": {str(k): v for k, v in counts.items()}}


# --- Compare API ---

@app.get("/api/compare/next")
async def compare_next(
    n: int = 5, mode: str = "swiss",
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: str = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    q: str = "", deep: bool = False,
):
    search = await _resolve_text_search(q, deep=deep)
    default_limited_candidates = False
    ranked_candidate_order = False
    has_filters = _has_candidate_filters(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        search=search,
    )
    response_cache_key = None
    if not has_filters and mode != "topn":
        response_cache_key = (
            "compare_next",
            db.DB_PATH,
            _cache_root(),
            int(n),
            mode,
        )
        cached_response = _interaction_response_cache.get(response_cache_key)
        if cached_response and cached_response["expires"] > time.monotonic():
            return _copy_interaction_response(cached_response["data"])
    if not has_filters and mode != "topn":
        counts_task = asyncio.create_task(db.get_visible_pairing_pool_counts("md", _cache_root()))
        image_dicts = await _default_visible_pairing_candidates(
            "md",
            limit=max(_SWISS_PAIR_WINDOW, n * 30),
            include_card_metadata=True,
        )
        past_task = asyncio.create_task(
            _get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )
        default_limited_candidates = True
        ranked_candidate_order = True
        counts = await counts_task
        visible_count = int(counts.get("visible_images") or 0)
        filtered_total = int(counts.get("active_images") or 0)
        stats = _interaction_pool_stats(filtered_total, visible_count)
    elif mode != "topn" and not search.get("active"):
        stats = None
        image_dicts, filtered_total, visible_count = await _filtered_visible_ranked_candidates(
            "md",
            limit=max(_FILTERED_SWISS_PAIR_WINDOW, n * 30),
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        past_task = asyncio.create_task(
            _get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )
        ranked_candidate_order = True
    elif mode != "topn" and search.get("active"):
        stats = None
        image_dicts, filtered_total, visible_count = await _search_visible_ranked_candidates(
            "md",
            limit=max(_FILTERED_SWISS_PAIR_WINDOW, n * 30),
            search=search,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        past_task = asyncio.create_task(
            _get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )
        ranked_candidate_order = True
    else:
        stats = None
        past_task = None
        if mode == "topn":
            images = await db.get_top_images(limit=50)
        else:
            images = await _get_pairing_images()
        image_dicts = app_helpers.filter_compare_mosaic_candidates(
            images,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        image_dicts = _apply_text_search_constraint(image_dicts, search)
        filtered_total = len(image_dicts)
        image_dicts = await _filter_visible_candidates(image_dicts, "md")
        visible_count = len(image_dicts)

    if len(image_dicts) < 2:
        stats = stats or _interaction_pool_stats(filtered_total, visible_count)
        stats["filtered_pool"] = visible_count
        stats["filtered_pool_visible"] = visible_count
        stats["filtered_pool_total"] = filtered_total
        response = {
            "pairs": [],
            **_visibility_counts(filtered_total, visible_count),
            "total_kept": filtered_total,
            "stats": stats,
            "search_mode": search["search_mode"],
            "ai_unavailable": search["ai_unavailable"],
            "deep_requested": search.get("deep_requested", False),
            "deep_search_cached": search.get("deep_search_cached", False),
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if response_cache_key is not None:
            _interaction_response_cache[response_cache_key] = {
                "data": _copy_interaction_response(response),
                "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
            }
        return response
    past = await past_task if past_task is not None else await _get_past_matchups()
    if not has_filters and mode != "topn" and len(image_dicts) > _SWISS_PAIR_WINDOW:
        pairs = pairing.swiss_pair(image_dicts[:_SWISS_PAIR_WINDOW], past, max_pairs=n, presorted=True)
        if len(pairs) < n:
            pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=True)
    else:
        pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=ranked_candidate_order)
    if default_limited_candidates and len(pairs) < n and visible_count > len(image_dicts):
        image_dicts = await _default_visible_pairing_candidates("md")
        past = await _get_visible_past_matchups("md")
        pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=True)
    elif (
        has_filters
        and mode != "topn"
        and not search.get("active")
        and len(pairs) < n
        and visible_count > len(image_dicts)
    ):
        image_dicts, _filtered_total, _visible_count = await _filtered_visible_ranked_candidates(
            "md",
            limit=visible_count,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        past = await _get_visible_past_matchups("md")
        pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=True)

    result = []
    prefetch_rows = []
    pair_rows = [row for pair in pairs for row in pair]
    hydrated_rows = await _hydrate_active_rows(pair_rows)
    hydrated_by_id = {row["id"]: row for row in hydrated_rows}
    for left, right in pairs:
        left_row = hydrated_by_id.get(left["id"], left)
        right_row = hydrated_by_id.get(right["id"], right)
        prefetch_rows.append(left_row)
        prefetch_rows.append(right_row)
        result.append({
            "left": app_helpers.image_card(left_row, "md"),
            "right": app_helpers.image_card(right_row, "md"),
        })

    if prefetch_rows:
        config = settings.get_settings()
        _schedule_thumbnail_prefetch(
            prefetch_rows,
            "md",
            limit=min(len(prefetch_rows), config["compare_prefetch_limit"]),
        )
        _schedule_cached_thumbnail_memory_warm(
            prefetch_rows,
            "md",
            limit=min(len(prefetch_rows), max(2, n * 2)),
        )

    stats = stats or _interaction_pool_stats(filtered_total, visible_count)
    stats["filtered_pool"] = visible_count
    stats["filtered_pool_visible"] = visible_count
    stats["filtered_pool_total"] = filtered_total
    response = {
        "pairs": result,
        **_visibility_counts(filtered_total, visible_count),
        "total_kept": filtered_total,
        "stats": stats,
        "search_mode": search["search_mode"],
        "ai_unavailable": search["ai_unavailable"],
        "deep_requested": search.get("deep_requested", False),
        "deep_search_cached": search.get("deep_search_cached", False),
        "fallback_reason": search.get("fallback_reason", ""),
    }
    if response_cache_key is not None:
        _interaction_response_cache[response_cache_key] = {
            "data": _copy_interaction_response(response),
            "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
        }
    return response


@app.post("/api/compare")
async def submit_comparison(request: Request):
    body, error = await _json_object(request)
    if error:
        return error
    winner_id = _positive_int(body.get("winner_id"))
    loser_id = _positive_int(body.get("loser_id"))
    mode = body.get("mode", "swiss")

    if winner_id is None or loser_id is None:
        return JSONResponse({"error": "winner_id and loser_id are required"}, status_code=400)
    if winner_id == loser_id:
        return JSONResponse({"error": "Winner and loser must be different images"}, status_code=400)

    action_id = uuid.uuid4().hex
    result = await db.record_active_comparison(winner_id, loser_id, mode, action_id=action_id)
    if not result:
        return JSONResponse(
            {"error": "Images must exist in an active online catalog"},
            status_code=400,
        )

    new_winner_elo = result["winner_elo"]
    new_loser_elo = result["loser_elo"]
    _patch_pairing_cache([(winner_id, new_winner_elo, 1), (loser_id, new_loser_elo, 1)])
    _add_past_matchups([(winner_id, loser_id)])
    # Fire-and-forget: propagate Elo to similar images via embeddings.
    _schedule_pairing_propagation(
        elo_propagation.propagate_comparison(winner_id, loser_id, result["k"], action_id=action_id)
    )

    return {
        "ok": True,
        "winner_elo": round(new_winner_elo, 1),
        "loser_elo": round(new_loser_elo, 1),
        "action_id": action_id,
    }


@app.post("/api/compare/undo")
async def compare_undo():
    result = await db.undo_last_comparison()
    if result:
        _invalidate_pairing_cache(matchups=True)
        return {"ok": True, **result}
    return JSONResponse({"error": "Nothing to undo"}, status_code=400)


# --- Rankings API ---

@app.get("/api/rankings")
async def api_rankings(
    limit: int = 100, offset: int = 0, sort: str = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", q: str = "", deep: bool = False,
    request: Request = None,
):
    limit = _clamp_int(limit, 100, 1, 500)
    offset = _clamp_int(offset, 0, 0, 1_000_000)
    search = await _resolve_text_search(q, deep=deep)
    search_ids = search["id_filter"]
    search_scores = search["scores"]
    search_mode = search["search_mode"]
    text_query = search["text_query"]
    if search_mode == "metadata" and text_query and not search_ids and not file_type:
        extension_query = text_query.lower().lstrip(".")
        if extension_query in db.IMAGE_EXTENSION_SEARCH_TERMS:
            file_type = extension_query
            text_query = ""
        elif search_ids is not None:
            return {
                "images": [],
                **_visibility_counts(0, 0),
                "total_kept": 0,
                "search_mode": search_mode,
                "ai_unavailable": search["ai_unavailable"],
                "deep_requested": search.get("deep_requested", False),
                "deep_search_cached": search.get("deep_search_cached", False),
                "fallback_reason": search.get("fallback_reason", ""),
            }

    db_sort = "elo" if sort == "similarity" and not search_scores else sort
    rankings_cache_key = None
    cacheable_metadata_search = search_mode == "metadata" and not search_scores
    cacheable_embedding_search = search_mode in {"embedding", "deep_embedding"}
    cacheable_search = cacheable_metadata_search or cacheable_embedding_search
    normalized_search_query = _normalize_search_query(q) if search["active"] else ""
    if not search["active"] or cacheable_search:
        rankings_cache_key = (
            db.DB_PATH,
            _cache_root(),
            limit,
            offset,
            sort,
            db_sort,
            orientation,
            compared,
            int(min_stars or 0),
            folder,
            flag,
            date_taken,
            file_type,
            camera,
            lens,
            text_query if cacheable_metadata_search else normalized_search_query if cacheable_embedding_search else "",
            search_mode if cacheable_search else "",
            bool(search["ai_unavailable"]) if cacheable_search else False,
            bool(search.get("deep_requested")),
            bool(search.get("deep_search_cached")),
            str(search.get("fallback_reason") or ""),
        )
        cached = _rankings_response_cache.get(rankings_cache_key)
        if cached and cached["expires"] > time.monotonic():
            _schedule_result_thumbnail_memory_warm((cached.get("data") or {}).get("images") or [])
            if request is not None and cached.get("json") is not None:
                return Response(content=cached["json"], media_type="application/json")
            response = _copy_rankings_response(cached["data"])
            return response

    # Similarity sort: fetch all matches, sort in Python, then paginate
    if sort == "similarity" and search_scores:
        total_task = asyncio.create_task(
            db.count_rankings(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens, id_filter=search_ids, text_query=text_query,
            )
        )
        visible_images = await db.count_rankings(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens,
            id_filter=search_ids,
            visible_thumb_size="sm", cache_root=_cache_root(),
            text_query=text_query,
        )
        total_images = await total_task
        images = await db.get_rankings(
            limit=visible_images, offset=0, sort="elo",
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens,
            id_filter=search_ids,
            visible_thumb_size="sm", cache_root=_cache_root(),
            text_query=text_query,
        )
        all_results = []
        for img in images:
            d = dict(img)
            all_results.append(
                app_helpers.image_card(d, "sm", similarity=search_scores.get(d["id"], 0))
            )
        all_results.sort(key=lambda x: x["similarity"], reverse=(sort == "similarity"))
        page = all_results[offset:offset + limit]
        if page:
            _schedule_thumbnail_prefetch(
                [{"id": r["id"], "filepath": ""} for r in page], "sm",
                limit=min(len(page), 48),
            )
            _schedule_result_thumbnail_memory_warm(page)
        response = {
            "images": page,
            **_visibility_counts(total_images, visible_images),
            "total_kept": total_images,
            "search_mode": search_mode,
            "ai_unavailable": search["ai_unavailable"],
            "deep_requested": search.get("deep_requested", False),
            "deep_search_cached": search.get("deep_search_cached", False),
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if rankings_cache_key is not None:
            _cache_rankings_response(rankings_cache_key, response)
        return response

    unfiltered_rankings = not any(
        (
            orientation,
            compared,
            int(min_stars or 0),
            folder,
            flag,
            date_taken,
            file_type,
            camera,
            lens,
            search_ids,
            text_query,
        )
    )
    if unfiltered_rankings:
        counts_task = asyncio.create_task(db.get_visible_pairing_pool_counts("sm", _cache_root()))
    else:
        defer_empty_first_page_counts = bool(text_query) and offset == 0
        total_task = None
        visible_task = None
        if not defer_empty_first_page_counts:
            total_task = asyncio.create_task(
                db.count_rankings(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens,
                    id_filter=search_ids,
                    text_query=text_query,
                )
            )
            visible_task = asyncio.create_task(
                db.count_rankings(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens,
                    id_filter=search_ids,
                    visible_thumb_size="sm", cache_root=_cache_root(),
                    text_query=text_query,
                )
            )
    images = await db.get_rankings(
        limit=limit, offset=offset, sort=db_sort,
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
        camera=camera, lens=lens,
        id_filter=search_ids,
        visible_thumb_size="sm", cache_root=_cache_root(),
        text_query=text_query,
    )
    if unfiltered_rankings:
        counts = await counts_task
        total_images = int(counts.get("active_images") or 0)
        visible_images = int(counts.get("visible_images") or 0)
    else:
        if defer_empty_first_page_counts and not images:
            visible_images = 0
            total_images = 0
        else:
            if total_task is None:
                total_task = asyncio.create_task(
                    db.count_rankings(
                        orientation=orientation, compared=compared, min_stars=min_stars,
                        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                        camera=camera, lens=lens,
                        id_filter=search_ids,
                        text_query=text_query,
                    )
                )
            if visible_task is None:
                visible_task = asyncio.create_task(
                    db.count_rankings(
                        orientation=orientation, compared=compared, min_stars=min_stars,
                        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                        camera=camera, lens=lens,
                        id_filter=search_ids,
                        visible_thumb_size="sm", cache_root=_cache_root(),
                        text_query=text_query,
                    )
                )
            visible_images = await visible_task
            total_images = await total_task
    if images:
        _schedule_thumbnail_prefetch(
            [dict(img) for img in images],
            "sm",
            limit=min(len(images), 48),
        )
        _schedule_result_thumbnail_memory_warm(images)
    result = []
    for img in images:
        d = dict(img)
        kwargs = {}
        if search_scores:
            kwargs["similarity"] = search_scores.get(d["id"], 0)
        if sort in ("date_taken", "date_taken_asc"):
            kwargs["date_group"] = app_helpers.date_group_for_image(d)
        entry = app_helpers.image_card(d, "sm", **kwargs)
        result.append(entry)
    response = {
        "images": result,
        **_visibility_counts(total_images, visible_images),
        "total_kept": total_images,
        "search_mode": search_mode,
        "ai_unavailable": search["ai_unavailable"],
        "deep_requested": search.get("deep_requested", False),
        "deep_search_cached": search.get("deep_search_cached", False),
        "fallback_reason": search.get("fallback_reason", ""),
    }
    if rankings_cache_key is not None:
        _cache_rankings_response(rankings_cache_key, response)
    return response


@app.get("/api/date-groups")
async def api_date_groups(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "",
):
    """Return date groups with counts for the scrubber, respecting active filters."""
    groups = await db.get_date_groups(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
        camera=camera, lens=lens,
        visible_thumb_size="sm", cache_root=_cache_root(),
    )
    return {"groups": groups}


@app.get("/api/map/markers")
async def api_map_markers(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "",
):
    """Return images with GPS data for map display."""
    return await db.get_map_markers(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
        camera=camera, lens=lens,
        visible_thumb_size="sm", cache_root=_cache_root(),
    )


@app.get("/api/export")
async def export_rankings(format: str = "json", ids: str = ""):
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        images_dict = await db.get_images_by_ids(id_list)
        images = [images_dict[i] for i in id_list if i in images_dict]
    else:
        images = await db.get_rankings(limit=10000)
    image_dicts = [dict(img) for img in images]
    data = [
        {
            "rank": i + 1,
            "filename": img["filename"],
            "filepath": img["filepath"],
            "elo": round(img["elo"], 1),
            "comparisons": img["comparisons"],
            "propagated_updates": img.get("propagated_updates") or 0,
            "status": img["status"],
            "flag": img.get("flag") or "unflagged",
            "date_taken": img.get("date_taken"),
            "camera_make": img.get("camera_make"),
            "camera_model": img.get("camera_model"),
            "lens": img.get("lens"),
            "file_ext": img.get("file_ext"),
            "file_size": img.get("file_size"),
            "file_modified_at": img.get("file_modified_at"),
            "width": img.get("width"),
            "height": img.get("height"),
            "latitude": img.get("latitude"),
            "longitude": img.get("longitude"),
        }
        for i, img in enumerate(image_dicts)
    ]

    if format == "csv":
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=rankings.csv"},
        )

    return data


@app.get("/api/search")
async def api_search(q: str = "", limit: int = 50, deep: bool = False):
    """Search images by text query using embedding similarity."""
    query = _normalize_search_query(q)
    if not query:
        return {"images": [], "query": query, **_visibility_counts(0, 0)}
    limit = _clamp_int(limit, 50, 1, 500)

    async def metadata_fallback(reason: str):
        cache_key = (
            "api_search_metadata",
            db.DB_PATH,
            _cache_root(),
            limit,
            query,
            reason,
            bool(deep),
        )
        cached = _rankings_response_cache.get(cache_key)
        if cached and cached["expires"] > time.monotonic():
            return _copy_rankings_response(cached["data"])
        rows, visible_images, total_images = await asyncio.gather(
            db.get_rankings(
                limit=limit,
                offset=0,
                sort="elo",
                visible_thumb_size="sm",
                cache_root=_cache_root(),
                text_query=query,
            ),
            db.count_rankings(
                visible_thumb_size="sm",
                cache_root=_cache_root(),
                text_query=query,
            ),
            db.count_rankings(text_query=query),
        )
        result = []
        for img in rows:
            d = dict(img)
            result.append(app_helpers.image_card(d, "sm", similarity=None))
        if rows:
            _schedule_thumbnail_prefetch(
                [dict(img) for img in rows],
                "sm",
                limit=min(len(rows), 48),
            )
            _schedule_result_thumbnail_memory_warm(rows)
        response = {
            "images": result,
            "query": query,
            "search_mode": "metadata",
            "ai_unavailable": True,
            "fallback_reason": reason,
            "deep_requested": bool(deep),
            "deep_search_cached": False,
            **_visibility_counts(total_images, visible_images),
        }
        _rankings_response_cache[cache_key] = {
            "data": _copy_rankings_response(response),
            "expires": time.monotonic() + _rankings_response_cache_ttl_seconds,
        }
        return response

    await _record_deep_search_query(query)
    deep_search = await _resolve_cached_deep_search(query)
    if deep_search is not None:
        image_ids = deep_search["image_ids"]
        similarities = deep_search["similarities"]
        visible_rows, visible_images, total_images = await _visible_embedding_page(
            image_ids,
            similarities,
            limit,
            "sm",
            model_key=deep_search.get("deep_model_key"),
        )
        id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}
        result = []
        for img in visible_rows:
            img_id = int(img["id"])
            idx = id_to_idx.get(img_id)
            score = float(similarities[idx]) if idx is not None else 0.0
            result.append(app_helpers.image_card(img, "sm", similarity=score))
        _schedule_result_thumbnail_memory_warm(visible_rows)

        return {
            "images": result,
            "query": query,
            "search_mode": deep_search["search_mode"],
            "ai_unavailable": False,
            "deep_search_cached": True,
            "deep_requested": bool(deep),
            **_visibility_counts(total_images, visible_images),
        }

    if deep:
        return await metadata_fallback("deep_search_not_cached")

    try:
        import embedding_worker
        import embed_cache
    except ImportError:
        return await metadata_fallback("embeddings_unavailable")

    fast_config = settings.fast_search_embedding_config()
    text_vec = await asyncio.get_event_loop().run_in_executor(
        None,
        _encode_text_with_config,
        embedding_worker.encode_text,
        query,
        fast_config,
    )
    if text_vec is None and await embedding_worker.ensure_model_loaded_for_search():
        text_vec = await asyncio.get_event_loop().run_in_executor(
            None,
            _encode_text_with_config,
            embedding_worker.encode_text,
            query,
            fast_config,
        )
    if text_vec is None:
        return await metadata_fallback("model_loading")

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None:
        return await metadata_fallback("embeddings_not_indexed")

    similarities = matrix @ text_vec
    visible_rows, visible_images, total_images = await _visible_embedding_page(
        image_ids,
        similarities,
        limit,
        "sm",
        model_key=fast_config["model_key"],
    )

    result = []
    id_to_idx = embed_cache.get_index()
    for img in visible_rows:
        img_id = int(img["id"])
        idx = id_to_idx.get(img_id)
        score = float(similarities[idx]) if idx is not None else 0.0
        result.append(app_helpers.image_card(img, "sm", similarity=score))
    _schedule_result_thumbnail_memory_warm(visible_rows)

    return {
        "images": result,
        "query": query,
        "search_mode": "embedding",
        "ai_unavailable": False,
        "deep_requested": False,
        "deep_search_cached": False,
        **_visibility_counts(total_images, visible_images),
    }


@app.get("/api/similar/{image_id}")
async def api_similar(image_id: int, limit: int = 50):
    """Find visually similar images using embedding cosine similarity."""
    limit = max(1, min(int(limit), 500))
    try:
        import embed_cache
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None:
        return {"images": [], "source_id": image_id, **_visibility_counts(0, 0)}

    source_vec = embed_cache.get_vector(image_id)
    if source_vec is None:
        return JSONResponse({"error": "Image not embedded yet"}, status_code=404)

    similarities = matrix @ source_vec
    visible_rows, visible_images, total_images = await _visible_embedding_page(
        image_ids,
        similarities,
        limit,
        "sm",
        exclude_id=image_id,
        model_key=db.active_embedding_model_key(),
    )
    results = []
    id_to_idx = embed_cache.get_index()
    for img in visible_rows:
        img_id = int(img["id"])
        idx = id_to_idx.get(img_id)
        score = float(similarities[idx]) if idx is not None else 0.0
        results.append(app_helpers.image_card(img, "sm", similarity=score))

    return {
        "images": results,
        "source_id": image_id,
        **_visibility_counts(total_images, visible_images),
    }


@app.get("/api/duplicates")
async def api_duplicates(threshold: float = 0.95, limit: int = 100):
    """Find near-duplicate image pairs using embedding similarity."""
    if not await db.get_active_source_id_set():
        return {"pairs": [], "visible_pairs": 0, "total_pairs": 0, "hidden_pending_thumbnails": 0}
    try:
        import numpy as np
        import embed_cache
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None or len(image_ids) < 2:
        return {"pairs": [], "visible_pairs": 0, "total_pairs": 0, "hidden_pending_thumbnails": 0}

    # Process in batches to avoid allocating a full n×n matrix (~850MB for 20k images).
    # Each batch computes similarities for a chunk of rows against all columns.
    BATCH = 500
    n = len(image_ids)
    cached_sm_ids = await _cached_image_ids([int(image_id) for image_id in image_ids], "sm")
    cache_key = (
        db.DB_PATH,
        round(float(threshold), 4),
        int(limit),
        id(matrix),
        len(image_ids),
        len(cached_sm_ids),
        hash(frozenset(cached_sm_ids)),
    )
    if _duplicates_cache["key"] == cache_key and _duplicates_cache["data"] is not None:
        return copy.deepcopy(_duplicates_cache["data"])

    pairs = []
    total_pairs = 0
    hidden_pairs = 0
    for start in range(0, n, BATCH):
        end = min(start + BATCH, n)
        chunk_sims = matrix[start:end] @ matrix.T  # (BATCH, n) — manageable
        for i_local in range(end - start):
            i = start + i_local
            # Only check upper triangle (j > i)
            j_start = max(i + 1, 0)
            row = chunk_sims[i_local, j_start:]
            above = (row >= threshold).nonzero()[0]
            for offset in above:
                j = j_start + int(offset)
                id_a = int(image_ids[i])
                id_b = int(image_ids[j])
                total_pairs += 1
                if id_a in cached_sm_ids and id_b in cached_sm_ids:
                    pairs.append((id_a, id_b, float(row[int(offset)])))
                else:
                    hidden_pairs += 1
                if len(pairs) >= limit:
                    break
            if len(pairs) >= limit:
                break
        if len(pairs) >= limit:
            break

    # Fetch image details
    all_ids = list({p[0] for p in pairs} | {p[1] for p in pairs})
    images = await db.get_active_images_by_ids(all_ids) if all_ids else {}

    result = []
    for id_a, id_b, sim in pairs[:limit]:
        a, b = images.get(id_a), images.get(id_b)
        if not a or not b:
            continue
        result.append({
            "similarity": round(sim, 4),
            "a": {"id": id_a, "filename": a["filename"], "elo": round(a["elo"], 1), "thumb_url": f"/api/thumb/sm/{id_a}"},
            "b": {"id": id_b, "filename": b["filename"], "elo": round(b["elo"], 1), "thumb_url": f"/api/thumb/sm/{id_b}"},
        })

    response = {
        "pairs": result,
        "visible_pairs": len(result),
        "total_pairs": total_pairs,
        "hidden_pending_thumbnails": hidden_pairs,
    }
    _duplicates_cache["key"] = cache_key
    _duplicates_cache["data"] = copy.deepcopy(response)
    return response


_exif_cache: dict[int, dict] = {}
_EXIF_CACHE_MAX = 2000
_collections_cache = {"key": None, "data": None}
_duplicates_cache = {"key": None, "data": None}

@app.get("/api/image/{image_id}/exif")
async def api_exif(image_id: int):
    """Extract EXIF metadata from an image (cached per image)."""
    if image_id in _exif_cache:
        return _exif_cache[image_id]
    image = await db.get_image_by_id(image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    try:
        exif = photo_metadata.extract_image_metadata(image["filepath"])
    except Exception:
        exif = {}

    row = dict(image)
    for key in (
        "date_taken", "camera_make", "camera_model", "lens", "file_ext",
        "file_size", "file_modified_at", "latitude", "longitude",
    ):
        if not exif.get(key) and row.get(key) is not None:
            exif[key] = row.get(key)
    if not exif.get("dimensions") and row.get("width") and row.get("height"):
        exif["dimensions"] = f"{row['width']} x {row['height']}"
    if not exif.get("filepath"):
        exif["filepath"] = image["filepath"]

    try:
        await db.batch_update_metadata([_metadata_update_tuple(image_id, exif)])
        _invalidate_pairing_cache()
    except Exception:
        pass

    result = {"exif": exif}
    _exif_cache[image_id] = result
    if len(_exif_cache) > _EXIF_CACHE_MAX:
        # Evict oldest entries
        to_remove = list(_exif_cache.keys())[:_EXIF_CACHE_MAX // 2]
        for k in to_remove:
            del _exif_cache[k]
    return result


@app.get("/api/collections")
async def api_collections(n_clusters: int = 20):
    """Auto-group images into collections using embedding clustering."""
    n_clusters = max(2, min(int(n_clusters), 100))
    if not await db.get_active_source_id_set():
        return {"collections": []}

    try:
        import numpy as np
        import embed_cache
    except ImportError:
        return JSONResponse({"error": "Dependencies not available"}, status_code=503)

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None or len(image_ids) < n_clusters:
        return {"collections": []}

    cached_sm_ids = await _cached_image_ids([int(image_id) for image_id in image_ids], "sm")
    if not cached_sm_ids:
        return {"collections": []}

    cache_key = (int(n_clusters), len(image_ids), len(cached_sm_ids))
    if _collections_cache["key"] == cache_key and _collections_cache["data"] is not None:
        return _collections_cache["data"]

    try:
        from sklearn.cluster import KMeans, MiniBatchKMeans
    except ImportError:
        return JSONResponse({"error": "Dependencies not available"}, status_code=503)

    loop = asyncio.get_running_loop()
    if len(image_ids) > 5000:
        kmeans = MiniBatchKMeans(
            n_clusters=n_clusters,
            batch_size=4096,
            n_init=3,
            random_state=42,
        )
    else:
        kmeans = KMeans(n_clusters=n_clusters, n_init=3, random_state=42)
    labels = await loop.run_in_executor(None, kmeans.fit_predict, matrix)

    # Group images by cluster and pick a representative (closest to centroid).
    # Only representative rows need DB metadata; fetching every embedded image
    # used to dominate this endpoint after clustering was cached/warm.
    collection_drafts = []
    representative_ids = []
    for c in range(n_clusters):
        cluster_indices = np.flatnonzero(labels == c)
        if cluster_indices.size == 0:
            continue

        centroid = kmeans.cluster_centers_[c]
        cluster_vecs = matrix[cluster_indices]
        dists = np.linalg.norm(cluster_vecs - centroid, axis=1)
        rep_idx = None
        for local_idx in np.argsort(dists):
            candidate_idx = int(cluster_indices[int(local_idx)])
            candidate_id = int(image_ids[candidate_idx])
            if candidate_id in cached_sm_ids:
                rep_idx = candidate_idx
                break
        if rep_idx is None:
            continue
        rep_id = int(image_ids[rep_idx])
        representative_ids.append(rep_id)
        member_ids = [int(image_ids[int(i)]) for i in cluster_indices[:50] if int(image_ids[int(i)]) in cached_sm_ids]
        if rep_id not in member_ids:
            member_ids.insert(0, rep_id)
        collection_drafts.append((c, int(cluster_indices.size), rep_id, member_ids))

    images_data = await db.get_active_images_by_ids(representative_ids)
    collections = []
    for c, count, rep_id, member_ids in collection_drafts:
        rep_img = images_data.get(rep_id, {})
        collections.append({
            "id": c,
            "count": count,
            "representative": {
                "id": rep_id,
                "filename": rep_img.get("filename", ""),
                "thumb_url": f"/api/thumb/sm/{rep_id}",
            },
            "image_ids": member_ids,
        })

    # Sort by size descending
    collections.sort(key=lambda c: c["count"], reverse=True)
    result = {"collections": collections}
    _collections_cache["key"] = cache_key
    _collections_cache["data"] = result
    return result


_folders_cache: dict[int | None, dict] = {}
_folders_refreshing: set[int | None] = set()
_folders_cache_ttl_seconds = 300.0


def _invalidate_folders_cache():
    for cached in _folders_cache.values():
        cached["expires"] = 0


def _clear_folders_cache():
    _folders_cache.clear()
    _folders_refreshing.clear()


def _add_folder_counts(
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
        rel = os.path.relpath(directory, root)

    start = 0
    while True:
        idx = rel.find(os.sep, start)
        if idx < 0:
            if max_depth is None or rel.count(os.sep) <= max_depth:
                folder_counts[rel] = folder_counts.get(rel, 0) + count
            return
        key = rel[:idx]
        if max_depth is None or key.count(os.sep) <= max_depth:
            folder_counts[key] = folder_counts.get(key, 0) + count
        start = idx + 1


def _parent_directory(path: str) -> str:
    split_at = path.rfind(os.sep)
    return path[:split_at] if split_at >= 0 else ""


def _build_source_level_folders_payload(sources: list[tuple[int, str, int]]) -> dict | None:
    source_paths = [path for _source_id, path, _active_count in sources if path]
    if len(source_paths) < 2:
        return None
    root = os.path.commonpath(source_paths)
    folders = []
    for _source_id, source_path, active_image_count in sources:
        if not source_path:
            return None
        rel = os.path.relpath(source_path, root)
        folders.append({
            "path": rel if rel != "." else os.path.basename(source_path.rstrip(os.sep)) or ".",
            "count": int(active_image_count or 0),
            "depth": 0,
        })
    return {"folders": sorted(folders, key=lambda item: item["path"]), "root": root}


def _build_folders_payload(max_depth: int | None = None) -> dict:
    conn = sqlite3.connect(db.DB_PATH)
    try:
        sources = conn.execute(
            "SELECT id, path, active_image_count FROM catalog_sources WHERE included = 1"
        ).fetchall()
        if max_depth == 0:
            source_level = _build_source_level_folders_payload(sources)
            if source_level is not None:
                return source_level
        directory_counts = {}
        fallback_dirs = []
        for source_id, source_path, active_image_count in sources:
            if int(active_image_count or 0) <= 0:
                continue
            for (filepath,) in conn.execute(
                "SELECT filepath FROM images "
                "WHERE source_id = ? AND missing_at IS NULL",
                (source_id,),
            ):
                directory = _parent_directory(filepath or "")
                directory_counts[directory] = directory_counts.get(directory, 0) + 1
                if not source_path:
                    fallback_dirs.append(directory)
    finally:
        conn.close()

    if not directory_counts:
        return {"folders": []}

    source_paths = [path for _source_id, path, _active_count in sources if path]
    root = os.path.commonpath(source_paths) if source_paths else os.path.commonpath(fallback_dirs)

    folder_counts = {}
    for directory, count in directory_counts.items():
        _add_folder_counts(folder_counts, root, directory, count, max_depth=max_depth)

    folders = [{"path": k, "count": v, "depth": k.count(os.sep)}
               for k, v in sorted(folder_counts.items())]
    return {"folders": folders, "root": root}


@app.get("/api/folders")
async def api_folders(max_depth: int | None = None):
    """Get folder tree with image counts."""
    import time as _time
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
                    result = await asyncio.to_thread(_build_folders_payload, normalized_max_depth)
                    _folders_cache[normalized_max_depth] = {
                        "data": result,
                        "expires": _time.time() + _folders_cache_ttl_seconds,
                    }
                finally:
                    _folders_refreshing.discard(normalized_max_depth)

            asyncio.create_task(_refresh_folders())
        return cached["data"]
    counts = await db.get_catalog_image_counts()
    if int(counts.get("active_images") or 0) <= 0:
        result = {"folders": []}
        _folders_cache[normalized_max_depth] = {
            "data": result,
            "expires": _time.time() + _folders_cache_ttl_seconds,
        }
        return result

    result = await asyncio.to_thread(_build_folders_payload, normalized_max_depth)
    _folders_cache[normalized_max_depth] = {
        "data": result,
        "expires": _time.time() + _folders_cache_ttl_seconds,
    }
    return result


@app.get("/api/filter-options")
async def api_filter_options():
    """Return metadata-backed filter choices for the bottom bar."""
    return await db.get_filter_options()


@app.get("/api/stats")
async def api_stats():
    return await db.get_stats()


async def build_ai_status(model_status: dict | None = None, *, force: bool = False):
    """Embedding worker + model install status for UI surfaces."""
    model_status = model_status or ai_models.get_model_status()
    cache_key = _ai_model_status_cache_key(model_status)
    if not force:
        cached = _ai_status_response_cache.get("data")
        if (
            cached is not None
            and _ai_status_response_cache.get("key") == cache_key
            and float(_ai_status_response_cache.get("expires") or 0) > time.monotonic()
        ):
            return _copy_ai_status_response(cached)

    counts = await db.get_ai_status_counts()
    embedded = counts["embedded"]
    total_images = counts["total_images"]
    remaining = max(total_images - embedded, 0)
    fast_config = settings.fast_search_embedding_config()
    deep_config = settings.deep_search_embedding_config()
    fast_model_status = ai_models.get_model_status(fast_config)
    deep_model_status = ai_models.get_model_status(deep_config)

    def index_install_fields(status: dict) -> dict:
        install = status.get("install") or {}
        install_applies = (
            bool(install.get("model_dir"))
            and str(install.get("model_dir")) == str(status.get("model_dir"))
        )
        return {
            "installed": bool(status.get("installed")),
            "installing": bool(install.get("running")) and install_applies,
            "install_status": str(install.get("status") or "idle") if install_applies else "idle",
            "install_message": str(install.get("message") or "") if install_applies else "",
        }

    worker_status = {}
    try:
        import embedding_worker
        worker_status = embedding_worker.get_worker_status()
    except Exception:
        worker_status = {
            "state": "unavailable",
            "message": "AI worker unavailable",
            "ready": False,
            "manual_pause": False,
            "model_id": "",
            "model_dir": "",
            "last_error": "",
            "last_batch_size": 0,
            "last_batch_seconds": 0.0,
            "last_embedded_at": None,
            "session_embedded": 0,
            "session_started_at": None,
            "session_embed_seconds": 0.0,
            "session_wall_seconds": 0.0,
            "recent_images_per_min": 0.0,
            "recent_wall_images_per_min": 0.0,
            "overall_images_per_min": 0.0,
            "overall_wall_images_per_min": 0.0,
            "active_batch_size": 0,
            "target_batch_size": 0,
            "successful_batches_at_size": 0,
            "last_batch_failures": 0,
            "last_batch_stage_seconds": {},
            "last_candidate_query_seconds": 0.0,
            "last_candidate_count": 0,
            "last_candidate_window_size": 0,
            "last_ready_count": 0,
            "last_cooled_down_count": 0,
            "next_retry_at": None,
            "oom_backoffs": 0,
            "last_oom_at": None,
            "batch_growth_paused_until": None,
            "governor": resource_governor.get_background_decision(
                thumbnails.get_idle_seconds()
            ).to_dict(),
        }

    compared = int(counts.get("rated_images") or 0)

    recent_rate = float(worker_status.get("recent_images_per_min") or 0.0)
    overall_rate = float(worker_status.get("overall_images_per_min") or 0.0)
    effective_rate = recent_rate if recent_rate > 0 else overall_rate
    eta_seconds = int((remaining / effective_rate) * 60) if remaining > 0 and effective_rate > 0 else None
    progress_pct = round((embedded / total_images) * 100, 1) if total_images > 0 else 0.0
    deep_embedded = await db.count_embeddings_for_model(deep_config, online_only=True)
    deep_remaining = max(total_images - deep_embedded, 0)
    deep_progress_pct = round((deep_embedded / total_images) * 100, 1) if total_images > 0 else 0.0
    try:
        deep_cache_counts = await db.get_deep_search_cache_status(deep_config, None)
        deep_queries = await db.list_deep_search_queries(deep_config)
    except Exception:
        deep_cache_counts = {"pending_queries": 0, "embedded_queries": 0}
        deep_queries = []
    deep_worker = worker_status.get("deep_search") or {}
    embedding_indexes = {
        "fast": {
            "role": "fast",
            "label": "Daily Search",
            "description": "Fast 2B image and text embeddings for normal browsing.",
            "model_id": fast_config["model_id"],
            "model_key": fast_config["model_key"],
            "model_dir": fast_config["model_dir"],
            "dimension": int(fast_config["dimension"]),
            **index_install_fields(fast_model_status),
            "embedded": embedded,
            "total_images": total_images,
            "remaining": remaining,
            "progress_pct": progress_pct,
            "worker_state": worker_status["state"],
            "worker_message": worker_status["message"],
            "manual_pause": bool(worker_status.get("manual_pause")),
        },
        "deep": {
            "role": "deep",
            "label": "Deep Search",
            "description": "Smarter scheduled 8B image index and saved-query cache.",
            "model_id": deep_config["model_id"],
            "model_key": deep_config["model_key"],
            "model_dir": deep_config["model_dir"],
            "dimension": int(deep_config["dimension"]),
            **index_install_fields(deep_model_status),
            "embedded": deep_embedded,
            "total_images": total_images,
            "remaining": deep_remaining,
            "progress_pct": deep_progress_pct,
            "worker_state": deep_worker.get("state") or "idle",
            "worker_message": deep_worker.get("message") or "",
            "schedule": deep_worker.get("schedule") or settings.deep_search_schedule_status(settings.get_settings()),
            "pending_queries": int(deep_cache_counts.get("pending_queries") or 0),
            "embedded_queries": int(deep_cache_counts.get("embedded_queries") or 0),
            "queries": deep_queries,
        },
    }

    response = {
        "embedded": embedded,
        "total_images": total_images,
        "total_kept": total_images,
        "remaining": remaining,
        "progress_pct": progress_pct,
        "compared": compared,
        "rated_images": compared,
        "direct_comparison_rows": int(counts.get("direct_comparison_rows") or 0),
        "ranking_signal_count": int(counts.get("ranking_signal_count") or 0),
        "imported_ranking_without_history": int(counts.get("imported_ranking_without_history") or 0),
        "model_installed": model_status["installed"],
        "installing": model_status["install"]["running"],
        "install_status": model_status["install"]["status"],
        "install_message": model_status["install"]["message"],
        "model_id": model_status["model_id"],
        "model_dir": model_status["model_dir"],
        "model_key": model_status.get("model_key", ""),
        "model_dimension": int(model_status.get("dimension") or 0),
        "worker_state": worker_status["state"],
        "worker_message": worker_status["message"],
        "worker_ready": worker_status["ready"],
        "embedding_manual_pause": bool(worker_status.get("manual_pause")),
        "worker_error": worker_status["last_error"],
        "last_batch_size": worker_status.get("last_batch_size", 0),
        "last_batch_seconds": worker_status.get("last_batch_seconds", 0.0),
        "last_embedded_at": worker_status.get("last_embedded_at"),
        "session_embedded": worker_status.get("session_embedded", 0),
        "session_started_at": worker_status.get("session_started_at"),
        "session_embed_seconds": worker_status.get("session_embed_seconds", 0.0),
        "session_wall_seconds": worker_status.get("session_wall_seconds", 0.0),
        "recent_images_per_min": recent_rate,
        "recent_wall_images_per_min": float(worker_status.get("recent_wall_images_per_min") or 0.0),
        "overall_images_per_min": overall_rate,
        "overall_wall_images_per_min": float(worker_status.get("overall_wall_images_per_min") or 0.0),
        "active_batch_size": worker_status.get("active_batch_size", 0),
        "target_batch_size": worker_status.get("target_batch_size", 0),
        "successful_batches_at_size": worker_status.get("successful_batches_at_size", 0),
        "last_batch_failures": worker_status.get("last_batch_failures", 0),
        "last_batch_stage_seconds": worker_status.get("last_batch_stage_seconds") or {},
        "last_candidate_query_seconds": worker_status.get("last_candidate_query_seconds", 0.0),
        "last_candidate_count": worker_status.get("last_candidate_count", 0),
        "last_candidate_window_size": worker_status.get("last_candidate_window_size", 0),
        "last_ready_count": worker_status.get("last_ready_count", 0),
        "last_cooled_down_count": worker_status.get("last_cooled_down_count", 0),
        "next_retry_at": worker_status.get("next_retry_at"),
        "oom_backoffs": worker_status.get("oom_backoffs", 0),
        "last_oom_at": worker_status.get("last_oom_at"),
        "batch_growth_paused_until": worker_status.get("batch_growth_paused_until"),
        "deep_search": worker_status.get("deep_search") or {},
        "embedding_indexes": embedding_indexes,
        "eta_seconds": eta_seconds,
        "governor": worker_status.get("governor") or resource_governor.get_background_decision(
            thumbnails.get_idle_seconds()
        ).to_dict(),
    }
    _ai_status_response_cache["data"] = _copy_ai_status_response(response)
    _ai_status_response_cache["key"] = cache_key
    _ai_status_response_cache["expires"] = time.monotonic() + _ai_status_response_cache_ttl_seconds
    return response


@app.get("/api/ai/status")
async def ai_status():
    """Embedding worker and taste model status for the bottom bar."""
    return await build_ai_status()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
