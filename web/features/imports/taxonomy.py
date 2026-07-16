"""Library taxonomy — where imports land on disk.

Four top-level destinations under the library root (siblings, never nested):

| Destination        | When                                                         |
|--------------------|--------------------------------------------------------------|
| Personal Photos    | Phone / cellphone stills (JPEG/HEIC/HEIF) or phone upload    |
| RAWS               | Digital-camera RAW (CR3/CR2/ARW/NEF/RAF/ORF/RW2/DNG, …)      |
| Exported Edits     | Our own edited exports                                       |
| Film Scans         | Scanner / lab film-scan inputs (typically TIFF)              |

Disk names match the live Photos README (`RAWS` spelling preserved).
Display may say "RAWs"; on-disk folder stays `RAWS`.

Routing is keyed on file type + source kind / folder hint — not scattered
if/else at each call site. Existing files are never moved automatically;
see `reclassify_misplaced_personal_photos` for an explicit repair action.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal


SourceKind = Literal[
    "phone",
    "camera_card",
    "export",
    "film_scan",
    "video",
    "unknown",
]

DEST_PERSONAL = "Personal Photos"
DEST_RAWS = "RAWS"
DEST_EXPORTS = "Exported Edits"
DEST_FILM = "Film Scans"
DEST_VIDEO = "Video"

# Canonical on-disk destinations (order is the product taxonomy).
DESTINATIONS: tuple[str, ...] = (
    DEST_PERSONAL,
    DEST_RAWS,
    DEST_EXPORTS,
    DEST_FILM,
)

DESTINATION_SET = frozenset(DESTINATIONS) | {DEST_VIDEO}

RAW_CAMERA_EXTENSIONS = frozenset({
    ".arw",
    ".cr2",
    ".cr3",
    ".dng",
    ".nef",
    ".orf",
    ".raf",
    ".rw2",
})

PHONE_STILL_EXTENSIONS = frozenset({
    ".heic",
    ".heif",
    ".jpg",
    ".jpeg",
})

FILM_SCAN_EXTENSIONS = frozenset({
    ".tif",
    ".tiff",
})

# Folder hints the Android backup / clients may send (exact match, case-sensitive
# for the known set; unknown hints still become top-level siblings).
KNOWN_FOLDER_HINTS = frozenset(DESTINATIONS)

# Strong signals uniquely identify a phone/personal source; they win over file
# extension. Weak signals are generic folder names ("Camera", "DCIM/Camera") that
# ALSO appear on cameras/card dumps — they must never override an unambiguous
# camera-RAW extension, or a CR3 off a card lands in Personal Photos.
_STRONG_PHONE_PATH_MARKERS = (
    "personal photos",
    "camera roll",
    "google photos",
    "facebook photos",
    "pxl_",
)
_WEAK_PHONE_PATH_MARKERS = (
    "/dcim/camera",
    "/camera/",
)
_PHONE_PATH_MARKERS = _STRONG_PHONE_PATH_MARKERS + _WEAK_PHONE_PATH_MARKERS

# Camera-RAW formats that are never produced by phones. .dng is deliberately
# excluded: Pixel and other phones shoot DNG, so its routing stays marker-driven.
UNAMBIGUOUS_RAW_EXTENSIONS = frozenset(RAW_CAMERA_EXTENSIONS - {".dng"})

_FILM_PATH_MARKERS = (
    "film scans",
    "film scan",
    "san marcos filmlab",
    "self develop",
)

_EXPORT_PATH_MARKERS = (
    "exported edits",
    "develop exports",
)

_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")


def extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def library_root_from_raws(raws_root: Path | str) -> Path:
    """Parent of the RAWS tree — the Photos library root."""
    root = Path(raws_root).expanduser().resolve()
    if root.name == DEST_RAWS:
        return root.parent
    return root


def default_library_root(*, intake_root: Path | None = None, raws_root: Path | None = None) -> Path:
    configured = os.environ.get("PHOTOARCHIVE_LIBRARY_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if raws_root is not None:
        return library_root_from_raws(raws_root)
    if intake_root is not None:
        intake = Path(intake_root).expanduser().resolve()
        if intake.name == "_intake":
            return intake.parent
        return intake.parent
    return Path("/mnt/expansion/Photos")


def normalize_folder_hint(value: str | None) -> str | None:
    if value is None:
        return None
    folder = str(value).strip()
    if not folder:
        return None
    if not _FOLDER_RE.fullmatch(folder):
        raise ValueError("folder must match ^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
    return folder


def infer_source_kind(
    *,
    filename: str,
    path: str = "",
    rel_path: str = "",
    card_source: bool = False,
    kind: str = "image",
) -> SourceKind:
    if kind == "video":
        return "video"
    haystack = " ".join(
        part.replace("\\", "/").lower()
        for part in (path, rel_path, filename)
        if part
    )
    if any(marker in haystack for marker in _EXPORT_PATH_MARKERS):
        return "export"
    if any(marker in haystack for marker in _FILM_PATH_MARKERS):
        return "film_scan"
    ext = extension_of(filename)
    if any(marker in haystack for marker in _STRONG_PHONE_PATH_MARKERS):
        return "phone"
    # An unambiguous camera-RAW file is a camera file no matter what folder it
    # sits in — this beats the weak "/camera/" markers and the card flag.
    if ext in UNAMBIGUOUS_RAW_EXTENSIONS:
        return "camera_card"
    if any(marker in haystack for marker in _WEAK_PHONE_PATH_MARKERS):
        return "phone"
    if ext in {".heic", ".heif"}:
        return "phone"
    if card_source:
        return "camera_card"
    if ext in FILM_SCAN_EXTENSIONS:
        return "film_scan"
    return "unknown"


# EXIF provenance signals. Makers announce themselves: phones in Make, scanners
# in Make, editing software in the Software tag. These beat weak path guessing
# but never a strong path marker or an unambiguous camera-RAW extension.
PHONE_MAKES = (
    "google", "apple", "samsung", "oneplus", "xiaomi", "huawei",
    "motorola", "oppo", "vivo", "nothing", "sony xperia", "lge", "lg electronics",
)
SCANNER_MAKES = (
    "epson", "noritsu", "fujifilm frontier", "frontier", "nikon scan",
    "pakon", "plustek", "reflecta", "minolta dimage scan", "canoscan",
)
EXPORT_SOFTWARE = (
    "lightroom", "adobe photoshop", "capture one", "darktable", "rawtherapee",
    "affinity photo", "luminar", "azimuth", "photoarchive", "gimp",
)

# User-facing category per source kind, and the destination tree per category.
CATEGORY_BY_KIND = {
    "camera_card": "raw", "unknown": "raw", "phone": "personal",
    "film_scan": "film", "export": "export", "video": "video",
}
DEST_BY_CATEGORY = {
    "raw": DEST_RAWS, "personal": DEST_PERSONAL, "film": DEST_FILM,
    "export": DEST_EXPORTS, "video": DEST_VIDEO,
}
KIND_BY_CATEGORY = {
    "raw": "camera_card", "personal": "phone", "film": "film_scan",
    "export": "export", "video": "video",
}
IMPORT_CATEGORIES = ("raw", "personal", "film", "export")


def classify_source_kind(
    *,
    filename: str,
    path: str = "",
    rel_path: str = "",
    card_source: bool = False,
    kind: str = "image",
    camera_make: str = "",
    software: str = "",
) -> SourceKind:
    """infer_source_kind plus EXIF provenance, with explicit precedence:
    strong path markers > unambiguous RAW extension > EXIF software/make >
    weak path markers / HEIC > card flag > TIFF > unknown."""
    if kind == "video":
        return "video"
    haystack = " ".join(
        part.replace("\\", "/").lower() for part in (path, rel_path, filename) if part
    )
    if any(marker in haystack for marker in _EXPORT_PATH_MARKERS):
        return "export"
    if any(marker in haystack for marker in _FILM_PATH_MARKERS):
        return "film_scan"
    if any(marker in haystack for marker in _STRONG_PHONE_PATH_MARKERS):
        return "phone"
    ext = extension_of(filename)
    if ext in UNAMBIGUOUS_RAW_EXTENSIONS:
        return "camera_card"
    make = (camera_make or "").lower()
    stamped = (software or "").lower()
    if stamped and any(marker in stamped for marker in EXPORT_SOFTWARE):
        return "export"
    if make and any(marker in make for marker in SCANNER_MAKES):
        return "film_scan"
    if make and any(marker in make for marker in PHONE_MAKES):
        return "phone"
    if any(marker in haystack for marker in _WEAK_PHONE_PATH_MARKERS):
        return "phone"
    if ext in {".heic", ".heif"}:
        return "phone"
    if card_source:
        return "camera_card"
    if ext in FILM_SCAN_EXTENSIONS:
        return "film_scan"
    return "unknown"


def route_destination(
    *,
    filename: str,
    source_kind: SourceKind | str | None = None,
    folder_hint: str | None = None,
) -> str:
    """Return the top-level destination folder name for an incoming file.

    Mapping table (first match wins):

    1. Explicit folder hint that names a known destination → that destination
    2. Explicit custom folder hint → that name (caller places under library root)
    3. source_kind=video → Video
    4. source_kind=export → Exported Edits
    5. source_kind=film_scan → Film Scans
    6. source_kind=phone → Personal Photos
       (phone DNG/RAW stays with Personal Photos per Photos README)
    7. RAW camera extensions → RAWS
    8. Film-scan extensions (TIFF) → Film Scans
    9. Phone still extensions when source is phone/unknown-but-HEIC already handled
       → Personal Photos only for .heic/.heif; JPEG without phone source stays RAWS
       for camera-card companions and legacy sync seeds
    10. Default → RAWS
    """
    hint = normalize_folder_hint(folder_hint) if folder_hint else None
    if hint:
        return hint

    kind = (source_kind or "unknown").strip().lower() or "unknown"
    ext = extension_of(filename)
    if kind == "video":
        return DEST_VIDEO
    if kind == "export":
        return DEST_EXPORTS
    if kind == "film_scan":
        return DEST_FILM
    if kind == "phone":
        # A camera RAW is never a phone still, regardless of inferred kind.
        return DEST_RAWS if ext in UNAMBIGUOUS_RAW_EXTENSIONS else DEST_PERSONAL

    if ext in RAW_CAMERA_EXTENSIONS:
        return DEST_RAWS
    if ext in FILM_SCAN_EXTENSIONS:
        return DEST_FILM
    if ext in {".heic", ".heif"}:
        return DEST_PERSONAL
    return DEST_RAWS


def destination_directory(
    library_root: Path | str,
    *,
    filename: str,
    year: str,
    day: str,
    source_kind: SourceKind | str | None = None,
    folder_hint: str | None = None,
) -> Path:
    """`library_root / <destination> / YYYY / YYYY-MM-DD`."""
    dest = route_destination(
        filename=filename,
        source_kind=source_kind,
        folder_hint=folder_hint,
    )
    return Path(library_root) / dest / year / day


def destination_source_root(library_root: Path | str, destination: str) -> Path:
    """Catalog source path for a destination tree (sibling under library root)."""
    return Path(library_root) / destination


def misplaced_personal_under_raws_prefix(library_root: Path | str) -> str:
    """Path prefix for the dogfood mis-nest: RAWS/Personal Photos/…"""
    return str(Path(library_root) / DEST_RAWS / DEST_PERSONAL)


async def preview_misplaced_personal_photos(
    db_path: str,
    library_root: Path | str,
) -> dict[str, Any]:
    """List catalog rows that landed under RAWS/Personal Photos (read-only)."""
    from data import connection

    prefix = misplaced_personal_under_raws_prefix(library_root)
    empty = {
        "prefix": prefix,
        "count": 0,
        "truncated": False,
        "samples": [],
    }
    try:
        conn = await connection.open_async(db_path)
    except Exception:
        return empty
    try:
        try:
            rows = await (
                await conn.execute(
                    "SELECT id, filepath FROM images WHERE filepath LIKE ? ESCAPE '\\' "
                    "ORDER BY id LIMIT 5000",
                    (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
                )
            ).fetchall()
        except Exception:
            return empty
    finally:
        await connection.close_async(conn, db_path=db_path)
    samples = [{"id": int(row["id"]), "filepath": row["filepath"]} for row in rows[:20]]
    return {
        "prefix": prefix,
        "count": len(rows),
        "truncated": len(rows) >= 5000,
        "samples": samples,
    }


async def reclassify_misplaced_personal_photos(
    db_path: str,
    library_root: Path | str,
    *,
    confirm: bool,
    move_files: bool = True,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Explicit repair for phone files nested under RAWS/Personal Photos.

    Never runs as a side effect of import. Requires confirm=True and dry_run=False
    to mutate. When move_files is False, only catalog filepath/source_id update
    (expects files already relocated).
    """
    from data import connection
    from data.repositories import catalog as catalog_repository

    preview = await preview_misplaced_personal_photos(db_path, library_root)
    if not confirm or dry_run:
        return {
            "action": "preview",
            "dry_run": True,
            "moved": 0,
            "updated": 0,
            "errors": [],
            **preview,
        }

    library = Path(library_root)
    bad_prefix = Path(preview["prefix"])
    good_root = library / DEST_PERSONAL
    source = await catalog_repository.add_or_restore_source(db_path, str(good_root))
    source_id = int(source["id"])

    conn = await connection.open_async(db_path)
    moved = 0
    updated = 0
    errors: list[dict[str, Any]] = []
    try:
        rows = await (
            await conn.execute(
                "SELECT id, filepath FROM images WHERE filepath LIKE ? ESCAPE '\\'",
                (
                    str(bad_prefix).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    + "%",
                ),
            )
        ).fetchall()
        for row in rows:
            old_path = Path(row["filepath"])
            try:
                relative = old_path.relative_to(bad_prefix)
            except ValueError:
                errors.append({"id": int(row["id"]), "message": "path outside prefix"})
                continue
            new_path = good_root / relative
            if move_files:
                try:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    if old_path.exists():
                        if new_path.exists():
                            errors.append({
                                "id": int(row["id"]),
                                "message": f"destination exists: {new_path}",
                            })
                            continue
                        await _to_thread_move(old_path, new_path)
                        moved += 1
                    elif not new_path.exists():
                        errors.append({
                            "id": int(row["id"]),
                            "message": "source missing and destination missing",
                        })
                        continue
                except OSError as exc:
                    errors.append({"id": int(row["id"]), "message": str(exc)})
                    continue
            await conn.execute(
                "UPDATE images SET filepath = ?, source_id = ? WHERE id = ?",
                (str(new_path), source_id, int(row["id"])),
            )
            updated += 1
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)

    return {
        "action": "reclassify",
        "dry_run": False,
        "moved": moved,
        "updated": updated,
        "errors": errors,
        "prefix": preview["prefix"],
        "count": preview["count"],
        "destination_root": str(good_root),
    }


async def _to_thread_move(src: Path, dest: Path) -> None:
    import asyncio

    def _move() -> None:
        shutil.move(str(src), str(dest))

    await asyncio.to_thread(_move)
