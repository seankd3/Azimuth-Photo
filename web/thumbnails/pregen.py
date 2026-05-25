import sqlite3
import time
from collections import deque
from dataclasses import asdict, dataclass


WINDOW_SECONDS = 30 * 60


@dataclass(frozen=True)
class BackgroundDecision:
    mode: str
    intensity: float
    pause: bool
    sleep_seconds: float
    thumbnail_batch_size: int
    thumbnail_pause_seconds: float
    embedding_pause_seconds: float
    reason: str
    checked_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class SessionBookkeeping:
    def __init__(
        self,
        *,
        history=None,
        session_started_at: float | None = None,
        session_generated: int = 0,
        source_read_failures: int = 0,
    ):
        self.history = history if history is not None else deque()
        self.session_started_at = session_started_at
        self.session_generated = session_generated
        self.source_read_failures = source_read_failures


def reset_cursor(cursor: dict) -> None:
    cursor["source_id"] = 0
    cursor["filepath"] = ""
    cursor["id"] = 0


def update_cursor_from_row(cursor: dict, row) -> None:
    cursor["source_id"] = int(row["source_id"] or 0)
    cursor["filepath"] = str(row["filepath"] or "")
    cursor["id"] = int(row["id"] or 0)


async def cache_target_total(get_db) -> int:
    conn = await get_db()
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 AND i.missing_at IS NULL"
        )
        row = await cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        await conn.close()


async def candidate_batch(get_db, cursor_state: dict, limit: int):
    conn = await get_db()
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.source_id, i.filepath, i.file_size, i.file_modified_at "
            "FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 "
            "AND i.missing_at IS NULL "
            "AND ("
            "  i.source_id > ? "
            "  OR (i.source_id = ? AND (i.filepath > ? OR (i.filepath = ? AND i.id > ?)))"
            ") "
            "ORDER BY i.source_id ASC, i.filepath ASC, i.id ASC "
            "LIMIT ?",
            (
                int(cursor_state.get("source_id") or 0),
                int(cursor_state.get("source_id") or 0),
                str(cursor_state.get("filepath") or ""),
                str(cursor_state.get("filepath") or ""),
                int(cursor_state.get("id") or 0),
                limit,
            ),
        )
        rows = await cursor.fetchall()
        if rows:
            update_cursor_from_row(cursor_state, rows[-1])
        return rows
    finally:
        await conn.close()


def bulk_tier_budgets(thumb_tiers, background_tier_budget) -> dict[str, int]:
    return {size: background_tier_budget(size) for size in thumb_tiers}


def full_tier_room(
    budget: int,
    *,
    full_tier: str,
    meta_lock,
    db_connect,
    tier_bytes,
    cache_metadata_backoff_active,
    clear_cache_metadata_lock_backoff,
    note_cache_metadata_lock,
    is_sqlite_locked,
) -> int:
    if budget <= 0 or cache_metadata_backoff_active():
        return 0
    with meta_lock:
        conn = None
        try:
            conn = db_connect()
            room = max(0, int(budget) - tier_bytes(conn, full_tier))
            clear_cache_metadata_lock_backoff()
            return room
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                note_cache_metadata_lock()
                return 0
            raise
        finally:
            if conn is not None:
                conn.close()


def bulk_tier_room(
    tier_budgets: dict[str, int],
    *,
    thumb_tiers,
    meta_lock,
    db_connect,
    tier_bytes,
    cache_metadata_backoff_active,
    clear_cache_metadata_lock_backoff,
    note_cache_metadata_lock,
    is_sqlite_locked,
) -> dict[str, int]:
    if cache_metadata_backoff_active():
        return {size: 0 for size in thumb_tiers}
    with meta_lock:
        conn = None
        try:
            conn = db_connect()
            room = {
                size: max(0, int(tier_budgets.get(size, 0) or 0) - tier_bytes(conn, size))
                for size in thumb_tiers
            }
            clear_cache_metadata_lock_backoff()
            return room
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                note_cache_metadata_lock()
                return {size: 0 for size in thumb_tiers}
            raise
        finally:
            if conn is not None:
                conn.close()


def set_state(
    pregen_state: dict,
    state: str,
    message: str = "",
    phase: str | None = None,
    error: str = "",
    *,
    enabled: bool,
    manual_mode: bool,
    manual_pause: bool,
    now_provider=None,
) -> None:
    if now_provider is None:
        now_provider = time.time
    pregen_state["enabled"] = enabled
    pregen_state["manual_mode"] = manual_mode
    pregen_state["manual_pause"] = manual_pause
    pregen_state["state"] = state
    pregen_state["message"] = message
    pregen_state["active_phase"] = phase
    pregen_state["last_error"] = error
    if pregen_state["started_at"] is None and state == "running":
        pregen_state["started_at"] = now_provider()


def history_entry(
    count: int,
    *,
    ended_at: float,
    thumbnails_written: int | None = None,
    source_bytes: int = 0,
    read_seconds: float = 0.0,
    decode_encode_seconds: float = 0.0,
    source_read_failures: int = 0,
) -> dict | None:
    if count <= 0 and not thumbnails_written and source_read_failures <= 0:
        return None
    return {
        "ended_at": ended_at,
        "count": int(count),
        "thumbnails_written": int(thumbnails_written if thumbnails_written is not None else count),
        "source_bytes": int(source_bytes or 0),
        "read_seconds": float(read_seconds or 0.0),
        "decode_encode_seconds": float(decode_encode_seconds or 0.0),
        "source_read_failures": int(source_read_failures or 0),
    }


def trim_history(history, now: float, *, window_seconds: float = WINDOW_SECONDS) -> None:
    cutoff = now - window_seconds
    while history and history[0]["ended_at"] < cutoff:
        history.popleft()


def record_batch(
    bookkeeping: SessionBookkeeping,
    count: int,
    *,
    now: float,
    thumbnails_written: int | None = None,
    source_bytes: int = 0,
    read_seconds: float = 0.0,
    decode_encode_seconds: float = 0.0,
    source_read_failures: int = 0,
) -> None:
    entry = history_entry(
        count,
        ended_at=now,
        thumbnails_written=thumbnails_written,
        source_bytes=source_bytes,
        read_seconds=read_seconds,
        decode_encode_seconds=decode_encode_seconds,
        source_read_failures=source_read_failures,
    )
    if entry is None:
        return
    if bookkeeping.session_started_at is None:
        bookkeeping.session_started_at = now
    bookkeeping.session_generated += count
    bookkeeping.source_read_failures += max(0, int(source_read_failures))
    bookkeeping.history.append(entry)
    trim_history(bookkeeping.history, now)


def record_result(result: dict, pregen_state: dict, *, record_batch, now_provider=None) -> int:
    if now_provider is None:
        now_provider = time.time

    source_reads = int(result.get("source_reads", 0))
    thumbnails_written = int(result.get("thumbnails_written", 0))
    originals_written = int(result.get("originals_written", 0))
    source_bytes = int(result.get("source_bytes", 0))
    read_seconds = float(result.get("read_seconds", 0.0))
    decode_encode_seconds = float(result.get("decode_encode_seconds", 0.0))
    source_read_failures = int(result.get("source_read_failures", 0))
    completed = max(source_reads, originals_written)
    useful_work = thumbnails_written + originals_written

    if completed or thumbnails_written or source_read_failures:
        if useful_work:
            pregen_state["last_generated_at"] = now_provider()
            pregen_state["generated_this_session"] += completed
        record_batch(
            completed,
            thumbnails_written=thumbnails_written,
            source_bytes=source_bytes,
            read_seconds=read_seconds,
            decode_encode_seconds=decode_encode_seconds,
            source_read_failures=source_read_failures,
        )

    return useful_work


def rates(
    history,
    *,
    now: float,
    session_started_at: float | None,
    session_generated: int,
    source_read_failures: int,
    window_seconds: float = WINDOW_SECONDS,
) -> tuple[float, float, dict]:
    recent_count = sum(item["count"] for item in history)
    recent_thumbnails = sum(item.get("thumbnails_written", item["count"]) for item in history)
    recent_bytes = sum(item.get("source_bytes", 0) for item in history)
    recent_read_seconds = sum(item.get("read_seconds", 0.0) for item in history)
    recent_decode_encode_seconds = sum(item.get("decode_encode_seconds", 0.0) for item in history)
    recent_failures = sum(item.get("source_read_failures", 0) for item in history)
    recent_window = max(1.0, min(window_seconds, now - history[0]["ended_at"])) if history else 0.0
    recent_rate = (recent_count / recent_window) * 60.0 if recent_window > 0 else 0.0
    recent_thumbnail_rate = (recent_thumbnails / recent_window) * 60.0 if recent_window > 0 else 0.0
    session_window = max(1.0, now - session_started_at) if session_started_at else 0.0
    overall_rate = (session_generated / session_window) * 60.0 if session_window > 0 else 0.0
    diagnostics = {
        "recent_source_reads_per_min": recent_rate,
        "recent_thumbnails_written_per_min": recent_thumbnail_rate,
        "recent_read_mbps": (
            (recent_bytes / (1024 * 1024)) / recent_read_seconds
            if recent_read_seconds > 0
            else 0.0
        ),
        "avg_source_read_seconds": (
            recent_read_seconds / recent_count
            if recent_count > 0
            else 0.0
        ),
        "avg_decode_encode_seconds": (
            recent_decode_encode_seconds / recent_count
            if recent_count > 0
            else 0.0
        ),
        "recent_source_read_failures": recent_failures,
        "source_read_failures": source_read_failures,
    }
    return recent_rate, overall_rate, diagnostics


def session_rates(bookkeeping: SessionBookkeeping, *, now: float) -> tuple[float, float, dict]:
    trim_history(bookkeeping.history, now)
    return rates(
        bookkeeping.history,
        now=now,
        session_started_at=bookkeeping.session_started_at,
        session_generated=bookkeeping.session_generated,
        source_read_failures=bookkeeping.source_read_failures,
    )


def generate_batch_for_decision(decision, configured_batch: int) -> int:
    if getattr(decision, "pause", False):
        return 0
    return max(
        1,
        min(
            int(getattr(decision, "thumbnail_batch_size", 1) or 1),
            int(configured_batch or 1),
        ),
    )


def background_decision(
    idle_seconds: float,
    *,
    decision_provider=None,
):
    del idle_seconds
    if decision_provider is not None:
        return decision_provider()
    return BackgroundDecision(
        mode="manual",
        intensity=1.0,
        pause=False,
        sleep_seconds=0.0,
        thumbnail_batch_size=16,
        thumbnail_pause_seconds=0.0,
        embedding_pause_seconds=0.0,
        reason="manual background work",
        checked_at=time.time(),
    )


def should_pause_for_priority() -> bool:
    return False
