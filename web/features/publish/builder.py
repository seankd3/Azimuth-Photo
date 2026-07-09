"""Build static public gallery bundles from photoArchive collections."""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates


@dataclass(frozen=True)
class BundleSummary:
    slug: str
    title: str
    photo_count: int
    date_range: str
    cover: str
    bundle_bytes: int
    file_count: int


def date_range_for_images(images: list[dict]) -> str:
    dates = sorted(
        {
            str(image.get("date_taken") or "")[:10]
            for image in images
            if str(image.get("date_taken") or "")[:10]
        }
    )
    if not dates:
        return ""
    if dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} to {dates[-1]}"


async def build_public_gallery_bundle(
    *,
    slug: str,
    title: str,
    destination: str | Path,
    templates: Jinja2Templates,
    get_collection,
    collection_id: int,
    get_images_by_ids,
    collection_image_ids=None,
    resolve_smart_image_ids=None,
    thumbnails: Any,
) -> BundleSummary:
    collection = await get_collection(int(collection_id), limit=1, offset=0)
    if collection is None:
        raise ValueError("Collection not found")

    image_ids = await _snapshot_image_ids(
        collection,
        collection_id=int(collection_id),
        collection_image_ids=collection_image_ids,
        resolve_smart_image_ids=resolve_smart_image_ids,
    )
    rows_by_id = await get_images_by_ids(image_ids) if image_ids else {}
    images = [rows_by_id[image_id] for image_id in image_ids if image_id in rows_by_id]

    target = Path(destination)
    await asyncio.to_thread(_reset_bundle_dir, target)
    await asyncio.to_thread((target / "thumb" / "sm").mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((target / "img").mkdir, parents=True, exist_ok=True)

    gallery_images = []
    for image in images:
        image_id = int(image["id"])
        await _write_cached_jpeg(
            thumbnails=thumbnails,
            image=image,
            size="sm",
            output_path=target / "thumb" / "sm" / f"{image_id}.jpg",
        )
        await _write_cached_jpeg(
            thumbnails=thumbnails,
            image=image,
            size="md",
            output_path=target / "img" / f"{image_id}.jpg",
        )
        gallery_images.append(
            {
                "id": image_id,
                "filename": image.get("filename") or f"Photo {image_id}",
                "aspect_ratio": float(image.get("aspect_ratio") or 1.5),
                "date_taken": image.get("date_taken"),
                "thumb": f"./thumb/sm/{image_id}.jpg",
                "preview": f"./img/{image_id}.jpg",
                "full": f"./img/{image_id}.jpg",
            }
        )

    date_range = date_range_for_images(gallery_images)
    cover = f"/g/{slug}/thumb/sm/{gallery_images[0]['id']}.jpg" if gallery_images else ""
    html = templates.env.get_template("share_gallery.html").render(
        not_found=False,
        locked=False,
        public_static=True,
        token=f"public-{slug}",
        collection_name=title or collection.get("name") or "Gallery",
        photo_count=len(gallery_images),
        date_range=date_range,
        gallery_json={
            "token": f"public-{slug}",
            "name": title or collection.get("name") or "Gallery",
            "photo_count": len(gallery_images),
            "date_range": date_range,
            "images": gallery_images,
        },
    )
    await asyncio.to_thread((target / "index.html").write_text, html, "utf-8")
    bundle_bytes, file_count = await asyncio.to_thread(_bundle_size, target)
    return BundleSummary(
        slug=slug,
        title=title or collection.get("name") or "Gallery",
        photo_count=len(gallery_images),
        date_range=date_range,
        cover=cover,
        bundle_bytes=bundle_bytes,
        file_count=file_count,
    )


async def _snapshot_image_ids(
    collection: dict,
    *,
    collection_id: int,
    collection_image_ids,
    resolve_smart_image_ids,
) -> list[int]:
    if collection.get("smart"):
        if resolve_smart_image_ids is None:
            raise RuntimeError("Smart collection publishing is not configured")
        return _unique_ids(await resolve_smart_image_ids(collection.get("query") or {}))
    if collection_image_ids is not None:
        return _unique_ids(await collection_image_ids(collection_id))
    return _unique_ids([row["id"] for row in collection.get("images") or []])


async def _write_cached_jpeg(*, thumbnails, image: dict, size: str, output_path: Path) -> None:
    image_id = int(image["id"])
    path_entry = await asyncio.to_thread(thumbnails.fast_disk_path_entry, size, image_id)
    if path_entry is not None:
        _signature, cached_path = path_entry
        await asyncio.to_thread(shutil.copyfile, cached_path, output_path)
        return

    read_entry = await asyncio.to_thread(thumbnails.fast_disk_read_entry, size, image_id, None)
    if read_entry is not None:
        _signature, data = read_entry
        await asyncio.to_thread(output_path.write_bytes, data)
        return

    data = await thumbnails.get_thumbnail(image["filepath"], size, image_id)
    if not data:
        raise RuntimeError(f"Could not build {size} image for photo {image_id}")
    await asyncio.to_thread(output_path.write_bytes, data)


def _reset_bundle_dir(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def _bundle_size(target: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for root, _dirs, files in os.walk(target):
        for filename in files:
            path = Path(root) / filename
            total += path.stat().st_size
            count += 1
    return total, count


def _unique_ids(image_ids) -> list[int]:
    seen = set()
    unique = []
    for raw_id in image_ids or []:
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if image_id > 0 and image_id not in seen:
            seen.add(image_id)
            unique.append(image_id)
    return unique
