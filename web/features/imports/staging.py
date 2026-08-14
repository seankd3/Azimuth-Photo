"""Staged-import scans, previews, and background commit jobs."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.dates import safe_datetime_fromtimestamp
import db
import scanner
import settings
import thumbnails
from thumbnails import config
from data.repositories import catalog as catalog_repository
from data.repositories import collections as collection_repository
from data.repositories import imports as import_repository
from features.catalog import routes as catalog_routes
from features.imports import card
from features.imports import film
from features.imports import taxonomy
from features.library import geodata, keywords
from features.quality import routes as quality_routes
from image_headers import read_header_dimensions


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
    film_source: bool = False
    label: str = ""
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
    roll_name: str | None = None  # film only: the folder the owner named
    phase: str = "queued"
    files_done: int = 0
    bytes_done: int = 0
    cleared_bytes: int = 0
    skipped_duplicates: int = 0
    errors: list[dict] = field(default_factory=list)
    cancel_requested: bool = False
    image_rows: list[dict] = field(default_factory=list)
    move_degraded: bool = False  # Move from inside the library behaves as Copy
    library_roots: list[str] = field(default_factory=list)  # deletion-guard fence, cached at commit

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
            "move_degraded": self.move_degraded,
        }


_scans: dict[str, Scan] = {}
_jobs: dict[str, ImportJob] = {}
_tasks: dict[str, asyncio.Task] = {}


def originals_root() -> Path:
    configured = os.environ.get("AZIMUTH_ORIGINALS_DIR") or settings.get_settings().get("import_root")
    return Path(str(configured or settings.DEFAULT_SETTINGS["import_root"])).expanduser().resolve()


async def allowed_roots() -> list[str]:
    roots = [str(originals_root())]
    roots.extend(await import_repository.catalog_source_paths(db.DB_PATH))
    roots.extend(row["path"] for row in catalog_routes.quick_browse_roots())
    return list(dict.fromkeys(catalog_repository.normalize_source_path(path) for path in roots))


ASTRO_ROOT_NAME = "astrophotography"


def _under_astro(path: str | Path) -> bool:
    """MASTER_PLAN §1.10: Astrophotography/ is out of scope for every import
    path — never read from it, never delete under it. Case-blind on purpose:
    over-fencing only skips a file, under-fencing loses one."""
    return any(str(part).lower() == ASTRO_ROOT_NAME for part in Path(path).parts)


async def library_roots() -> list[str]:
    """Roots that hold catalog data. Moving *from* one of these would relocate
    library files, not drain a card, so Move degrades to Copy under them."""
    roots = [str(originals_root())]
    roots.extend(await import_repository.catalog_source_paths(db.DB_PATH))
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
    library = await library_roots()
    root_rows = [{
        "id": f"root:{path}", "kind": "root", "label": Path(path).name or path, "path": path,
        "library": _under_roots(path, library),
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


async def start_film_scan(path: str, *, label: str) -> Scan:
    """Stage a server-created film extraction dir (already under our cache root,
    so the allowed-roots fence does not apply)."""
    scan = Scan(
        id=uuid.uuid4().hex, path=str(Path(path).resolve()), include_subfolders=True,
        card_source=False, film_source=True, label=label,
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
            relative = path.relative_to(root)
            if scanner.is_junk_file(path.name) or any(
                scanner.is_junk_directory(part) for part in relative.parts[:-1]
            ):
                continue
            if _under_astro(path):
                continue  # Astrophotography/ never stages — MASTER_PLAN §1.10
            stat = path.stat()
            metadata = geodata.extract_file_metadata(str(path)) if path.suffix.lower() not in card.VIDEO_EXTENSIONS else {}
            modified = safe_datetime_fromtimestamp(stat.st_mtime)
            taken_at = metadata.get("date_taken") or (modified.strftime("%Y-%m-%d %H:%M:%S") if modified else "")
            kind = "video" if path.suffix.lower() in card.VIDEO_EXTENSIONS else "image"
            if scan.film_source and kind != "video":
                source_kind = "film_scan"  # the user said these are film scans; provenance guessing would lie
            else:
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
    if entry["kind"] == "video":
        # Custody-only: videos are imported, stored and backed up, never decoded.
        # The client draws its own video tile — a fake still would read as a bug.
        raise ValueError("Videos are stored without a preview")
    cached = preview_path(scan, entry)
    if cached.is_file():
        os.utime(cached, None)
        return cached.read_bytes()
    cached.parent.mkdir(parents=True, exist_ok=True)
    # Deferred like every generation caller: Pillow stays off boot, and the
    # submodule is imported by name rather than hoped-for as a package
    # attribute someone else materialized. A stale third positional argument
    # also survived a load_source_image signature change here and 500'd every
    # canvas preview — nothing tested this route, so it broke silently until
    # a real lab roll hit the import canvas.
    from thumbnails import generation

    image = generation.load_source_image(
        entry["path"], THUMB_MAX_EDGE,
        jpeg_extensions=config.JPEG_EXTENSIONS,
        raw_extensions=config.RAW_EXTENSIONS,
    )
    try:
        resized = generation.resize_to_long_side(image, THUMB_MAX_EDGE)
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")  # alpha PNG/HEIC cannot encode as JPEG
        data = generation.thumbnail_jpeg_bytes(resized, "sm", 85)
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
    roll_name: str | None = None,
) -> ImportJob:
    if scan.status != "done":
        raise ValueError("Scan is not ready to import")
    if mode not in {"copy", "add", "move"}:
        raise ValueError("Import mode must be copy, add, or move")
    if scan.card_source and mode == "add":
        raise ValueError("Removable cards must be copied or moved before import")
    if scan.film_source and mode != "copy":
        raise ValueError("Film scans are always copied into the library")
    # Moving from inside the library would relocate catalog data, not drain a
    # card: silently behave as Copy and say so, never delete library originals.
    # This scan-root check is the UX signal; the per-entry guard in
    # _refuse_source_delete is the safety (an ancestor scan or a junction can
    # reach library files from a scan root that is not itself inside one).
    fence = await library_roots()
    move_degraded = mode == "move" and _under_roots(scan.path, fence)
    if category is not None and category not in taxonomy.IMPORT_CATEGORIES:
        raise ValueError("Unknown import category")
    if category and not scan.film_source:
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
    cleaned_roll = film.clean_roll_name(roll_name) if scan.film_source else None
    batch_id = await import_repository.create_import_batch(db.DB_PATH, {
        "name": cleaned_roll or scan.label or Path(scan.path).name or "Import",
        "destination_mode": mode,
        "destination_root": str(originals_root()),
        "destination_path": str(originals_root()),
        "preserve_structure": False,
        "total_files": len(selected),
    })
    job = ImportJob(
        id=uuid.uuid4().hex, scan=scan, entries=selected, mode=mode,
        clear_card=bool((clear_card or mode == "move") and scan.card_source),
        category=category, keywords=[str(path) for path in keyword_paths if str(path).strip()],
        collection_id=collection_id, batch_id=batch_id, move_degraded=move_degraded,
        library_roots=fence, roll_name=cleaned_roll,
    )
    _jobs[job.id] = job
    _tasks[job.id] = asyncio.create_task(_commit_worker(job))
    return job


async def _commit_worker(job: ImportJob) -> None:
    job.phase = "registering" if job.mode == "add" else "copying"
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
        await _reclaim_film_staging(job)
    except Exception as exc:
        job.errors.append({"message": str(exc)})
        job.phase = "failed"
        await import_repository.fail_import_batch(db.DB_PATH, job.batch_id, str(exc))


async def _reclaim_film_staging(job: ImportJob) -> None:
    """The film extraction dir is transient upload spill. Reclaim it only once
    every staged file was covered by a clean commit; anything less keeps the
    dir for the 24h stale sweep (never risk the only server-side copy)."""
    scan = job.scan
    if not (scan.film_source and job.phase == "complete" and not job.errors):
        return
    if len(job.entries) != len(scan.entries):
        return
    if film.is_staging_path(scan.path):
        await asyncio.to_thread(shutil.rmtree, scan.path, True)
    _scans.pop(scan.id, None)


async def _import_entry(job: ImportJob, entry: dict) -> None:
    try:
        # Resolved, not lexical: a junction must not smuggle astro files in.
        if _under_astro(await asyncio.to_thread(os.path.realpath, entry["path"])):
            raise ValueError("Astrophotography/ is out of scope for import")
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
    if job.scan.film_source:
        return _film_destination_directory(job, entry)
    taken = str(entry.get("taken_at") or "")[:10]
    try:
        parsed = datetime.strptime(taken, "%Y-%m-%d")
    except ValueError:
        parsed = safe_datetime_fromtimestamp(entry.get("mtime")) or datetime.now()
    library_root = originals_root()
    # If import_root was pointed at the Raws tree itself, climb to the library root
    # so destinations stay siblings (Snapshots must not nest under Raws).
    if library_root.name in taxonomy.RAWS_ROOT_NAMES:
        library_root = library_root.parent
    return taxonomy.destination_directory(
        library_root,
        filename=entry["name"],
        year=parsed.strftime("%Y"),
        day=parsed.strftime("%Y-%m-%d"),
        source_kind=_source_kind_for_entry(job, entry),
    )


def _film_destination_directory(job: ImportJob, entry: dict) -> Path:
    """Scan dates are not shoot dates: one archive/batch lands in one roll
    folder, filed by delivery date — ``Film Scans/<YYYY>/<YYYY-MM-DD>/<roll>/``
    (the owner's convention, 08-14). The date is the lab's own stamp in the
    archive name (``…_2026-08-06_2203``) and falls back to the import date;
    the roll folder is whatever the owner typed at import, or the archive
    name when they typed nothing.
    """
    import re

    library_root = originals_root()
    if library_root.name in taxonomy.RAWS_ROOT_NAMES:
        library_root = library_root.parent
    parts = str(entry.get("rel_path") or "").split("/")
    derived = parts[0] if len(parts) > 1 else (job.scan.label or "Film scans")
    date_match = re.search(r"(?:19|20)\d{2}-\d{2}-\d{2}", derived)
    day = date_match.group(0) if date_match else time.strftime("%Y-%m-%d")
    roll = job.roll_name or derived
    return taxonomy.resolve_destination_dir(library_root, taxonomy.DEST_FILM) / day[:4] / day / roll


def _catalog_source_root_for_destination(destination: str | Path) -> str:
    path = Path(destination)
    library_root = originals_root()
    if library_root.name in taxonomy.RAWS_ROOT_NAMES:
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
    known_duplicate = None if duplicate_destination else await _known_exact_duplicate(
        result["content_hash"], result["full_hash"]
    )
    if duplicate_destination or known_duplicate:
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
        await _clear_source_after_verified_duplicate(
            job, entry, duplicate_destination or known_duplicate
        )
        return
    destination = result["destination"]
    image_id = await _register_file(
        destination,
        entry,
        result["content_hash"],
        source_root=_catalog_source_root_for_destination(destination),
    )
    job.image_rows.append({"image_id": image_id, "filepath": destination, "original_name": entry["name"]})
    if _removes_source(job):
        await _remove_verified_source(job, entry, destination, int(result["bytes"]))


async def _known_exact_duplicate(content_hash: str, full_hash: str) -> str | None:
    """Return the catalog's verified copy so deletion can guard against it."""
    candidates = await import_repository.image_paths_by_content_hash(db.DB_PATH, content_hash)
    for candidate in candidates:
        try:
            if await asyncio.to_thread(card.compute_full_hash, candidate) == full_hash:
                return candidate
        except OSError:
            continue
    return None


def _removes_source(job: ImportJob) -> bool:
    return job.clear_card or (job.mode == "move" and not job.move_degraded)


def _refuse_source_delete(source: str, landed: str | None, library: list[str]) -> str | None:
    """The per-entry deletion guard — the safety behind Move. Returns the
    reason the source must be kept, or None when deletion is safe. Judged on
    the resolved path (junctions, symlinks, subst): the guard is about where
    the bytes really live, not what the scan called them. The scan-root
    degrade check cannot cover an ancestor scan that walks *into* the library,
    or a link that points there."""
    try:
        resolved = os.path.realpath(source)
        if not os.path.exists(resolved):
            return "source could not be resolved"
    except OSError:
        return "source could not be resolved"
    if _under_astro(resolved):
        return "inside Astrophotography/"
    if _under_roots(resolved, library):
        return "inside a library root"
    if landed:
        try:
            if os.path.samefile(resolved, landed):
                return "source and library copy are the same file"
        except OSError:
            return "library copy could not be compared with the source"
    return None


async def _guarded_source_delete(job: ImportJob, entry: dict, landed: str | None) -> None:
    reason = await asyncio.to_thread(
        _refuse_source_delete, entry["path"], landed, job.library_roots
    )
    if reason:
        # Move reached library bytes: behave as Copy for this file — keep the
        # source and surface the degrade. A kept original is never an error.
        job.move_degraded = True
        return
    await asyncio.to_thread(card.remove_verified_card_file, entry["path"])
    job.cleared_bytes += int(entry["size"])


async def _remove_verified_source(job: ImportJob, entry: dict, destination: str, verified_bytes: int) -> None:
    """The deletion gate: the source only goes once the *final* landed file —
    hash-verified by copy_verified, registered in the catalog — still holds
    every byte on disk. Any doubt keeps the source and reports the file."""
    landed = await asyncio.to_thread(os.path.getsize, destination)
    if landed != verified_bytes:
        raise RuntimeError(
            f"Landed copy is {landed} bytes, expected {verified_bytes} — source kept"
        )
    await _guarded_source_delete(job, entry, destination)


async def _clear_source_after_verified_duplicate(job: ImportJob, entry: dict, duplicate: str | None) -> None:
    # The catalog provably owns a full-hash-identical copy; draining the
    # source is safe — unless the "source" IS that copy (ancestor scan,
    # junction), which the guard refuses.
    if _removes_source(job):
        await _guarded_source_delete(job, entry, duplicate)


async def _register_existing(job: ImportJob, entry: dict) -> None:
    content_hash, _full_hash, _size = await asyncio.to_thread(card.content_hash_from_stream, Path(entry["path"]))
    image_id = await _register_file(entry["path"], entry, content_hash, source_root=job.scan.path)
    job.image_rows.append({"image_id": image_id, "filepath": entry["path"], "original_name": entry["name"]})


async def _register_file(path: str, entry: dict, content_hash: str, *, source_root: str | None = None) -> int:
    root = source_root or str(originals_root())
    source = await db.add_or_restore_source(root)
    stat = await asyncio.to_thread(os.stat, path)
    dimensions = await asyncio.to_thread(
        read_header_dimensions, path, budget_seconds=scanner.SCAN_HEADER_BUDGET_SECONDS
    )
    if dimensions is None:
        orientation = None
        aspect_ratio = None
    else:
        width, height = dimensions
        orientation = "landscape" if width >= height else "portrait"
        aspect_ratio = round(width / height, 4)
    await db.insert_images_batch([(Path(path).name, path, Path(path).suffix.lower(), int(stat.st_size), float(stat.st_mtime), orientation, aspect_ratio)], source_id=int(source["id"]))
    image_id = await import_repository.set_image_content_hash(db.DB_PATH, path, content_hash)
    if image_id is None:
        raise RuntimeError("Verified import was not registered")
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
