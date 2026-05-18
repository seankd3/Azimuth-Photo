import io
import os

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
