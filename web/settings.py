import copy
import json
import os
import threading

from core.runtime_paths import resolve_runtime_paths

WEB_DIR = os.path.dirname(__file__)
SETTINGS_PATH = resolve_runtime_paths().settings_file
SETTINGS_VERSION = 2
DEFAULT_EMBED_MODEL_PRESET_KEY = "qwen3-vl-embedding-8b"
LEGACY_2B_PRESET_KEY = "qwen3-vl-embedding-2b"
DEFAULT_CAPTION_MODEL_PRESET_KEY = "qwen2.5-vl-7b-instruct-bnb-4bit"


def _default_import_root() -> str:
    return os.path.join(os.path.expanduser("~"), "Pictures", "photoArchive Imports")


def _default_model_dir(model_id: str) -> str:
    safe = model_id.replace("/", "--").replace("\\", "--").replace(":", "-")
    return os.path.join(resolve_runtime_paths().model_root, safe)


def _default_thumb_cache_dir() -> str:
    return resolve_runtime_paths().thumb_cache_dir


DEFAULT_SETTINGS = {
    "settings_version": SETTINGS_VERSION,
    "thumb_size_sm": 400,
    "thumb_size_md": 1920,
    "thumb_size_lg": 3840,
    "thumb_quality": 92,
    "ssd_cache_dir": _default_thumb_cache_dir(),
    "ssd_cache_gb": 100,
    "memory_cache_gb": 0.5,
    "cache_profile": "original_heavy",
    "background_thumb_workers": 2,
    "pregen_generate_batch": 16,
    "pregen_batch_pause_ms": 250,
    "embed_batch_pause_ms": 250,
    "embed_batch_size": 1,
    "embed_model_preset": DEFAULT_EMBED_MODEL_PRESET_KEY,
    "embed_model_id": "Qwen/Qwen3-VL-Embedding-8B",
    "embed_model_revision": "main",
    "embed_model_dir": _default_model_dir("Qwen/Qwen3-VL-Embedding-8B"),
    "embed_model_dim": 4096,
    "search_similarity_threshold": 0.35,
    "ranking_taste_blend": True,
    "taste_blend_min_signal": 25,
    "refine_semantic_pairing": True,
    "show_loupe_cache_status": True,
    "people_scan_enabled": True,
    "caption_scan_enabled": False,
    "caption_model_preset": DEFAULT_CAPTION_MODEL_PRESET_KEY,
    "caption_model_id": "Qwen/Qwen2.5-VL-7B-Instruct",
    "caption_model_revision": "main",
    "caption_model_dir": _default_model_dir("Qwen/Qwen2.5-VL-7B-Instruct"),
    "caption_model_quantization": "bnb-4bit",
    "caption_prompt_version": "caption-json-v1",
    "caption_batch_size": 1,
    "people_auto_install": True,
    "face_model_id": "buffalo_l",
    "face_model_dir": _default_model_dir("insightface"),
    "face_detection_size": 640,
    "face_similarity_threshold": 0.52,
    "face_merge_suggestion_threshold": 0.62,
    "import_root": _default_import_root(),
    "publish_dir": "",
    "publish_hook": "",
    "publish_site_base_url": "",
    "share_brand_name": "",
    "share_cookie_secret": "",
    "owner_key_hash": "",
    "owner_session_epoch": 0,
    "import_category_memory": {},
    "sync_bandwidth_mbps": 0,
    "sync_thumb_budget_gb": 0,  # 0 = auto (adapts to free disk)
    "hub_url": "",
    "device_token": "",
    "paired_hub_id": "",
    "require_device_token": False,  # opt-in until pairing is wired into onboarding; flip to True for public release
    "setup_completed": False,
    # Lightroom bridge: Elo → star projection (≥N comparisons; cumulative percentiles).
    "elo_stars_min_comparisons": 3,
    "elo_stars_thresholds": [0.02, 0.10, 0.30],
    # Cloud Backup (rclone vault for original files — complementary to catalog snapshots).
    "cloud_backup_remote": "",
    "cloud_backup_dest_prefix": "",
    "cloud_backup_trees": [],
    "cloud_backup_bwlimit": "07:00,3M 23:00,off",
    "cloud_backup_exclude_from_catalog": True,
    "cloud_backup_nightly_enabled": False,
}

PRIVATE_SETTING_KEYS = {
    "share_cookie_secret",
    "device_token",
    "owner_key_hash",
    "owner_session_epoch",
    "import_category_memory",
}
# Server-side-only configuration: readable (masked) but never writable via the API.
SERVER_ONLY_SETTING_KEYS = {"publish_hook"}

EMBED_MODEL_PRESETS = {
    "qwen3-vl-embedding-8b": {
        "label": "Qwen3-VL Embedding 8B",
        "model_id": "Qwen/Qwen3-VL-Embedding-8B",
        "revision": "main",
        "dimension": 4096,
        "description": "Default local text-to-image search model. Heavier, smarter embeddings.",
    },
    "qwen3-vl-embedding-2b": {
        "label": "Qwen3-VL Embedding 2B",
        "model_id": "Qwen/Qwen3-VL-Embedding-2B",
        "revision": "main",
        "dimension": 2048,
        "description": "Lighter fallback for lower-memory machines.",
    },
}

CAPTION_MODEL_PRESETS = {
    "qwen2.5-vl-3b-instruct-bnb-4bit": {
        "label": "Qwen2.5-VL 3B Captioner",
        "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
        "revision": "main",
        "quantization": "bnb-4bit",
        "prompt_version": "caption-json-v1",
        "description": "Compact local VLM captioner that fits an 8GB GPU shared with a resident voice daemon.",
    },
    "qwen2.5-vl-7b-instruct-bnb-4bit": {
        "label": "Qwen2.5-VL 7B Captioner",
        "model_id": "Qwen/Qwen2.5-VL-7B-Instruct",
        "revision": "main",
        "quantization": "bnb-4bit",
        "prompt_version": "caption-json-v1",
        "description": "Default local VLM captioner, loaded sequentially with embeddings on 8GB GPUs.",
    },
}

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
    "taste_blend_min_signal": (1, 10000),
    "elo_stars_min_comparisons": (1, 1000),
    "face_detection_size": (160, 1280),
    "caption_batch_size": (1, 4),
    "sync_bandwidth_mbps": (0, 10000),
    "sync_thumb_budget_gb": (0, 1024),
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

BROWSER_CACHE_MAX_AGE = 86400
BROWSER_CACHE_STALE_WHILE_REVALIDATE = 2592000


def _copy_settings(value: dict) -> dict:
    return copy.deepcopy(value)


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


def caption_model_key(config: dict | None = None) -> str:
    config = config or get_settings()
    model_id = config.get("caption_model_id") or DEFAULT_SETTINGS["caption_model_id"]
    revision = config.get("caption_model_revision") or "main"
    quantization = config.get("caption_model_quantization") or "none"
    prompt_version = config.get("caption_prompt_version") or "caption-json-v1"
    return (
        f"{_safe_model_key_part(model_id)}@{_safe_model_key_part(revision)}:"
        f"{_safe_model_key_part(quantization)}:{_safe_model_key_part(prompt_version)}"
    )


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


def active_embedding_config(config: dict | None = None) -> dict:
    config = config or get_settings()
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
        "embed_batch_size": int(config.get("embed_batch_size") or DEFAULT_SETTINGS["embed_batch_size"]),
    }


def active_caption_config(config: dict | None = None) -> dict:
    config = config or get_settings()
    return {
        "model_key": caption_model_key(config),
        "model_id": config["caption_model_id"],
        "revision": config["caption_model_revision"],
        "model_dir": config["caption_model_dir"],
        "quantization": config.get("caption_model_quantization") or "bnb-4bit",
        "prompt_version": config.get("caption_prompt_version") or "caption-json-v1",
        "batch_size": int(config.get("caption_batch_size") or DEFAULT_SETTINGS["caption_batch_size"]),
    }


def fast_search_embedding_config() -> dict:
    return active_embedding_config()


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


def _resolve_user_dir(path: str, default: str) -> str:
    value = (path or "").strip()
    if not value:
        return default
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        value = os.path.join(os.path.expanduser("~"), value)
    value = os.path.abspath(value)
    if value == os.path.sep:
        return default
    return value


def _settings_version(raw: dict) -> int:
    try:
        return int(raw.get("settings_version") or 0)
    except (TypeError, ValueError):
        return 0


def _raw_uses_legacy_2b_default(raw: dict) -> bool:
    preset = str(raw.get("embed_model_preset") or LEGACY_2B_PRESET_KEY).strip()
    model_id = str(raw.get("embed_model_id") or "Qwen/Qwen3-VL-Embedding-2B").strip()
    revision = str(raw.get("embed_model_revision") or "main").strip()
    try:
        dimension = int(raw.get("embed_model_dim") or 2048)
    except (TypeError, ValueError):
        dimension = 2048
    return (
        preset == LEGACY_2B_PRESET_KEY
        and model_id == "Qwen/Qwen3-VL-Embedding-2B"
        and revision == "main"
        and dimension == 2048
    )


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

    if _settings_version(raw) < SETTINGS_VERSION and _raw_uses_legacy_2b_default(raw):
        raw = {**raw, "embed_model_preset": DEFAULT_EMBED_MODEL_PRESET_KEY}

    preset = str(raw.get("embed_model_preset", normalized.get("embed_model_preset", ""))).strip()
    if preset not in EMBED_MODEL_PRESETS:
        preset = DEFAULT_EMBED_MODEL_PRESET_KEY
    normalized["embed_model_preset"] = preset
    preset_config = EMBED_MODEL_PRESETS[preset]
    raw = {
        **raw,
        "embed_model_id": preset_config["model_id"],
        "embed_model_revision": preset_config["revision"],
        "embed_model_dim": preset_config["dimension"],
        "embed_model_dir": (
            _default_model_dir(preset_config["model_id"])
            if os.environ.get("PHOTOARCHIVE_MODELS_DIR")
            else raw.get("embed_model_dir")
            if raw.get("embed_model_id") == preset_config["model_id"]
            and raw.get("embed_model_dir")
            else _default_model_dir(preset_config["model_id"])
        ),
    }

    caption_preset = str(
        raw.get("caption_model_preset", normalized.get("caption_model_preset", ""))
    ).strip()
    if caption_preset not in CAPTION_MODEL_PRESETS:
        caption_preset = DEFAULT_CAPTION_MODEL_PRESET_KEY
    normalized["caption_model_preset"] = caption_preset
    caption_preset_config = CAPTION_MODEL_PRESETS[caption_preset]
    raw = {
        **raw,
        "caption_model_id": caption_preset_config["model_id"],
        "caption_model_revision": caption_preset_config["revision"],
        "caption_model_dir": (
            _default_model_dir(caption_preset_config["model_id"])
            if os.environ.get("PHOTOARCHIVE_MODELS_DIR")
            else raw.get("caption_model_dir")
            if raw.get("caption_model_id") == caption_preset_config["model_id"]
            and raw.get("caption_model_dir")
            else _default_model_dir(caption_preset_config["model_id"])
        ),
        "caption_model_quantization": caption_preset_config["quantization"],
        "caption_prompt_version": caption_preset_config["prompt_version"],
    }

    model_id = (raw.get("embed_model_id") or normalized["embed_model_id"]).strip()
    if not model_id:
        model_id = normalized["embed_model_id"]
    normalized["embed_model_id"] = model_id

    revision = (raw.get("embed_model_revision") or normalized["embed_model_revision"]).strip()
    normalized["embed_model_revision"] = revision or "main"

    normalized["ssd_cache_dir"] = _resolve_cache_dir(
        os.environ.get("PHOTOARCHIVE_THUMB_CACHE_DIR")
        or raw.get("ssd_cache_dir", normalized["ssd_cache_dir"]),
        _default_thumb_cache_dir(),
    )
    normalized["embed_model_dir"] = _resolve_cache_dir(
        raw.get("embed_model_dir", _default_model_dir(model_id)),
        _default_model_dir(model_id),
    )
    caption_model_id = str(raw.get("caption_model_id") or normalized["caption_model_id"]).strip()
    normalized["caption_model_id"] = caption_model_id or DEFAULT_SETTINGS["caption_model_id"]
    normalized["caption_model_revision"] = str(
        raw.get("caption_model_revision") or normalized["caption_model_revision"]
    ).strip() or "main"
    normalized["caption_model_dir"] = _resolve_cache_dir(
        raw.get("caption_model_dir", _default_model_dir(caption_model_id)),
        _default_model_dir(caption_model_id),
    )
    normalized["caption_model_quantization"] = str(
        raw.get("caption_model_quantization") or normalized["caption_model_quantization"]
    ).strip() or "bnb-4bit"
    normalized["caption_prompt_version"] = str(
        raw.get("caption_prompt_version") or normalized["caption_prompt_version"]
    ).strip() or "caption-json-v1"
    face_model_id = str(raw.get("face_model_id") or normalized["face_model_id"]).strip()
    normalized["face_model_id"] = face_model_id or DEFAULT_SETTINGS["face_model_id"]
    normalized["face_model_dir"] = _resolve_cache_dir(
        _default_model_dir("insightface")
        if os.environ.get("PHOTOARCHIVE_MODELS_DIR")
        else raw.get("face_model_dir", normalized["face_model_dir"]),
        _default_model_dir("insightface"),
    )
    normalized["import_root"] = _resolve_user_dir(
        raw.get("import_root", normalized["import_root"]),
        DEFAULT_SETTINGS["import_root"],
    )
    normalized["publish_dir"] = str(raw.get("publish_dir") or "").strip()
    if normalized["publish_dir"]:
        normalized["publish_dir"] = os.path.abspath(os.path.expanduser(normalized["publish_dir"]))
    normalized["publish_hook"] = str(raw.get("publish_hook") or "").strip()
    normalized["publish_site_base_url"] = str(raw.get("publish_site_base_url") or "").strip().rstrip("/")
    normalized["share_brand_name"] = str(raw.get("share_brand_name") or "").strip()

    profile = str(raw.get("cache_profile", normalized["cache_profile"])).strip().lower()
    normalized["cache_profile"] = profile if profile in CACHE_PROFILES else DEFAULT_SETTINGS["cache_profile"]
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

    normalized["people_scan_enabled"] = _normalize_bool(
        raw.get("people_scan_enabled", normalized["people_scan_enabled"]),
        DEFAULT_SETTINGS["people_scan_enabled"],
    )
    normalized["caption_scan_enabled"] = _normalize_bool(
        raw.get("caption_scan_enabled", normalized["caption_scan_enabled"]),
        DEFAULT_SETTINGS["caption_scan_enabled"],
    )
    normalized["people_auto_install"] = _normalize_bool(
        raw.get("people_auto_install", normalized["people_auto_install"]),
        DEFAULT_SETTINGS["people_auto_install"],
    )
    normalized["show_loupe_cache_status"] = bool(
        raw.get("show_loupe_cache_status", normalized["show_loupe_cache_status"])
    )
    normalized["refine_semantic_pairing"] = _normalize_bool(
        raw.get("refine_semantic_pairing", normalized["refine_semantic_pairing"]),
        DEFAULT_SETTINGS["refine_semantic_pairing"],
    )
    normalized["ranking_taste_blend"] = _normalize_bool(
        raw.get("ranking_taste_blend", normalized["ranking_taste_blend"]),
        DEFAULT_SETTINGS["ranking_taste_blend"],
    )
    normalized["share_cookie_secret"] = str(raw.get("share_cookie_secret") or "").strip()
    normalized["owner_key_hash"] = str(raw.get("owner_key_hash") or "").strip()
    try:
        normalized["owner_session_epoch"] = max(0, int(raw.get("owner_session_epoch") or 0))
    except (TypeError, ValueError):
        normalized["owner_session_epoch"] = 0
    category_memory = raw.get("import_category_memory")
    normalized["import_category_memory"] = {
        str(path): str(category)
        for path, category in (category_memory.items() if isinstance(category_memory, dict) else ())
        if str(category) in {"raw", "personal", "film", "export"}  # taxonomy.IMPORT_CATEGORIES
    }
    normalized["hub_url"] = str(raw.get("hub_url") or "").strip().rstrip("/")
    normalized["device_token"] = str(raw.get("device_token") or "").strip()
    normalized["paired_hub_id"] = str(raw.get("paired_hub_id") or "").strip()
    normalized["require_device_token"] = _normalize_bool(
        raw.get("require_device_token", normalized["require_device_token"]),
        DEFAULT_SETTINGS["require_device_token"],
    )
    normalized["setup_completed"] = _normalize_bool(
        raw.get("setup_completed", normalized["setup_completed"]), False
    )
    try:
        normalized["elo_stars_min_comparisons"] = max(
            1,
            int(raw.get("elo_stars_min_comparisons", normalized["elo_stars_min_comparisons"])),
        )
    except (TypeError, ValueError):
        normalized["elo_stars_min_comparisons"] = DEFAULT_SETTINGS["elo_stars_min_comparisons"]
    thresholds = raw.get("elo_stars_thresholds", normalized.get("elo_stars_thresholds"))
    if isinstance(thresholds, (list, tuple)) and len(thresholds) >= 3:
        try:
            parsed = [float(thresholds[0]), float(thresholds[1]), float(thresholds[2])]
            if 0 < parsed[0] <= parsed[1] <= parsed[2] <= 1.0:
                normalized["elo_stars_thresholds"] = parsed
            else:
                normalized["elo_stars_thresholds"] = list(DEFAULT_SETTINGS["elo_stars_thresholds"])
        except (TypeError, ValueError):
            normalized["elo_stars_thresholds"] = list(DEFAULT_SETTINGS["elo_stars_thresholds"])
    else:
        normalized["elo_stars_thresholds"] = list(DEFAULT_SETTINGS["elo_stars_thresholds"])
    normalized["cloud_backup_remote"] = str(raw.get("cloud_backup_remote") or "").strip().rstrip(":")
    normalized["cloud_backup_dest_prefix"] = str(raw.get("cloud_backup_dest_prefix") or "").strip().rstrip("/")
    trees_raw = raw.get("cloud_backup_trees", normalized.get("cloud_backup_trees"))
    if isinstance(trees_raw, (list, tuple)):
        seen_trees: set[str] = set()
        trees: list[str] = []
        for item in trees_raw:
            path = os.path.abspath(os.path.expanduser(str(item or "").strip()))
            if not path or path in seen_trees:
                continue
            seen_trees.add(path)
            trees.append(path)
        normalized["cloud_backup_trees"] = trees
    else:
        normalized["cloud_backup_trees"] = []
    normalized["cloud_backup_bwlimit"] = (
        str(raw.get("cloud_backup_bwlimit") or DEFAULT_SETTINGS["cloud_backup_bwlimit"]).strip()
        or DEFAULT_SETTINGS["cloud_backup_bwlimit"]
    )
    normalized["cloud_backup_exclude_from_catalog"] = _normalize_bool(
        raw.get(
            "cloud_backup_exclude_from_catalog",
            normalized["cloud_backup_exclude_from_catalog"],
        ),
        DEFAULT_SETTINGS["cloud_backup_exclude_from_catalog"],
    )
    normalized["cloud_backup_nightly_enabled"] = _normalize_bool(
        raw.get("cloud_backup_nightly_enabled", normalized["cloud_backup_nightly_enabled"]),
        DEFAULT_SETTINGS["cloud_backup_nightly_enabled"],
    )
    normalized.update(_derive_runtime_tuning(normalized["memory_cache_gb"]))
    normalized["prefetch_workers"] = min(
        normalized["prefetch_workers"],
        normalized["background_thumb_workers"],
    )
    normalized["settings_version"] = SETTINGS_VERSION

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


def public_settings(raw: dict | None = None) -> dict:
    values = _copy_settings(raw if isinstance(raw, dict) else get_settings())
    for key in PRIVATE_SETTING_KEYS:
        values.pop(key, None)
    for key in SERVER_ONLY_SETTING_KEYS:
        if key in values:
            values[key] = ""  # masked: configured server-side (env or settings file)
    return values


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
    defaults = _copy_settings(DEFAULT_SETTINGS)
    for key in PRIVATE_SETTING_KEYS:
        defaults.pop(key, None)
    return {
        "settings_path": SETTINGS_PATH,
        "defaults": defaults,
        "cache_profiles": list(CACHE_PROFILES),
        "embedding_model_presets": [
            {"key": key, **value, "model_dir": _default_model_dir(value["model_id"])}
            for key, value in EMBED_MODEL_PRESETS.items()
        ],
        "caption_model_presets": [
            {"key": key, **value, "model_dir": _default_model_dir(value["model_id"])}
            for key, value in CAPTION_MODEL_PRESETS.items()
        ],
    }
