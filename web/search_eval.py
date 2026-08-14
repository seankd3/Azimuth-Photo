"""Measure Azimuth Photo search against graded archive judgments.

The default query file is still an unjudged draft. It is useful for collecting
candidate IDs, but quality scores only include queries with explicit image-ID
judgments. This prevents filenames and folders from masquerading as truth.

Examples:
    web/.venv/bin/python web/search_eval.py --mode fused
    web/.venv/bin/python web/search_eval.py --dataset /path/to/judged.json --format json
    web/.venv/bin/python web/search_eval.py --output /tmp/search-quality.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR))

import app as app_module  # noqa: E402,F401
import db  # noqa: E402
import settings  # noqa: E402
from core import query_constraints  # noqa: E402
from data.repositories import images as image_repository  # noqa: E402
from data.repositories import rankings as ranking_repository  # noqa: E402
from features.search import evaluation  # noqa: E402


DEFAULT_DATASET = WEB_DIR / "eval" / "queries.json"


def _folder_for(row: dict) -> str:
    return os.path.dirname(str(row.get("filepath") or ""))


async def _rows_for_scores(scores: dict[int, float], limit: int) -> list[dict]:
    ordered_ids = [
        image_id
        for image_id, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ][:limit]
    rows_by_id = await image_repository.get_active_images_by_ids(db.DB_PATH, ordered_ids)
    return [rows_by_id[image_id] for image_id in ordered_ids if image_id in rows_by_id]


async def _search(query: str, mode: str, limit: int) -> tuple[list[dict], dict]:
    if mode == "metadata":
        ranked = await db.metadata_search_ranked_image_ids(query, max_results=max(400, limit))
        rows = await _rows_for_scores(dict(ranked), limit)
        return rows, {"search_mode": "metadata", "search_sources": ["metadata"]}

    if mode == "embedding":
        async def _noop_apply_metadata(_result, _query):
            return None

        result = await query_constraints.resolve_text_search(
            query,
            deep=True,
            apply_metadata_ids=_noop_apply_metadata,
            get_search_query_embedding=db.get_search_query_embedding,
            store_search_query_embedding=db.store_search_query_embedding,
            extension_search_terms=ranking_repository.IMAGE_EXTENSION_SEARCH_TERMS,
            active_embedding_config=settings.active_embedding_config,
            get_settings=__import__("settings").get_settings,
        )
    else:
        # Evaluation waits for one stable semantic answer. It never grades the
        # 350 ms as-you-type metadata fallback against an embedding result.
        result = await query_constraints.resolve_configured_text_search(query, deep=True)
    rows = await _rows_for_scores(result.get("scores") or {}, limit)
    return rows, result


def _result_preview(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": int(row["id"]),
            "filename": str(row.get("filename") or ""),
            "folder": _folder_for(row),
        }
        for row in rows
    ]


async def run(dataset: Path, mode: str, limit: int) -> dict:
    specs = evaluation.load_evaluation_set(dataset)
    await db.init_db()
    rows = []
    for spec in specs:
        started = time.perf_counter()
        results, search = await _search(spec.query, mode, limit)
        latency_ms = (time.perf_counter() - started) * 1000
        row = evaluation.evaluate_query(
            spec,
            [int(result["id"]) for result in results],
            latency_ms=latency_ms,
            search_mode=str(search.get("search_mode") or mode),
            search_sources=list(search.get("search_sources") or []),
        )
        row["results"] = _result_preview(results)
        rows.append(row)
    return {
        "version": 1,
        "dataset": str(dataset),
        "requested_mode": mode,
        "result_limit": limit,
        "summary": evaluation.aggregate_evaluations(rows),
        "queries": rows,
    }


def _metric(value: object) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    overall = summary["overall"]
    latency = summary["latency_ms"]
    lines = [
        f"# Search Quality — {report['requested_mode']}",
        "",
        (
            f"**{summary['judged_query_count']} judged** / {summary['query_count']} total queries · "
            f"p50 {latency['p50']:.1f} ms · p95 {latency['p95']:.1f} ms"
        ),
        "",
    ]
    if summary["unjudged_query_count"]:
        lines.extend([
            f"> {summary['unjudged_query_count']} queries are candidate-gathering drafts. "
            "They are excluded from quality scores until image IDs receive 0–3 relevance judgments.",
            "",
        ])
    lines.extend([
        "| MRR | nDCG@10 | nDCG@20 | Recall@20 | Recall@50 |",
        "| ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_metric(overall.get('reciprocal_rank'))} "
            f"| {_metric(overall.get('ndcg@10'))} "
            f"| {_metric(overall.get('ndcg@20'))} "
            f"| {_metric(overall.get('recall@20'))} "
            f"| {_metric(overall.get('recall@50'))} |"
        ),
        "",
        "## Queries",
        "",
        "| Query | Category | Mode | ms | nDCG@10 | Recall@20 | Top candidate IDs |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ])
    for row in report["queries"]:
        metrics = row.get("metrics") or {}
        candidate_ids = ", ".join(str(result["id"]) for result in row.get("results", [])[:10])
        lines.append(
            f"| {row['query'].replace('|', chr(92) + '|')} "
            f"| {row['category'].replace('|', chr(92) + '|')} "
            f"| {row['search_mode']} | {row['latency_ms']:.1f} "
            f"| {_metric(metrics.get('ndcg@10'))} "
            f"| {_metric(metrics.get('recall@20'))} "
            f"| {candidate_ids or '—'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mode", choices=("metadata", "embedding", "fused"), default="fused")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be between 1 and 500")

    report = asyncio.run(run(args.dataset, args.mode, args.limit))
    rendered = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_markdown(report)
    )
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
