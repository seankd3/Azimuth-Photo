WINDOW_SECONDS = 30 * 60
VALID_WORK_MODES = {"browse", "balanced", "max"}


def reset_cursor(cursor: dict) -> None:
    cursor["source_id"] = 0
    cursor["filepath"] = ""
    cursor["id"] = 0


def update_cursor_from_row(cursor: dict, row) -> None:
    cursor["source_id"] = int(row["source_id"] or 0)
    cursor["filepath"] = str(row["filepath"] or "")
    cursor["id"] = int(row["id"] or 0)


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


def normalize_work_mode(value, *, default: str = "balanced") -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_WORK_MODES else default


def background_work_mode(settings_getter=None, *, default: str = "balanced") -> str:
    try:
        if settings_getter is None:
            import settings

            settings_getter = settings.get_settings

        mode = normalize_work_mode(
            settings_getter().get("background_work_mode"),
            default=default,
        )
        if mode:
            return mode
    except Exception:
        pass
    return default


def background_decision(
    idle_seconds: float,
    *,
    work_mode_provider=None,
    decision_provider=None,
):
    if work_mode_provider is None:
        work_mode_provider = background_work_mode
    if decision_provider is None:
        import resource_governor

        decision_provider = resource_governor.get_background_decision
    return decision_provider(idle_seconds, work_mode=work_mode_provider())


def should_yield_to_foreground(work_mode_provider=None) -> bool:
    if work_mode_provider is None:
        work_mode_provider = background_work_mode
    return work_mode_provider() == "browse"
