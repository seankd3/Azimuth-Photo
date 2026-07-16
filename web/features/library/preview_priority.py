"""Recent library scopes that need preview-cache attention."""

import re
import time
from collections import deque

from data.repositories import rankings as ranking_repository


_priority_scopes = deque(maxlen=4)


def record_scope(*, folder="", collection_id: int = 0, timestamp: float | None = None) -> None:
    normalized_folder = ranking_repository.folder_cache_value(folder)
    normalized_collection_id = max(0, int(collection_id or 0))
    if not normalized_folder and not normalized_collection_id:
        return

    scope = {
        "folder": normalized_folder,
        "collection_id": normalized_collection_id,
        "timestamp": time.time() if timestamp is None else float(timestamp),
    }
    for existing in tuple(_priority_scopes):
        if (
            existing["folder"] == scope["folder"]
            and existing["collection_id"] == scope["collection_id"]
        ):
            _priority_scopes.remove(existing)
    _priority_scopes.append(scope)


def recent_scopes() -> list[dict]:
    return [dict(scope) for scope in reversed(_priority_scopes)]


def discard_scope(scope: dict) -> None:
    try:
        _priority_scopes.remove(scope)
    except ValueError:
        pass


def clear_scopes() -> None:
    _priority_scopes.clear()


def scope_label(scope: dict, *, collection_name: str = "") -> str:
    folder = scope.get("folder") or ""
    folders = list(folder) if isinstance(folder, tuple) else [folder] if folder else []
    if folders:
        first = re.split(r"[/\\]+", str(folders[0]).rstrip("/\\"))[-1] or str(folders[0])
        return f"{first} +{len(folders) - 1}" if len(folders) > 1 else first
    if collection_name:
        return str(collection_name)
    collection_id = int(scope.get("collection_id") or 0)
    return f"Collection {collection_id}" if collection_id else ""
