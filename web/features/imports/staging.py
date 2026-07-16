"""Staged-import scans, previews, and background commit jobs."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.dates import safe_datetime_fromtimestamp
import db
import scanner
import settings
import thumbnails
from data.repositories import catalog as catalog_repository
from data.repositories import collections as collection_repository
from data.repositories import imports as import_repository
from features.catalog import routes as catalog_routes
from features.imports import card
from features.imports import taxonomy
from features.library import geodata, keywords
from features.quality import routes as quality_routes
from features.sync import satellite


SUPPORTED_EXTENSIONS = set(scanner.SUPPORTED_EXTENSIONS) | card.VIDEO_EXTENSIONS
THUMB_MAX_EDGE = 640
THUMB_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
THUMB_CACHE_MAX_ENTRIES = 20_000


@dataclass
class Scan:
    id: str
    path: str
    include_subfolders: bool
    card_source: bool
    status: str = "scanning"
    error: str = ""
    entries: list[dict] = field(default_factory=list)


@dataclass
class ImportJob:
    id: str
    scan: Scan
    entries: list[dict]
    mode: str
    clear_card: bool
    category: str | None
    keywords: list[str]
    collection_id: int | None
    batch_id: int
    phase: str = "queued"
    files_done: int = 0
    bytes_done: int = 0
    cleared_bytes: int = 0
    skipped_duplicates: int = 0
    errors: list[dict] = field(default_factory=list)
    cancel_requested: bool = False
    image_rows: list[dict] = field(default_factory=list)

    def status(self) -> dict:
        total_bytes = sum(int(entry["size"]) for entry in self.entries)
        elapsed = max(time.monotonic() - getattr(self, "started_at", time.monotonic()), 0.001)
        remaining = max(0, total_bytes - self.cleared_bytes)
        rate = self.cleared_bytes / elapsed
        return {
            "phase": self.phase,
            "files_done": self.files_done,
            "files_total": len(self.entries),
            "bytes_done": self.bytes_done,
            "bytes_total": total_bytes,
            "card_free_eta_seconds": int(remaining / rate) if self.clear_card and rate > 0 else None,
            "cleared_bytes": self.cleared_bytes,
            "skipped_duplicates": self.skipped_duplicates,
            "errors": list(self.errors),
            "batch_id": self.batch_id,
        }


_scans: dict[str, Scan] = {}
_jobs: dict[str, ImportJob] = {}
_tasks: dict[str, asyncio.Task] = {}


def originals_root() -> Path:
    configured = os.environ.get("PHOTOARCHIVE_ORIGINALS_DIR") or settings.get_settings().get("import_root")
    return Path(str(configured or settings.DEFAULT_SETTINGS["import_root"])).expanduser().resolve()


async def allowed_roots() -> list[str]:
    roots = [str(originals_root())]
    roots.extend(await import_repository.catalog_source_paths(db.DB_PATH))
    roots.extend(row["path"] for row in catalog_routes.quick_browse_roots())
    return list(dict.fromkeys(catalog_repository.normalize_source_path(path) for path in roots))


def _under_roots(path: str, roots: list[str]) -> bool:
    candidate = Path(path).expanduser().resolve()
    return any(_is_under(candidate, Path(root)) for root in roots)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def scan_cards() -> list[dict]:
    return card.polled_cards(supported=SUPPORTED_EXTENSIONS)


async def sources() -> list[dict]:
    cards = await asyncio.to_thread(scan_cards)
    roots = await allowed_roots()
    root_rows = [{
        "id": f"root:{path}", "kind": "root", "label": Path(path).name or path, "path": path,
    } for path in roots if Path(path).is_dir()]
    return cards + root_rows


async def browse(path: str) -> list[dict]:
    cards = await asyncio.to_thread(scan_cards)
    roots = await allowed_roots()
    roots.extend(row["path"] for row in cards)
    candidate = catalog_repository.normalize_source_path(path)
    if not _under_roots(candidate, roots) or not os.path.isdir(candidate):
        raise ValueError("Folder is outside staged-import sources")
    return await asyncio.to_thread(_browse_dirs, candidate)


def _browse_dirs(path: str) -> list[dict]:
    dirs = []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
                file_count = sum(1 for child in os.scandir(entry.path) if child.is_file())
                dirs.append({"name": entry.name, "path": str(Path(entry.path).resolve()), "file_count": file_count})
            except OSError:
                continue
    return sorted(dirs, key=lambda row: row["name"].lower())


async def start_scan(path: str, include_subfolders: bool) -> Scan:
    cards = await asyncio.to_thread(scan_cards)
    roots = await allowed_roots()
    roots.extend(row["path"] for row in cards)
    normalized = catalog_repository.normalize_source_path(path)
    if not _under_roots(normalized, roots) or not os.path.isdir(normalized):
        raise ValueError("Folder is outside staged-import sources")
    scan = Scan(
        id=uuid.uuid4().hex,
        path=normalized,
        include_subfolders=bool(include_subfolders),
        card_source=card.is_card_path(normalized, cards),
    )
    _scans[scan.id] = scan
    _tasks[scan.id] = asyncio.create_task(_scan_worker(scan))
    return scan


def scan_page(scan: Scan, offset: int) -> dict:
    start = max(0, int(offset))
    entries = [
        {key: value for key, value in entry.items() if key != "path"}
        for entry in scan.entries[start:start + 500]
    ]
    return {
        "status": scan.status,
        "total_seen": len(scan.entries),
        "entries": entries,
        **({"error": scan.error} if scan.error else {}),
    }


async def _scan_worker(scan: Scan) -> None:
    try:
        await asyncio.to_thread(_enumerate_scan, scan)
        suspects = await import_repository.suspect_catalog_paths(
            db.DB_PATH, [(entry["name"], entry["size"]) for entry in scan.entries]
        )
        for entry in scan.entries:
            if (entry["name"], entry["size"]) in suspects:
                entry["suspect"] = True
                entry["suspect_reason"] = "matching filename and size"
        scan.status = "done"
    except Exception as exc:
        scan.error = str(exc)
        scan.status = "error"


def _remembered_category(path: str) -> str | None:
    memory = settings.get_settings().get("import_category_memory") or {}
    category = str(memory.get(str(path)) or "")
    return category if category in taxonomy.IMPORT_CATEGORIES else None


def _enumerate_scan(scan: Scan) -> None:
    root = Path(scan.path)
    remembered = _remembered_category(scan.path)
    remembered_kind = taxonomy.KIND_BY_CATEGORY.get(remembered) if remembered else None
    paths = root.rglob("*") if scan.include_subfolders else root.glob("*")
    for path in paths:
        try:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            stat = path.stat()
            metadata = geodata.extract_file_metadata(str(path)) if path.suffix.lower() not in card.VIDEO_EXTENSIONS else {}
            modified = safe_datetime_fromtimestamp(stat.st_mtime)
            taken_at = metadata.get("date_taken") or (modified.strftime("%Y-%m-%d %H:%M:%S") if modified else "")
            kind = "video" if path.suffix.lower() in card.VIDEO_EXTENSIONS else "image"
            source_kind = remembered_kind if (remembered_kind and kind != "video") else taxonomy.classify_source_kind(
                filename=path.name,
                path=str(path),
                rel_path=str(path.relative_to(root)).replace(os.sep, "/"),
                card_source=bool(scan.card_source),
                kind=kind,
                camera_make=str(metadata.get("camera_make") or ""),
                software=str(metadata.get("software") or ""),
            )
            scan.entries.append({
                "key": uuid.uuid4().hex,
                "name": path.name,
                "rel_path": str(path.relative_to(root)).replace(os.sep, "/"),
                "path": str(path),
                "size": int(stat.st_size),
                "mtime": float(stat.st_mtime),
                "taken_at": taken_at,
                "kind": kind,
                "source_kind": source_kind,
                "category": taxonomy.CATEGORY_BY_KIND.get(source_kind, "raw"),
                "suspect": False,
                "suspect_reason": "",
            })
        except OSError:
            continue


def scan_for_id(scan_id: str) -> Scan | None:
    return _scans.get(scan_id)


def entry_for_key(scan: Scan, key: str) -> dict | None:
    return next((entry for entry in scan.entries if entry["key"] == key), None)


def preview_path(scan: Scan, entry: dict) -> Path:
    cache = Path(thumbnails.SSD_CACHE_DIR) / "import-staging"
    return cache / scan.id / f"{entry['key']}.jpg"


def thumbnail_bytes(scan: Scan, entry: dict) -> bytes:
    cached = preview_path(scan, entry)
    if cached.is_file():
        os.utime(cached, None)
        return cached.read_bytes()
    cached.parent.mkdir(parents=True, exist_ok=True)
    if entry["kind"] == "video":
        from PIL import Image
        import io
        image = Image.new("RGB", (320, 180), (45, 45, 45))
    else:
        image = thumbnails.generation.load_source_image(
            entry["path"], THUMB_MAX_EDGE, True,
            jpeg_extensions=thumbnails.JPEG_EXTENSIONS,
            raw_extensions=thumbnails.RAW_EXTENSIONS,
        )
    try:
        resized = thumbnails.generation.resize_to_long_side(image, THUMB_MAX_EDGE)
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")  # alpha PNG/HEIC cannot encode as JPEG
        data = thumbnails.generation.thumbnail_jpeg_bytes(resized, "sm", 85)
        resized.close()
    finally:
        image.close()
    temporary = cached.with_suffix(".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, cached)
    _trim_preview_cache(cached.parents[1])
    return data


def _trim_preview_cache(root: Path) -> None:
    files = [path for path in root.rglob("*.jpg") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    if len(files) <= THUMB_CACHE_MAX_ENTRIES and total <= THUMB_CACHE_MAX_BYTES:
        return
    remaining = len(files)
    for path in sorted(files, key=lambda candidate: candidate.stat().st_atime):
        if remaining <= THUMB_CACHE_MAX_ENTRIES and total <= THUMB_CACHE_MAX_BYTES:
            break
        try:
            total -= path.stat().st_size
            path.unlink()
            remaining -= 1
        except OSError:
            continue


async def start_commit(
    scan: Scan,
    *,
    keys: list[str] | str,
    mode: str,
    skip_suspects: bool,
    clear_card: bool,
    keyword_paths: list[str],
    collection_id: int | None,
    category: str | None = None,
) -> ImportJob:
    if scan.status != "done":
        raise ValueError("Scan is not ready to import")
    if mode not in {"copy", "add"}:
        raise ValueError("Import mode must be copy or add")
    if scan.card_source and mode != "copy":
        raise ValueError("Removable cards must be copied before import")
    if category is not None and category not in taxonomy.IMPORT_CATEGORIES:
        raise ValueError("Unknown import category")
    if category:
        # The correction is remembered: this source classifies itself from now on.
        current = settings.get_settings()
        memory = dict(current.get("import_category_memory") or {})
        memory[str(scan.path)] = category
        settings.save_settings({**current, "import_category_memory": memory})
    if keys == "all_checked_default":
        selected = [entry for entry in scan.entries if not (skip_suspects and entry["suspect"])]
    elif isinstance(keys, list):
        requested = {str(key) for key in keys}
        selected = [entry for entry in scan.entries if entry["key"] in requested]
    else:
        raise ValueError("keys must be a list or all_checked_default")
    batch_id = await import_repository.create_import_batch(db.DB_PATH, {
        "name": Path(scan.path).name or "Import",
        "destination_mode": mode,
        "destination_root": str(originals_root()),
        "destination_path": str(originals_root()),
        "preserve_structure": False,
        "total_files": len(selected),
    })
    job = ImportJob(
        id=uuid.uuid4().hex, scan=scan, entries=selected, mode=mode,
        clear_card=bool(clear_card and scan.card_source),
        category=category, keywords=[str(path) for path in keyword_paths if str(path).strip()],
        collection_id=collection_id, batch_id=batch_id,
    )
    _jobs[job.id] = job
    _tasks[job.id] = asyncio.create_task(_commit_worker(job))
    return job


async def _commit_worker(job: ImportJob) -> None:
    job.phase = "copying" if job.mode == "copy" else "registering"
    job.started_at = time.monotonic()
    try:
        for entry in job.entries:
            if job.cancel_requested:
                break
            await _import_entry(job, entry)
            if job.cancel_requested:
                break
        else:
            job.phase = "applying"
            await _apply_during_import(job)
            job.phase = "complete"
        if job.cancel_requested:
            job.phase = "applying"
            await _apply_during_import(job)
            job.phase = "cancelled"
        finish_batch = (
            import_repository.cancel_import_batch
            if job.phase == "cancelled"
            else import_repository.complete_import_batch
        )
        await finish_batch(
            db.DB_PATH, job.batch_id,
            source_id=None,
            image_rows=job.image_rows,
            imported_files=len(job.image_rows),
            skipped_files=job.skipped_duplicates,
            collision_count=max(0, len(job.image_rows) - len({row["original_name"] for row in job.image_rows})),
        )
        if job.image_rows:
            await quality_routes.scan_image_ids([int(row["image_id"]) for row in job.image_rows])
        catalog_routes.invalidate_folders_cache()
    except Exception as exc:
        job.errors.append({"message": str(exc)})
        job.phase = "failed"
        await import_repository.fail_import_batch(db.DB_PATH, job.batch_id, str(exc))


async def _import_entry(job: ImportJob, entry: dict) -> None:
    try:
        if job.mode == "add":
            await _register_existing(job, entry)
        else:
            await _copy_and_register(job, entry)
    except Exception as exc:
        job.errors.append({"name": entry["name"], "message": str(exc)})
    finally:
        job.files_done += 1
        job.bytes_done += int(entry["size"])


def _source_kind_for_entry(job: ImportJob, entry: dict) -> taxonomy.SourceKind:
    if job.category and str(entry.get("kind") or "image") != "video":
        return taxonomy.KIND_BY_CATEGORY.get(job.category, "unknown")
    stored = str(entry.get("source_kind") or "")
    if stored:
        return stored
    return taxonomy.infer_source_kind(
        filename=entry["name"],
        path=entry.get("path", ""),
        rel_path=entry.get("rel_path", ""),
        card_source=bool(job.scan.card_source),
        kind=str(entry.get("kind") or "image"),
    )


def _destination_directory(job: ImportJob, entry: dict) -> Path:
    taken = str(entry.get("taken_at") or "")[:10]
    try:
        parsed = datetime.strptime(taken, "%Y-%m-%d")
    except ValueError:
        parsed = safe_datetime_fromtimestamp(entry.get("mtime")) or datetime.now()
    library_root = originals_root()
    # If import_root was pointed at the RAWS tree itself, climb to the library root
    # so destinations stay siblings (Personal Photos must not nest under RAWS).
    if library_root.name == taxonomy.DEST_RAWS:
        library_root = library_root.parent
    return taxonomy.destination_directory(
        library_root,
        filename=entry["name"],
        year=parsed.strftime("%Y"),
        day=parsed.strftime("%Y-%m-%d"),
        source_kind=_source_kind_for_entry(job, entry),
    )


def _catalog_source_root_for_destination(destination: str | Path) -> str:
    path = Path(destination)
    library_root = originals_root()
    if library_root.name == taxonomy.DEST_RAWS:
        library_root = library_root.parent
    try:
        top = path.relative_to(library_root).parts[0]
    except (ValueError, IndexError):
        return str(library_root)
    if top in taxonomy.DESTINATION_SET:
        return str(taxonomy.destination_source_root(library_root, top))
    return str(library_root)


async def _copy_and_register(job: ImportJob, entry: dict) -> None:
    source = Path(entry["path"])
    result = await asyncio.to_thread(
        card.copy_verified, str(source), str(_destination_directory(job, entry))
    )
    duplicate_destination = result.get("duplicate_destination")
    if duplicate_destination or await _known_exact_duplicate(result["content_hash"], result["full_hash"]):
        if result.get("destination"):
            await asyncio.to_thread(Path(result["destination"]).unlink)
        if duplicate_destination:
            # A crashed earlier import can leave a verified copy at the destination
            # that never reached the catalog; register it (idempotent) so the card
            # original is only cleared once the catalog owns a copy.
            await _register_file(
                duplicate_destination,
                entry,
                result["content_hash"],
                source_root=_catalog_source_root_for_destination(duplicate_destination),
            )
        job.skipped_duplicates += 1
        await _clear_card_after_verified_duplicate(job, entry)
        return
    destination = result["destination"]
    image_id = await _register_file(
        destination,
        entry,
        result["content_hash"],
        source_root=_catalog_source_root_for_destination(destination),
    )
    job.image_rows.append({"image_id": image_id, "filepath": destination, "original_name": entry["name"]})
    if job.clear_card:
        await asyncio.to_thread(card.remove_verified_card_file, entry["path"])
        job.cleared_bytes += int(entry["size"])


async def _known_exact_duplicate(content_hash: str, full_hash: str) -> bool:
    candidates = await import_repository.image_paths_by_content_hash(db.DB_PATH, content_hash)
    for candidate in candidates:
        try:
            if await asyncio.to_thread(card.compute_full_hash, candidate) == full_hash:
                return True
        except OSError:
            continue
    return False


async def _clear_card_after_verified_duplicate(job: ImportJob, entry: dict) -> None:
    if job.clear_card:
        await asyncio.to_thread(card.remove_verified_card_file, entry["path"])
        job.cleared_bytes += int(entry["size"])


async def _register_existing(job: ImportJob, entry: dict) -> None:
    content_hash, _full_hash, _size = await asyncio.to_thread(card.content_hash_from_stream, Path(entry["path"]))
    image_id = await _register_file(entry["path"], entry, content_hash, source_root=job.scan.path)
    job.image_rows.append({"image_id": image_id, "filepath": entry["path"], "original_name": entry["name"]})


async def _register_file(path: str, entry: dict, content_hash: str, *, source_root: str | None = None) -> int:
    root = source_root or str(originals_root())
    source = await db.add_or_restore_source(root)
    stat = await asyncio.to_thread(os.stat, path)
    await db.insert_images_batch([(Path(path).name, path, Path(path).suffix.lower(), int(stat.st_size), float(stat.st_mtime))], source_id=int(source["id"]))
    image_id = await import_repository.set_image_content_hash(db.DB_PATH, path, content_hash)
    if image_id is None:
        raise RuntimeError("Verified import was not registered")
    await satellite.mark_image_dirty(image_id, db_path=db.DB_PATH)
    return image_id


async def _apply_during_import(job: ImportJob) -> None:
    ids = [int(row["image_id"]) for row in job.image_rows]
    if not ids:
        return
    for path in job.keywords:
        keyword = await keywords.resolve_keyword_path(path)
        if keyword:
            await keywords.assign_keyword(ids, int(keyword["id"]), origin="import")
    if job.collection_id is not None:
        await collection_repository.add_images(db.DB_PATH, int(job.collection_id), ids)


def job_for_id(job_id: str) -> ImportJob | None:
    return _jobs.get(job_id)


def request_cancel(job: ImportJob) -> None:
    job.cancel_requested = True
