"""Film-scan intake — extract uploaded archives into a staging dir for the canvas.

Film scans arrive as lab ZIPs (or loose TIFF/JPEG frames), not camera cards.
Uploads spill into a per-upload staging directory under the cache root, ZIPs
are extracted server-side, and the result is staged like any other source:
the import canvas previews it and the verified copy pipeline lands it under
``Raws/Film Scans/<archive name>/`` (scan dates are not shoot dates, so film
never routes into date-guessed folders).

RAR is not supported in v1 — no rar library ships in requirements, and the
picker copy says "ZIP or TIFF files" honestly.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath

import db
import scanner
import thumbnails
from data import connection
from features.imports import service
from photo import location


ARCHIVE_EXTENSIONS = frozenset({".zip"})
UNSUPPORTED_ARCHIVE_EXTENSIONS = frozenset({".rar", ".7z"})
STAGING_DIR_NAME = "import-film"
STALE_STAGING_SECONDS = 24 * 3600
RAR_MESSAGE = "RAR archives aren't supported yet — send ZIP or the TIFF files themselves"


def clean_roll_name(value: str | None) -> str | None:
    """A folder name the owner typed: one path segment, Windows-legal."""

    text = str(value or "").strip().strip(".")
    for forbidden in '<>:"/\\|?*':
        text = text.replace(forbidden, " ")
    text = " ".join(text.split())
    return text[:120] or None


# A year ("2026") or a day ("2026-08-06") — the dated shelving between
# Film Scans/ and the roll folders, never itself a roll.
_DATED_SHELF = re.compile(r"^(?:19|20)\d{2}(?:-\d{2}-\d{2})?$")


def is_roll_path(path: str) -> bool:
    """True when *path* names a roll folder under Raws/Film Scans/ —
    the unit ``rename_roll`` operates on, in either the old
    ``<year>/<roll>`` or the current ``<year>/<day>/<roll>`` layout."""

    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    try:
        anchor = parts.index("Film Scans")
    except ValueError:
        return False
    return (
        anchor >= 1
        and parts[anchor - 1] == "Raws"
        and len(parts) > anchor + 1
        and not _DATED_SHELF.match(parts[-1])
    )


def rename_roll(tree_path: str, new_name: str) -> dict:
    """Rename a landed roll folder — bytes and catalog rows together.

    *tree_path* is the folder as the tree shows it (the catalog form,
    forward slashes). The physical directory is found through
    ``photo.location`` so a roll on the attached archive drive renames the
    same way as one on the laptop. The directory rename and the row
    updates succeed or fail as one: any database failure renames the
    directory back.
    """

    cleaned = clean_roll_name(new_name)
    if not cleaned:
        raise ValueError("Type a folder name")
    if not is_roll_path(tree_path):
        raise ValueError("Only film roll folders can be renamed")
    prefix = str(tree_path or "").replace("\\", "/").rstrip("/")
    if cleaned == prefix.rsplit("/", 1)[-1]:
        return {"path": prefix, "name": cleaned}

    conn = connection.open_sync(db.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, filepath, file_size FROM images "
            "WHERE substr(replace(filepath, char(92), '/'), 1, ?) = ?",
            (len(prefix) + 1, prefix + "/"),
        ).fetchall()
        if not rows:
            raise ValueError("No photos found in this folder")
        first = rows[0]
        resolved = location.local_path(first["filepath"], expected_size=first["file_size"])
        if resolved is None:
            raise ValueError("The roll's files aren't reachable right now")
        depth_below_roll = str(first["filepath"]).replace("\\", "/")[len(prefix) + 1:].count("/") + 1
        physical = Path(resolved)
        for _ in range(depth_below_roll):
            physical = physical.parent
        target = physical.with_name(cleaned)
        case_change_only = target.name.lower() == physical.name.lower()
        if target.exists() and not case_change_only:
            raise ValueError("A folder with that name already exists")

        new_prefix = prefix.rsplit("/", 1)[0] + "/" + cleaned
        try:
            os.rename(physical, target)
        except OSError as exc:
            raise ValueError("The folder is busy — try again in a moment") from exc
        try:
            with conn:
                for row in rows:
                    tail = str(row["filepath"]).replace("\\", "/")[len(prefix):]
                    new_filepath = new_prefix + tail
                    if "\\" in str(row["filepath"]):
                        new_filepath = new_filepath.replace("/", "\\")
                    conn.execute(
                        "UPDATE images SET filepath = ? WHERE id = ?",
                        (new_filepath, row["id"]),
                    )
                # The import batch named after the roll follows the rename.
                conn.execute(
                    "UPDATE import_batches SET name = ? WHERE name = ? AND id IN ("
                    "  SELECT DISTINCT batch_id FROM import_batch_images WHERE image_id IN ("
                    f"    {','.join('?' * len(rows))}))",
                    (cleaned, prefix.rsplit("/", 1)[-1], *[row["id"] for row in rows]),
                )
        except Exception:
            os.rename(target, physical)
            raise
    finally:
        connection.close_sync(conn, db_path=db.DB_PATH)
    return {"path": new_prefix, "name": cleaned}


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
                except (zipfile.BadZipFile, OSError):
                    # A truncated download raises OSError from deep inside
                    # zipfile (seek past the corrupt central directory), not
                    # BadZipFile — same honest answer either way.
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
