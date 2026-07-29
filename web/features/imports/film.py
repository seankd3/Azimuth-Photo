"""Film-scan intake — extract uploaded archives into a staging dir for the canvas.

Film scans arrive as lab ZIPs (or loose TIFF/JPEG frames), not camera cards.
Uploads spill into a per-upload staging directory under the cache root, ZIPs
are extracted server-side, and the result is staged like any other source:
the import canvas previews it and the verified copy pipeline lands it under
``Film Scans/<archive name>/`` (scan dates are not shoot dates, so film never
routes into date-guessed folders).

RAR is not supported in v1 — no rar library ships in requirements, and the
picker copy says "ZIP or TIFF files" honestly.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath

import scanner
import thumbnails
from features.imports import service


ARCHIVE_EXTENSIONS = frozenset({".zip"})
UNSUPPORTED_ARCHIVE_EXTENSIONS = frozenset({".rar", ".7z"})
STAGING_DIR_NAME = "import-film"
STALE_STAGING_SECONDS = 24 * 3600
RAR_MESSAGE = "RAR archives aren't supported yet — send ZIP or the TIFF files themselves"


def staging_root() -> Path:
    return Path(thumbnails.SSD_CACHE_DIR) / STAGING_DIR_NAME


def is_staging_path(path: Path | str) -> bool:
    try:
        Path(path).resolve().relative_to(staging_root().resolve())
        return True
    except (OSError, ValueError):
        return False


def sweep_stale_staging() -> None:
    """Reclaim extraction dirs abandoned without a commit (best effort)."""
    root = staging_root()
    if not root.is_dir():
        return
    cutoff = time.time() - STALE_STAGING_SECONDS
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _batch_directory(scan_dir: Path, name: str) -> Path:
    preferred = scan_dir / service.safe_name(name, "Film scans")
    if not preferred.exists():
        return preferred
    for index in range(2, 10_000):
        candidate = preferred.with_name(f"{preferred.name}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not create a staging batch folder")


def _is_junk_member(name: str, parts: tuple[str, ...]) -> bool:
    return (
        scanner.is_junk_file(name)
        or name.startswith("._")
        or any(scanner.is_junk_directory(part) for part in parts[:-1])
    )


def _extract_zip(archive: Path, batch_dir: Path) -> int:
    """Extract supported image members flat into one batch folder.

    Member paths are reduced to their basename, so hostile ``../`` entries
    cannot escape the batch dir; in-archive name collisions get ``-N``.
    """
    extracted = 0
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            posix = PurePosixPath(member.filename.replace("\\", "/"))
            name = posix.name
            if not name or _is_junk_member(name, posix.parts):
                continue
            if Path(name).suffix.lower() not in scanner.SUPPORTED_EXTENSIONS:
                continue
            batch_dir.mkdir(parents=True, exist_ok=True)
            for candidate in service.destination_candidates(str(batch_dir / service.safe_name(name, "frame"))):
                try:
                    with bundle.open(member) as incoming, open(candidate, "xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                except FileExistsError:
                    continue
                extracted += 1
                break
    return extracted


async def stage_uploads(uploads) -> dict:
    """Spill uploads into a fresh staging dir; extract archives. Returns
    ``{path, label, staged_files, skipped}`` or raises ValueError when
    nothing usable was staged."""
    await asyncio.to_thread(sweep_stale_staging)
    scan_dir = staging_root() / uuid.uuid4().hex
    scan_dir.mkdir(parents=True, exist_ok=True)
    loose_batch = f"Scans {date.today().isoformat()}"
    batches: list[str] = []
    staged_files = 0
    skipped: list[dict] = []
    try:
        for index, upload in enumerate(uploads):
            name = Path(str(upload.filename or f"upload-{index + 1}")).name
            ext = Path(name).suffix.lower()
            if ext in UNSUPPORTED_ARCHIVE_EXTENSIONS:
                skipped.append({"name": name, "reason": RAR_MESSAGE})
                continue
            if ext in ARCHIVE_EXTENSIONS:
                spill = scan_dir / f".{uuid.uuid4().hex}.zip"
                await service.copy_upload(upload, str(spill))
                batch_dir = _batch_directory(scan_dir, Path(name).stem)
                try:
                    count = await asyncio.to_thread(_extract_zip, spill, batch_dir)
                except zipfile.BadZipFile:
                    skipped.append({"name": name, "reason": "not a readable ZIP archive"})
                    continue
                finally:
                    spill.unlink(missing_ok=True)
                if not count:
                    skipped.append({"name": name, "reason": "no supported images inside"})
                    continue
                batches.append(batch_dir.name)
                staged_files += count
                continue
            if ext in scanner.SUPPORTED_EXTENSIONS:
                batch_dir = scan_dir / service.safe_name(loose_batch, "Film scans")
                batch_dir.mkdir(parents=True, exist_ok=True)
                for candidate in service.destination_candidates(str(batch_dir / service.safe_name(name, "frame"))):
                    try:
                        await service.copy_upload(upload, candidate)
                    except FileExistsError:
                        continue
                    break
                if batch_dir.name not in batches:
                    batches.append(batch_dir.name)
                staged_files += 1
                continue
            skipped.append({"name": name, "reason": "unsupported file type — send ZIP or TIFF files"})
        if not staged_files:
            raise ValueError(skipped[0]["reason"] if skipped else "Choose ZIP or TIFF files to import")
    except Exception:
        await asyncio.to_thread(shutil.rmtree, scan_dir, ignore_errors=True)
        raise
    label = batches[0] if len(batches) == 1 else f"Film scans · {len(batches)} batches"
    return {"path": str(scan_dir), "label": label, "staged_files": staged_files, "skipped": skipped}
