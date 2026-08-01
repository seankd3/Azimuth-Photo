import io
import os
import time

from PIL import Image, ImageOps
from core import pil_limits  # noqa: F401  # disables the decompression-bomb limit process-wide

from raw_thumb_ops import (
    apply_raw_orientation,
    demosaic_raw_for_thumbnail,
    demosaic_tier_jpegs as _demosaic_tier_jpegs_local,
    exiftool_raw_flip as _exiftool_raw_flip,
    resize_to_long_side,  # noqa: F401 - compatibility facade for thumbnails.__init__
    resize_to_long_side_exact,
    thumbnail_jpeg_bytes,
)


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


# Still-frame thumbnail pipeline only. Videos live in the catalog for
# playback/backup, but Pillow cannot decode them — skip quietly so bulk
# pregen does not thrash retries on every .mp4/.mov in Snapshots.
VIDEO_THUMB_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"})


# A name is not a format. This archive holds 1,306 files named .CR2 that are
# really full-resolution JPEGs: they open fine in the viewer, LibRaw refuses
# them as "not a raw file", and every RAW branch below used to be chosen by
# extension alone — so those photos could never get a thumbnail. Decide once,
# by the first bytes. When the original is already in RAM this costs nothing;
# otherwise it is a 3-byte read of a file that is about to be read in full.
_JPEG_MAGIC = b"\xff\xd8\xff"


def is_raw_original(
    filepath: str,
    ext: str,
    raw_extensions: set[str] | None,
    *,
    source_data: bytes | None = None,
) -> bool:
    """Whether RAW decoding applies — by content, not by file name."""

    if not raw_extensions or ext not in raw_extensions:
        return False
    if source_data is not None:
        head = source_data[:3]
    else:
        try:
            with open(filepath, "rb") as handle:
                head = handle.read(3)
        except OSError:
            head = b""  # unreadable: let the RAW path report the real error
    return not head.startswith(_JPEG_MAGIC)


def _open_by_content(filepath: str, max_target: int) -> Image.Image:
    """Decode a still image by what it contains, ignoring what it is named.

    Always at the smallest scale that still oversamples the thumbnail being
    built. ``draft`` never returns fewer pixels than asked for and is a no-op
    for formats that cannot scale on decode, so there is nothing to decide:
    the archive holds photos up to 527 megapixels — 1.5GB of RGB each — and
    decoding one of those in full to make a 400px tile is how the hub grew to
    6.6GB and started swapping.
    """

    with Image.open(filepath) as source:
        source.draft("RGB", (max_target * 2, max_target * 2))
        source.load()
        img = ImageOps.exif_transpose(source)
        if img is source:
            img = source.copy()
        return img


def load_source_image(
    filepath: str,
    max_target: int,
    *,
    jpeg_extensions: set[str],
    raw_extensions: set[str],
    image_id: int | None = None,
) -> Image.Image:
    ext = os.path.splitext(filepath)[1].lower()
    if is_raw_original(filepath, ext, raw_extensions):
        # Library / on-demand thumbs: embedded when large enough, else a single
        # LibRaw demosaic. Never touch Develop's base-cache gzip path here —
        # that stays lazy for the editor.
        preview = load_raw_preview(filepath, max_target)
        if preview is not None:
            return preview
        return demosaic_raw_for_thumbnail(filepath, max_target)

    return _open_by_content(filepath, max_target)


def _open_bytes_by_content(data: bytes, max_target: int) -> Image.Image:
    """Decode an in-RAM original by content, at the scale the thumbnail needs."""

    with Image.open(io.BytesIO(data)) as source:
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
    *,
    jpeg_extensions: set[str],
    raw_extensions: set[str],
) -> Image.Image:
    """Decode from an in-RAM original buffer — no second spindle touch."""
    ext = os.path.splitext(filepath)[1].lower()
    if is_raw_original(filepath, ext, raw_extensions, source_data=data):
        preview = load_raw_preview(filepath, max_target, source_data=data)
        if preview is not None:
            return preview
        return demosaic_raw_for_thumbnail(filepath, max_target, source_data=data)

    return _open_bytes_by_content(data, max_target)


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
    variant: Image.Image | None,
    *,
    hot: bool,
    thumb_quality: int,
    memory_put,
    write_thumbnail_to_disk,
    thumbnail_retry_after: dict,
    preencoded: bytes | None = None,
) -> tuple[Image.Image | None, bytes, bool]:
    if preencoded is not None:
        data = preencoded
    else:
        if variant is None:
            raise ValueError("encode_and_cache_thumbnail requires variant or preencoded")
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
    resize_to_long_side,  # noqa: F811 - injected facade dependency
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
    demosaic: bool = True,
) -> tuple[list[tuple[Image.Image, list[str], bool]], list[str]]:
    """Split RAW work: embed for covered tiers; optionally demosaic uncovered.

    Returns ``(source_groups, uncovered_tiers)``. When ``demosaic`` is False
    (process-pool path), uncovered tiers are returned for the caller to run
    out-of-process — never demosaic on the calling thread.
    """
    if not needed_sizes:
        return [], []

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

    if uncovered and demosaic:
        max_target = max(sizes[size] for size in uncovered)
        sources.append(
            (
                demosaic_raw_for_thumbnail(filepath, max_target, source_data=source_data),
                uncovered,
                True,
            )
        )
        return sources, []
    return sources, uncovered


def run_demosaic_tier_jpegs(
    filepath: str,
    uncovered: list[str],
    *,
    sizes: dict[str, int],
    thumb_quality: int,
    source_data: bytes | None = None,
    interactive: bool = False,
) -> dict:
    """Demosaic uncovered tiers via process pool (GIL bypass) or in-process."""
    if not uncovered:
        return {"jpegs": {}, "width": 0, "height": 0}
    from . import demosaic_pool

    if demosaic_pool.is_enabled():
        return demosaic_pool.run_demosaic_tier_jpegs(
            filepath,
            uncovered,
            sizes=sizes,
            thumb_quality=thumb_quality,
            source_data=source_data,
            interactive=interactive,
        )
    return _demosaic_tier_jpegs_local(
        filepath,
        list(uncovered),
        dict(sizes),
        int(thumb_quality),
        source_data=source_data,
    )


def cache_preencoded_thumbnails(
    jpeg_by_size: dict[str, bytes],
    *,
    image_id: int,
    filepath: str,
    hot: bool,
    sizes: dict[str, int],
    encode_and_cache_thumbnail,
    build_source_signature=None,
    size_signatures: dict[str, str] | None = None,
    fast_disk_has=None,
    requested_size: str | None = None,
) -> tuple[bytes | None, int]:
    """Cache JPEG bytes from the demosaic process pool (no re-encode)."""
    requested_data = None
    written_count = 0
    ordered = sorted(jpeg_by_size, key=lambda tier: sizes[tier], reverse=True)
    for size in ordered:
        data = jpeg_by_size[size]
        source_signature = (
            size_signatures[size]
            if size_signatures is not None
            else build_source_signature(filepath, size, image_id)
        )
        if fast_disk_has is not None and fast_disk_has(size, image_id, source_signature):
            if requested_size is not None and size == requested_size:
                requested_data = data
            continue
        _variant, stored, written = encode_and_cache_thumbnail(
            size,
            image_id,
            source_signature,
            None,
            hot=hot,
            preencoded=data,
        )
        if written:
            written_count += 1
        if requested_size is not None and size == requested_size:
            requested_data = stored
    return requested_data, written_count


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
    resize_to_long_side,  # noqa: F811 - injected facade dependency
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
    thumb_quality: int = 92,
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
        use_raw_split = is_raw_original(filepath, ext, raw_extensions, source_data=source_data)

        if use_raw_split:
            # Embedded preview stays on this thread (releases GIL). Demosaic
            # goes through the process pool so rawpy.postprocess can parallelize.
            source_groups, uncovered = _raw_library_sources(
                filepath,
                needed_sizes,
                sizes=sizes,
                source_data=source_data,
                demosaic=False,
            )
            if not source_groups and not uncovered:
                return None
            for index, (source_img, group_sizes, allow_upscale) in enumerate(source_groups):
                owned_sources.append(source_img)
                if on_source_loaded is not None and index == 0:
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
            if uncovered:
                pool_result = run_demosaic_tier_jpegs(
                    filepath,
                    uncovered,
                    sizes=sizes,
                    thumb_quality=thumb_quality,
                    source_data=source_data,
                    interactive=True,
                )
                jpegs = pool_result.get("jpegs") or {}
                if jpegs:
                    width = int(pool_result.get("width") or 0)
                    height = int(pool_result.get("height") or 0)
                    if not source_groups and width > 0 and height > 0:
                        orient = Image.new("RGB", (width, height))
                        try:
                            if on_source_loaded is not None:
                                on_source_loaded(source_data, orient)
                            queue_orientation(image_id, orient)
                        finally:
                            orient.close()
                    group_requested, _written = cache_preencoded_thumbnails(
                        jpegs,
                        image_id=image_id,
                        filepath=filepath,
                        hot=hot,
                        sizes=sizes,
                        encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                        build_source_signature=build_source_signature,
                        requested_size=requested_size,
                    )
                    if group_requested is not None:
                        requested_data = group_requested
        else:
            max_target = max(sizes[size] for size in needed_sizes)
            if source_data is not None and load_source_image_from_bytes is not None:
                img = load_source_image_from_bytes(
                    filepath,
                    source_data,
                    max_target,
                )
            else:
                img = load_source_image(
                    filepath, max_target, image_id=image_id
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
    resize_to_long_side,  # noqa: F811 - injected facade dependency
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
    thumb_quality: int = 92,
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

    ext = os.path.splitext(filepath)[1].lower()
    if ext in VIDEO_THUMB_EXTENSIONS:
        # Not a still image — do not count as a source-read failure or schedule retries.
        log(f"Thumbnail skip video (no still pipeline): {filepath}")
        return metrics

    img = None
    current = None
    file_bytes = source_data
    owned_sources: list[Image.Image] = []
    try:
        if needed_sizes:
            use_raw_split = (
                is_raw_original(filepath, ext, raw_extensions, source_data=file_bytes)
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
                # Embedded preview covers sm (thread pool — releases GIL);
                # demosaic for the rest runs in the process pool (GIL bypass).
                source_groups, uncovered = _raw_library_sources(
                    filepath,
                    needed_sizes,
                    sizes=sizes,
                    source_data=file_bytes,
                    demosaic=False,
                )
                if file_bytes is None:
                    metrics["read_seconds"] = max(0.0, monotonic_provider() - read_started)
                    metrics["source_bytes"] = int(source_bytes or 0)
                else:
                    metrics["read_seconds"] = 0.0
                    metrics["source_bytes"] = len(file_bytes)
                metrics["source_reads"] = 1 if (source_groups or uncovered) else 0
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
                if uncovered:
                    pool_result = run_demosaic_tier_jpegs(
                        filepath,
                        uncovered,
                        sizes=sizes,
                        thumb_quality=thumb_quality,
                        source_data=file_bytes,
                        interactive=False,
                    )
                    jpegs = pool_result.get("jpegs") or {}
                    if jpegs:
                        width = int(pool_result.get("width") or 0)
                        height = int(pool_result.get("height") or 0)
                        if not source_groups and width > 0 and height > 0:
                            orient = Image.new("RGB", (width, height))
                            try:
                                if on_source_loaded is not None:
                                    on_source_loaded(file_bytes, orient)
                                queue_orientation(image_id, orient)
                            finally:
                                orient.close()
                        _requested, written = cache_preencoded_thumbnails(
                            jpegs,
                            image_id=image_id,
                            filepath=filepath,
                            hot=hot,
                            sizes=sizes,
                            encode_and_cache_thumbnail=encode_and_cache_thumbnail,
                            size_signatures=size_signatures,
                            fast_disk_has=fast_disk_has,
                        )
                        metrics["thumbnails_written"] += written
                metrics["decode_encode_seconds"] = max(
                    0.0, monotonic_provider() - process_started
                )
            else:
                max_target = max(sizes[size] for size in needed_sizes)
                if file_bytes is not None:
                    img = load_source_image_from_bytes(
                        filepath,
                        file_bytes,
                        max_target,
                    )
                    metrics["source_bytes"] = len(file_bytes)
                    metrics["read_seconds"] = 0.0
                    if on_source_loaded is not None:
                        on_source_loaded(file_bytes, img)
                else:
                    img = load_source_image(
                        filepath, max_target, image_id=image_id
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
    resize_to_long_side,  # noqa: F811 - injected facade dependency
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
        img = load_source_image(filepath, md_size, image_id=image_id)
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
