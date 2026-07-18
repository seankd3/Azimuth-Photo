import io
import json
import os
import subprocess
import time

from PIL import Image, ImageOps
from core import pil_limits  # noqa: F401  # disables the decompression-bomb limit process-wide



def apply_raw_orientation(img: Image.Image, flip: int) -> Image.Image:
    """Apply libraw's container orientation to an untagged RAW image."""
    transforms = {
        1: Image.Transpose.FLIP_LEFT_RIGHT,
        2: Image.Transpose.FLIP_TOP_BOTTOM,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.TRANSPOSE,
        5: Image.Transpose.ROTATE_90,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
    }
    transform = transforms.get(int(flip or 0))
    return img.transpose(transform) if transform is not None else img


def _exiftool_raw_flip(filepath: str) -> int:
    """Map the container Orientation tag to libraw's flip values when needed."""
    try:
        result = subprocess.run(
            ["exiftool", "-n", "-j", "-Orientation", filepath],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        orientation = int((json.loads(result.stdout) or [{}])[0].get("Orientation", 1))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, IndexError):
        return 0
    return {2: 1, 3: 3, 4: 2, 5: 4, 6: 6, 7: 7, 8: 5}.get(orientation, 0)


def _raw_preview_flip(raw, filepath: str) -> int:
    flip = getattr(getattr(raw, "sizes", None), "flip", None)
    return int(flip) if flip is not None else _exiftool_raw_flip(filepath)


def load_raw_preview(filepath: str, max_target: int) -> Image.Image | None:
    import rawpy

    try:
        with rawpy.imread(filepath) as raw:
            thumb = raw.extract_thumb()
            raw_flip = _raw_preview_flip(raw, filepath)
        if thumb.format == rawpy.ThumbFormat.JPEG:
            with Image.open(io.BytesIO(thumb.data)) as source:
                source.load()
                has_embedded_orientation = source.getexif().get(274, 1) != 1
                img = ImageOps.exif_transpose(source)
                if img is source:
                    img = source.copy()
                if not has_embedded_orientation:
                    img = apply_raw_orientation(img, raw_flip)
        elif thumb.format == rawpy.ThumbFormat.BITMAP:
            img = Image.fromarray(thumb.data)
            img = apply_raw_orientation(img, raw_flip)
        else:
            return None

        if max(img.width, img.height) >= max_target:
            return img
        img.close()
    except Exception:
        return None
    return None


def load_source_image(
    filepath: str,
    max_target: int,
    prefer_draft: bool,
    *,
    jpeg_extensions: set[str],
    raw_extensions: set[str],
    image_id: int | None = None,
) -> Image.Image:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in raw_extensions:
        preview = load_raw_preview(filepath, max_target)
        if preview is not None:
            return preview

        if image_id is not None:
            # Same color truth as the Develop canvas (camera-space WB,
            # dual-illuminant matrices, saved edits); LibRaw only as last resort.
            try:
                from features.develop.render import render_display_preview

                developed = render_display_preview(image_id, filepath, max_px=max_target)
            except Exception:
                developed = None
            if developed is not None:
                return developed

        import rawpy

        try:
            # half_size when the caller only needs browse-tier long edges — full
            # demosaic of a 40–60MP RAW is the bulk of the overnight RSS spike.
            half_size = max_target <= 1920
            with rawpy.imread(filepath) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    half_size=half_size,
                )
            # postprocess already applies the container rotation; do not rotate again.
            img = Image.fromarray(rgb)
            del rgb
            return img
        except Exception:
            # Lossy (JPEG XL) DNGs: decode a pyramid level and display-encode.
            from features.develop.lossydng import decode_lossy_dng, is_lossy_dng

            if not is_lossy_dng(filepath):
                raise
            import numpy as _np

            arr, _meta = decode_lossy_dng(filepath, max_px=max(max_target, 512))
            linear = arr.astype(_np.float32) / 65535.0
            del arr
            encoded = _np.where(
                linear <= 0.0031308,
                linear * 12.92,
                1.055 * _np.power(_np.clip(linear, 0.0, 1.0), 1.0 / 2.4) - 0.055,
            )
            del linear
            image = Image.fromarray((_np.clip(encoded, 0.0, 1.0) * 255.0 + 0.5).astype(_np.uint8))
            del encoded
            return apply_raw_orientation(image, _exiftool_raw_flip(filepath))

    with Image.open(filepath) as source:
        if ext in jpeg_extensions:
            source.draft("RGB", (max_target * 2, max_target * 2))
        source.load()
        img = ImageOps.exif_transpose(source)
        if img is source:
            img = source.copy()
        return img


def load_source_image_from_bytes(
    filepath: str,
    data: bytes,
    max_target: int,
    prefer_draft: bool,
    *,
    jpeg_extensions: set[str],
    raw_extensions: set[str],
) -> Image.Image:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in raw_extensions:
        return load_source_image(
            filepath,
            max_target,
            prefer_draft,
            jpeg_extensions=jpeg_extensions,
            raw_extensions=raw_extensions,
        )

    with Image.open(io.BytesIO(data)) as source:
        if ext in jpeg_extensions:
            source.draft("RGB", (max_target * 2, max_target * 2))
        source.load()
        img = ImageOps.exif_transpose(source)
        if img is source:
            img = source.copy()
        return img


def resize_to_long_side(img: Image.Image, target_long_side: int) -> Image.Image:
    long_side = max(img.width, img.height)
    if long_side <= target_long_side:
        return img.copy()

    factor = max(1, long_side // (target_long_side * 2))
    if factor > 1:
        img = img.reduce(factor)
        long_side = max(img.width, img.height)

    scale = target_long_side / long_side
    new_size = (
        max(1, int(round(img.width * scale))),
        max(1, int(round(img.height * scale))),
    )
    resample = Image.BILINEAR if target_long_side >= 1920 else Image.LANCZOS
    return img.resize(new_size, resample)


def thumbnail_jpeg_bytes(variant: Image.Image, size: str, quality: int) -> bytes:
    buf = io.BytesIO()
    variant.save(
        buf,
        "JPEG",
        quality=quality,
        progressive=(size != "sm"),
    )
    return buf.getvalue()


def queue_orientation(image_id: int, img: Image.Image, *, orientation_lock, orientation_queue) -> None:
    orientation = "landscape" if img.width >= img.height else "portrait"
    aspect_ratio = round(img.width / img.height, 4) if img.height > 0 else 1.5
    with orientation_lock:
        orientation_queue[image_id] = (orientation, aspect_ratio)


async def flush_orientation_updates(
    *,
    orientation_lock,
    orientation_queue,
    batch_set_orientations,
) -> None:
    with orientation_lock:
        pending = dict(orientation_queue)
        orientation_queue.clear()

    if not pending:
        return

    await batch_set_orientations(
        [
            (orientation, aspect_ratio, image_id)
            for image_id, (orientation, aspect_ratio) in pending.items()
        ]
    )


def encode_and_cache_thumbnail(
    size: str,
    image_id: int,
    source_signature: str,
    variant: Image.Image,
    *,
    hot: bool,
    thumb_quality: int,
    memory_put,
    write_thumbnail_to_disk,
    thumbnail_retry_after: dict,
) -> tuple[Image.Image, bytes, bool]:
    if variant.mode != "RGB":
        converted = variant.convert("RGB")
        variant.close()
        variant = converted

    data = thumbnail_jpeg_bytes(variant, size, thumb_quality)
    memory_put(size, image_id, source_signature, data)
    written = write_thumbnail_to_disk(size, image_id, source_signature, data, hot=hot)
    thumbnail_retry_after.pop((size, image_id, source_signature), None)
    return variant, data, written


def planned_thumbnail_sizes(
    filepath: str,
    image_id: int,
    requested_size: str,
    *,
    include_smaller_tiers: bool,
    allow_stale_fallback: bool,
    source_missing,
    sizes: dict[str, int],
    thumb_tiers: tuple[str, ...],
    disk_allocations: dict[str, int],
    build_source_signature,
    thumbnail_retry_after: dict,
    memory_get,
    fast_disk_has,
    get_disk_entry,
    now_provider=time.time,
) -> list[str]:
    if source_missing(filepath):
        return []

    needed = []
    now = now_provider()
    if include_smaller_tiers:
        requested_long_side = sizes.get(requested_size, 0)
        candidate_sizes = tuple(
            size for size in thumb_tiers
            if sizes[size] <= requested_long_side
            and (size == requested_size or disk_allocations.get(size, 0) > 0)
        )
    else:
        candidate_sizes = (requested_size,)
    for size in candidate_sizes:
        if size not in thumb_tiers:
            continue
        source_signature = build_source_signature(filepath, size, image_id)
        if thumbnail_retry_after.get((size, image_id, source_signature), 0) > now:
            continue
        if memory_get(size, image_id, source_signature) is not None:
            continue
        if fast_disk_has(size, image_id, source_signature):
            continue
        if allow_stale_fallback and fast_disk_has(size, image_id):
            continue
        if get_disk_entry(size, image_id, source_signature, touch=False) is not None:
            continue
        needed.append(size)
    if include_smaller_tiers:
        return sorted(needed, key=lambda tier: sizes[tier], reverse=True)
    return needed


def generate_missing_thumbnails(
    filepath: str,
    requested_size: str,
    image_id: int,
    *,
    include_smaller_tiers: bool,
    hot: bool,
    allow_stale_fallback: bool,
    planned_thumbnail_sizes,
    sizes: dict[str, int],
    load_source_image,
    queue_orientation,
    resize_to_long_side,
    build_source_signature,
    encode_and_cache_thumbnail,
    mark_source_missing_from_error,
    thumbnail_retry_after: dict,
    thumbnail_retry_seconds: float,
    now_provider=time.time,
    log=print,
):
    needed_sizes = planned_thumbnail_sizes(
        filepath,
        image_id,
        requested_size,
        include_smaller_tiers=include_smaller_tiers,
        allow_stale_fallback=allow_stale_fallback,
    )
    if not needed_sizes:
        return None

    img = None
    current = None
    requested_data = None

    try:
        max_target = max(sizes[size] for size in needed_sizes)
        prefer_draft = max_target <= sizes["sm"]
        img = load_source_image(filepath, max_target, prefer_draft=prefer_draft, image_id=image_id)
        queue_orientation(image_id, img)

        current = img
        for size in needed_sizes:
            variant = resize_to_long_side(current, sizes[size])
            source_signature = build_source_signature(filepath, size, image_id)
            variant, data, _written = encode_and_cache_thumbnail(
                size,
                image_id,
                source_signature,
                variant,
                hot=hot,
            )
            if size == requested_size:
                requested_data = data

            if current is not img:
                current.close()
            current = variant
    except Exception as exc:
        source_missing = mark_source_missing_from_error(filepath, image_id, exc)
        if not source_missing:
            retry_until = now_provider() + thumbnail_retry_seconds
            for size in needed_sizes:
                source_signature = build_source_signature(filepath, size, image_id)
                thumbnail_retry_after[(size, image_id, source_signature)] = retry_until
            log(f"Thumbnail error for {filepath}: {exc}")
        return None
    finally:
        if current is not None and current is not img:
            try:
                current.close()
            except Exception:
                pass
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
    return requested_data


def generate_thumbnail_set(
    filepath: str,
    image_id: int,
    size_signatures: dict[str, str],
    *,
    source_bytes: int | None = None,
    full_item: dict | None = None,
    hot: bool = False,
    sizes: dict[str, int],
    thumb_tiers: tuple[str, ...],
    full_tier: str,
    load_source_image,
    load_source_image_from_bytes,
    queue_orientation,
    resize_to_long_side,
    encode_and_cache_thumbnail,
    cache_full_image_sync,
    cache_full_image_bytes_sync,
    mark_source_missing_from_error,
    fast_disk_has,
    is_browser_displayable_original,
    thumbnail_retry_after: dict,
    thumbnail_retry_seconds: float,
    now_provider=time.time,
    monotonic_provider=time.monotonic,
    log=print,
) -> dict:
    """Generate cache tiers for one image during a single warm-up pass."""
    needed_sizes = [
        size for size in sorted(size_signatures, key=lambda tier: sizes[tier], reverse=True)
        if size in thumb_tiers
    ]
    metrics = {
        "source_reads": 0,
        "thumbnails_written": 0,
        "source_bytes": 0,
        "read_seconds": 0.0,
        "decode_encode_seconds": 0.0,
        "source_read_failures": 0,
        "originals_written": 0,
    }
    if not needed_sizes and not full_item:
        return metrics

    img = None
    current = None
    source_data = None
    try:
        if needed_sizes:
            max_target = max(sizes[size] for size in needed_sizes)
            prefer_draft = max_target <= sizes["sm"]
            read_started = monotonic_provider()
            if (
                full_item
                and full_item.get("filepath") == filepath
                and is_browser_displayable_original(filepath)
            ):
                with open(filepath, "rb") as f:
                    source_data = f.read()
                img = load_source_image_from_bytes(
                    filepath,
                    source_data,
                    max_target,
                    prefer_draft=prefer_draft,
                )
                metrics["source_bytes"] = len(source_data)
                # Write the SSD original before the encode loop so we can drop
                # source_data instead of holding file bytes + decoded frames.
                full_id = int(full_item["id"])
                result = cache_full_image_bytes_sync(
                    full_item["filepath"],
                    full_id,
                    full_item["signature"],
                    source_data,
                    hot=False,
                    room_prechecked=True,
                )
                source_data = None
                if result != full_item["filepath"] and fast_disk_has(
                    full_tier,
                    full_id,
                    full_item["signature"],
                ):
                    metrics["originals_written"] = 1
                full_item = None
            else:
                img = load_source_image(filepath, max_target, prefer_draft=prefer_draft, image_id=image_id)
                metrics["source_bytes"] = int(source_bytes or 0)
            metrics["read_seconds"] = max(0.0, monotonic_provider() - read_started)
            metrics["source_reads"] = 1
            queue_orientation(image_id, img)

            process_started = monotonic_provider()
            current = img
            for size in needed_sizes:
                source_signature = size_signatures[size]
                if fast_disk_has(size, image_id, source_signature):
                    continue

                variant = resize_to_long_side(current, sizes[size])
                variant, _data, written = encode_and_cache_thumbnail(
                    size,
                    image_id,
                    source_signature,
                    variant,
                    hot=hot,
                )
                if written:
                    metrics["thumbnails_written"] += 1

                if current is not img:
                    current.close()
                current = variant
            metrics["decode_encode_seconds"] = max(0.0, monotonic_provider() - process_started)

        if full_item:
            full_id = int(full_item["id"])
            full_started = monotonic_provider()
            result = cache_full_image_sync(
                full_item["filepath"],
                full_id,
                full_item["signature"],
                hot=False,
                room_prechecked=True,
            )
            full_seconds = max(0.0, monotonic_provider() - full_started)
            if result != full_item["filepath"]:
                metrics["read_seconds"] += full_seconds
                metrics["source_bytes"] += int(full_item.get("source_size") or 0)
                if metrics["source_reads"] <= 0:
                    metrics["source_reads"] = 1
            if result != full_item["filepath"] and fast_disk_has(
                full_tier,
                full_id,
                full_item["signature"],
            ):
                metrics["originals_written"] = 1
    except Exception as exc:
        source_missing = mark_source_missing_from_error(filepath, image_id, exc)
        if not source_missing:
            retry_until = now_provider() + thumbnail_retry_seconds
            for size in needed_sizes:
                source_signature = size_signatures[size]
                thumbnail_retry_after[(size, image_id, source_signature)] = retry_until
            log(f"Thumbnail bulk error for {filepath}: {exc}")
        metrics["source_read_failures"] = 1
    finally:
        if current is not None and current is not img:
            try:
                current.close()
            except Exception:
                pass
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
    return metrics


def load_embedding_image(
    filepath: str,
    image_id: int,
    *,
    require_cached: bool,
    sizes: dict[str, int],
    memory_get_fast,
    fast_disk_read,
    build_source_signature,
    memory_get,
    read_disk_thumbnail,
    load_source_image,
    resize_to_long_side,
) -> Image.Image | None:
    """Load an embedding input, preferring cached md thumbnails over originals."""
    data = memory_get_fast("md", image_id)
    if data is None:
        data = fast_disk_read("md", image_id)

    if data is None:
        source_signature = build_source_signature(filepath, "md", image_id)
        data = memory_get("md", image_id, source_signature)
        if data is None:
            data = read_disk_thumbnail("md", image_id, source_signature)

    if data is not None:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            img = source.copy()
        if img.mode != "RGB":
            converted = img.convert("RGB")
            img.close()
            img = converted
        return img

    if require_cached:
        return None

    try:
        md_size = sizes["md"]
        img = load_source_image(filepath, md_size, prefer_draft=False, image_id=image_id)
        resized = resize_to_long_side(img, md_size)
        if resized is not img:
            img.close()
        img = resized
        if img.mode != "RGB":
            converted = img.convert("RGB")
            img.close()
            img = converted
        return img
    except Exception:
        return None
