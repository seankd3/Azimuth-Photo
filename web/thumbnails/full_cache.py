import asyncio
import os
import shutil
import threading
from functools import partial

from core import work_coordination


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


async def run_full_image_job(
    filepath: str,
    image_id: int,
    hot: bool,
    *,
    executor,
    full_tier: str,
    build_source_signature,
    cache_full_image_sync,
) -> str:
    loop = asyncio.get_running_loop()
    source_signature = build_source_signature(filepath, full_tier, image_id)
    return await loop.run_in_executor(
        executor,
        partial(
            cache_full_image_sync,
            filepath,
            image_id,
            source_signature,
            hot=hot,
        ),
    )


def get_cached_full_image_path(
    filepath: str,
    image_id: int,
    *,
    full_tier: str,
    build_source_signature,
    get_disk_entry,
) -> str | None:
    source_signature = build_source_signature(filepath, full_tier, image_id)
    row = get_disk_entry(full_tier, image_id, source_signature)
    return row["path"] if row is not None else None


async def schedule_full_image_cache(
    filepath: str,
    image_id: int,
    *,
    hot: bool,
    cache_root: str,
    budget: int,
    path_exists,
    full_tier: str,
    build_source_signature,
    touch_cached_signature,
    inflight: dict,
    run_full_image_job,
) -> None:
    if not cache_root or budget <= 0:
        return
    if not path_exists(filepath):
        return

    source_signature = build_source_signature(filepath, full_tier, image_id)
    if touch_cached_signature(full_tier, image_id, source_signature):
        return
    inflight_key = ("full", image_id, source_signature)
    task = inflight.get(inflight_key)
    if task is not None:
        return

    async def run_when_warmers_have_turn():
        await work_coordination.wait_for_lane(work_coordination.AMBIENT_WARMING)
        return await run_full_image_job(filepath, image_id, hot)

    task = asyncio.create_task(run_when_warmers_have_turn())
    inflight[inflight_key] = task

    def release_when_done(done_task):
        if inflight.get(inflight_key) is done_task:
            inflight.pop(inflight_key, None)
        if not done_task.cancelled():
            done_task.exception()

    task.add_done_callback(release_when_done)


async def cancel_inflight_tasks(inflight: dict) -> None:
    tasks = list({task for task in inflight.values() if task is not None})
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    inflight.clear()


async def get_full_image_path(
    filepath: str,
    image_id: int,
    *,
    note_user_activity,
    full_tier: str,
    build_source_signature,
    get_disk_entry,
    inflight: dict,
    run_full_image_job,
) -> str:
    note_user_activity()
    source_signature = build_source_signature(filepath, full_tier, image_id)
    row = get_disk_entry(full_tier, image_id, source_signature)
    if row is not None:
        return row["path"]

    inflight_key = ("full", image_id, source_signature)
    task = inflight.get(inflight_key)
    if task is None:
        task = asyncio.create_task(run_full_image_job(filepath, image_id, True))
        inflight[inflight_key] = task

    try:
        result = await task
    finally:
        if inflight.get(inflight_key) is task and task.done():
            inflight.pop(inflight_key, None)

    return str(result)
