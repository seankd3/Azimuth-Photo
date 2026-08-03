"""Whether someone is using the app, and what background work owes them.

Two jobs, and they belong together. Classifying which HTTP traffic is really a
person browsing — monitoring, health and telemetry must never look like one,
or ops pollers clamp bulk waves forever. And holding every background chore to
one rule about it, because four chores each being politely half-considerate
adds up to an app that does not respond: measured on a 142k library, browsing
was 23ms with the chores quiet and minutes with them running.

The rule, in one sentence: a chore steps through its work, and between steps it
gives way. ``politely`` is that sentence as code — a chore wraps its own loop in
it and needs no counter, no threshold and no opinion of its own.
"""

from __future__ import annotations

import asyncio
import time

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


# How long since the last real interaction before background work may proceed.
QUIET_SECONDS = 2.0
# How often a waiting chore re-asks. Short enough to get going promptly once the
# app is idle, long enough that waiting costs nothing.
_POLL_SECONDS = 0.25

_last_activity_at = time.monotonic()


def note_activity(at: float | None = None) -> None:
    """Record that someone did something — now, or at a given monotonic time."""

    global _last_activity_at
    _last_activity_at = time.monotonic() if at is None else float(at)


def idle_seconds() -> float:
    """How long the app has been left alone."""

    return max(0.0, time.monotonic() - _last_activity_at)


def someone_is_here(quiet_seconds: float = QUIET_SECONDS) -> bool:
    return idle_seconds() < quiet_seconds


async def wait_for_quiet(quiet_seconds: float = QUIET_SECONDS) -> None:
    """Hold an async chore until the app is being left alone."""

    while someone_is_here(quiet_seconds):
        await asyncio.sleep(_POLL_SECONDS)


def wait_for_quiet_sync(quiet_seconds: float = QUIET_SECONDS) -> None:
    """The same, for a chore running on a worker thread."""

    while someone_is_here(quiet_seconds):
        time.sleep(_POLL_SECONDS)


# How many steps a chore may take before giving way again. Small enough that
# someone arriving mid-chore waits a blink; large enough that asking costs
# nothing. One number, because one number is easier to reason about than four.
STEPS_BETWEEN_PAUSES = 25


async def politely(items, *, every: int = STEPS_BETWEEN_PAUSES):
    """Step through work, giving way to whoever is using the app.

    The whole courtesy, in the only place it needs to exist:

        async for row in user_activity.politely(rows):
            ...
    """

    for index, item in enumerate(items):
        if index % every == 0:
            await wait_for_quiet()
        yield item


def politely_sync(items, *, every: int = STEPS_BETWEEN_PAUSES):
    """The same, for a chore running on a worker thread."""

    for index, item in enumerate(items):
        if index % every == 0:
            wait_for_quiet_sync()
        yield item
