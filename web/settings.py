import copy
from datetime import datetime
import json
import os
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEB_DIR = os.path.dirname(__file__)
SETTINGS_PATH = os.path.join(WEB_DIR, "settings.local.json")


def _default_model_dir(model_id: str) -> str:
    safe = model_id.replace("/", "--").replace("\\", "--").replace(":", "-")
    return os.path.join(WEB_DIR, ".models", safe)


DEFAULT_SETTINGS = {
    "thumb_size_sm": 400,
    "thumb_size_md": 1920,
    "thumb_size_lg": 3840,
    "thumb_quality": 92,
    "ssd_cache_dir": os.path.join(WEB_DIR, ".thumbcache"),
    "ssd_cache_gb": 100,
    "memory_cache_gb": 0.5,
    "background_work_mode": "balanced",
    "cache_profile": "original_heavy",
    "pregenerate_on_idle": True,
    "background_thumb_workers": 2,
    "pregen_generate_batch": 16,
    "pregen_batch_pause_ms": 250,
    "embed_batch_pause_ms": 250,
    "embed_batch_size": 8,
    "embed_model_preset": "qwen3-vl-embedding-2b",
    "embed_model_id": "Qwen/Qwen3-VL-Embedding-2B",
    "embed_model_revision": "main",
    "embed_model_dir": _default_model_dir("Qwen/Qwen3-VL-Embedding-2B"),
    "embed_model_dim": 2048,
    "defer_ai_on_startup": True,
    "deep_search_terms": [],
    "deep_search_schedule_enabled": True,
    "deep_search_schedule_days": ["mon", "tue", "wed", "thu", "fri"],
    "deep_search_schedule_start": "07:00",
    "deep_search_schedule_end": "16:00",
    "deep_search_schedule_timezone": "America/Chicago",
    "search_similarity_threshold": 0.35,
    "show_loupe_cache_status": True,
    "people_scan_enabled": True,
    "people_auto_install": True,
    "face_model_id": "buffalo_l",
    "face_model_dir": _default_model_dir("insightface"),
    "face_detection_size": 640,
    "face_similarity_threshold": 0.52,
    "face_merge_suggestion_threshold": 0.62,
}

EMBED_MODEL_PRESETS = {
    "qwen3-vl-embedding-2b": {
        "label": "Qwen3-VL Embedding 2B",
        "model_id": "Qwen/Qwen3-VL-Embedding-2B",
        "revision": "main",
        "dimension": 2048,
        "description": "Current local model. Fastest and already indexed.",
    },
    "qwen3-vl-embedding-8b": {
        "label": "Qwen3-VL Embedding 8B",
        "model_id": "Qwen/Qwen3-VL-Embedding-8B",
        "revision": "main",
        "dimension": 4096,
        "description": "Smartest local text-to-image search target. Slower and heavier.",
    },
}

FAST_SEARCH_PRESET_KEY = "qwen3-vl-embedding-2b"
DEEP_SEARCH_PRESET_KEY = "qwen3-vl-embedding-8b"

INT_RANGES = {
    "thumb_size_sm": (64, 4096),
    "thumb_size_md": (128, 8192),
    "thumb_size_lg": (128, 8192),
    "thumb_quality": (40, 100),
    "ssd_cache_gb": (0, 4096),
    "background_thumb_workers": (1, 4),
    "pregen_generate_batch": (4, 64),
    "pregen_batch_pause_ms": (0, 5000),
    "embed_batch_pause_ms": (0, 5000),
    "embed_batch_size": (1, 32),
    "embed_model_dim": (64, 4096),
    "face_detection_size": (160, 1280),
}

FLOAT_RANGES = {
    "memory_cache_gb": (0.0, 64.0),
    "search_similarity_threshold": (0.1, 0.8),
    "face_similarity_threshold": (0.1, 0.9),
    "face_merge_suggestion_threshold": (0.1, 0.95),
}

_lock = threading.Lock()
_settings = None

CACHE_PROFILES = ("browse_fast", "balanced", "original_heavy")
BACKGROUND_WORK_MODES = ("browse", "balanced", "max")
BACKGROUND_WORK_MODE_OPTIONS = (
    {
        "key": "browse",
        "label": "Browse",
        "description": "Keep browsing and comparing responsive by pausing background builders.",
    },
    {
        "key": "balanced",
        "label": "Light Background",
        "description": "Warm thumbnails and embeddings only when the computer is quiet.",
    },
    {
        "key": "max",
        "label": "Max Work",
        "description": "Use more idle compute for overnight or away-from-desk cache building.",
    },
)

BROWSER_CACHE_MAX_AGE = 86400
BROWSER_CACHE_STALE_WHILE_REVALIDATE = 604800
MAX_DEEP_SEARCH_TERMS = 200
MAX_DEEP_SEARCH_TERM_LENGTH = 160
DEEP_SEARCH_DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEEP_SEARCH_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")
DEEP_SEARCH_DAY_ALIASES = {
    "monday": "mon",
    "mon": "mon",
    "tuesday": "tue",
    "tue": "tue",
    "tues": "tue",
    "wednesday": "wed",
    "wed": "wed",
    "thursday": "thu",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "friday": "fri",
    "fri": "fri",
    "saturday": "sat",
    "sat": "sat",
    "sunday": "sun",
    "sun": "sun",
}
DEEP_SEARCH_TIMEZONE_ALIASES = {
    "america/chicago": "America/Chicago",
    "us/central": "America/Chicago",
    "central": "America/Chicago",
    "ct": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
}


def _copy_settings(value: dict) -> dict:
    return copy.deepcopy(value)


def _normalize_deep_search_terms(value) -> list[str]:
    if isinstance(value, str):
        raw_terms = value.splitlines()
    elif isinstance(value, (list, tuple)):
        raw_terms = value
    else:
        return []

    terms = []
    seen = set()
    for item in raw_terms:
        term = " ".join(str(item or "").split())
        if not term:
            continue
        term = term[:MAX_DEEP_SEARCH_TERM_LENGTH].strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= MAX_DEEP_SEARCH_TERMS:
            break
    return terms


def normalize_deep_search_terms(value) -> list[str]:
    return _normalize_deep_search_terms(value)


def _normalize_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    return bool(value)


def _normalize_deep_search_days(value, default=None) -> list[str]:
    if value is None:
        raw_days = list(default or DEEP_SEARCH_WEEKDAYS)
    elif isinstance(value, str):
        raw_days = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        raw_days = value
    else:
        raw_days = []

    selected = set()
    for item in raw_days:
        day = DEEP_SEARCH_DAY_ALIASES.get(str(item or "").strip().lower())
        if day:
            selected.add(day)
    return [day for day in DEEP_SEARCH_DAY_ORDER if day in selected]


def _normalize_deep_search_time(value, default: str) -> str:
    parts = str(value or "").strip().split(":")
    if len(parts) < 2:
        return default
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def _normalize_deep_search_timezone(value) -> str:
    timezone = str(value or "").strip()
    return DEEP_SEARCH_TIMEZONE_ALIASES.get(timezone.lower(), DEFAULT_SETTINGS["deep_search_schedule_timezone"])


def _safe_model_key_part(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .replace("/", "--")
        .replace("\\", "--")
        .replace(":", "-")
        .replace("@", "-")
    )


def embedding_model_key(config: dict | None = None) -> str:
    config = config or get_settings()
    model_id = config.get("embed_model_id") or DEFAULT_SETTINGS["embed_model_id"]
    revision = config.get("embed_model_revision") or "main"
    dimension = int(config.get("embed_model_dim") or DEFAULT_SETTINGS["embed_model_dim"])
    return f"{_safe_model_key_part(model_id)}@{_safe_model_key_part(revision)}:{dimension}"


def embedding_model_config_for_preset(preset_key: str) -> dict:
    preset = EMBED_MODEL_PRESETS[preset_key]
    config = {
        "embed_model_id": preset["model_id"],
        "embed_model_revision": preset["revision"],
        "embed_model_dim": preset["dimension"],
        "embed_model_dir": _default_model_dir(preset["model_id"]),
    }
    return {
        "model_key": embedding_model_key(config),
        "model_id": config["embed_model_id"],
        "revision": config["embed_model_revision"],
        "dimension": int(config["embed_model_dim"]),
        "model_dir": config["embed_model_dir"],
        "embed_model_id": config["embed_model_id"],
        "embed_model_revision": config["embed_model_revision"],
        "embed_model_dim": int(config["embed_model_dim"]),
        "embed_model_dir": config["embed_model_dir"],
        "embed_batch_size": 1 if preset_key == "qwen3-vl-embedding-8b" else DEFAULT_SETTINGS["embed_batch_size"],
    }


def deep_search_embedding_config() -> dict:
    return embedding_model_config_for_preset(DEEP_SEARCH_PRESET_KEY)


def fast_search_embedding_config() -> dict:
    return embedding_model_config_for_preset(FAST_SEARCH_PRESET_KEY)


def _minutes_since_midnight(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def deep_search_schedule_status(config: dict | None = None, now: datetime | None = None) -> dict:
    config = config or get_settings()
    timezone_name = config.get("deep_search_schedule_timezone") or DEFAULT_SETTINGS["deep_search_schedule_timezone"]
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = DEFAULT_SETTINGS["deep_search_schedule_timezone"]
        timezone = ZoneInfo(timezone_name)

    local_now = (now or datetime.now(timezone))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=timezone)
    else:
        local_now = local_now.astimezone(timezone)

    days = _normalize_deep_search_days(
        config.get("deep_search_schedule_days"),
        DEFAULT_SETTINGS["deep_search_schedule_days"],
    )
    enabled = _normalize_bool(
        config.get("deep_search_schedule_enabled"),
        DEFAULT_SETTINGS["deep_search_schedule_enabled"],
    )
    start = _normalize_deep_search_time(
        config.get("deep_search_schedule_start"),
        DEFAULT_SETTINGS["deep_search_schedule_start"],
    )
    end = _normalize_deep_search_time(
        config.get("deep_search_schedule_end"),
        DEFAULT_SETTINGS["deep_search_schedule_end"],
    )
    day = DEEP_SEARCH_DAY_ORDER[local_now.weekday()]
    now_minutes = local_now.hour * 60 + local_now.minute
    start_minutes = _minutes_since_midnight(start)
    end_minutes = _minutes_since_midnight(end)
    if start_minutes <= end_minutes:
        in_time_window = start_minutes <= now_minutes < end_minutes
    else:
        in_time_window = now_minutes >= start_minutes or now_minutes < end_minutes

    active = enabled and day in days and in_time_window
    reason = "active"
    if not enabled:
        reason = "disabled"
    elif day not in days:
        reason = "outside_selected_days"
    elif not in_time_window:
        reason = "outside_time_window"

    return {
        "enabled": enabled,
        "active": active,
        "reason": reason,
        "day": day,
        "days": days,
        "start": start,
        "end": end,
        "timezone": timezone_name,
        "local_time": local_now.isoformat(),
    }


def _system_memory_gb() -> float | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if page_size <= 0 or phys_pages <= 0:
            return None
        return (page_size * phys_pages) / (1024 ** 3)
    except (AttributeError, OSError, ValueError):
        return None


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _derive_runtime_tuning(memory_cache_gb: float) -> dict:
    cpu_count = max(1, os.cpu_count() or 4)
    system_memory_gb = _system_memory_gb()

    user_workers = _clamp(cpu_count - 1, 2, 12)
    if system_memory_gb is not None:
        if system_memory_gb < 8:
            user_workers = min(user_workers, 4)
        elif system_memory_gb < 16:
            user_workers = min(user_workers, 6)

    cache_aggression = max(1, min(4, int(memory_cache_gb / 0.5) if memory_cache_gb > 0 else 1))
    prefetch_target = min(user_workers - 2, user_workers // 2 + cache_aggression - 1)
    prefetch_workers = _clamp(max(1, prefetch_target), 1, 4)
    warm_factor = max(1, prefetch_workers * cache_aggression)

    return {
        "cpu_count": cpu_count,
        "system_memory_gb": round(system_memory_gb, 1) if system_memory_gb is not None else None,
        "user_workers": user_workers,
        "prefetch_workers": prefetch_workers,
        "scan_prefetch_limit": _clamp(warm_factor * 8, 24, 96),
        "review_prefetch_limit": _clamp(warm_factor * 6, 12, 48),
        "compare_prefetch_limit": _clamp(warm_factor * 4, 8, 32),
        "mosaic_prefetch_limit": _clamp(warm_factor * 6, 12, 48),
        "browser_cache_max_age": BROWSER_CACHE_MAX_AGE,
        "browser_cache_stale_while_revalidate": BROWSER_CACHE_STALE_WHILE_REVALIDATE,
    }


def _resolve_cache_dir(path: str, default: str) -> str:
    value = (path or "").strip()
    if not value:
        return default
    if not os.path.isabs(value):
        value = os.path.abspath(os.path.join(WEB_DIR, value))
    if value == os.path.sep:
        return default
    return value


def normalize_settings(raw: dict | None) -> dict:
    normalized = _copy_settings(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return normalized

    if "thumb_quality" not in raw and "jpeg_quality" in raw:
        raw = {**raw, "thumb_quality": raw.get("jpeg_quality")}
    if "ssd_cache_dir" not in raw and "disk_cache_dir" in raw:
        raw = {**raw, "ssd_cache_dir": raw.get("disk_cache_dir")}
    if "memory_cache_gb" not in raw and "memory_cache_mb" in raw:
        try:
            raw = {
                **raw,
                "memory_cache_gb": max(0.0, float(raw.get("memory_cache_mb", 0)) / 1024.0),
            }
        except (TypeError, ValueError):
            pass
    if "memory_cache_gb" not in raw:
        cache_limit_values = [
            raw.get("cache_limit_sm"),
            raw.get("cache_limit_md"),
            raw.get("cache_limit_lg"),
        ]
        if any(value is not None for value in cache_limit_values):
            try:
                approx_entries = sum(int(value or 0) for value in cache_limit_values)
                raw = {
                    **raw,
                    "memory_cache_gb": max(0.25, min(64.0, (approx_entries // 8 or 512) / 1024.0)),
                }
            except (TypeError, ValueError):
                pass

    preset = str(raw.get("embed_model_preset", normalized.get("embed_model_preset", ""))).strip()
    if preset not in EMBED_MODEL_PRESETS:
        preset = "custom"
    normalized["embed_model_preset"] = preset
    if preset != "custom":
        preset_config = EMBED_MODEL_PRESETS[preset]
        raw = {
            **raw,
            "embed_model_id": preset_config["model_id"],
            "embed_model_revision": preset_config["revision"],
            "embed_model_dim": preset_config["dimension"],
            "embed_model_dir": _default_model_dir(preset_config["model_id"]),
        }

    model_id = (raw.get("embed_model_id") or normalized["embed_model_id"]).strip()
    if not model_id:
        model_id = normalized["embed_model_id"]
    normalized["embed_model_id"] = model_id

    revision = (raw.get("embed_model_revision") or normalized["embed_model_revision"]).strip()
    normalized["embed_model_revision"] = revision or "main"

    normalized["ssd_cache_dir"] = _resolve_cache_dir(
        raw.get("ssd_cache_dir", normalized["ssd_cache_dir"]),
        DEFAULT_SETTINGS["ssd_cache_dir"],
    )
    normalized["embed_model_dir"] = _resolve_cache_dir(
        raw.get("embed_model_dir", _default_model_dir(model_id)),
        _default_model_dir(model_id),
    )
    face_model_id = str(raw.get("face_model_id") or normalized["face_model_id"]).strip()
    normalized["face_model_id"] = face_model_id or DEFAULT_SETTINGS["face_model_id"]
    normalized["face_model_dir"] = _resolve_cache_dir(
        raw.get("face_model_dir", normalized["face_model_dir"]),
        DEFAULT_SETTINGS["face_model_dir"],
    )

    profile = str(raw.get("cache_profile", normalized["cache_profile"])).strip().lower()
    normalized["cache_profile"] = profile if profile in CACHE_PROFILES else DEFAULT_SETTINGS["cache_profile"]
    work_mode = str(raw.get("background_work_mode", normalized["background_work_mode"])).strip().lower()
    normalized["background_work_mode"] = (
        work_mode if work_mode in BACKGROUND_WORK_MODES else DEFAULT_SETTINGS["background_work_mode"]
    )

    for key, (min_value, max_value) in INT_RANGES.items():
        value = raw.get(key, normalized[key])
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = normalized[key]
        normalized[key] = max(min_value, min(max_value, value))

    for key, (min_value, max_value) in FLOAT_RANGES.items():
        value = raw.get(key, normalized[key])
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = normalized[key]
        normalized[key] = round(max(min_value, min(max_value, value)), 2)

    if normalized["thumb_size_sm"] > normalized["thumb_size_md"]:
        normalized["thumb_size_md"] = normalized["thumb_size_sm"]
    if normalized["thumb_size_md"] > normalized["thumb_size_lg"]:
        normalized["thumb_size_lg"] = normalized["thumb_size_md"]

    normalized["pregenerate_on_idle"] = bool(raw.get("pregenerate_on_idle", normalized["pregenerate_on_idle"]))
    normalized["defer_ai_on_startup"] = bool(raw.get("defer_ai_on_startup", normalized["defer_ai_on_startup"]))
    normalized["people_scan_enabled"] = _normalize_bool(
        raw.get("people_scan_enabled", normalized["people_scan_enabled"]),
        DEFAULT_SETTINGS["people_scan_enabled"],
    )
    normalized["people_auto_install"] = _normalize_bool(
        raw.get("people_auto_install", normalized["people_auto_install"]),
        DEFAULT_SETTINGS["people_auto_install"],
    )
    normalized["show_loupe_cache_status"] = bool(
        raw.get("show_loupe_cache_status", normalized["show_loupe_cache_status"])
    )
    normalized["deep_search_terms"] = _normalize_deep_search_terms(
        raw.get("deep_search_terms", normalized["deep_search_terms"])
    )
    normalized["deep_search_schedule_enabled"] = _normalize_bool(
        raw.get("deep_search_schedule_enabled", normalized["deep_search_schedule_enabled"]),
        DEFAULT_SETTINGS["deep_search_schedule_enabled"],
    )
    normalized["deep_search_schedule_days"] = _normalize_deep_search_days(
        raw.get("deep_search_schedule_days"),
        DEFAULT_SETTINGS["deep_search_schedule_days"],
    )
    normalized["deep_search_schedule_start"] = _normalize_deep_search_time(
        raw.get("deep_search_schedule_start", normalized["deep_search_schedule_start"]),
        DEFAULT_SETTINGS["deep_search_schedule_start"],
    )
    normalized["deep_search_schedule_end"] = _normalize_deep_search_time(
        raw.get("deep_search_schedule_end", normalized["deep_search_schedule_end"]),
        DEFAULT_SETTINGS["deep_search_schedule_end"],
    )
    normalized["deep_search_schedule_timezone"] = _normalize_deep_search_timezone(
        raw.get("deep_search_schedule_timezone", normalized["deep_search_schedule_timezone"])
    )
    normalized.update(_derive_runtime_tuning(normalized["memory_cache_gb"]))
    normalized["prefetch_workers"] = min(
        normalized["prefetch_workers"],
        normalized["background_thumb_workers"],
    )

    return normalized


def load_settings(force: bool = False) -> dict:
    global _settings
    with _lock:
        if _settings is not None and not force:
            return _copy_settings(_settings)

        raw = {}
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                raw = {}

        _settings = normalize_settings(raw)
        return _copy_settings(_settings)


def get_settings() -> dict:
    return load_settings()


def save_settings(raw: dict | None) -> dict:
    global _settings
    normalized = normalize_settings(raw)
    persisted = {key: copy.deepcopy(normalized[key]) for key in DEFAULT_SETTINGS}
    temp_path = f"{SETTINGS_PATH}.tmp"

    with _lock:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(persisted, f, indent=2, sort_keys=True)
        os.replace(temp_path, SETTINGS_PATH)
        _settings = normalized
        return _copy_settings(_settings)


def reset_settings() -> dict:
    global _settings
    with _lock:
        try:
            os.remove(SETTINGS_PATH)
        except FileNotFoundError:
            pass
        _settings = normalize_settings({})
        return _copy_settings(_settings)


def settings_metadata() -> dict:
    return {
        "settings_path": SETTINGS_PATH,
        "defaults": _copy_settings(DEFAULT_SETTINGS),
        "cache_profiles": list(CACHE_PROFILES),
        "background_work_modes": [dict(option) for option in BACKGROUND_WORK_MODE_OPTIONS],
        "embedding_model_presets": [
            {"key": key, **value, "model_dir": _default_model_dir(value["model_id"])}
            for key, value in EMBED_MODEL_PRESETS.items()
        ],
    }
