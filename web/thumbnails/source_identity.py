"""Source identity, signature, and HTTP cache helpers for thumbnails."""

import hashlib
import os
import time
from collections import OrderedDict
from collections.abc import Callable, MutableMapping

SOURCE_STAT_CACHE_MAX = 25000
SOURCE_STAT_CACHE_TTL_SECONDS = 60.0
source_stat_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


def get_source_bits(
    filepath: str,
    *,
    stat_cache: MutableMapping[str, tuple[float, str]] | None = None,
    ttl_seconds: float = SOURCE_STAT_CACHE_TTL_SECONDS,
    max_entries: int = SOURCE_STAT_CACHE_MAX,
    now_fn: Callable[[], float] = time.monotonic,
) -> str:
    """Cache os.stat results per filepath to avoid repeated HDD stat calls."""
    cache = stat_cache if stat_cache is not None else source_stat_cache
    now = now_fn()
    cached = cache.get(filepath)
    if cached is not None and now - cached[0] <= ttl_seconds:
        if hasattr(cache, "move_to_end"):
            cache.move_to_end(filepath)  # type: ignore[attr-defined]
        return cached[1]
    try:
        stat = os.stat(filepath)
        bits = f"{stat.st_size}|{stat.st_mtime_ns}|{filepath}"
    except OSError:
        bits = f"missing|{filepath}"
    cache[filepath] = (now, bits)
    if len(cache) > max_entries:
        if hasattr(cache, "popitem"):
            try:
                cache.popitem(last=False)  # type: ignore[call-arg]
            except TypeError:
                cache.pop(next(iter(cache)))
    return bits


def source_bits_from_catalog_metadata(
    filepath: str,
    file_size,
    file_modified_at,
    *,
    get_source_bits_fn: Callable[[str], str] = get_source_bits,
) -> tuple[str, int | None, bool]:
    """Build source identity from scan-time metadata, falling back to stat only if needed."""
    if file_size is not None and file_modified_at is not None:
        try:
            source_size = int(file_size)
            source_mtime = float(file_modified_at)
            return f"catalog|{source_size}|{source_mtime:.9f}|{filepath}", source_size, False
        except (TypeError, ValueError):
            pass

    bits = get_source_bits_fn(filepath)
    if bits.startswith("missing|"):
        return bits, None, True
    try:
        source_size = int(bits.split("|", 1)[0])
    except (TypeError, ValueError):
        source_size = None
    return bits, source_size, False


def build_source_signature_from_bits(
    source_bits: str,
    size: str,
    image_id: int,
    *,
    cache_version: str,
    full_tier: str,
    sizes: dict[str, int],
    thumb_quality: int,
) -> str:
    if size == full_tier:
        signature = f"{cache_version}|full|{image_id}|{source_bits}"
    else:
        signature = (
            f"{cache_version}|thumb|{size}|{image_id}|{sizes[size]}|"
            f"{thumb_quality}|{source_bits}"
        )
    return hashlib.sha1(signature.encode("utf-8", "surrogateescape")).hexdigest()


def build_catalog_source_signature(
    filepath: str,
    size: str,
    image_id: int,
    file_size,
    file_modified_at,
    *,
    get_source_bits_fn: Callable[[str], str],
    cache_version: str,
    full_tier: str,
    sizes: dict[str, int],
    thumb_quality: int,
) -> tuple[str, int | None, bool]:
    source_bits, source_size, source_missing = source_bits_from_catalog_metadata(
        filepath,
        file_size,
        file_modified_at,
        get_source_bits_fn=get_source_bits_fn,
    )
    return (
        build_source_signature_from_bits(
            source_bits,
            size,
            image_id,
            cache_version=cache_version,
            full_tier=full_tier,
            sizes=sizes,
            thumb_quality=thumb_quality,
        ),
        source_size,
        source_missing,
    )


def source_missing(filepath: str, *, get_source_bits_fn: Callable[[str], str]) -> bool:
    return get_source_bits_fn(filepath).startswith("missing|")


def source_missing_error(filepath: str, exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    try:
        return not os.path.exists(filepath)
    except OSError:
        return False


def mark_source_missing_from_error(
    filepath: str,
    image_id: int,
    exc: Exception,
    *,
    source_missing_error_fn: Callable[[str, Exception], bool],
    mark_missing_sync: Callable[[int], object],
    print_fn: Callable[[str], object] = print,
) -> bool:
    if not source_missing_error_fn(filepath, exc):
        return False
    try:
        mark_missing_sync(image_id)
    except Exception as mark_error:
        print_fn(f"Failed to mark missing image {image_id} for {filepath}: {mark_error}")
        return False
    return True


def response_headers(
    *,
    etag: str,
    browser_cache_max_age: int,
    browser_cache_stale_while_revalidate: int,
) -> dict[str, str]:
    return {
        "Cache-Control": (
            f"public, max-age={browser_cache_max_age}, "
            f"stale-while-revalidate={browser_cache_stale_while_revalidate}"
        ),
        "ETag": etag,
    }
