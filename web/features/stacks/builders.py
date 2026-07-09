"""Automatic stack builders for variants, bursts, and cross-source duplicates."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import PurePath

from data import connection as data_connection
from data.repositories import stacks as stack_repository
from features.search.similarity import scan_duplicate_pairs


logger = logging.getLogger(__name__)
STACK_KINDS = ("burst", "variant", "crosssource")
RAW_EXTS = {"arw", "cr2", "cr3", "dng", "nef", "orf", "raf", "rw2"}
TIFF_EXTS = {"tif", "tiff"}
JPG_EXTS = {"jpg", "jpeg"}
SOCIAL_HINTS = ("facebook photos", "google photos")
EXPORTED_EDITS_HINT = "exported edits"
VARIANT_MARKER_TOKENS = (
    "-Edit",
    "-Edit-N",
    "-FullJPG",
    "-Discord",
    "-Edited PNG",
    "-Web",
    "-Instagram",
    "-Print",
)
VARIANT_EXTENSION_SIBLING_EXTS = JPG_EXTS | TIFF_EXTS | {"png"}
VARIANT_GROUP_MEMBER_CAP = 12
_TRAILING_VARIANT_COUNTER_RE = re.compile(r"-\d+$")
_VARIANT_MARKER_SUFFIXES = tuple(sorted(VARIANT_MARKER_TOKENS, key=len, reverse=True))


def _active_rows(db_path: str) -> dict[int, dict]:
    conn = data_connection.open_sync(db_path)
    try:
        cursor = conn.execute(
            "SELECT i.*, s.path AS source_path, s.display_name AS source_name "
            "FROM images i LEFT JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE i.status IN ('kept', 'maybe') AND i.missing_at IS NULL"
        )
        return {int(row["id"]): dict(row) for row in cursor.fetchall()}
    finally:
        data_connection.close_sync(conn, db_path=db_path)


def _capture_ts(value) -> float | None:
    if not value:
        return None
    text = str(value)
    for fmt, length in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:length], fmt).timestamp()
        except ValueError:
            continue
    return None


def _variant_key(filename: str) -> tuple[str, bool]:
    stem = os.path.splitext(filename or "")[0]
    text = (stem or "").strip(" -_.,")
    stripped_marker = False
    for _ in range(16):
        counter = _TRAILING_VARIANT_COUNTER_RE.search(text)
        if counter and _ends_with_variant_marker(text[: counter.start()]):
            text = text[: counter.start()].strip(" -_.,")
            continue

        next_text = _strip_variant_marker(text)
        if next_text is None:
            break
        stripped_marker = True
        text = next_text
    return text.lower(), stripped_marker


def _variant_stem(filename: str) -> str:
    return _variant_key(filename)[0]


def _ends_with_variant_marker(text: str) -> bool:
    lower = (text or "").lower()
    return any(lower.endswith(token.lower()) for token in _VARIANT_MARKER_SUFFIXES)


def _strip_variant_marker(text: str) -> str | None:
    lower = (text or "").lower()
    for token in _VARIANT_MARKER_SUFFIXES:
        if lower.endswith(token.lower()):
            return text[: -len(token)].strip(" -_.,")
    return None


def _image_ext(row: dict) -> str:
    value = row.get("file_ext") or os.path.splitext(row.get("filename") or "")[1]
    return str(value or "").lower().lstrip(".")


def _top_folder(row: dict) -> str:
    filepath = row.get("filepath") or ""
    source_path = row.get("source_path") or ""
    try:
        rel = os.path.relpath(filepath, source_path) if source_path else filepath
    except ValueError:
        rel = filepath
    parts = PurePath(rel).parts
    return (parts[0] if parts else "").lower()


def _source_bucket(row: dict) -> tuple:
    return (row.get("source_id"), _top_folder(row))


def _ext_rank(row: dict) -> int:
    ext = str(row.get("file_ext") or os.path.splitext(row.get("filename") or "")[1]).lower().lstrip(".")
    if ext in RAW_EXTS:
        return 4
    if ext in TIFF_EXTS:
        return 3
    if ext == "png":
        return 2
    if ext in JPG_EXTS:
        return 1
    return 0


def _crosssource_origin_rank(row: dict) -> int:
    haystack = " ".join(
        str(value or "").lower()
        for value in (row.get("source_name"), row.get("source_path"), row.get("filepath"))
    )
    if EXPORTED_EDITS_HINT in haystack:
        return 2
    if any(hint in haystack for hint in SOCIAL_HINTS):
        return 0
    return 1


def representative_id(member_ids: list[int], rows: dict[int, dict], *, kind: str) -> int:
    def key(image_id: int):
        row = rows[image_id]
        cross_rank = _crosssource_origin_rank(row) if kind == "crosssource" else 0
        pixels = int(row.get("width") or 0) * int(row.get("height") or 0)
        return (
            cross_rank,
            _ext_rank(row),
            pixels,
            int(row.get("file_size") or 0),
            float(row.get("elo") or 1200.0),
            -int(image_id),
        )

    return max(member_ids, key=key)


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, value: int) -> int:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left

    def groups(self) -> list[list[int]]:
        grouped: dict[int, list[int]] = {}
        for value in list(self.parent):
            grouped.setdefault(self.find(value), []).append(value)
        return [sorted(values) for values in grouped.values() if len(values) >= 2]


def _groups_from_union(uf: _UnionFind, rows: dict[int, dict], kind: str, scores: dict[tuple[int, int], float]):
    groups = []
    for member_ids in uf.groups():
        rep_id = representative_id(member_ids, rows, kind=kind)
        member_scores = {}
        for image_id in member_ids:
            best = None
            for other_id in member_ids:
                if other_id == image_id:
                    continue
                pair = tuple(sorted((image_id, other_id)))
                if pair in scores:
                    best = scores[pair] if best is None else max(best, scores[pair])
            member_scores[image_id] = best
        groups.append((member_ids, rep_id, member_scores))
    return groups


def _build_variant_groups_with_stats(db_path: str, rows: dict[int, dict] | None = None) -> dict:
    rows = rows or _active_rows(db_path)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for image_id, row in rows.items():
        stem, stripped_marker = _variant_key(row.get("filename") or "")
        if not stem:
            continue
        folder = os.path.dirname(row.get("filepath") or "")
        grouped.setdefault((folder, stem), []).append({
            "image_id": image_id,
            "stripped_marker": stripped_marker,
            "ext": _image_ext(row),
        })
    groups = []
    oversize_candidate_groups = 0
    for members in grouped.values():
        if len(members) < 2:
            continue
        has_marker = any(member["stripped_marker"] for member in members)
        sibling_exts = {
            member["ext"]
            for member in members
            if member["ext"] in VARIANT_EXTENSION_SIBLING_EXTS
        }
        if not has_marker and len(sibling_exts) < 2:
            continue
        member_ids = [int(member["image_id"]) for member in members]
        if len(member_ids) > VARIANT_GROUP_MEMBER_CAP:
            oversize_candidate_groups += 1
            continue
        rep_id = representative_id(member_ids, rows, kind="variant")
        groups.append((member_ids, rep_id, {}))
    if oversize_candidate_groups:
        logger.warning("Skipped %s oversize variant candidate groups", oversize_candidate_groups)
    return {
        "groups": groups,
        "oversize_candidate_groups": oversize_candidate_groups,
    }


def build_variant_groups(db_path: str, rows: dict[int, dict] | None = None):
    return _build_variant_groups_with_stats(db_path, rows)["groups"]


def _embedding_pairs():
    try:
        import embed_cache
    except ImportError:
        return None, None, "embed_cache unavailable"
    image_ids, matrix = embed_cache.get_matrix_sync() if hasattr(embed_cache, "get_matrix_sync") else (None, None)
    if image_ids is None or matrix is None:
        try:
            import asyncio

            image_ids, matrix = asyncio.run(embed_cache.get_matrix())
        except Exception as exc:
            return None, None, str(exc)
    if image_ids is None or matrix is None or len(image_ids) < 2:
        return None, None, "embedding matrix unavailable"
    return image_ids, matrix, ""


def build_embedding_groups(db_path: str, rows: dict[int, dict] | None = None):
    rows = rows or _active_rows(db_path)
    image_ids, matrix, error = _embedding_pairs()
    if image_ids is None or matrix is None:
        return {"burst": [], "crosssource": [], "skipped": error}
    indexed_ids = [int(image_id) for image_id in image_ids if int(image_id) in rows]
    if len(indexed_ids) < 2:
        return {"burst": [], "crosssource": [], "skipped": "no active embedded images"}
    id_positions = {int(image_id): idx for idx, image_id in enumerate(image_ids)}
    matrix_positions = [id_positions[image_id] for image_id in indexed_ids]
    filtered_matrix = matrix[matrix_positions]
    pairs, _total, _hidden = scan_duplicate_pairs(
        filtered_matrix,
        indexed_ids,
        threshold=0.92,
        batch_size=500,
        limit=None,
    )
    burst = _UnionFind()
    crosssource = _UnionFind()
    burst_scores: dict[tuple[int, int], float] = {}
    cross_scores: dict[tuple[int, int], float] = {}
    for left_id, right_id, score in pairs:
        left = rows.get(int(left_id))
        right = rows.get(int(right_id))
        if left is None or right is None:
            continue
        pair_key = tuple(sorted((int(left_id), int(right_id))))
        if score >= 0.97:
            if _source_bucket(left) != _source_bucket(right):
                crosssource.union(int(left_id), int(right_id))
                cross_scores[pair_key] = score
            else:
                burst.union(int(left_id), int(right_id))
                burst_scores[pair_key] = score
            continue
        left_ts = _capture_ts(left.get("date_taken"))
        right_ts = _capture_ts(right.get("date_taken"))
        if left_ts is None or right_ts is None:
            continue
        if abs(left_ts - right_ts) > 60:
            continue
        if (left.get("camera_model") or "") != (right.get("camera_model") or ""):
            continue
        burst.union(int(left_id), int(right_id))
        burst_scores[pair_key] = score
    return {
        "burst": _groups_from_union(burst, rows, "burst", burst_scores),
        "crosssource": _groups_from_union(crosssource, rows, "crosssource", cross_scores),
        "skipped": "",
    }


def _auto_stack_counts(db_path: str, kinds) -> dict[str, int]:
    requested = [kind for kind in kinds if kind in STACK_KINDS]
    if not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    conn = data_connection.open_sync(db_path)
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS count FROM stacks "
            f"WHERE auto = 1 AND kind IN ({placeholders}) "
            "GROUP BY kind",
            requested,
        ).fetchall()
        counts = {str(row["kind"]): int(row["count"]) for row in rows}
        return {kind: counts.get(kind, 0) for kind in requested}
    finally:
        data_connection.close_sync(conn, db_path=db_path)


def rebuild_stacks(db_path: str, kinds=None) -> dict:
    requested = tuple(kind for kind in (kinds or STACK_KINDS) if kind in STACK_KINDS)
    timings = {}
    results = {}
    rows = _active_rows(db_path)
    started = time.perf_counter()
    if "burst" in requested or "crosssource" in requested:
        pair_started = time.perf_counter()
        embedding_groups = build_embedding_groups(db_path, rows)
        timings["embedding_scan_ms"] = round((time.perf_counter() - pair_started) * 1000, 1)
    else:
        embedding_groups = {"burst": [], "crosssource": [], "skipped": ""}

    build_order = [kind for kind in ("burst", "variant", "crosssource") if kind in requested]
    for kind in build_order:
        kind_started = time.perf_counter()
        variant_stats = {}
        if kind == "variant":
            variant_stats = _build_variant_groups_with_stats(db_path, rows)
            groups = variant_stats["groups"]
        else:
            groups = embedding_groups.get(kind) or []
        upserted = stack_repository.upsert_auto_stacks_sync(db_path, kind, groups)
        results[kind] = {
            **upserted,
            "inserted": upserted.get("created", 0),
            "candidate_groups": len(groups),
        }
        if variant_stats.get("oversize_candidate_groups"):
            results[kind]["skipped_oversize_candidate_groups"] = variant_stats["oversize_candidate_groups"]
        timings[f"{kind}_ms"] = round((time.perf_counter() - kind_started) * 1000, 1)

    final_counts = _auto_stack_counts(db_path, build_order)
    for kind, count in final_counts.items():
        if kind in results:
            results[kind]["created"] = count
            results[kind]["stack_count"] = count

    if embedding_groups.get("skipped"):
        results["embedding_skipped"] = embedding_groups["skipped"]
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    logger.info("Rebuilt stacks: results=%s timings=%s", results, timings)
    return {"results": results, "timings": timings}
