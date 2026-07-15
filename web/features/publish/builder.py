"""Build static public gallery bundles from Azimuth Photo collections."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import json
import logging
import os
import platform
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.templating import Jinja2Templates

import settings
from features.publish import nodes as published_nodes


log = logging.getLogger(__name__)


class GalleryImageUnavailable(RuntimeError):
    """One source image cannot produce a gallery derivative."""


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
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    work_target = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent)))

    try:
        await asyncio.to_thread((work_target / "thumb" / "sm").mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((work_target / "img").mkdir, parents=True, exist_ok=True)

        gallery_images = []
        for image in images:
            image_id = int(image["id"])
            thumb_path = work_target / "thumb" / "sm" / f"{image_id}.jpg"
            preview_path = work_target / "img" / f"{image_id}.jpg"
            try:
                await _write_cached_jpeg(
                    thumbnails=thumbnails,
                    image=image,
                    size="sm",
                    output_path=thumb_path,
                )
                await _write_cached_jpeg(
                    thumbnails=thumbnails,
                    image=image,
                    size="md",
                    output_path=preview_path,
                )
            except GalleryImageUnavailable as exc:
                await asyncio.gather(
                    asyncio.to_thread(thumb_path.unlink, missing_ok=True),
                    asyncio.to_thread(preview_path.unlink, missing_ok=True),
                )
                log.warning(
                    "worker=publish image_id=%s skipped unavailable image: %s",
                    image_id,
                    exc,
                )
                continue
            gallery_images.append(
                {
                    "id": image_id,
                    "filename": image.get("filename") or f"Photo {image_id}",
                    "aspect_ratio": float(image.get("aspect_ratio") or 1.5),
                    "date_taken": image.get("date_taken"),
                    "thumb": f"./thumb/sm/{image_id}.jpg",
                    "preview": f"./img/{image_id}.jpg",
                    "full": f"./img/{image_id}.jpg",
                    "download": f"./img/{image_id}.jpg",
                    "download_name": _download_name(image_id, image.get("filename") or ""),
                }
            )

        date_range = date_range_for_images(gallery_images)
        for index, image in enumerate(gallery_images, start=1):
            image["display_label"] = f"Photo {index} of {len(gallery_images)}"
        cover = f"/g/{slug}/thumb/sm/{gallery_images[0]['id']}.jpg" if gallery_images else ""
        brand = _brand_payload()
        gallery_json = {
            "token": f"public-{slug}",
            "name": title or collection.get("name") or "Gallery",
            "photo_count": len(gallery_images),
            "date_range": date_range,
            "brand": brand,
            "download_size_label": "gallery-size copy",
            "images": gallery_images,
        }
        html = templates.env.get_template("share_gallery.html").render(
            not_found=False,
            locked=False,
            public_static=True,
            token=f"public-{slug}",
            collection_name=title or collection.get("name") or "Gallery",
            photo_count=len(gallery_images),
            date_range=date_range,
            brand=brand,
            gallery_json=gallery_json,
        )
        await asyncio.to_thread((work_target / "index.html").write_text, html, "utf-8")
        bundle_bytes, file_count = await asyncio.to_thread(_bundle_size, work_target)
        summary = BundleSummary(
            slug=slug,
            title=title or collection.get("name") or "Gallery",
            photo_count=len(gallery_images),
            date_range=date_range,
            cover=cover,
            bundle_bytes=bundle_bytes,
            file_count=file_count,
        )
        await asyncio.to_thread(_replace_bundle_dir, work_target, target)
        return summary
    except Exception:
        await asyncio.to_thread(shutil.rmtree, work_target, True)
        raise


async def build_published_node_bundle(
    *,
    node: dict,
    images: list[dict],
    destination: str | Path,
    templates: Jinja2Templates,
    thumbnails: Any,
) -> BundleSummary:
    """Build one destination node with the legacy gallery renderer."""

    image_ids = [int(image["id"]) for image in images]
    rows_by_id = {int(image["id"]): image for image in images}

    async def get_collection(_collection_id: int, **_kwargs):
        return {"id": int(node["id"]), "name": node["title"], "smart": False, "images": images}

    async def get_images_by_ids(_image_ids: list[int]):
        return rows_by_id

    async def collection_image_ids(_collection_id: int):
        return image_ids

    return await build_public_gallery_bundle(
        slug=node["slug"],
        title=node["title"],
        destination=destination,
        templates=templates,
        get_collection=get_collection,
        collection_id=int(node["id"]),
        get_images_by_ids=get_images_by_ids,
        collection_image_ids=collection_image_ids,
        thumbnails=thumbnails,
    )


async def export_website_tree(
    *,
    db_path: str,
    destination: str | Path,
    templates: Jinja2Templates,
    thumbnails: Any,
) -> dict:
    """Write the website destination tree, its node bundles, and manifest."""

    tree = await published_nodes.published_tree(db_path, "website")
    children: dict[int | None, list[dict]] = {}
    for node in tree["nodes"]:
        children.setdefault(node["parent_id"], []).append(node)

    root = Path(destination)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    images_by_node: dict[int, list[dict]] = {}

    async def write_subtree(node: dict, parent_path: Path) -> None:
        node_id = int(node["id"])
        images = await published_nodes.node_images(db_path, node_id) or []
        images_by_node[node_id] = images
        node_path = parent_path / node["slug"]
        await build_published_node_bundle(
            node=node,
            images=images,
            destination=node_path,
            templates=templates,
            thumbnails=thumbnails,
        )
        for child in children.get(node_id, []):
            await write_subtree(child, node_path)

    for root_node in children.get(None, []):
        await write_subtree(root_node, root)

    manifest = website_tree_manifest(tree, images_by_node)
    await asyncio.to_thread(_write_json_atomic, root / "manifest.json", manifest)
    return manifest


def website_tree_manifest(tree: dict, images_by_node: dict[int, list[dict]]) -> dict:
    """Return nested destination JSON with stable URLs and display metadata."""

    children: dict[int | None, list[dict]] = {}
    for node in tree.get("nodes") or []:
        children.setdefault(node.get("parent_id"), []).append(node)

    def build(node: dict, parent_slugs: tuple[str, ...]) -> dict:
        slugs = (*parent_slugs, node["slug"])
        url_root = "/g/" + "/".join(slugs)
        images = images_by_node.get(int(node["id"]), [])
        photos = []
        for index, image in enumerate(images, start=1):
            image_id = int(image["id"])
            photos.append(
                {
                    "id": image_id,
                    "urls": {
                        "thumb": f"{url_root}/thumb/sm/{image_id}.jpg",
                        "preview": f"{url_root}/img/{image_id}.jpg",
                        "full": f"{url_root}/img/{image_id}.jpg",
                        "download": f"{url_root}/img/{image_id}.jpg",
                    },
                    "caption": image.get("caption") or "",
                    "filename": image.get("filename") or f"Photo {image_id}",
                    "aspect_ratio": float(image.get("aspect_ratio") or 1.5),
                    "date_taken": image.get("date_taken"),
                    "display_label": f"Photo {index} of {len(images)}",
                }
            )
        return {
            "slug": node["slug"],
            "title": node["title"],
            "children": [build(child, slugs) for child in children.get(int(node["id"]), [])],
            "photos": photos,
        }

    roots = [build(node, ()) for node in children.get(None, [])]
    if len(roots) == 1:
        return roots[0]
    return {"slug": "", "title": "Website", "children": roots, "photos": []}


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        temp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


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
        try:
            await asyncio.to_thread(shutil.copyfile, cached_path, output_path)
            return
        except FileNotFoundError:
            pass

    read_entry = await asyncio.to_thread(thumbnails.fast_disk_read_entry, size, image_id, None)
    if read_entry is not None:
        _signature, data = read_entry
        await asyncio.to_thread(output_path.write_bytes, data)
        return

    try:
        data = await thumbnails.get_thumbnail(image["filepath"], size, image_id)
    except OSError as exc:
        raise GalleryImageUnavailable(f"could not read source for {size} derivative") from exc
    if not data:
        raise GalleryImageUnavailable(f"could not build {size} derivative")
    await asyncio.to_thread(output_path.write_bytes, data)


def _replace_bundle_dir(work_target: Path, target: Path) -> None:
    if not target.exists():
        os.replace(work_target, target)
        return

    _atomic_exchange_paths(work_target, target)
    shutil.rmtree(work_target, ignore_errors=True)


def _atomic_exchange_paths(source: Path, target: Path) -> None:
    if platform.system() != "Linux":
        raise RuntimeError("Atomic gallery republish requires Linux rename exchange support.")
    renameat2_syscall = _renameat2_syscall_number()
    if renameat2_syscall is None:
        raise RuntimeError("Atomic gallery republish requires renameat2 support.")

    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(
        ctypes.c_long(renameat2_syscall),
        ctypes.c_int(-100),
        ctypes.c_char_p(os.fsencode(source)),
        ctypes.c_int(-100),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_uint(2),
    )
    if result != 0:
        err = ctypes.get_errno()
        message = os.strerror(err) if err else "unknown error"
        raise OSError(err or errno.EIO, f"Could not atomically replace published gallery: {message}")


def _renameat2_syscall_number() -> int | None:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return 316
    if machine in {"aarch64", "arm64"}:
        return 276
    return None


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


def _brand_payload() -> dict:
    config = settings.get_settings()
    site_url = str(config.get("publish_site_base_url") or "").strip().rstrip("/")
    site_label = _site_label(site_url)
    name = str(config.get("share_brand_name") or "").strip()
    if not name:
        name = site_label or "Your photographer"
    return {"name": name, "site_url": site_url, "site_label": site_label}


def _site_label(site_url: str) -> str:
    if not site_url:
        return ""
    parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
    return (parsed.netloc or parsed.path).removeprefix("www.")


def _download_name(image_id: int, filename: str) -> str:
    basename = os.path.basename(filename or "").strip() or f"photo-{image_id}.jpg"
    return basename.replace('"', "").replace("'", "")
