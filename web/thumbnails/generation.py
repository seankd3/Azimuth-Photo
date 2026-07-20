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


def _raw_open_target(filepath: str, source_data: bytes | None = None):
    """Path or BytesIO for rawpy.imread — caller must keep BytesIO alive."""
    if source_data is not None:
        return io.BytesIO(source_data)
    return filepath


def extract_embedded_raw_preview(
    filepath: str,
    *,
    source_data: bytes | None = None,
) -> Image.Image | None:
    """Extract the oriented embedded JPEG/bitmap from a RAW — no size gate.

    Library thumbs use this for every tier the embed can cover without
    upscaling (typically sm at 400 from a ~1024px DNG preview). Larger
    tiers demosaic once via ``demosaic_raw_for_thumbnail`` instead of
    running Develop's linear base + gzip path.

    When ``source_data`` is provided, decode from the in-RAM buffer (no disk).
    """
    import rawpy

    try:
        target = _raw_open_target(filepath, source_data)
        with rawpy.imread(target) as raw:
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
            return img
        if thumb.format == rawpy.ThumbFormat.BITMAP:
            img = Image.fromarray(thumb.data)
            return apply_raw_orientation(img, raw_flip)
    except Exception:
        return None
    return None


def load_raw_preview(
    filepath: str,
    max_target: int,
    *,
    source_data: bytes | None = None,
) -> Image.Image | None:
    """Return the embedded RAW preview only when it covers ``max_target``."""
    img = extract_embedded_raw_preview(filepath, source_data=source_data)
    if img is None:
        return None
    if max(img.width, img.height) >= max_target:
        return img
    img.close()
    return None


def demosaic_raw_for_thumbnail(
    filepath: str,
    max_target: int,
    *,
    source_data: bytes | None = None,
) -> Image.Image:
    """Fast LibRaw (or lossy-DNG) demosaic for library thumbs — not Develop.

    One demosaic per source. Prefers half_size whenever the half frame is
    within ~25% of the target (mild upscale beats a multi-second full
    demosaic for grid/loupe previews). Does not build or read the Develop
    ``.bin.gz`` base cache.

    When ``source_data`` is provided, demosaic from the in-RAM buffer (no disk).
    """
    import rawpy

    try:
        target = _raw_open_target(filepath, source_data)
        with rawpy.imread(target) as raw:
            sizes = raw.sizes
            long_side = max(sizes.width, sizes.height)
            # Full demosaic only when half-res would undershoot the target a lot
            # (small RAWs, or tiny embeds forcing a demosaic for sm alone).
            half_size = (long_side // 2) >= int(max_target * 0.75)
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                half_size=half_size,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            )
        # postprocess already applies the container rotation; do not rotate again.
        img = Image.fromarray(rgb)
        del rgb
        return img
    except Exception:
        # Lossy (JPEG XL) DNGs: decode a pyramid level and display-encode.
        from features.develop.lossydng import decode_lossy_dng, is_lossy_dng

        lossy_path = filepath
        tmp_path = None
        try:
            if source_data is not None:
                import tempfile

                tmp = tempfile.NamedTemporaryFile(
                    suffix=os.path.splitext(filepath)[1] or ".dng",
                    delete=False,
                )
                try:
                    tmp.write(source_data)
                    tmp.flush()
                finally:
                    tmp.close()
                tmp_path = tmp.name
                lossy_path = tmp_path
            if not is_lossy_dng(lossy_path):
                raise
            import numpy as _np

            arr, _meta = decode_lossy_dng(lossy_path, max_px=max(max_target, 512))
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
            return apply_raw_orientation(image, _exiftool_raw_flip(lossy_path))
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


def partition_raw_thumbnail_tiers(
    needed_sizes: list[str],
    *,
    sizes: dict[str, int],
    embedded_long_side: int,
) -> tuple[list[str], list[str]]:
    """Split tiers into those the embed can serve vs those needing demosaic."""
    covered: list[str] = []
    uncovered: list[str] = []
    for size in needed_sizes:
        if embedded_long_side >= sizes[size]:
            covered.append(size)
        else:
            uncovered.append(size)
    return covered, uncovered


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
        # Library / on-demand thumbs: embedded when large enough, else a single
        # LibRaw demosaic. Never touch Develop's base-cache gzip path here —
        # that stays lazy for the editor.
        preview = load_raw_preview(filepath, max_target)
        if preview is not None:
            return preview
        return demosaic_raw_for_thumbnail(filepath, max_target)

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
    """Decode from an in-RAM original buffer — no second spindle touch."""
    del prefer_draft  # draft sizing is applied for JPEG below; kept for API parity
    ext = os.path.splitext(filepath)[1].lower()
    if ext in raw_extensions:
        preview = load_raw_preview(filepath, max_target, source_data=data)
        if preview is not None:
            return preview
        return demosaic_raw_for_thumbnail(filepath, max_target, source_data=data)

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


def resize_to_long_side_exact(img: Image.Image, target_long_side: int) -> Image.Image:
    """Like ``resize_to_long_side`` but upscales when the source is smaller.

    Used for library demosaic frames that intentionally half-size decode
    (~3k) then stretch to the lg tier (3840) — mild upscale beats a full
    40–60MP demosaic for preview quality.
    """
    long_side = max(img.width, img.height)
    if long_side == target_long_side:
        return img.copy()
    if long_side > target_long_side:
        return resize_to_long_side(img, target_long_side)
    scale = target_long_side / long_side
    new_size = (
        max(1, int(round(img.width * scale))),
        max(1, int(round(img.height * scale))),
    )
    return img.resize(new_size, Image.BILINEAR)


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


def _encode_size_ladder(
    img: Image.Image,
    needed_sizes: list[str],
    *,
    image_id: int,
    filepath: str,
    hot: bool,
    sizes: dict[str, int],
    resize_to_long_side,
    encode_and_cache_thumbnail,
    build_source_signature=None,
    size_signatures: dict[str, str] | None = None,
    fast_disk_has=None,
    requested_size: str | None = None,
    allow_upscale: bool = False,
) -> tuple[Image.Image | None, bytes | None, int]:
    """Resize+encode ``needed_sizes`` (largest-first) from one decoded source.

    Returns (last_variant_or_None, requested_jpeg_or_None, written_count).
    Caller owns ``img`` and must close the returned variant if it differs.
    """
    resize = resize_to_long_side_exact if allow_upscale else resize_to_long_side
    current = img
    requested_data = None
    written_count = 0
    for size in needed_sizes:
        source_signature = (
            size_signatures[size]
            if size_signatures is not None
            else build_source_signature(filepath, size, image_id)
        )
        if fast_disk_has is not None and fast_disk_has(size, image_id, source_signature):
            continue

        variant = resize(current, sizes[size])
        variant, data, written = encode_and_cache_thumbnail(
            size,
            image_id,
            source_signature,
            variant,
            hot=hot,
        )
        if written:
            written_count += 1
        if requested_size is not None and size == requested_size:
            requested_data = data

        if current is not img:
            current.close()
        current = variant
    return (current if current is not img else None), requested_data, written_count


def _raw_library_sources(
    filepath: str,
    needed_sizes: list[str],
    *,
    sizes: dict[str, int],
    source_data: bytes | None = None,
) -> list[tuple[Image.Image, list[str], bool]]:
    """Build one or two RAW sources: embed for covered tiers, demosaic for the rest.

    Each tuple is ``(image, tiers, allow_upscale)``. Demosaic frames may be
    half-size and need mild upscale to hit lg. When ``source_data`` is set,
    both paths decode from RAM (HDD slot already released).
    """
    if not needed_sizes:
        return []

    embedded = extract_embedded_raw_preview(filepath, source_data=source_data)
    emb_long = max(embedded.width, embedded.height) if embedded is not None else 0
    covered, uncovered = partition_raw_thumbnail_tiers(
        needed_sizes,
        sizes=sizes,
        embedded_long_side=emb_long,
    )

    sources: list[tuple[Image.Image, list[str], bool]] = []
    if covered and embedded is not None:
        sources.append((embedded, covered, False))
    elif embedded is not None:
        embedded.close()

    if uncovered:
        max_target = max(sizes[size] for size in uncovered)
        sources.append(
            (
                demosaic_raw_for_thumbnail(filepath, max_target, source_data=source_data),
                uncovered,
                True,
            )
        )
    return sources


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
    on_source_loaded=None,
    raw_extensions: set[str] | frozenset[str] | None = None,
    source_data: bytes | None = None,
    load_source_image_from_bytes=None,
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
    owned_sources: list[Image.Image] = []

    try:
        ext = os.path.splitext(filepath)[1].lower()
        use_raw_split = raw_extensions is not None and ext in raw_extensions

        if use_raw_split:
            source_groups = _raw_library_sources(
                filepath,
                needed_sizes,
                sizes=sizes,
                source_data=source_data,
            )
            if not source_groups:
                return None
            for source_img, group_sizes, allow_upscale in source_groups:
                owned_sources.append(source_img)
                if on_source_loaded is not None and source_img is source_groups[0][0]:
                    on_source_loaded(source_data, source_img)
                queue_orientation(image_id, source_img)
                ladder_variant, group_requested, _written = _encode_size_ladder(
                    source_img,
                    group_sizes,
                    image_id=image_id,
                    filepath=filepath,
                    hot=hot,
                    sizes=sizes,
                    resize_to_long_side=resize_to_long_side,
                    build_source_signature=build_source_signature,
                    encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                    requested_size=requested_size,
                    allow_upscale=allow_upscale,
                )
                if group_requested is not None:
                    requested_data = group_requested
                if ladder_variant is not None:
                    ladder_variant.close()
        else:
            max_target = max(sizes[size] for size in needed_sizes)
            prefer_draft = max_target <= sizes["sm"]
            if source_data is not None and load_source_image_from_bytes is not None:
                img = load_source_image_from_bytes(
                    filepath,
                    source_data,
                    max_target,
                    prefer_draft=prefer_draft,
                )
            else:
                img = load_source_image(
                    filepath, max_target, prefer_draft=prefer_draft, image_id=image_id
                )
            if on_source_loaded is not None:
                on_source_loaded(source_data, img)
            queue_orientation(image_id, img)

            current, requested_data, _written = _encode_size_ladder(
                img,
                needed_sizes,
                image_id=image_id,
                filepath=filepath,
                hot=hot,
                sizes=sizes,
                resize_to_long_side=resize_to_long_side,
                build_source_signature=build_source_signature,
                encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                requested_size=requested_size,
            )
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
        if current is not None:
            try:
                current.close()
            except Exception:
                pass
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
        for source in owned_sources:
            try:
                source.close()
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
    on_source_loaded=None,
    raw_extensions: set[str] | frozenset[str] | None = None,
    source_data: bytes | None = None,
) -> dict:
    """Generate cache tiers for one image during a single warm-up pass.

    When ``source_data`` is provided (bulk harvest after the HDD slot released),
    all decode paths use the in-RAM buffer — no further spindle opens.
    """
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
    file_bytes = source_data
    owned_sources: list[Image.Image] = []
    try:
        if needed_sizes:
            ext = os.path.splitext(filepath)[1].lower()
            use_raw_split = (
                raw_extensions is not None
                and ext in raw_extensions
                and not (
                    full_item
                    and full_item.get("filepath") == filepath
                    and is_browser_displayable_original(filepath)
                )
            )

            read_started = monotonic_provider()
            if (
                full_item
                and full_item.get("filepath") == filepath
                and is_browser_displayable_original(filepath)
            ):
                max_target = max(sizes[size] for size in needed_sizes)
                prefer_draft = max_target <= sizes["sm"]
                if file_bytes is None:
                    with open(filepath, "rb") as f:
                        file_bytes = f.read()
                    metrics["read_seconds"] = max(0.0, monotonic_provider() - read_started)
                else:
                    # Caller already timed the spindle read under the HDD gate.
                    metrics["read_seconds"] = 0.0
                img = load_source_image_from_bytes(
                    filepath,
                    file_bytes,
                    max_target,
                    prefer_draft=prefer_draft,
                )
                metrics["source_bytes"] = len(file_bytes)
                if on_source_loaded is not None:
                    on_source_loaded(file_bytes, img)
                # Write the SSD original before the encode loop so we can drop
                # file_bytes instead of holding file bytes + decoded frames.
                full_id = int(full_item["id"])
                result = cache_full_image_bytes_sync(
                    full_item["filepath"],
                    full_id,
                    full_item["signature"],
                    file_bytes,
                    hot=False,
                    room_prechecked=True,
                )
                file_bytes = None
                if result != full_item["filepath"] and fast_disk_has(
                    full_tier,
                    full_id,
                    full_item["signature"],
                ):
                    metrics["originals_written"] = 1
                full_item = None
                metrics["source_reads"] = 1
                queue_orientation(image_id, img)

                process_started = monotonic_provider()
                current, _requested, written = _encode_size_ladder(
                    img,
                    needed_sizes,
                    image_id=image_id,
                    filepath=filepath,
                    hot=hot,
                    sizes=sizes,
                    resize_to_long_side=resize_to_long_side,
                    encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                    size_signatures=size_signatures,
                    fast_disk_has=fast_disk_has,
                )
                metrics["thumbnails_written"] += written
                metrics["decode_encode_seconds"] = max(
                    0.0, monotonic_provider() - process_started
                )
            elif use_raw_split:
                # Embedded preview covers sm (and any tier ≤ embed long-side);
                # demosaic once for the rest. Never builds Develop .bin.gz.
                source_groups = _raw_library_sources(
                    filepath,
                    needed_sizes,
                    sizes=sizes,
                    source_data=file_bytes,
                )
                if file_bytes is None:
                    metrics["read_seconds"] = max(0.0, monotonic_provider() - read_started)
                    metrics["source_bytes"] = int(source_bytes or 0)
                else:
                    metrics["read_seconds"] = 0.0
                    metrics["source_bytes"] = len(file_bytes)
                metrics["source_reads"] = 1 if source_groups else 0
                process_started = monotonic_provider()
                for index, (source_img, group_sizes, allow_upscale) in enumerate(source_groups):
                    owned_sources.append(source_img)
                    if on_source_loaded is not None and index == 0:
                        on_source_loaded(file_bytes, source_img)
                    queue_orientation(image_id, source_img)
                    ladder_variant, _requested, written = _encode_size_ladder(
                        source_img,
                        group_sizes,
                        image_id=image_id,
                        filepath=filepath,
                        hot=hot,
                        sizes=sizes,
                        resize_to_long_side=resize_to_long_side,
                        encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                        size_signatures=size_signatures,
                        fast_disk_has=fast_disk_has,
                        allow_upscale=allow_upscale,
                    )
                    metrics["thumbnails_written"] += written
                    if ladder_variant is not None:
                        ladder_variant.close()
                metrics["decode_encode_seconds"] = max(
                    0.0, monotonic_provider() - process_started
                )
            else:
                max_target = max(sizes[size] for size in needed_sizes)
                prefer_draft = max_target <= sizes["sm"]
                if file_bytes is not None:
                    img = load_source_image_from_bytes(
                        filepath,
                        file_bytes,
                        max_target,
                        prefer_draft=prefer_draft,
                    )
                    metrics["source_bytes"] = len(file_bytes)
                    metrics["read_seconds"] = 0.0
                    if on_source_loaded is not None:
                        on_source_loaded(file_bytes, img)
                else:
                    img = load_source_image(
                        filepath, max_target, prefer_draft=prefer_draft, image_id=image_id
                    )
                    metrics["source_bytes"] = int(source_bytes or 0)
                    metrics["read_seconds"] = max(0.0, monotonic_provider() - read_started)
                    if on_source_loaded is not None:
                        on_source_loaded(None, img)
                metrics["source_reads"] = 1
                queue_orientation(image_id, img)

                process_started = monotonic_provider()
                current, _requested, written = _encode_size_ladder(
                    img,
                    needed_sizes,
                    image_id=image_id,
                    filepath=filepath,
                    hot=hot,
                    sizes=sizes,
                    resize_to_long_side=resize_to_long_side,
                    encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                    size_signatures=size_signatures,
                    fast_disk_has=fast_disk_has,
                )
                metrics["thumbnails_written"] += written
                metrics["decode_encode_seconds"] = max(
                    0.0, monotonic_provider() - process_started
                )

        if full_item:
            full_id = int(full_item["id"])
            if file_bytes is not None:
                full_started = monotonic_provider()
                result = cache_full_image_bytes_sync(
                    full_item["filepath"],
                    full_id,
                    full_item["signature"],
                    file_bytes,
                    hot=False,
                    room_prechecked=True,
                )
                full_seconds = max(0.0, monotonic_provider() - full_started)
                # Bytes already in RAM — this is an SSD write, not a spindle read.
                metrics["decode_encode_seconds"] += full_seconds
                if metrics["source_reads"] <= 0:
                    metrics["source_reads"] = 1
                    metrics["source_bytes"] = max(metrics["source_bytes"], len(file_bytes))
            else:
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
        if current is not None:
            try:
                current.close()
            except Exception:
                pass
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
        for source in owned_sources:
            try:
                source.close()
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
