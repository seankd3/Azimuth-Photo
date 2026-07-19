"""Classify which HTTP traffic counts as real browsing for pregen politeness.

Monitoring, health, status, and telemetry must never look like a user in the
library — otherwise ops pollers clamp bulk waves to ACTIVITY_BURST forever.
"""

from __future__ import annotations

# Exact paths that never count as browsing (ops / health / telemetry).
NON_BROWSE_PATHS = frozenset(
    {
        "/api/ai/status",
        "/api/auth/status",
        "/api/backup/cloud/status",
        "/api/cache/status",
        "/api/cache/pregen/status",
        "/api/captions/status",
        "/api/catalog/metadata/status",
        "/api/counts",
        "/api/dev/status",
        "/api/geo/status",
        "/api/health",
        "/api/health/details",
        "/api/pair/status",
        "/api/people/status",
        "/api/quality/status",
        "/api/scan/status",
        "/api/settings",
        "/api/stacks/rebuild/status",
        "/api/sync/status",
        "/api/system/backup/restore-status",
        "/api/system/integrity/status",
        "/api/ui/settings",
    }
)

# Prefixes for families of non-browse probes (keep short; prefer exact paths).
NON_BROWSE_PREFIXES = (
    "/api/health",
    "/api/telemetry",
)

# Path suffixes that are always ops/status probes, never media browsing.
NON_BROWSE_SUFFIXES = (
    "/status",
    "/counts",
)

# Static assets never count.
NON_BROWSE_STATIC_PREFIX = "/static"


def marks_user_activity(path: str) -> bool:
    """Return True only for genuine media/browse traffic."""
    cleaned = (path or "").split("?", 1)[0]
    if not cleaned or cleaned.startswith(NON_BROWSE_STATIC_PREFIX):
        return False
    if cleaned in NON_BROWSE_PATHS:
        return False
    if any(cleaned.startswith(prefix) for prefix in NON_BROWSE_PREFIXES):
        return False
    if any(cleaned.endswith(suffix) for suffix in NON_BROWSE_SUFFIXES):
        return False
    return True


# Back-compat alias used by app factory / middleware wiring.
IDLE_ACTIVITY_EXCLUDED_PATHS = NON_BROWSE_PATHS
