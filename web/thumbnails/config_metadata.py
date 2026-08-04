"""Persistent thumbnail configuration metadata helpers."""

from collections.abc import Callable


def thumb_config_signature(cache_version: str, sizes: dict[str, int], thumb_quality: int) -> str:
    return f"{cache_version}|{sizes['sm']}|{sizes['md']}|{sizes['lg']}|{thumb_quality}"


def _is_sqlite_lock(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text or "database schema is locked" in text


def sync_thumb_config_metadata(
    new_signature: str,
    *,
    cache_root: str,
    current_signature: str,
    current_changed_at: float,
    current_replace_stale: bool,
    replace_thumbnail_cache: bool,
    now: float,
    meta_lock,
    db_connect: Callable,
    clear_memory_tiers: Callable[[tuple[str, ...]], object],
    thumb_tiers: tuple[str, ...],
    reset_pregen_bulk_cursor: Callable[[], object],
    reset_pregen_full_cursor: Callable[[], object],
) -> dict:
    with meta_lock:
        conn = None
        try:
            conn = db_connect()
            row = conn.execute(
                "SELECT thumb_config_signature, thumb_config_changed_at, replace_stale_thumbnails "
                "FROM cache_metadata WHERE cache_root = ?",
                (cache_root,),
            ).fetchone()
            previous_signature = row["thumb_config_signature"] if row else current_signature
            previous_changed_at = float(row["thumb_config_changed_at"]) if row else current_changed_at
            previous_replace_stale = bool(row["replace_stale_thumbnails"]) if row else current_replace_stale
        except Exception as exc:
            if not _is_sqlite_lock(exc):
                raise
            previous_signature = current_signature
            previous_changed_at = current_changed_at
            previous_replace_stale = current_replace_stale
        finally:
            if conn is not None:
                conn.close()

    changed = bool(previous_signature and previous_signature != new_signature)
    if changed:
        clear_memory_tiers(thumb_tiers)
        changed_at = now
        replace_stale = bool(replace_thumbnail_cache)
        reset_pregen_bulk_cursor()
        reset_pregen_full_cursor()
    else:
        changed_at = previous_changed_at or 0.0
        replace_stale = previous_replace_stale

    with meta_lock:
        conn = None
        try:
            conn = db_connect()
            conn.execute(
                "INSERT OR REPLACE INTO cache_metadata "
                "(cache_root, thumb_config_signature, thumb_config_changed_at, replace_stale_thumbnails) "
                "VALUES (?, ?, ?, ?)",
                (
                    cache_root,
                    new_signature,
                    changed_at,
                    1 if replace_stale else 0,
                ),
            )
            conn.commit()
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if not _is_sqlite_lock(exc):
                raise
        finally:
            if conn is not None:
                conn.close()

    return {
        "last_signature": new_signature,
        "changed": changed,
        "changed_at": changed_at,
        "replace_stale_thumbnails": replace_stale,
    }
