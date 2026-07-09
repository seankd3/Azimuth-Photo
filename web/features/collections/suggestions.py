"""Suggested collections from real shoot structure and visual coherence.

Suggestions are read-only drafts: accepting one goes through the normal
create-collection API. Shoot suggestions are generated from folder and filename
structure first, then ranked with size, recency, people, and embedding coherence.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import math
import re
import time
from datetime import datetime
from pathlib import PurePosixPath

from date_inference import DATE_RE
from data import connection
import settings

EVENT_GAP_SECONDS = 6 * 3600
SUGGESTION_MIN_PHOTOS = 8
SUGGESTION_CAP = 12
FALLBACK_EVENT_MAX_SUGGESTIONS = 8
CLUSTER_MIN_PHOTOS = 8
CLUSTER_MAX_PHOTOS = 1500
CLUSTER_MAX_SUGGESTIONS = 6
CLUSTER_COUNT = 24
MEMBER_ID_LIMIT = 500
EXISTING_OVERLAP_LIMIT = 0.7
THEME_MIN_PHOTOS = 12
THEME_MIN_CAPTIONED_RATIO = 0.005
THEME_PAIR_MAX_TAGS = 80
THEME_MAX_CANDIDATES = 24
THEME_FULL_STRENGTH_COVERAGE = 0.25
_CACHE_TTL_SECONDS = 600.0
_DEFAULT_COHERENCE = 0.72
_DATE_RE = DATE_RE
_CAMERA_STEM_RE = re.compile(
    r"^(?:IMG|DSC|PXL|DJI|LRT|R5|R5_|7N4A|_MG|MG|PHOTO|VID)[-_]?\d+",
    re.IGNORECASE,
)
_TRAILING_TOKEN_RE = re.compile(
    r"[-_ ]*(?:\d+\s*of\s*\d+|N?\d{1,6}|IMG[_-]?\d+|DSC[_-]?\d+|R5_*\d+|7N4A\d+|DJI[_-]?\d+|LRT[_-]?\d+|edit|edited|copy|from dng|collage|fulljpg|png|jpg|jpeg)\s*$",
    re.IGNORECASE,
)
_GENERIC_FOLDERS = {
    "",
    "run",
    "media",
    "sean",
    "expansion",
    "photos",
    "photo",
    "personal photos",
    "exported edits",
    "google photos",
    "facebook photos",
    "all selected",
    "fulljpg",
    "full jpg",
    "jpg",
    "jpeg",
    "png",
    "raw",
    "edits",
    "edit",
    "trash",
    "quick edits",
    "2nd edits",
    "second edits",
    "20mb for website",
    "website",
    "general",
    "events",
    "event",
    "portraits",
    "portrait",
    "landscapes",
    "landscape",
    "social",
    "film",
    "weddings",
    "wedding",
    "pets",
    "timelapes",
    "timelapse",
}

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


def _parse_path_date(value: str) -> float | None:
    match = _DATE_RE.search(value or "")
    if not match:
        return None
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            12,
            0,
            0,
        ).timestamp()
    except ValueError:
        return None


def _event_title(start_ts: float, end_ts: float, *, short: bool = False) -> str:
    start = datetime.fromtimestamp(start_ts)
    end = datetime.fromtimestamp(end_ts)
    if start.date() == end.date():
        fmt = "%b %d, %Y" if short else "%B %d, %Y"
        return start.strftime(fmt).replace(" 0", " ")
    if (start.year, start.month) == (end.year, end.month):
        fmt = "%b %d" if short else "%B %d"
        return f"{start.strftime(fmt).replace(' 0', ' ')}–{end.day}, {end.year}"
    if start.year == end.year:
        return (
            f"{start.strftime('%b %d').replace(' 0', ' ')} – "
            f"{end.strftime('%b %d, %Y').replace(' 0', ' ')}"
        )
    return f"{start.strftime('%b %Y')} – {end.strftime('%b %Y')}"


def _clean_words(value: str) -> list[str]:
    text = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", value or "")
    text = re.sub(r"\d+\s*of\s*\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\([^)]*\bof\b[^)]*\)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = text.replace("&", " & ")
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.,")
    if not text:
        return []
    words = [word for word in text.split(" ") if word]
    while words and words[0].upper() in {"SKD", "WAI"}:
        words.pop(0)
    return words


def _title_from_words(words: list[str]) -> str:
    titled = []
    for word in words:
        if word == "&":
            titled.append("&")
        elif word.isupper() and len(word) <= 4:
            titled.append(word)
        else:
            titled.append(word[:1].upper() + word[1:].lower())
    title = " ".join(titled)
    title = re.sub(r"\bAirshow\b", "Air Show", title)
    title = re.sub(r"\bStarbase Wm\b", "Starbase WM", title)
    return title.strip()


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _strip_trailing_noise(value: str) -> str:
    text = (value or "").strip(" -_.,")
    for _ in range(8):
        cleaned = _TRAILING_TOKEN_RE.sub("", text).strip(" -_.,")
        if cleaned == text:
            return cleaned
        text = cleaned
    return text


def _format_shoot_title(base_title: str, date_ts: float | None = None) -> str:
    title = base_title.strip()
    if date_ts is not None:
        return f"{title} - {datetime.fromtimestamp(date_ts).strftime('%b %-d, %Y')}"
    return title


def _is_date_folder(value: str) -> bool:
    text = (value or "").strip()
    return bool(re.fullmatch(r"(?:19|20)\d{2}(?:[-_ ]\d{2}[-_ ]\d{2})?", text))


@lru_cache(maxsize=8192)
def _folder_hint(folder: str) -> dict | None:
    words = _clean_words(folder)
    if not words:
        return None
    title = _title_from_words(words)
    lower = title.lower()
    if not _normalize_key(title) or lower in _GENERIC_FOLDERS or lower.startswith("photos from ") or _is_date_folder(title):
        return None
    date_ts = _parse_path_date(folder)
    return {
        "title": title,
        "key": _normalize_key(title),
        "date": date_ts,
        "source": "folder",
    }


@lru_cache(maxsize=16384)
def _filename_hint(filename: str) -> dict | None:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename or "")
    if not stem or _CAMERA_STEM_RE.match(stem):
        return None
    date_match = _DATE_RE.search(stem)
    date_ts = _parse_path_date(stem) if date_match else None
    prefix = stem[:date_match.start()] if date_match else stem
    prefix = _strip_trailing_noise(prefix)
    if not prefix and date_match:
        prefix = stem[:date_match.start()].strip(" -_.,")
    if not prefix:
        return None
    words = _clean_words(prefix)
    if not words:
        return None
    title = _title_from_words(words)
    if not title or title.lower() in _GENERIC_FOLDERS or _CAMERA_STEM_RE.match(title):
        return None
    key = _normalize_key(title)
    if date_ts is not None:
        key = f"{key}-{datetime.fromtimestamp(date_ts).strftime('%Y-%m-%d')}"
    return {
        "title": title,
        "key": key,
        "date": date_ts,
        "source": "filename",
    }


def shoot_hint_from_path(filepath: str, filename: str = "") -> dict | None:
    """Return the strongest Lightroom-style shoot hint for a path."""
    path = PurePosixPath(filepath or filename or "")
    name = filename or path.name
    file_hint = _filename_hint(name)
    folder_hints = [_folder_hint(part) for part in path.parts[:-1]]
    folder_hints = [hint for hint in folder_hints if hint]
    folder_hint = folder_hints[-1] if folder_hints else None
    if file_hint and (file_hint.get("date") is not None or folder_hint is None):
        return file_hint
    if folder_hint and file_hint:
        folder_words = len(str(folder_hint["title"]).split())
        file_words = len(str(file_hint["title"]).split())
        if folder_words >= file_words:
            return folder_hint
    return folder_hint or file_hint


def _subtitle(count: int, start_ts: float | None, end_ts: float | None) -> str:
    if start_ts is None or end_ts is None:
        return f"{count:,} photos"
    return f"{count:,} photos - {_event_title(start_ts, end_ts, short=True)}"


def _caption_tag_title(tag: str) -> str:
    words = re.sub(r"\s+", " ", str(tag or "").replace("_", " ")).strip()
    return words[:1].upper() + words[1:].lower() if words else "Caption theme"


def _theme_threshold(captioned_count: int) -> int:
    return max(THEME_MIN_PHOTOS, math.ceil(max(0, int(captioned_count)) * THEME_MIN_CAPTIONED_RATIO))


def _coverage_rank_multiplier(captioned_count: int, total_count: int) -> float:
    if total_count <= 0:
        return 0.35
    coverage = max(0.0, min(1.0, float(captioned_count) / float(total_count)))
    strength = min(1.0, coverage / THEME_FULL_STRENGTH_COVERAGE)
    return 0.35 + (0.65 * strength)


def _fingerprint(kind: str, key: str, start_ts: float | None, end_ts: float | None, ids: list[int]) -> str:
    start = int((start_ts or 0) // 86400)
    end = int((end_ts or 0) // 86400)
    sample = "-".join(str(image_id) for image_id in sorted(ids[:8])[:8])
    return f"{kind}|{key}|{start}|{end}|{len(ids)}|{sample}"


def group_events(rows: list[dict], *, gap_seconds: float = EVENT_GAP_SECONDS,
                 min_photos: int = SUGGESTION_MIN_PHOTOS) -> list[dict]:
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


def _best_camera(rows: list[dict]) -> str:
    cameras: dict[str, int] = {}
    for row in rows:
        camera = (row.get("camera_model") or "").strip()
        if camera:
            cameras[camera] = cameras.get(camera, 0) + 1
    return max(cameras, key=cameras.get) if cameras else ""


def _candidate_from_rows(
    *,
    kind: str,
    title: str,
    reason: str,
    key: str,
    rows: list[dict],
) -> dict | None:
    if len(rows) < SUGGESTION_MIN_PHOTOS:
        return None
    ordered = sorted(rows, key=lambda row: (row.get("taken") is None, row.get("taken") or 0, row["id"]))
    ids = [int(row["id"]) for row in ordered]
    dated = [row["taken"] for row in ordered if row.get("taken") is not None]
    start_ts = dated[0] if dated else None
    end_ts = dated[-1] if dated else None
    count = len(ids)
    cover = max(ordered, key=lambda row: float(row.get("elo") or 0))
    return {
        "kind": kind,
        "title": title,
        "subtitle": _subtitle(count, start_ts, end_ts),
        "reason": reason,
        "count": count,
        "cover_image_id": int(cover["id"]),
        "image_ids": ids[:MEMBER_ID_LIMIT],
        "_all_ids": ids,
        "_key": key,
        "_start": start_ts,
        "_end": end_ts,
        "_camera": _best_camera(ordered),
        "_coherence": _DEFAULT_COHERENCE,
    }


def _split_by_time(rows: list[dict]) -> list[list[dict]]:
    dated = [row for row in rows if row.get("taken") is not None]
    if len(dated) < len(rows) * 0.65:
        return [rows]
    ordered = sorted(rows, key=lambda row: (row.get("taken") is None, row.get("taken") or 0, row["id"]))
    groups: list[list[dict]] = []
    current: list[dict] = []
    for row in ordered:
        if current and row.get("taken") is not None and current[-1].get("taken") is not None:
            if (row["taken"] - current[-1]["taken"]) > EVENT_GAP_SECONDS:
                groups.append(current)
                current = []
        current.append(row)
    if current:
        groups.append(current)
    return groups


def _build_shoot_candidates(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_key: dict[str, dict] = {}
    unstructured: list[dict] = []
    for raw in rows:
        row = dict(raw)
        row["taken"] = _parse_taken(row.get("date_taken")) or _parse_path_date(row.get("filepath") or "")
        hint = shoot_hint_from_path(row.get("filepath") or "", row.get("filename") or "")
        if not hint:
            if row["taken"] is not None:
                unstructured.append(row)
            continue
        key = str(hint["key"])
        bucket = by_key.setdefault(key, {"hint": hint, "rows": []})
        bucket["rows"].append(row)

    candidates: list[dict] = []
    for key, bucket in by_key.items():
        hint = bucket["hint"]
        parts = _split_by_time(bucket["rows"])
        for index, part in enumerate(parts):
            dated = [row["taken"] for row in part if row.get("taken") is not None]
            start = min(dated) if dated else hint.get("date")
            end = max(dated) if dated else hint.get("date")
            title = _format_shoot_title(hint["title"], hint.get("date") if hint.get("date") is not None else None)
            if hint.get("date") is None and start is not None and len(parts) > 1:
                title = _format_shoot_title(hint["title"], start)
            reason = "Same shoot folder" if hint.get("source") == "folder" else "Same filename pattern"
            if start is not None and end is not None:
                days = max(1, (datetime.fromtimestamp(end).date() - datetime.fromtimestamp(start).date()).days + 1)
                if days > 1:
                    reason = f"{reason} - {days} days"
            suffix = f"-{index}" if len(parts) > 1 else ""
            candidate = _candidate_from_rows(
                kind="shoot",
                title=title,
                reason=reason,
                key=f"{key}{suffix}",
                rows=part,
            )
            if candidate:
                candidates.append(candidate)

    fallback_events = group_events(
        sorted(unstructured, key=lambda row: row["taken"]),
        min_photos=SUGGESTION_MIN_PHOTOS,
    )
    fallback_candidates: list[dict] = []
    for event in fallback_events[:FALLBACK_EVENT_MAX_SUGGESTIONS]:
        rows_by_id = {int(row["id"]): row for row in unstructured}
        event_rows = [rows_by_id[image_id] for image_id in event["image_ids"] if image_id in rows_by_id]
        candidate = _candidate_from_rows(
            kind="event",
            title=_event_title(event["start"], event["end"]),
            reason="Close capture times",
            key=f"event-{int(event['start'] // 86400)}-{int(event['end'] // 86400)}",
            rows=event_rows,
        )
        if candidate:
            fallback_candidates.append(candidate)
    return candidates, fallback_candidates


async def _shoot_suggestions(db_path: str) -> list[dict]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.filename, i.filepath, i.date_taken, i.elo, i.camera_model "
            "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "ORDER BY i.date_taken ASC, i.id ASC"
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)

    shoot_candidates, fallback_candidates = await asyncio.to_thread(_build_shoot_candidates, rows)
    return shoot_candidates + fallback_candidates


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

    try:
        image_ids, matrix = await embed_cache.get_matrix()
    except RuntimeError:
        return []
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

    # One metadata pass for date spans, covers, and possible human titles.
    wanted = {member_id for cluster in clusters for member_id in cluster["member_ids"][:MEMBER_ID_LIMIT]}
    meta: dict[int, dict] = {}
    conn = await connection.open_async(db_path)
    try:
        ids = list(wanted)
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                f"SELECT i.id, i.filename, i.filepath, i.date_taken, i.elo FROM images i "
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
        hints: dict[str, dict] = {}
        for member_id in members[:120]:
            hint = shoot_hint_from_path(meta[member_id].get("filepath") or "", meta[member_id].get("filename") or "")
            if not hint:
                continue
            item = hints.setdefault(str(hint["key"]), {"hint": hint, "count": 0})
            item["count"] += 1
        top_hint = max(hints.values(), key=lambda item: item["count"])["hint"] if hints else None
        title = "Photos that belong together"
        if top_hint and hints[str(top_hint["key"])]["count"] >= max(6, len(members[:120]) * 0.35):
            title = str(top_hint["title"])
            if top_hint.get("date") is not None:
                title = _format_shoot_title(title, top_hint["date"])
        start_ts = taken[0] if taken else None
        end_ts = taken[-1] if taken else None
        suggestions.append({
            "kind": "cluster",
            "title": title,
            "subtitle": f"{len(members):,} similar photos {span}",
            "reason": "Visual match",
            "count": len(members),
            "cover_image_id": cover,
            "image_ids": members,
            "cohesion": round(cluster["cohesion"], 3),
            "_all_ids": members,
            "_key": f"cluster-{cover}-{len(members)}",
            "_start": start_ts,
            "_end": end_ts,
            "_coherence": float(cluster["cohesion"]),
        })
    return suggestions


async def _caption_theme_stats(conn, model_key: str) -> dict:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c "
        "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
        "AND i.missing_at IS NULL"
    )
    total_row = await cursor.fetchone()
    cursor = await conn.execute(
        "SELECT COUNT(DISTINCT c.image_id) AS c "
        "FROM image_captions c "
        "JOIN images i ON i.id = c.image_id "
        "JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE c.model_key = ? AND s.included = 1 "
        "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
        (model_key,),
    )
    captioned_row = await cursor.fetchone()
    return {
        "total": int(total_row["c"] if total_row else 0),
        "captioned": int(captioned_row["c"] if captioned_row else 0),
    }


def _theme_candidate_from_rows(
    *,
    tags: tuple[str, ...],
    rows: list[dict],
    captioned_count: int,
    total_count: int,
    query: dict | None = None,
) -> dict | None:
    if len(rows) < _theme_threshold(captioned_count):
        return None
    ordered = sorted(rows, key=lambda row: (-float(row.get("elo") or 0), int(row["id"])))
    ids = [int(row["id"]) for row in ordered]
    dated = sorted(
        ts for ts in (_parse_taken(row.get("date_taken")) for row in ordered)
        if ts is not None
    )
    start_ts = dated[0] if dated else None
    end_ts = dated[-1] if dated else None
    cover = ordered[0]
    title = " · ".join(_caption_tag_title(tag) for tag in tags)
    return {
        "kind": "theme",
        "title": title,
        "subtitle": _subtitle(len(ids), start_ts, end_ts),
        "reason": "Caption theme" if len(tags) == 1 else "Shared caption tags",
        "count": len(ids),
        "cover_image_id": int(cover["id"]),
        "image_ids": ids[:MEMBER_ID_LIMIT],
        "_all_ids": ids,
        "_key": "tag:" + "+".join(tags),
        "_start": start_ts,
        "_end": end_ts,
        "_coherence": _DEFAULT_COHERENCE,
        "_rank_multiplier": _coverage_rank_multiplier(captioned_count, total_count),
        "query": query,
    }


async def _theme_suggestions(db_path: str) -> list[dict]:
    model_key = settings.active_caption_config()["model_key"]
    conn = await connection.open_async(db_path)
    try:
        stats = await _caption_theme_stats(conn, model_key)
        threshold = _theme_threshold(stats["captioned"])
        if stats["captioned"] <= 0:
            return []

        cursor = await conn.execute(
            "SELECT it.tag, COUNT(DISTINCT it.image_id) AS count "
            "FROM image_tags it "
            "JOIN images i ON i.id = it.image_id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE it.model_key = ? AND s.included = 1 "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            "GROUP BY it.tag HAVING count >= ? "
            "ORDER BY count DESC, it.tag ASC LIMIT ?",
            (model_key, threshold, THEME_PAIR_MAX_TAGS),
        )
        tag_rows = [dict(row) for row in await cursor.fetchall()]
        if not tag_rows:
            return []

        candidates: list[dict] = []
        for row in tag_rows:
            tag = str(row["tag"])
            cursor = await conn.execute(
                "SELECT i.id, i.date_taken, i.elo "
                "FROM image_tags it "
                "JOIN images i ON i.id = it.image_id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE it.model_key = ? AND it.tag = ? AND s.included = 1 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                "ORDER BY i.elo DESC, i.id ASC",
                (model_key, tag),
            )
            candidate = _theme_candidate_from_rows(
                tags=(tag,),
                rows=[dict(item) for item in await cursor.fetchall()],
                captioned_count=stats["captioned"],
                total_count=stats["total"],
                query={"tag": tag},
            )
            if candidate:
                candidates.append(candidate)

        top_tags = [str(row["tag"]) for row in tag_rows]
        if len(top_tags) > 1:
            placeholders = ",".join("?" for _ in top_tags)
            cursor = await conn.execute(
                "SELECT a.tag AS tag_a, b.tag AS tag_b, COUNT(DISTINCT a.image_id) AS count "
                "FROM image_tags a "
                "JOIN image_tags b ON b.model_key = a.model_key "
                "AND b.image_id = a.image_id AND b.tag > a.tag "
                "JOIN images i ON i.id = a.image_id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE a.model_key = ? AND a.tag IN ({placeholders}) "
                f"AND b.tag IN ({placeholders}) AND s.included = 1 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                "GROUP BY a.tag, b.tag HAVING count >= ? "
                "ORDER BY count DESC, a.tag ASC, b.tag ASC LIMIT ?",
                (model_key, *top_tags, *top_tags, threshold, THEME_MAX_CANDIDATES),
            )
            pair_rows = [dict(row) for row in await cursor.fetchall()]
            for row in pair_rows:
                tag_a = str(row["tag_a"])
                tag_b = str(row["tag_b"])
                cursor = await conn.execute(
                    "SELECT i.id, i.date_taken, i.elo "
                    "FROM image_tags a "
                    "JOIN image_tags b ON b.model_key = a.model_key "
                    "AND b.image_id = a.image_id AND b.tag = ? "
                    "JOIN images i ON i.id = a.image_id "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    "WHERE a.model_key = ? AND a.tag = ? AND s.included = 1 "
                    "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                    "ORDER BY i.elo DESC, i.id ASC",
                    (tag_b, model_key, tag_a),
                )
                candidate = _theme_candidate_from_rows(
                    tags=(tag_a, tag_b),
                    rows=[dict(item) for item in await cursor.fetchall()],
                    captioned_count=stats["captioned"],
                    total_count=stats["total"],
                )
                if candidate:
                    candidates.append(candidate)
        return candidates[:THEME_MAX_CANDIDATES]
    finally:
        await connection.close_async(conn, db_path=db_path)


def _candidate_rank(candidate: dict, now_ts: float) -> float:
    end_ts = candidate.get("_end") or candidate.get("_start") or 0
    age_days = max(0.0, (now_ts - float(end_ts or 0)) / 86400.0) if end_ts else 3650.0
    recency = 1.0 / (1.0 + age_days / 730.0)
    size = min(int(candidate.get("count") or 0), MEMBER_ID_LIMIT)
    coherence = float(candidate.get("_coherence") or _DEFAULT_COHERENCE)
    multiplier = float(candidate.get("_rank_multiplier") or 1.0)
    return coherence * max(1.0, math.sqrt(size)) * recency * multiplier


def _score_coherence(candidates: list[dict], image_ids, matrix) -> list[dict]:
    import numpy as np

    index_by_id = {int(image_id): index for index, image_id in enumerate(image_ids)}
    for candidate in candidates:
        indices = [index_by_id[image_id] for image_id in candidate.get("_all_ids", [])[:240] if image_id in index_by_id]
        if len(indices) < 3:
            continue
        vectors = matrix[np.array(indices)]
        centroid = vectors.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if not norm:
            continue
        sims = vectors @ (centroid / norm)
        candidate["_coherence"] = max(0.05, min(1.0, float(sims.mean())))
        candidate["cohesion"] = round(candidate["_coherence"], 3)
    return candidates


async def _enrich_coherence(candidates: list[dict]) -> list[dict]:
    if not candidates:
        return candidates
    try:
        import embed_cache
    except ImportError:
        return candidates
    try:
        image_ids, matrix = await embed_cache.get_matrix()
    except RuntimeError:
        return candidates
    if image_ids is None or matrix is None:
        return candidates
    return await asyncio.to_thread(_score_coherence, candidates, image_ids, matrix)


def _people_title(names: list[str], start_ts: float | None, end_ts: float | None) -> str:
    label = " & ".join(names[:2])
    if start_ts is None and end_ts is None:
        return label
    ts = start_ts or end_ts
    start = datetime.fromtimestamp(ts)
    if end_ts and start_ts and datetime.fromtimestamp(end_ts).date() != start.date():
        return f"{label} - {_event_title(start_ts, end_ts, short=True)}"
    return f"{label} - {start.strftime('%B %Y')}"


async def _enrich_people(db_path: str, candidates: list[dict]) -> list[dict]:
    wanted = sorted({image_id for candidate in candidates for image_id in candidate.get("_all_ids", [])})
    if not wanted:
        return candidates
    people_by_image: dict[int, set[str]] = {}
    conn = await connection.open_async(db_path)
    try:
        for start in range(0, len(wanted), 900):
            chunk = wanted[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT pim.image_id, TRIM(p.name) AS name "
                "FROM person_image_membership pim "
                "JOIN people p ON p.id = pim.person_id "
                f"WHERE pim.image_id IN ({placeholders}) "
                "AND p.merged_into_person_id IS NULL AND p.status != 'ignored' "
                "AND TRIM(COALESCE(p.name, '')) != ''",
                chunk,
            )
            for row in await cursor.fetchall():
                people_by_image.setdefault(int(row["image_id"]), set()).add(str(row["name"]))
    finally:
        await connection.close_async(conn, db_path=db_path)

    for candidate in candidates:
        ids = candidate.get("_all_ids", [])
        if not ids:
            continue
        counts: dict[str, int] = {}
        for image_id in ids:
            for name in people_by_image.get(image_id, set()):
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            continue
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        top_names = [ranked[0][0]]
        top_images = ranked[0][1]
        if len(ranked) > 1 and ranked[1][1] >= max(3, len(ids) * 0.18):
            top_names.append(ranked[1][0])
            top_images = len({
                image_id
                for image_id in ids
                if people_by_image.get(image_id, set()).intersection(top_names)
            })
        if top_images / len(ids) < 0.6:
            continue
        candidate["title"] = _people_title(top_names, candidate.get("_start"), candidate.get("_end"))
        candidate["reason"] = f"Mostly {' & '.join(top_names)}"
    return candidates


def _public_suggestion(candidate: dict) -> dict:
    ids = candidate.get("_all_ids") or candidate.get("image_ids") or []
    suggestion = {
        "kind": candidate.get("kind") or "shoot",
        "title": candidate.get("title") or "Suggested collection",
        "subtitle": candidate.get("subtitle") or _subtitle(len(ids), candidate.get("_start"), candidate.get("_end")),
        "reason": candidate.get("reason") or "Same shoot",
        "count": int(candidate.get("count") or len(ids)),
        "cover_image_id": int(candidate.get("cover_image_id") or (ids[0] if ids else 0)),
        "image_ids": list(ids[:MEMBER_ID_LIMIT]),
        "fingerprint": _fingerprint(
            str(candidate.get("kind") or "shoot"),
            str(candidate.get("_key") or candidate.get("title") or ""),
            candidate.get("_start"),
            candidate.get("_end"),
            list(ids),
        ),
        "cohesion": round(float(candidate.get("_coherence") or candidate.get("cohesion") or _DEFAULT_COHERENCE), 3),
    }
    if candidate.get("query"):
        suggestion["query"] = dict(candidate["query"])
    return suggestion


def _distinct_candidates(candidates: list[dict]) -> list[dict]:
    selected: list[dict] = []
    selected_ids: list[set[int]] = []
    for candidate in candidates:
        ids = set(candidate.get("_all_ids") or candidate.get("image_ids") or [])
        if not ids:
            continue
        duplicate = False
        for existing in selected_ids:
            smaller = min(len(ids), len(existing))
            if smaller and len(ids & existing) / smaller >= 0.7:
                duplicate = True
                break
        if duplicate:
            continue
        selected.append(candidate)
        selected_ids.append(ids)
        if len(selected) >= SUGGESTION_CAP:
            break
    return selected


async def _existing_member_ids(db_path: str) -> set[int]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT DISTINCT image_id FROM collection_images")
        return {int(row["image_id"]) for row in await cursor.fetchall()}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def collection_suggestions(db_path: str, *, db_signature: str = "") -> dict:
    now = time.monotonic()
    model_key = settings.active_caption_config()["model_key"]
    caption_signature = await _caption_tag_signature(db_path, model_key)
    cache_key = (db_signature or db_path, model_key, caption_signature)
    if _cache["key"] == cache_key and _cache["data"] is not None and now < _cache["expires"]:
        return _cache["data"]

    shoots, themes, existing = await asyncio.gather(
        _shoot_suggestions(db_path),
        _theme_suggestions(db_path),
        _existing_member_ids(db_path),
    )

    candidates = []
    for suggestion in shoots + themes:
        ids = suggestion.get("_all_ids") or suggestion.get("image_ids") or []
        if existing and ids:
            overlap = sum(1 for image_id in ids if image_id in existing) / len(ids)
            if overlap >= EXISTING_OVERLAP_LIMIT:
                continue
        candidates.append(suggestion)

    if len(candidates) < SUGGESTION_CAP:
        for suggestion in await _cluster_suggestions(db_path):
            ids = suggestion.get("_all_ids") or suggestion.get("image_ids") or []
            if existing and ids:
                overlap = sum(1 for image_id in ids if image_id in existing) / len(ids)
                if overlap >= EXISTING_OVERLAP_LIMIT:
                    continue
            candidates.append(suggestion)

    candidates = await _enrich_coherence(candidates)
    candidates = await _enrich_people(db_path, candidates)
    now_ts = time.time()
    candidates.sort(key=lambda item: _candidate_rank(item, now_ts), reverse=True)
    suggestions = [_public_suggestion(candidate) for candidate in _distinct_candidates(candidates)]

    response = {"suggestions": suggestions}
    _cache.update({"key": cache_key, "data": response, "expires": now + _CACHE_TTL_SECONDS})
    return response


def invalidate_cache() -> None:
    _cache.update({"key": None, "data": None, "expires": 0.0})


async def _caption_tag_signature(db_path: str, model_key: str) -> int:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM image_tags WHERE model_key = ?",
            (model_key,),
        )
        row = await cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        await connection.close_async(conn, db_path=db_path)
