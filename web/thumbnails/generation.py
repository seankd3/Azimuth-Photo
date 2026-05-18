import io
import os
import time

from PIL import Image, ImageOps


def load_raw_preview(filepath: str, max_target: int) -> Image.Image | None:
    import rawpy

    try:
        with rawpy.imread(filepath) as raw:
            thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            with Image.open(io.BytesIO(thumb.data)) as source:
                source.load()
                img = ImageOps.exif_transpose(source)
                if img is source:
                    img = source.copy()
        elif thumb.format == rawpy.ThumbFormat.BITMAP:
            img = Image.fromarray(thumb.data)
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
) -> Image.Image:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in raw_extensions:
        preview = load_raw_preview(filepath, max_target)
        if preview is not None:
            return preview

        import rawpy

        with rawpy.imread(filepath) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True)
        return Image.fromarray(rgb)

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
        img = load_source_image(filepath, max_target, prefer_draft=prefer_draft)
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
        img = load_source_image(filepath, md_size, prefer_draft=False)
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
