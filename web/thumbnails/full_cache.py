import os
import shutil
import threading


def has_room(
    image_id: int,
    source_size: int,
    budget: int,
    *,
    cache_root: str,
    full_tier: str,
    metadata_backoff_active,
    meta_lock,
    db_connect,
    tier_bytes,
    clear_metadata_backoff,
    note_metadata_lock,
    is_sqlite_locked,
) -> bool:
    if budget <= 0 or source_size > budget:
        return False
    if metadata_backoff_active():
        return False
    with meta_lock:
        conn = None
        try:
            conn = db_connect()
            total = tier_bytes(conn, full_tier)
            previous = conn.execute(
                "SELECT size_bytes FROM cache_entries "
                "WHERE cache_root = ? AND size = ? AND image_id = ?",
                (cache_root, full_tier, image_id),
            ).fetchone()
            previous_bytes = int(previous["size_bytes"]) if previous is not None else 0
            clear_metadata_backoff()
            return max(0, total - previous_bytes) + source_size <= budget
        except Exception as exc:
            if is_sqlite_locked(exc):
                note_metadata_lock()
                return False
            raise
        finally:
            if conn is not None:
                conn.close()


def cache_full_image(
    filepath: str,
    image_id: int,
    source_signature: str,
    *,
    cache_root: str,
    budget: int,
    read_disk_entry,
    has_cache_room,
    full_disk_path,
    store_disk_entry,
    hot: bool = True,
    room_prechecked: bool = False,
) -> str:
    if not os.path.exists(filepath):
        return filepath

    if not cache_root or budget <= 0:
        return filepath

    try:
        source_size = os.path.getsize(filepath)
    except OSError:
        return filepath

    if source_size > budget:
        return filepath

    row = read_disk_entry(source_signature)
    if row is not None:
        return row["path"]

    if not hot and not room_prechecked and not has_cache_room(image_id, source_size, budget):
        return filepath

    path = full_disk_path(image_id, filepath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    shutil.copyfile(filepath, temp_path)
    if not hot and not room_prechecked and not has_cache_room(image_id, source_size, budget):
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return filepath
    os.replace(temp_path, path)
    store_disk_entry(image_id, source_signature, path, source_size, hot=hot)
    return path


def cache_full_image_bytes(
    filepath: str,
    image_id: int,
    source_signature: str,
    data: bytes,
    *,
    cache_root: str,
    budget: int,
    read_disk_entry,
    has_cache_room,
    full_disk_path,
    store_disk_entry,
    hot: bool = True,
    room_prechecked: bool = False,
) -> str:
    if not data:
        return filepath

    source_size = len(data)
    if not cache_root or budget <= 0 or source_size > budget:
        return filepath

    row = read_disk_entry(source_signature)
    if row is not None:
        return row["path"]

    if not hot and not room_prechecked and not has_cache_room(image_id, source_size, budget):
        return filepath

    path = full_disk_path(image_id, filepath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    with open(temp_path, "wb") as f:
        f.write(data)
    if not hot and not room_prechecked and not has_cache_room(image_id, source_size, budget):
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return filepath
    os.replace(temp_path, path)
    store_disk_entry(image_id, source_signature, path, source_size, hot=hot)
    return path
