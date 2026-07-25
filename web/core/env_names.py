"""Canonical env names for Azimuth Photo with legacy PHOTOARCHIVE_* fallback.

New spelling: AZIMUTH_*
Legacy spelling: PHOTOARCHIVE_* (accepted until at least one release after
scripts/docs prefer AZIMUTH_*).
"""

from __future__ import annotations

import logging
import os
from typing import Mapping

log = logging.getLogger(__name__)

NEW_PREFIX = "AZIMUTH_"
LEGACY_PREFIX = "PHOTOARCHIVE_"

# Suffixes shared by both prefixes (HOME, DB_PATH, …).
KNOWN_SUFFIXES = frozenset(
    {
        "HOME",
        "DATA_DIR",
        "CONFIG_DIR",
        "CACHE_DIR",
        "STATE_DIR",
        "DB_PATH",
        "SETTINGS_PATH",
        "THUMB_CACHE_DIR",
        "MODELS_DIR",
        "EMBED_CACHE_DIR",
        "DEVELOP_CACHE_DIR",
        "EXPORT_DIR",
        "LIBRARY_EXPORT_DIR",
        "BACKUP_DIR",
        "RUN_DIR",
        "LOG_DIR",
        "SMOKE_MODE",
        "MODE",
        "HUB_URL",
        "PORT",
        "ACCESS",
        "SHARE_BASE_URL",
        "MAX_LOG_BYTES",
        "MIRROR_MAX_BYTES",
        "GPU_OWNER_FLAG",
        "MEMORY_SOFT_BYTES",
        "MEMORY_HARD_BYTES",
        "MEMORY_RESUME_BYTES",
        "MODEL_IDLE_TTL_SECONDS",
        "MODEL_BUDGET_VRAM_BYTES",
        "MODEL_BUDGET_RAM_BYTES",
        "MODEL_PIN_SECONDS",
        "DEMOSAIC_PROCESSES",
        "DEMOSAIC_IPC",
        "SSD_CACHE_BYTES",
        "TS_DRYRUN",
        "CLIENT_SHA",
    }
)

_warned: set[str] = set()


def env_get(
    suffix: str,
    default: str = "",
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return AZIMUTH_<suffix> if set, else PHOTOARCHIVE_<suffix>, else default."""

    environment = os.environ if environ is None else environ
    key = str(suffix or "").strip().lstrip("_").upper()
    if not key:
        return default
    new_key = f"{NEW_PREFIX}{key}"
    old_key = f"{LEGACY_PREFIX}{key}"
    new_val = str(environment.get(new_key) or "").strip()
    if new_val:
        return new_val
    old_val = str(environment.get(old_key) or "").strip()
    if old_val:
        if old_key not in _warned:
            _warned.add(old_key)
            log.info(
                "env_names: using legacy %s; prefer %s",
                old_key,
                new_key,
            )
        return old_val
    return default


def env_truthy(suffix: str, *, environ: Mapping[str, str] | None = None) -> bool:
    raw = env_get(suffix, "", environ=environ).lower()
    return raw in {"1", "true", "yes", "on"}


def setdefault_both(suffix: str, value: str) -> None:
    """Set both spellings only when neither is already present."""

    key = str(suffix or "").strip().lstrip("_").upper()
    new_key = f"{NEW_PREFIX}{key}"
    old_key = f"{LEGACY_PREFIX}{key}"
    if new_key not in os.environ and old_key not in os.environ:
        os.environ[new_key] = value
        # Keep legacy key too so third-party scripts still see a value.
        os.environ[old_key] = value
    elif new_key not in os.environ and old_key in os.environ:
        os.environ[new_key] = os.environ[old_key]
    elif old_key not in os.environ and new_key in os.environ:
        os.environ[old_key] = os.environ[new_key]
