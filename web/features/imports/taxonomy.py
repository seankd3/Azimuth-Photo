"""Library taxonomy — where imports land on disk.

Three top-level roots under the library root (siblings, never nested):

| Root       | When                                                              |
|------------|-------------------------------------------------------------------|
| Edits      | Our own edited exports, ready for sharing                         |
| Raws       | Digital-camera RAW (CR3/CR2/ARW/NEF/RAF/ORF/RW2/DNG, …) and film  |
|            | scans (a scan is a negative). Exactly two shelves inside:         |
|            | `Raws/Digital/` (date folders) and `Raws/Film Scans/` (batches)   |
| Snapshots  | Phone stills, takeout dumps, memes — browsed, never developed     |

The three roots (plus `Video`) are enforced: routing can never mint a new
top-level sibling, and provenance decides the root — a RAW shot on a phone
is still a snapshot; the extension alone never picks the root.

The archive filesystem is case-sensitive and the spellings above are exact
(`RAWS` and `Raws` are different directories — never normalise or guess case).
The owner renames the roots on disk himself; while that is underway the
retired four-destination names (`Personal Photos`, `RAWS`, `Exported Edits`,
`Film Scans`) are still read and routed correctly, but nothing writes new
files into them.

Routing is keyed on file type + source kind / folder hint — not scattered
if/else at each call site. Existing files are never moved automatically;
see `reclassify_misplaced_personal_photos` for an explicit repair action and
`features.imports.relocation` for catalog-follows-rename repair.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from photo import kind


SourceKind = Literal[
    "phone",
    "camera_card",
    "export",
    "film_scan",
    "video",
    "unknown",
]

DEST_EDITS = "Edits"
DEST_RAWS = "Raws"
DEST_SNAPSHOTS = "Snapshots"
DEST_VIDEO = "Video"
# Film scans are Raws; batches keep their own subtree under the Raws root.
DEST_FILM = f"{DEST_RAWS}/Film Scans"
# Digital-camera files are also Raws. The Raws root holds exactly two shelves —
# Digital and Film Scans — so nothing new files bare under Raws itself.
DEST_DIGITAL = f"{DEST_RAWS}/Digital"

DESTINATIONS: tuple[str, ...] = (
    DEST_EDITS,
    DEST_RAWS,
    DEST_SNAPSHOTS,
)

# Retired four-destination root names → where their content lives now. Read
# tolerance for the manual on-disk rename: hints and persisted paths naming
# these still route correctly, but no new file is written under them and no
# code path re-creates them.
LEGACY_DESTINATIONS: dict[str, str] = {
    "Personal Photos": DEST_SNAPSHOTS,
    "RAWS": DEST_RAWS,
    "Exported Edits": DEST_EDITS,
    "Film Scans": DEST_FILM,
}

# A configured raws tree may still carry the pre-rename spelling on disk.
RAWS_ROOT_NAMES = frozenset({DEST_RAWS, "RAWS"})

DESTINATION_SET = (
    frozenset(DESTINATIONS) | {DEST_VIDEO} | frozenset(LEGACY_DESTINATIONS)
)


def normalize_destination(name: str) -> str:
    """Map a retired root name to its canonical destination; pass others through."""
    return LEGACY_DESTINATIONS.get(name, name)


def existing_root(library_root: Path | str, canonical: str, legacy: str) -> Path:
    """Prefer the canonical root; fall back to a still-unrenamed legacy tree.

    Chooses between directories that already exist — it never creates either.
    On a case-folding filesystem the canonical name can report present while
    the directory on disk is the legacy spelling (`Raws` vs `RAWS`); the real
    name wins so catalog paths match the disk.
    """
    library = Path(library_root)
    canonical_dir = library / canonical
    if canonical_dir.is_dir():
        try:
            real = canonical_dir.resolve().name
        except OSError:
            real = canonical
        if real == legacy:
            return library / legacy
        return canonical_dir
    if (library / legacy).is_dir():
        return library / legacy
    return canonical_dir


RAW_CAMERA_EXTENSIONS = kind.RAW_FORMATS

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

UNAMBIGUOUS_RAW_EXTENSIONS = kind.CAMERA_ONLY

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
    """Parent of the Raws tree — the Photos library root."""
    root = Path(raws_root).expanduser().resolve()
    if root.name in RAWS_ROOT_NAMES:
        return root.parent
    return root


def default_library_root(*, intake_root: Path | None = None, raws_root: Path | None = None) -> Path:
    configured = os.environ.get("AZIMUTH_LIBRARY_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if raws_root is not None:
        return library_root_from_raws(raws_root)
    if intake_root is not None:
        intake = Path(intake_root).expanduser().resolve()
        if intake.name == "_intake":
            return intake.parent
        return intake.parent
    return (Path.home() / "Pictures").resolve()


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
        # A folder name alone is not provenance against camera evidence: a
        # camera-only RAW off a card is a camera file even inside a takeout
        # or Camera Roll dump. Without EXIF here, the card flag is the only
        # camera evidence available.
        if ext in UNAMBIGUOUS_RAW_EXTENSIONS and card_source:
            return "camera_card"
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
    "affinity photo", "luminar", "azimuth", "gimp",
)

# User-facing category per source kind, and the destination tree per category.
CATEGORY_BY_KIND = {
    "camera_card": "raw", "unknown": "raw", "phone": "personal",
    "film_scan": "film", "export": "export", "video": "video",
}
DEST_BY_CATEGORY = {
    "raw": DEST_DIGITAL, "personal": DEST_SNAPSHOTS, "film": DEST_FILM,
    "export": DEST_EDITS, "video": DEST_VIDEO,
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
    strong path markers (unless the file itself carries camera evidence) >
    unambiguous RAW extension > EXIF software/make > weak path markers /
    HEIC > card flag > TIFF > unknown."""
    if kind == "video":
        return "video"
    haystack = " ".join(
        part.replace("\\", "/").lower() for part in (path, rel_path, filename) if part
    )
    if any(marker in haystack for marker in _EXPORT_PATH_MARKERS):
        return "export"
    if any(marker in haystack for marker in _FILM_PATH_MARKERS):
        return "film_scan"
    ext = extension_of(filename)
    make = (camera_make or "").lower()
    phone_make = bool(make) and any(marker in make for marker in PHONE_MAKES)
    scanner = bool(make) and any(marker in make for marker in SCANNER_MAKES)
    if any(marker in haystack for marker in _STRONG_PHONE_PATH_MARKERS):
        # A folder name alone is not provenance when the file itself carries
        # camera evidence: a camera-only RAW backed by camera EXIF or a card
        # source is a camera file even inside a takeout or Camera Roll dump.
        # A phone-make DNG (Pixel, iPhone, Galaxy) stays phone.
        if ext in UNAMBIGUOUS_RAW_EXTENSIONS and (
            card_source or (make and not phone_make and not scanner)
        ):
            return "camera_card"
        return "phone"
    if ext in UNAMBIGUOUS_RAW_EXTENSIONS:
        return "camera_card"
    stamped = (software or "").lower()
    if stamped and any(marker in stamped for marker in EXPORT_SOFTWARE):
        return "export"
    if scanner:
        return "film_scan"
    if phone_make:
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
    """Return the library-relative destination folder for an incoming file.

    Every destination lives inside the enforced roots — Edits, Raws (on its
    Digital or Film Scans shelf), Snapshots — plus Video. Routing can never
    mint a new top-level sibling.

    Mapping table (first match wins):

    1. Folder hint naming a root, current or retired → its canonical
       destination (older clients still say "Personal Photos"; never
       re-create a dead root); a bare Raws hint lands on the Digital shelf
    2. Any other folder hint → that folder inside Snapshots (device folders
       like "Screenshots" from phone sync; never a stray sibling root)
    3. source_kind=video → Video
    4. source_kind=export → Edits
    5. source_kind=film_scan → Raws/Film Scans
    6. source_kind=phone → Snapshots. Provenance decides the root: a
       phone-provenance DNG/RAW stays in Snapshots. Classification only
       says "phone" on strong provenance (strong path marker, phone EXIF
       make, explicit phone source), so the extension never overrides it
    7. RAW camera extensions → Raws/Digital
    8. Film-scan extensions (TIFF) → Raws/Film Scans
    9. .heic/.heif → Snapshots
    10. Default (camera-card JPEG companions, legacy sync seeds) → Raws/Digital
    """
    hint = normalize_folder_hint(folder_hint) if folder_hint else None
    if hint:
        dest = normalize_destination(hint)
        if dest == DEST_RAWS:
            return DEST_DIGITAL
        if Path(dest).parts[0] in {DEST_EDITS, DEST_RAWS, DEST_SNAPSHOTS, DEST_VIDEO}:
            return dest
        return f"{DEST_SNAPSHOTS}/{hint}"

    kind = (source_kind or "unknown").strip().lower() or "unknown"
    ext = extension_of(filename)
    if kind == "video":
        return DEST_VIDEO
    if kind == "export":
        return DEST_EDITS
    if kind == "film_scan":
        return DEST_FILM
    if kind == "phone":
        return DEST_SNAPSHOTS

    if ext in RAW_CAMERA_EXTENSIONS:
        return DEST_DIGITAL
    if ext in FILM_SCAN_EXTENSIONS:
        return DEST_FILM
    if ext in {".heic", ".heif"}:
        return DEST_SNAPSHOTS
    return DEST_DIGITAL


# Canonical root → its retired name, for write-time tolerance while the
# archive is renamed by hand.
_LEGACY_BY_ROOT = {
    DEST_RAWS: "RAWS",
    DEST_EDITS: "Exported Edits",
    DEST_SNAPSHOTS: "Personal Photos",
}


def resolve_destination_dir(library_root: Path | str, destination: str) -> Path:
    """Absolute directory for a routed destination.

    Tolerates a library whose roots are not renamed yet: when the canonical
    root is absent but its legacy-named tree exists, keep writing into the
    legacy tree instead of creating a second root beside it. Fresh libraries
    get the canonical name.
    """
    parts = Path(destination).parts
    legacy = _LEGACY_BY_ROOT.get(parts[0])
    if legacy is None:
        return Path(library_root).joinpath(*parts)
    return existing_root(library_root, parts[0], legacy).joinpath(*parts[1:])


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
    return resolve_destination_dir(library_root, dest) / year / day


def destination_source_root(library_root: Path | str, destination: str) -> Path:
    """Catalog source path for a destination tree (sibling under library root)."""
    return Path(library_root) / destination


def misplaced_personal_under_raws_prefix(library_root: Path | str) -> str:
    """Path prefix for the dogfood mis-nest: <raws root>/Personal Photos/…

    The mis-nest was produced by the retired four-destination router, so the
    nested folder keeps its legacy spelling regardless of the raws root name.
    """
    raws = existing_root(library_root, DEST_RAWS, "RAWS")
    return str(raws / "Personal Photos")


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
    from features.imports import move_journal

    recovery_conn = await connection.open_async(db_path)
    try:
        recovery = await move_journal.recover(recovery_conn)
    finally:
        await connection.close_async(recovery_conn, db_path=db_path)

    preview = await preview_misplaced_personal_photos(db_path, library_root)
    if not confirm or dry_run:
        return {
            "action": "preview",
            "dry_run": True,
            "moved": 0,
            "updated": 0,
            "errors": [],
            "recovery": recovery,
            **preview,
        }

    library = Path(library_root)
    bad_prefix = Path(preview["prefix"])
    good_root = existing_root(library, DEST_SNAPSHOTS, "Personal Photos")
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
                        await move_journal.record_intent(
                            conn,
                            image_id=int(row["id"]),
                            old_path=old_path,
                            new_path=new_path,
                            new_source_id=source_id,
                        )
                        await _to_thread_move(old_path, new_path)
                        try:
                            # Commit per file so prior moves stay durable if a
                            # later catalog commit fails mid-run.
                            await move_journal.apply_and_clear(
                                conn,
                                image_id=int(row["id"]),
                                new_path=new_path,
                                new_source_id=source_id,
                            )
                        except Exception as exc:
                            errors.append({"id": int(row["id"]), "message": str(exc)})
                            break
                        moved += 1
                        updated += 1
                        continue
                    elif not new_path.exists():
                        errors.append({
                            "id": int(row["id"]),
                            "message": "source missing and destination missing",
                        })
                        continue
                except OSError as exc:
                    errors.append({"id": int(row["id"]), "message": str(exc)})
                    continue
            # move_files=False promises the files were already relocated; never
            # re-point a catalog row at a path that does not exist.
            if not move_files and not new_path.exists():
                errors.append({
                    "id": int(row["id"]),
                    "message": f"destination missing: {new_path}",
                })
                continue
            try:
                await conn.execute(
                    "UPDATE images SET filepath = ?, source_id = ? WHERE id = ?",
                    (str(new_path), source_id, int(row["id"])),
                )
                await conn.commit()
            except Exception as exc:
                errors.append({"id": int(row["id"]), "message": str(exc)})
                break
            updated += 1
    finally:
        await connection.close_async(conn, db_path=db_path)

    return {
        "action": "reclassify",
        "dry_run": False,
        "moved": moved + int(recovery.get("redone") or 0),
        "updated": updated + int(recovery.get("redone") or 0),
        "errors": errors,
        "recovery": recovery,
        "prefix": preview["prefix"],
        "count": preview["count"],
        "destination_root": str(good_root),
    }


async def _to_thread_move(src: Path, dest: Path) -> None:
    import asyncio

    await asyncio.to_thread(_move_original, src, dest)


def _move_original(src: Path, dest: Path) -> None:
    """Same-device moves rename atomically. Cross-device, shutil.move would be
    copy2 + unlink with no byte check — a silently corrupted copy destroys the
    only original — so copy + fsync + full-hash verify before unlinking."""
    try:
        os.rename(src, dest)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    from features.sync.hashing import compute_full_hash

    partial = dest.with_name(f".{dest.name}.moving")
    try:
        with src.open("rb") as incoming, partial.open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if compute_full_hash(partial) != compute_full_hash(src):
            raise OSError(f"cross-device copy verification failed: {src} -> {dest}")
        shutil.copystat(src, partial)
        os.replace(partial, dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    src.unlink()
