"""Build the deterministic catalog in an isolated Python process."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from tifffile import imwrite

from qa.config import (
    ACTIVE_IMAGE_COUNT,
    CATALOG_DB,
    COLLECTION_IMAGE_COUNT,
    FIXTURE_HOME,
    FIXTURE_VERSION,
    MANIFEST_PATH,
    PRISTINE_DB,
    TRASH_IMAGE_COUNT,
    TRASH_MIRROR_IMAGE_COUNT,
    VISIBLE_IMAGE_COUNT,
)


PRIMARY_COUNT = 2_203
SECONDARY_COUNT = 1_300
HUB_COUNT = 500
DEVELOP_IMAGE_COUNT = 6
RAW_IMAGE_ID = 1
DEVELOP_PRESET_NAME = "Clean Color"
STACK_MEMBER_IDS = (1, 7, 8, 9)
EXACT_DUPLICATE_STACK_ID = 2
EXACT_DUPLICATE_IDS = (30, 31)
LOCATED_IMAGE_IDS = (101, 102, 103, 104, 105, 106)
PEOPLE_IDS = {"named": 1, "merge_source": 2, "merge_target": 3, "hide": 4}


def _jpeg(path: Path, index: int, *, size: tuple[int, int] = (160, 106)) -> None:
    """Write a small valid image with enough texture to exercise Develop."""

    path.parent.mkdir(parents=True, exist_ok=True)
    base = ((index * 41) % 210 + 20, (index * 73) % 210 + 20, (index * 97) % 210 + 20)
    image = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(image)
    for step in range(0, size[0], 16):
        shade = ((base[0] + step) % 255, (base[1] + step * 2) % 255, (base[2] + step * 3) % 255)
        draw.rectangle((step, 0, min(step + 7, size[0]), size[1]), fill=shade)
    draw.ellipse((36, 20, 124, 88), outline=(245, 245, 245), width=4)
    image.save(path, "JPEG", quality=88)


def _dng(path: Path, index: int, *, size: tuple[int, int] = (160, 108)) -> None:
    """Write a tiny standards-readable Bayer DNG for real RAW QA."""

    width, height = size
    y, x = np.mgrid[:height, :width]
    mosaic = ((x / width * 0.7 + y / height * 0.3) * 12_000 + 512).astype(np.uint16)
    mosaic += (((x // 8 + y // 8 + index) % 2) * 1_800).astype(np.uint16)
    path.parent.mkdir(parents=True, exist_ok=True)
    imwrite(
        path,
        mosaic,
        photometric=32803,
        metadata=None,
        extratags=[
            (50706, "B", 4, (1, 4, 0, 0), False),
            (50707, "B", 4, (1, 3, 0, 0), False),
            (50708, "s", 0, "Azimuth QA Camera", False),
            (33421, "H", 2, (2, 2), False),
            (33422, "B", 4, (0, 1, 1, 2), False),
            (50713, "H", 2, (1, 1), False),
            (50714, "I", 1, 512, False),
            (50717, "I", 1, 16_383, False),
            (50718, "2I", 2, ((1, 1), (1, 1)), False),
            (50719, "I", 2, (0, 0), False),
            (50720, "I", 2, (width, height), False),
            (50721, "2i", 9, tuple((value, 10_000) for value in (10_000, 0, 0, 0, 10_000, 0, 0, 0, 10_000)), False),
            (50728, "2I", 3, ((1, 2), (1, 1), (2, 3)), False),
            (50778, "H", 1, 21, False),
        ],
    )


def _month_for(index: int) -> tuple[int, int]:
    ordinal = index % (9 * 12)
    return 2018 + ordinal // 12, ordinal % 12 + 1


def _active_rows(primary: Path, secondary: Path) -> list[tuple]:
    rows: list[tuple] = []
    shared_source = FIXTURE_HOME / "cache" / "previews" / "qa-shared-preview.jpg"
    _jpeg(shared_source, 777)
    real_paths = []
    for image_id in range(1, DEVELOP_IMAGE_COUNT + 1):
        suffix = ".dng" if image_id == RAW_IMAGE_ID else ".jpg"
        path = primary / "Develop" / f"000-qa-develop-{image_id}{suffix}"
        if image_id == RAW_IMAGE_ID:
            _dng(path, image_id)
        else:
            _jpeg(path, image_id)
        real_paths.append(path)

    primary_folders = ("Travel/2022", "Family/2023", "Studio/2024", "Inbox")
    secondary_folders = ("Archive/2019", "Client/2020", "Scans/2021")
    for image_id in range(1, ACTIVE_IMAGE_COUNT + 1):
        if image_id <= PRIMARY_COUNT:
            source_id = 1
            if image_id <= DEVELOP_IMAGE_COUNT:
                filepath = real_paths[image_id - 1]
                filename = filepath.name
                elo = 5_000 - image_id
            else:
                folder = primary_folders[(image_id - DEVELOP_IMAGE_COUNT) % len(primary_folders)]
                filename = f"qa-nebula-{image_id:05d}.jpg"
                filepath = primary / folder / filename
                elo = 1_200 + (image_id % 900)
        elif image_id <= PRIMARY_COUNT + SECONDARY_COUNT:
            source_id = 2
            folder = secondary_folders[(image_id - PRIMARY_COUNT) % len(secondary_folders)]
            filename = f"qa-nebula-{image_id:05d}.jpg"
            filepath = secondary / folder / filename
            elo = 1_200 + (image_id % 900)
        else:
            source_id = 3
            remote_index = image_id - PRIMARY_COUNT - SECONDARY_COUNT
            filename = f"qa-nebula-{image_id:05d}.jpg"
            filepath = Path(f"hub://archive/{2020 + remote_index % 6}/{filename}")
            elo = 1_200 + (image_id % 900)

        year, month = _month_for(image_id)
        if source_id != 3 and image_id > DEVELOP_IMAGE_COUNT:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(shared_source, filepath)
        landscape = image_id % 4 != 0
        width, height = ((1_600, 1_067) if landscape else (1_067, 1_600))
        rows.append(
            (
                image_id,
                source_id,
                filename,
                str(filepath),
                f"{image_id:032x}",
                image_id if source_id == 3 else None,
                1 if source_id == 3 else 0,
                elo,
                image_id % 8,
                "kept",
                "picked" if image_id % 17 == 0 else "unflagged",
                f"{year:04d}-{month:02d}-{(image_id % 27) + 1:02d}T12:00:00",
                "QA Camera Co",
                f"QA-Cam {image_id % 3 + 1}",
                f"QA Lens {image_id % 4 + 1}",
                filepath.suffix.lower(),
                8_192 + image_id,
                width,
                height,
                "landscape" if landscape else "portrait",
            )
        )
    return rows


def _trash_rows(primary: Path) -> list[tuple]:
    rows: list[tuple] = []
    now = time.time()
    for offset in range(TRASH_IMAGE_COUNT):
        image_id = ACTIVE_IMAGE_COUNT + offset + 1
        filename = f"qa-trash-{offset + 1}.jpg"
        original = primary / "Trash candidates" / filename
        trash_path = primary / ".trash" / "Trash candidates" / filename
        _jpeg(trash_path, 100 + offset, size=(96, 64))
        rows.append(
            (
                image_id,
                1,
                filename,
                str(original),
                f"{image_id:032x}",
                900 + offset,
                "trashed",
                "unflagged",
                f"2026-07-{offset + 1:02d}T10:00:00",
                ".jpg",
                trash_path.stat().st_size,
                96,
                64,
                now - offset,
                str(trash_path),
            )
        )
    return rows


async def _seed_discovery_surfaces(conn, preview_path: Path, now: float) -> None:
    """Seed the smallest real state needed by Stacks, People, and Map QA."""

    await conn.execute(
        "INSERT INTO stacks (id, kind, representative_image_id, auto, created_at, updated_at) "
        "VALUES (1, 'manual', ?, 0, ?, ?)",
        (STACK_MEMBER_IDS[0], now, now),
    )
    await conn.executemany(
        "INSERT INTO stack_members (stack_id, image_id, score, added_at) VALUES (1, ?, ?, ?)",
        [(image_id, 1.0 - position / 10, now) for position, image_id in enumerate(STACK_MEMBER_IDS)],
    )
    await conn.execute(
        "INSERT INTO stacks (id, kind, representative_image_id, auto, created_at, updated_at) "
        "VALUES (?, 'crosssource', ?, 0, ?, ?)",
        (EXACT_DUPLICATE_STACK_ID, EXACT_DUPLICATE_IDS[0], now, now),
    )
    await conn.executemany(
        "INSERT INTO stack_members (stack_id, image_id, score, added_at) VALUES (?, ?, ?, ?)",
        [(EXACT_DUPLICATE_STACK_ID, image_id, 1.0 - position / 10, now) for position, image_id in enumerate(EXACT_DUPLICATE_IDS)],
    )

    await conn.executemany(
        "UPDATE images SET latitude = ?, longitude = ?, location_source = 'exif' WHERE id = ?",
        [
            (30.2672, -97.7431, image_id)
            if image_id <= 104
            else (48.8566, 2.3522, image_id)
            for image_id in LOCATED_IMAGE_IDS
        ],
    )

    await conn.executemany(
        "INSERT INTO people (id, name, status, representative_face_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (PEOPLE_IDS["named"], "Ada QA", "named", 1, now, now),
            (PEOPLE_IDS["merge_source"], "", "unknown", 2, now, now),
            (PEOPLE_IDS["merge_target"], "", "unknown", 3, now, now),
            (PEOPLE_IDS["hide"], "Hide QA", "named", 4, now, now),
        ],
    )
    await conn.executemany(
        "INSERT INTO face_detections "
        "(id, image_id, detection_key, bbox_x, bbox_y, bbox_w, bbox_h, confidence, quality, "
        "embedding_model, cache_path, created_at, updated_at) "
        "VALUES (?, ?, ?, 36, 20, 88, 68, .98, .95, 'buffalo_l', ?, ?, ?)",
        [
            (1, 10, "qa-face-ada", str(preview_path), now, now),
            (2, 20, "qa-face-merge-source", str(preview_path), now, now),
            (3, 30, "qa-face-merge-target", str(preview_path), now, now),
            (4, 40, "qa-face-hide", str(preview_path), now, now),
        ],
    )
    await conn.executemany(
        "INSERT INTO face_assignments (face_id, person_id, source, active, assigned_at) "
        "VALUES (?, ?, 'worker', 1, ?)",
        [(1, 1, now), (2, 2, now), (3, 3, now), (4, 4, now)],
    )
    await conn.executemany(
        "INSERT INTO person_image_membership "
        "(person_id, image_id, face_count, best_quality, latest_face_at) VALUES (?, ?, 1, .95, ?)",
        [
            (1, 10, now),
            (1, 11, now - 1),
            (1, 12, now - 2),
            (2, 20, now),
            (2, 21, now - 1),
            (3, 30, now),
            (3, 31, now - 1),
            (4, 40, now),
        ],
    )


async def _seed() -> None:
    # Imports must happen after PHOTOARCHIVE_HOME is present in the worker env.
    import db
    import thumbnails
    from core.runtime_paths import ensure_runtime_dirs, resolve_runtime_paths
    from features.library.saved_views import SAVED_VIEWS_DDL

    ensure_runtime_dirs(resolve_runtime_paths())
    await db.init_db()
    conn = await db.get_db()
    primary = FIXTURE_HOME / "library" / "primary"
    secondary = FIXTURE_HOME / "library" / "removable"
    primary.mkdir(parents=True, exist_ok=True)
    secondary.mkdir(parents=True, exist_ok=True)

    now = time.time()
    active_rows = _active_rows(primary, secondary)
    await conn.executemany(
        "INSERT INTO catalog_sources "
        "(id, path, display_name, included, online, image_count, active_image_count, created_at, last_seen_at) "
        "VALUES (?, ?, ?, 1, 1, 0, 0, ?, ?)",
        [
            (1, str(primary), "QA Primary", now, now),
            (2, str(secondary), "QA Removable", now, now),
            (3, "hub://", "QA Hub mirror", now, now),
        ],
    )
    await conn.executemany(
        "INSERT INTO images "
        "(id, source_id, filename, filepath, content_hash, hub_image_id, hub_remote, elo, comparisons, "
        "status, flag, date_taken, camera_make, camera_model, lens, file_ext, file_size, width, height, orientation) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        active_rows,
    )
    await conn.executemany(
        "INSERT INTO images "
        "(id, source_id, filename, filepath, content_hash, elo, status, flag, date_taken, file_ext, "
        "file_size, width, height, trashed_at, trash_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _trash_rows(primary),
    )
    for offset in range(TRASH_MIRROR_IMAGE_COUNT):
        image_id = ACTIVE_IMAGE_COUNT + TRASH_IMAGE_COUNT + offset + 1
        await conn.execute(
            "INSERT INTO images "
            "(id, source_id, filename, filepath, content_hash, hub_image_id, hub_remote, elo, comparisons, "
            "status, flag, file_ext, file_size, width, height, trashed_at) "
            "VALUES (?, 3, ?, ?, ?, ?, 1, 1200, 0, 'trashed', 'unflagged', '.jpg', 8192, 96, 64, ?)",
            (image_id, "qa-hub-trash.jpg", "hub://archive/qa-hub-trash.jpg", f"{image_id:032x}", image_id, now),
        )

    await conn.execute(
        "INSERT INTO collections (id, uuid, name, description, cover_image_id, created_at, updated_at) "
        "VALUES (1, '00000000-0000-4000-8000-000000000001', 'QA Favorites', 'Deterministic browser QA collection', 1, ?, ?)",
        (now, now),
    )
    await conn.executemany(
        "INSERT INTO collection_images (collection_id, image_id, position, added_at) VALUES (1, ?, ?, ?)",
        [(image_id, image_id - 1, now) for image_id in range(1, COLLECTION_IMAGE_COUNT + 1)],
    )
    await conn.executescript(SAVED_VIEWS_DDL)
    await conn.execute(
        "INSERT INTO saved_views (id, name, query, created_at) VALUES (1, ?, ?, ?)",
        (
            "QA Landscape workspace",
            json.dumps({
                "scope": {"orientation": "landscape", "sort": "date_taken"},
                "layout": {"density": "comfortable", "collapseStacks": True},
            }),
            "2026-07-15T00:00:00+00:00",
        ),
    )
    await conn.execute(
        "INSERT INTO develop_settings (image_id, settings, origin, updated_at) "
        "VALUES (1, '{}', 'user', '2026-07-15T00:00:00+00:00')"
    )
    await conn.execute(
        "UPDATE images SET content_hash = ? WHERE id IN (?, ?)",
        ("qa-exact-duplicate-pair", *EXACT_DUPLICATE_IDS),
    )
    await conn.execute(
        "INSERT INTO develop_presets (name, folder, settings, created_at) VALUES (?, ?, ?, ?)",
        (
            DEVELOP_PRESET_NAME,
            "QA",
            json.dumps({"Exposure2012": 0.65, "Contrast2012": 18, "Vibrance": 22}),
            "2026-07-15T00:00:00+00:00",
        ),
    )

    shared_preview = FIXTURE_HOME / "cache" / "previews" / "qa-shared-preview.jpg"
    await _seed_discovery_surfaces(conn, shared_preview, now)
    cache_root = str(Path(thumbnails.SSD_CACHE_DIR).resolve())
    filepaths = {int(row[0]): str(row[3]) for row in active_rows}
    cache_rows = []
    for image_id in range(1, ACTIVE_IMAGE_COUNT + TRASH_IMAGE_COUNT + TRASH_MIRROR_IMAGE_COUNT + 1):
        for size in ("sm", "md", "lg"):
            filepath = filepaths.get(image_id, "")
            try:
                signature = thumbnails._build_source_signature(filepath, size, image_id)
            except OSError:
                signature = f"qa-{size}-{image_id}"
            cache_rows.append(
                (cache_root, size, image_id, str(shared_preview), signature, shared_preview.stat().st_size, now, now)
            )
    await conn.executemany(
        "INSERT INTO cache_entries "
        "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        cache_rows,
    )

    for source_id in (1, 2, 3):
        await conn.execute(
            "UPDATE catalog_sources SET "
            "image_count = (SELECT COUNT(*) FROM images WHERE source_id = ?), "
            "active_image_count = (SELECT COUNT(*) FROM images WHERE source_id = ? AND status IN ('kept', 'maybe')) "
            "WHERE id = ?",
            (source_id, source_id, source_id),
        )
    await conn.commit()
    await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await conn.close()

    import_source = FIXTURE_HOME / "import-source" / "QA Card"
    for index in range(1, 4):
        _jpeg(import_source / f"qa-import-{index}.jpg", 800 + index)
    cancellable_import_source = primary / "Import candidates" / "QA Cancellable Card"
    for index in range(1, 65):
        _jpeg(cancellable_import_source / f"qa-cancel-{index:03d}.jpg", 900 + index)

    shutil.copy2(CATALOG_DB, PRISTINE_DB)
    manifest = {
        "version": FIXTURE_VERSION,
        "active_images": ACTIVE_IMAGE_COUNT,
        "visible_images": VISIBLE_IMAGE_COUNT,
        "trash_images": TRASH_IMAGE_COUNT,
        "trash_mirror_images": TRASH_MIRROR_IMAGE_COUNT,
        "trash_total": TRASH_IMAGE_COUNT + TRASH_MIRROR_IMAGE_COUNT,
        "collection_images": COLLECTION_IMAGE_COUNT,
        "primary_source": str(primary),
        "secondary_source": str(secondary),
        "hub_source": "hub://",
        "database": str(CATALOG_DB),
        "raw_image_id": RAW_IMAGE_ID,
        "develop_preset": DEVELOP_PRESET_NAME,
        "import_source": str(import_source),
        "import_source_images": 3,
        "cancellable_import_source": str(cancellable_import_source),
        "cancellable_import_source_images": 64,
        "stack_id": 1,
        "stack_members": list(STACK_MEMBER_IDS),
        "exact_duplicate_stack_id": EXACT_DUPLICATE_STACK_ID,
        "exact_duplicate_ids": list(EXACT_DUPLICATE_IDS),
        "people": PEOPLE_IDS,
        "located_images": len(LOCATED_IMAGE_IDS),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
