"""Read-only health receipt for Azimuth Photo's intelligence engine."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from core.runtime_paths import resolve_runtime_paths
from features.search import evaluation
import settings


DEFAULT_QUERY_SET = Path(__file__).resolve().parent / "eval" / "queries.json"


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _ratio(part: int, total: int) -> float:
    return round((int(part) / int(total)) * 100, 2) if total > 0 else 0.0


def _active_join(alias: str = "i") -> str:
    return (
        f"JOIN catalog_sources s ON s.id = {alias}.source_id AND s.included = 1 "
        f"WHERE {alias}.status IN ('kept', 'maybe') AND {alias}.missing_at IS NULL "
        f"AND {alias}.trashed_at IS NULL"
    )


def collect_health(
    conn: sqlite3.Connection,
    *,
    embedding_model_key: str,
    caption_model_key: str,
    face_model_id: str,
    query_set: Path = DEFAULT_QUERY_SET,
) -> dict:
    active_images = _count(
        conn,
        "SELECT COUNT(*) FROM images i " + _active_join(),
    )
    embedded = _count(
        conn,
        "SELECT COUNT(DISTINCT e.image_id) FROM embeddings_by_model e "
        "JOIN images i ON i.id = e.image_id " + _active_join() + " AND e.model_key = ?",
        (embedding_model_key,),
    )
    captioned = _count(
        conn,
        "SELECT COUNT(DISTINCT c.image_id) FROM image_captions c "
        "JOIN images i ON i.id = c.image_id " + _active_join() + " AND c.model_key = ?",
        (caption_model_key,),
    )
    caption_done = _count(
        conn,
        "SELECT COUNT(*) FROM caption_scan_images WHERE model_key = ? AND status = 'done'",
        (caption_model_key,),
    )
    caption_error_rows = _count(
        conn,
        "SELECT COUNT(*) FROM caption_scan_images WHERE model_key = ? AND status = 'error'",
        (caption_model_key,),
    )
    caption_oom = _count(
        conn,
        "SELECT COUNT(*) FROM caption_scan_images WHERE model_key = ? AND status = 'error' "
        "AND LOWER(last_error) LIKE '%out of memory%'",
        (caption_model_key,),
    )
    caption_errors = max(0, caption_error_rows - caption_oom)
    face_scanned = _count(
        conn,
        "SELECT COUNT(DISTINCT fsi.image_id) FROM face_scan_images fsi "
        "JOIN images i ON i.id = fsi.image_id " + _active_join() + " AND fsi.model_id = ?",
        (face_model_id,),
    )
    detected_faces = _count(
        conn,
        "SELECT COUNT(*) FROM face_detections WHERE embedding_model = ? AND ignored = 0",
        (face_model_id,),
    )
    active_people = _count(
        conn,
        "SELECT COUNT(*) FROM people WHERE merged_into_person_id IS NULL "
        "AND status != 'ignored' AND face_count > 0",
    )
    singleton_people = _count(
        conn,
        "SELECT COUNT(*) FROM people WHERE merged_into_person_id IS NULL "
        "AND status != 'ignored' AND face_count = 1",
    )
    named_people = _count(
        conn,
        "SELECT COUNT(*) FROM people WHERE merged_into_person_id IS NULL "
        "AND status != 'ignored' AND face_count > 0 "
        "AND (status = 'named' OR TRIM(COALESCE(name, '')) != '')",
    )
    merge_suggestions = _count(
        conn,
        "SELECT COUNT(*) FROM people_merge_suggestions WHERE status = 'pending'",
    )
    comparisons = _count(conn, "SELECT COUNT(*) FROM comparisons")

    query_specs = evaluation.load_evaluation_set(query_set)
    judged_queries = sum(1 for query in query_specs if query.judged)
    return {
        "models": {
            "embedding": embedding_model_key,
            "caption": caption_model_key,
            "face": face_model_id,
        },
        "library": {"active_images": active_images},
        "search": {
            "embedded_images": embedded,
            "coverage_pct": _ratio(embedded, active_images),
            "evaluation_queries": len(query_specs),
            "judged_evaluation_queries": judged_queries,
        },
        "captions": {
            "captioned_images": captioned,
            "coverage_pct": _ratio(captioned, active_images),
            "done": caption_done,
            "errors": caption_errors,
            "system_deferred": caption_oom,
            "oom_errors": caption_oom,
            "success_pct": _ratio(caption_done, caption_done + caption_errors),
        },
        "people": {
            "scanned_images": face_scanned,
            "scan_coverage_pct": _ratio(face_scanned, active_images),
            "detected_faces": detected_faces,
            "active_clusters": active_people,
            "singleton_clusters": singleton_people,
            "singleton_cluster_pct": _ratio(singleton_people, active_people),
            "named_clusters": named_people,
            "pending_merge_suggestions": merge_suggestions,
        },
        "taste": {"direct_comparisons": comparisons},
    }


def render_markdown(health: dict) -> str:
    search = health["search"]
    captions = health["captions"]
    people = health["people"]
    lines = [
        "# Azimuth Intelligence Health",
        "",
        f"Active searchable photos: **{health['library']['active_images']:,}**",
        "",
        "| Capability | Coverage / evidence | Reliability debt |",
        "| --- | ---: | --- |",
        (
            f"| Semantic search | {search['embedded_images']:,} · {search['coverage_pct']:.2f}% "
            f"| {search['judged_evaluation_queries']}/{search['evaluation_queries']} judged quality queries |"
        ),
        (
            f"| Captions | {captions['captioned_images']:,} · {captions['coverage_pct']:.2f}% "
            f"| {captions['errors']:,} media errors · "
            f"{captions['system_deferred']:,} system deferrals |"
        ),
        (
            f"| People | {people['scanned_images']:,} photos · {people['scan_coverage_pct']:.2f}% "
            f"| {people['active_clusters']:,} clusters · {people['singleton_cluster_pct']:.2f}% singletons |"
        ),
        (
            f"| Taste | {health['taste']['direct_comparisons']:,} direct comparisons "
            "| Pairwise prediction quality not yet measured |"
        ),
        "",
        "## Model identities",
        "",
        f"- Embedding: `{health['models']['embedding']}`",
        f"- Caption: `{health['models']['caption']}`",
        f"- Face: `{health['models']['face']}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(resolve_runtime_paths().catalog_db))
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_SET)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    config = settings.get_settings()
    database_uri = f"file:{args.db.resolve()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10)) as conn, conn:
        conn.execute("PRAGMA query_only = ON")
        health = collect_health(
            conn,
            embedding_model_key=settings.embedding_model_key(config),
            caption_model_key=settings.caption_model_key(config),
            face_model_id=str(config.get("face_model_id") or "buffalo_l"),
            query_set=args.queries,
        )
    if args.format == "json":
        print(json.dumps(health, indent=2, sort_keys=True))
    else:
        print(render_markdown(health), end="")


if __name__ == "__main__":
    main()
