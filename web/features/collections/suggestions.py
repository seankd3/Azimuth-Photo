"""Suggested collections from time gaps and embedding clusters.

Suggestions are read-only drafts: accepting one goes through the normal
create-collection API. Event suggestions group by capture-time gaps; cluster
suggestions group by embedding similarity across time ("photos like these").
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from data import connection

EVENT_GAP_SECONDS = 6 * 3600
EVENT_MIN_PHOTOS = 12
EVENT_MAX_SUGGESTIONS = 8
CLUSTER_MIN_PHOTOS = 12
CLUSTER_MAX_PHOTOS = 1500
CLUSTER_MAX_SUGGESTIONS = 6
CLUSTER_COUNT = 24
MEMBER_ID_LIMIT = 500
EXISTING_OVERLAP_LIMIT = 0.6
_CACHE_TTL_SECONDS = 600.0

_cache: dict = {"key": None, "data": None, "expires": 0.0}


def _parse_taken(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").timestamp()
        except ValueError:
            return None


def _event_title(start_ts: float, end_ts: float) -> str:
    start = datetime.fromtimestamp(start_ts)
    end = datetime.fromtimestamp(end_ts)
    if start.date() == end.date():
        return start.strftime("%B %d, %Y").replace(" 0", " ")
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.strftime('%B %d').replace(' 0', ' ')}–{end.day}, {end.year}"
    if start.year == end.year:
        return (
            f"{start.strftime('%b %d').replace(' 0', ' ')} – "
            f"{end.strftime('%b %d, %Y').replace(' 0', ' ')}"
        )
    return f"{start.strftime('%b %Y')} – {end.strftime('%b %Y')}"


def group_events(rows: list[dict], *, gap_seconds: float = EVENT_GAP_SECONDS,
                 min_photos: int = EVENT_MIN_PHOTOS) -> list[dict]:
    """Group date-sorted rows into events split on capture-time gaps.

    Rows need id, taken (epoch seconds), elo, camera_model. Pure function so
    the grouping rule stays unit-testable.
    """
    events: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if len(current) < min_photos:
            current.clear()
            return
        best = max(current, key=lambda row: float(row.get("elo") or 0))
        cameras: dict[str, int] = {}
        for row in current:
            camera = (row.get("camera_model") or "").strip()
            if camera:
                cameras[camera] = cameras.get(camera, 0) + 1
        top_camera = max(cameras, key=cameras.get) if cameras else ""
        events.append({
            "image_ids": [int(row["id"]) for row in current],
            "start": current[0]["taken"],
            "end": current[-1]["taken"],
            "cover_image_id": int(best["id"]),
            "camera": top_camera,
        })
        current.clear()

    for row in rows:
        if current and (row["taken"] - current[-1]["taken"]) > gap_seconds:
            flush()
        current.append(row)
    flush()
    return events


async def _event_suggestions(db_path: str) -> list[dict]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.date_taken, i.elo, i.camera_model "
            "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL AND i.date_taken IS NOT NULL "
            "AND i.date_taken != '' "
            "ORDER BY i.date_taken ASC"
        )
        rows = []
        for row in await cursor.fetchall():
            taken = _parse_taken(row["date_taken"])
            if taken is None:
                continue
            rows.append({
                "id": row["id"],
                "taken": taken,
                "elo": row["elo"],
                "camera_model": row["camera_model"],
            })
    finally:
        await connection.close_async(conn, db_path=db_path)

    events = group_events(rows)
    # Newest first; size as a tiebreaker so big recent shoots lead.
    events.sort(key=lambda event: (event["end"], len(event["image_ids"])), reverse=True)
    suggestions = []
    for event in events[:EVENT_MAX_SUGGESTIONS]:
        count = len(event["image_ids"])
        subtitle = f"{count:,} photos"
        if event["camera"]:
            subtitle = f"{event['camera']} · {subtitle}"
        suggestions.append({
            "kind": "event",
            "title": _event_title(event["start"], event["end"]),
            "subtitle": subtitle,
            "count": count,
            "cover_image_id": event["cover_image_id"],
            "image_ids": event["image_ids"][:MEMBER_ID_LIMIT],
        })
    return suggestions


def _cluster_assignments(matrix, n_clusters: int):
    import numpy as np
    from sklearn.cluster import MiniBatchKMeans

    kmeans = MiniBatchKMeans(
        n_clusters=n_clusters,
        batch_size=4096,
        n_init=3,
        random_state=42,
    )
    labels = kmeans.fit_predict(matrix)
    # Vectors are L2-normalized, so dot with the centroid direction is the
    # cosine similarity used as the cohesion score.
    centroids = kmeans.cluster_centers_
    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit_centroids = centroids / norms
    return labels, unit_centroids


async def _cluster_suggestions(db_path: str) -> list[dict]:
    try:
        import numpy as np
        import embed_cache
    except ImportError:
        return []

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None or len(image_ids) < CLUSTER_COUNT * 2:
        return []

    labels, unit_centroids = await asyncio.to_thread(
        _cluster_assignments, matrix, CLUSTER_COUNT
    )

    def score_clusters():
        results = []
        for cluster_id in range(CLUSTER_COUNT):
            indices = np.flatnonzero(labels == cluster_id)
            if not (CLUSTER_MIN_PHOTOS <= indices.size <= CLUSTER_MAX_PHOTOS):
                continue
            sims = matrix[indices] @ unit_centroids[cluster_id]
            cohesion = float(sims.mean())
            order = np.argsort(-sims)
            member_ids = [int(image_ids[int(indices[int(i)])]) for i in order]
            results.append({
                "cohesion": cohesion,
                "member_ids": member_ids,
            })
        results.sort(key=lambda item: item["cohesion"] * len(item["member_ids"]), reverse=True)
        return results[:CLUSTER_MAX_SUGGESTIONS]

    clusters = await asyncio.to_thread(score_clusters)
    if not clusters:
        return []

    # One metadata pass for date spans and covers.
    wanted = {member_id for cluster in clusters for member_id in cluster["member_ids"][:MEMBER_ID_LIMIT]}
    meta: dict[int, dict] = {}
    conn = await connection.open_async(db_path)
    try:
        ids = list(wanted)
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                f"SELECT i.id, i.date_taken, i.elo FROM images i "
                f"JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
                f"AND i.missing_at IS NULL AND i.id IN ({placeholders})",
                chunk,
            )
            for row in await cursor.fetchall():
                meta[int(row["id"])] = dict(row)
    finally:
        await connection.close_async(conn, db_path=db_path)

    suggestions = []
    for cluster in clusters:
        members = [m for m in cluster["member_ids"][:MEMBER_ID_LIMIT] if m in meta]
        if len(members) < CLUSTER_MIN_PHOTOS:
            continue
        taken = sorted(
            ts for ts in (_parse_taken(meta[m].get("date_taken")) for m in members)
            if ts is not None
        )
        if taken:
            start_year = datetime.fromtimestamp(taken[0]).year
            end_year = datetime.fromtimestamp(taken[-1]).year
            span = (
                f"spanning {start_year}–{end_year}" if end_year > start_year
                else f"from {start_year}"
            )
        else:
            span = "undated"
        cover = max(members, key=lambda m: float(meta[m].get("elo") or 0))
        suggestions.append({
            "kind": "cluster",
            "title": "Photos that belong together",
            "subtitle": f"{len(members):,} similar photos {span}",
            "count": len(members),
            "cover_image_id": cover,
            "image_ids": members,
            "cohesion": round(cluster["cohesion"], 3),
        })
    return suggestions


async def _existing_member_ids(db_path: str) -> set[int]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT DISTINCT image_id FROM collection_images")
        return {int(row["image_id"]) for row in await cursor.fetchall()}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def collection_suggestions(db_path: str, *, db_signature: str = "") -> dict:
    now = time.monotonic()
    cache_key = db_signature or db_path
    if _cache["key"] == cache_key and _cache["data"] is not None and now < _cache["expires"]:
        return _cache["data"]

    events, clusters, existing = await asyncio.gather(
        _event_suggestions(db_path),
        _cluster_suggestions(db_path),
        _existing_member_ids(db_path),
    )

    suggestions = []
    for suggestion in events + clusters:
        ids = suggestion["image_ids"]
        if existing and ids:
            overlap = sum(1 for image_id in ids if image_id in existing) / len(ids)
            if overlap > EXISTING_OVERLAP_LIMIT:
                continue
        suggestions.append(suggestion)

    response = {"suggestions": suggestions}
    _cache.update({"key": cache_key, "data": response, "expires": now + _CACHE_TTL_SECONDS})
    return response


def invalidate_cache() -> None:
    _cache.update({"key": None, "data": None, "expires": 0.0})
