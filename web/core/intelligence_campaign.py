"""Keep archive-understanding workers aligned with the user's saved intent."""

from __future__ import annotations

from typing import Any


WORKER_SETTING_KEYS = (
    "embedding_scan_enabled",
    "people_scan_enabled",
    "caption_scan_enabled",
)


def apply_saved_worker_intent(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[str, ...]:
    """Apply changed toggles; resource deferrals never change saved intent."""

    changed = tuple(
        key
        for key in WORKER_SETTING_KEYS
        if bool(previous.get(key)) != bool(current.get(key))
    )
    if not changed:
        return ()

    if "embedding_scan_enabled" in changed:
        import embedding_worker

        if current["embedding_scan_enabled"]:
            embedding_worker.resume_embedding_worker(persist=False)
        else:
            embedding_worker.pause_embedding_worker(persist=False)

    if "people_scan_enabled" in changed:
        import face_worker

        if current["people_scan_enabled"]:
            face_worker.resume_face_worker(persist=False)
        else:
            face_worker.pause_face_worker(persist=False)

    if "caption_scan_enabled" in changed:
        import caption_worker

        if current["caption_scan_enabled"]:
            caption_worker.resume_caption_worker(persist=False)
        else:
            caption_worker.pause_caption_worker(persist=False)

    return changed
