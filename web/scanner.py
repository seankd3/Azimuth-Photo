import os
import asyncio
import logging
from collections.abc import Awaitable, Callable

from core.source_files import inspect_source_file
from data.repositories.catalog import StorageUnavailableDuringScan, SuspiciousEmptyScan
from image_headers import HEADER_GEOMETRY_EXTENSIONS, read_header_dimensions

SUPPORTED_EXTENSIONS = HEADER_GEOMETRY_EXTENSIONS - {".bmp", ".gif"}
SCAN_HEADER_BUDGET_SECONDS = 0.005

MarkSourceScanStarted = Callable[[int], Awaitable[None]]
InsertImagesBatch = Callable[..., Awaitable[None]]
MarkSourceScanFinished = Callable[..., Awaitable[None]]
_mark_source_scan_started: MarkSourceScanStarted | None = None
_insert_images_batch: InsertImagesBatch | None = None
_mark_source_scan_finished: MarkSourceScanFinished | None = None
log = logging.getLogger(__name__)

# Global scan state
scan_state = {
    "scanning": False,
    "total_found": 0,
    "total_inserted": 0,
    "folder": None,
    "source_id": None,
    "done": False,
    "error": "",
    "warning": "",
    "recoverable": False,
    "action": "",
}


def configure(
    *,
    mark_source_scan_started: MarkSourceScanStarted | None = None,
    insert_images_batch: InsertImagesBatch | None = None,
    mark_source_scan_finished: MarkSourceScanFinished | None = None,
) -> None:
    global _mark_source_scan_started, _insert_images_batch, _mark_source_scan_finished
    if mark_source_scan_started is not None:
        _mark_source_scan_started = mark_source_scan_started
    if insert_images_batch is not None:
        _insert_images_batch = insert_images_batch
    if mark_source_scan_finished is not None:
        _mark_source_scan_finished = mark_source_scan_finished


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"scanner is missing configured dependency: {name}")
    return provider


# Unambiguous derivative/app-data directories that must never enter the library.
JUNK_DIRECTORY_NAMES = {
    "previewcache",
    "__macosx",
    "luminar neo catalog",
    "lightroom catalog",
    ".thumbnails",
    ".lrt",
}
JUNK_DIRECTORY_SUFFIXES = (".lrdata", ".lrcat-data")


def is_junk_directory(name: str) -> bool:
    normalized = name.lower()
    return normalized in JUNK_DIRECTORY_NAMES or normalized.endswith(JUNK_DIRECTORY_SUFFIXES)


def is_junk_file(name: str) -> bool:
    return name.startswith("._")  # AppleDouble sidecars


class ScanInterrupted(RuntimeError):
    """The source could not be fully read, so catalog availability must not change."""


def _interrupted_message() -> str:
    return "We couldn't finish checking this folder. Your library is unchanged."


def walk_images(folder: str, *, excluded_directory_paths: list[str] | None = None):
    """Yield image rows with cheap filesystem metadata."""
    def onerror(_error: OSError) -> None:
        raise ScanInterrupted from _error

    for root, _dirs, files in os.walk(folder, onerror=onerror):
        included_dirs = []
        for directory in _dirs:
            if is_junk_directory(directory):
                if excluded_directory_paths is not None:
                    excluded_directory_paths.append(os.path.join(root, directory))
            else:
                included_dirs.append(directory)
        _dirs[:] = included_dirs
        for f in files:
            if is_junk_file(f):
                continue
            file_ext = os.path.splitext(f)[1].lower()
            if file_ext in SUPPORTED_EXTENSIONS:
                filepath = os.path.join(root, f)
                state, file_stat = inspect_source_file(filepath, folder)
                if state == "unavailable":
                    raise ScanInterrupted
                if state not in {"available", "empty"} or file_stat is None:
                    continue
                file_size = int(file_stat.st_size)
                file_modified_at = float(file_stat.st_mtime)
                dimensions = read_header_dimensions(
                    filepath,
                    budget_seconds=SCAN_HEADER_BUDGET_SECONDS,
                )
                if dimensions is None:
                    orientation = None
                    aspect_ratio = None
                else:
                    width, height = dimensions
                    orientation = "landscape" if width >= height else "portrait"
                    aspect_ratio = round(width / height, 4)
                yield (
                    f,
                    filepath,
                    file_ext,
                    file_size,
                    file_modified_at,
                    orientation,
                    aspect_ratio,
                )


def try_begin_scan() -> bool:
    """Synchronously claim the scanner before any await.

    Callers that check scan_state and then await before create_task leave a
    window where a second request also passes the check; claiming the flag
    synchronously closes it. Release with release_scan_claim() if starting
    the scan task fails after a successful claim.
    """
    if scan_state["scanning"]:
        return False
    scan_state["scanning"] = True
    return True


def release_scan_claim() -> None:
    scan_state["scanning"] = False


async def scan_folder(folder: str, source_id: int | None = None, on_batch=None):
    """Scan a folder for images and insert them into the database in batches."""
    scan_state["scanning"] = True
    scan_state["total_found"] = 0
    scan_state["total_inserted"] = 0
    scan_state["folder"] = folder
    scan_state["source_id"] = source_id
    scan_state["done"] = False
    scan_state["error"] = ""
    scan_state["warning"] = ""
    scan_state["recoverable"] = False
    scan_state["action"] = ""

    batch = []
    batch_size = 100
    seen_filepaths = []
    excluded_directory_paths = []

    try:
        if source_id is not None:
            await _configured(_mark_source_scan_started, "mark_source_scan_started")(source_id)

        for row in walk_images(folder, excluded_directory_paths=excluded_directory_paths):
            batch.append(row)
            seen_filepaths.append(row[1])
            scan_state["total_found"] += 1

            if len(batch) >= batch_size:
                await _configured(_insert_images_batch, "insert_images_batch")(batch, source_id=source_id)
                scan_state["total_inserted"] += len(batch)
                if on_batch:
                    await on_batch(scan_state["total_inserted"])
                batch = []
                # Yield control so other tasks can run
                await asyncio.sleep(0)

        if batch:
            await _configured(_insert_images_batch, "insert_images_batch")(batch, source_id=source_id)
            scan_state["total_inserted"] += len(batch)
            if on_batch:
                await on_batch(scan_state["total_inserted"])

        if source_id is not None:
            await _configured(_mark_source_scan_finished, "mark_source_scan_finished")(
                source_id,
                seen_filepaths=seen_filepaths,
                excluded_directory_paths=excluded_directory_paths,
            )
    except SuspiciousEmptyScan as exc:
        scan_state["warning"] = str(exc)
        log.warning(
            "worker=catalog_scan source_id=%s folder=%r warning: %s",
            source_id,
            folder,
            exc,
        )
    except StorageUnavailableDuringScan as exc:
        scan_state["error"] = str(exc)
        scan_state["recoverable"] = True
        scan_state["action"] = "Try again"
        log.warning(
            "worker=catalog_scan source_id=%s folder=%r halted: %s",
            source_id,
            folder,
            exc,
        )
    except asyncio.CancelledError:
        scan_state["error"] = _interrupted_message()
        scan_state["recoverable"] = True
        scan_state["action"] = "Try again"
        log.info("worker=catalog_scan source_id=%s folder=%r interrupted", source_id, folder)
        raise
    except Exception as exc:
        if source_id is not None and not os.path.isdir(folder):
            try:
                await _configured(_mark_source_scan_finished, "mark_source_scan_finished")(source_id)
            except Exception:
                pass
        scan_state["error"] = _interrupted_message()
        scan_state["recoverable"] = True
        scan_state["action"] = "Try again"
        log.warning(
            "worker=catalog_scan source_id=%s folder=%r interrupted: %s",
            source_id,
            folder,
            exc,
        )
    finally:
        scan_state["scanning"] = False
        scan_state["done"] = True
