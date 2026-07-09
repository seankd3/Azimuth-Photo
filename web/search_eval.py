"""Run a lightweight proxy eval over local search channels.

Usage:
    web/.venv/bin/python web/search_eval.py --mode fused
    web/.venv/bin/python web/search_eval.py --mode metadata
    web/.venv/bin/python web/search_eval.py --mode embedding
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import os
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR))

import app as app_module  # noqa: E402,F401
import db  # noqa: E402
from core import query_constraints  # noqa: E402

EVAL_DIR = WEB_DIR / "eval"
QUERIES_PATH = EVAL_DIR / "queries.json"
REPORT_PATH = EVAL_DIR / "last_run.md"


def _load_queries() -> list[dict]:
    text = QUERIES_PATH.read_text(encoding="utf-8")
    text = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("//")
    )
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("queries.json must contain a list")
    return data


def _folder_for(row: dict) -> str:
    return os.path.dirname(str(row.get("filepath") or ""))


def _hint_match(row: dict, hints: dict) -> bool:
    filename = str(row.get("filename") or "").lower()
    folder = _folder_for(row).lower()
    for pattern in hints.get("filename_patterns") or []:
        if fnmatch.fnmatch(filename, str(pattern).lower()):
            return True
    for pattern in hints.get("folders") or []:
        if fnmatch.fnmatch(folder, str(pattern).lower()):
            return True
    return False


async def _rows_for_scores(scores: dict[int, float], limit: int = 20) -> list[dict]:
    ordered_ids = [
        image_id for image_id, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ][:limit]
    rows_by_id = await db.get_active_images_by_ids(ordered_ids)
    return [rows_by_id[image_id] for image_id in ordered_ids if image_id in rows_by_id]


async def _search(query: str, mode: str) -> tuple[list[dict], dict]:
    if mode == "metadata":
        ranked = await db.metadata_search_ranked_image_ids(query, max_results=400)
        rows = await _rows_for_scores(dict(ranked), limit=20)
        return rows, {"search_mode": "metadata", "search_sources": ["metadata"]}

    if mode == "embedding":
        async def _noop_apply_metadata(_result, _query):
            return None

        result = await query_constraints.resolve_text_search(
            query,
            apply_metadata_ids=_noop_apply_metadata,
            get_search_query_embedding=db.get_search_query_embedding,
            store_search_query_embedding=db.store_search_query_embedding,
            extension_search_terms=db.IMAGE_EXTENSION_SEARCH_TERMS,
            active_embedding_config=db.active_embedding_config,
            get_settings=__import__("settings").get_settings,
        )
        rows = await _rows_for_scores(result.get("scores") or {}, limit=20)
        return rows, result

    result = await query_constraints.resolve_configured_text_search(query)
    rows = await _rows_for_scores(result.get("scores") or {}, limit=20)
    return rows, result


def _metric(rows: list[dict], hints: dict, k: int) -> float:
    top = rows[:k]
    if not top:
        return 0.0
    return sum(1 for row in top if _hint_match(row, hints)) / len(top)


async def run(mode: str) -> str:
    await db.init_db()
    lines = [
        f"# Search Eval: {mode}",
        "",
        "Proxy metrics use placeholder folder/filename hints. Sean should refine these against the archive.",
        "",
        "| Query | Mode | Sources | Hit@10 | Hit@20 | Top Results |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for item in _load_queries():
        query = str(item.get("query") or "").strip()
        hints = dict(item.get("relevant_hints") or {})
        rows, result = await _search(query, mode)
        top_results = "<br>".join(
            f"{row.get('filename')} <small>{_folder_for(row)}</small>"
            for row in rows[:20]
        )
        lines.append(
            "| {query} | {mode} | {sources} | {hit10:.2f} | {hit20:.2f} | {top} |".format(
                query=query.replace("|", "\\|"),
                mode=str(result.get("search_mode") or mode),
                sources=", ".join(result.get("search_sources") or []),
                hit10=_metric(rows, hints, 10),
                hit20=_metric(rows, hints, 20),
                top=top_results.replace("|", "\\|") or "(no results)",
            )
        )
    report = "\n".join(lines) + "\n"
    REPORT_PATH.write_text(report, encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("metadata", "embedding", "fused"), default="fused")
    args = parser.parse_args()
    asyncio.run(run(args.mode))
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
