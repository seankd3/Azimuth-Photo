"""Disk cache path, marker, and in-memory index helpers."""

import os


def cache_marker_path(cache_root: str, cache_marker: str) -> str:
    return os.path.join(cache_root, cache_marker)


def cache_dir_has_marker(cache_root: str, cache_marker: str) -> bool:
    return bool(cache_root and os.path.isfile(cache_marker_path(cache_root, cache_marker)))


def cache_dir_is_legacy_cache_layout(
    cache_root: str,
    tiers: tuple[str, ...],
    cache_marker: str,
) -> bool:
    if not cache_root or not os.path.isdir(cache_root):
        return True
    allowed = set(tiers) | {cache_marker}
    try:
        entries = os.listdir(cache_root)
    except OSError:
        return False
    for entry in entries:
        if entry not in allowed:
            return False
        path = os.path.join(cache_root, entry)
        if entry == cache_marker:
            if not os.path.isfile(path):
                return False
        elif not os.path.isdir(path):
            return False
    return True


def write_cache_marker(cache_root: str, cache_marker: str) -> None:
    if not cache_root:
        return
    with open(cache_marker_path(cache_root, cache_marker), "w", encoding="utf-8") as marker:
        marker.write("photoArchive thumbnail cache\n")


def cache_dir_safe_to_clear(
    cache_root: str,
    tiers: tuple[str, ...],
    cache_marker: str,
) -> tuple[bool, str, bool]:
    if not cache_root:
        return True, "", False
    if not os.path.exists(cache_root):
        return True, "", False
    if not os.path.isdir(cache_root):
        return False, f"Cache path is not a directory: {cache_root}", False
    if cache_dir_has_marker(cache_root, cache_marker):
        return True, "", False
    if cache_dir_is_legacy_cache_layout(cache_root, tiers, cache_marker):
        return True, "", True
    return (
        False,
        "Refusing to clear an unmarked cache directory that contains non-cache files",
        False,
    )


def ensure_cache_dirs(
    cache_root: str,
    thumb_tiers: tuple[str, ...],
    full_tier: str,
    cache_marker: str,
) -> bool:
    if not cache_root:
        return False
    should_mark = (
        not os.path.exists(cache_root)
        or cache_dir_has_marker(cache_root, cache_marker)
        or cache_dir_is_legacy_cache_layout(cache_root, thumb_tiers + (full_tier,), cache_marker)
    )
    os.makedirs(cache_root, exist_ok=True)
    for size in thumb_tiers:
        os.makedirs(os.path.join(cache_root, size), exist_ok=True)
    os.makedirs(os.path.join(cache_root, full_tier), exist_ok=True)
    return should_mark


def cleanup_stale_cache_temps(
    cache_root: str,
    tiers: tuple[str, ...],
    *,
    cutoff: float,
) -> dict:
    if not cache_root or not os.path.isdir(cache_root):
        return {"files_removed": 0, "bytes_removed": 0}
    files_removed = 0
    bytes_removed = 0
    for tier in tiers:
        tier_dir = os.path.join(cache_root, tier)
        if not os.path.isdir(tier_dir):
            continue
        for root, _dirs, files in os.walk(tier_dir):
            for filename in files:
                if not filename.endswith(".tmp"):
                    continue
                path = os.path.join(root, filename)
                try:
                    stat = os.stat(path)
                    if stat.st_mtime > cutoff:
                        continue
                    os.remove(path)
                    files_removed += 1
                    bytes_removed += int(stat.st_size)
                except (FileNotFoundError, OSError):
                    continue
    return {"files_removed": files_removed, "bytes_removed": bytes_removed}


def thumbnail_disk_path(cache_root: str, size: str, image_id: int) -> str:
    return os.path.join(cache_root, size, f"{image_id}.jpg")


def full_disk_path(cache_root: str, full_tier: str, image_id: int, filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower() or ".bin"
    return os.path.join(cache_root, full_tier, f"{image_id}{ext}")


def is_browser_displayable_original(filepath: str, browser_extensions: set[str]) -> bool:
    return os.path.splitext(filepath or "")[1].lower() in browser_extensions


def index_from_rows(rows) -> dict[tuple[str, int], tuple[str, str]]:
    return {
        (row["size"], row["image_id"]): (row["path"], row["source_signature"])
        for row in rows
    }


def index_entry(
    index: dict[tuple[str, int], tuple[str, str]],
    lock,
    size: str,
    image_id: int,
    path: str,
    source_signature: str,
) -> None:
    with lock:
        index[(size, image_id)] = (path, source_signature)


def unindex_entry(
    index: dict[tuple[str, int], tuple[str, str]],
    lock,
    size: str,
    image_id: int,
) -> None:
    with lock:
        index.pop((size, image_id), None)


def clear_index(
    index: dict[tuple[str, int], tuple[str, str]],
    lock,
    tiers: tuple[str, ...] | None = None,
) -> None:
    with lock:
        if tiers is None:
            index.clear()
            return
        for key in list(index.keys()):
            if key[0] in tiers:
                index.pop(key, None)


def lookup_index_entry(
    index: dict[tuple[str, int], tuple[str, str]],
    lock,
    size: str,
    image_id: int,
    source_signature: str | None = None,
) -> tuple[str, str] | None:
    with lock:
        entry = index.get((size, image_id))
    if entry is None:
        return None
    path, cached_signature = entry
    if source_signature is not None and cached_signature != source_signature:
        return None
    return path, cached_signature
