"""Runtime helpers shared by the thumbnail compatibility facade."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor


def as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def current_time() -> float:
    return time.time()


def is_sqlite_locked(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def replace_executor(
    current: ThreadPoolExecutor,
    workers: int,
    prefix: str,
) -> ThreadPoolExecutor:
    replacement = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=prefix)
    try:
        current.shutdown(wait=False, cancel_futures=False)
    except TypeError:
        current.shutdown(wait=False)
    return replacement
