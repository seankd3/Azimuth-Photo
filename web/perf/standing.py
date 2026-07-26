"""Standing end-to-end benchmark against the deterministic QA catalog."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import statistics
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx


BENCH_IMAGE_COUNT = 5_003
DEFAULT_ITERATIONS = 20
THUMB_SAMPLE_COUNT = 50
COLD_THUMB_START = 2
CACHED_THUMB_START = COLD_THUMB_START + THUMB_SAMPLE_COUNT


def _ms(seconds: float) -> float:
    return round(seconds * 1_000.0, 2)


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": _ms(statistics.median(samples)),
        "p95_ms": _ms(_percentile(samples, 0.95)),
    }


class _Session:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=120.0)

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_args) -> None:
        self._client.close()

    def fetch(self, path: str) -> tuple[int, bytes, float]:
        started = time.perf_counter()
        response = self._client.get(path)
        return response.status_code, response.content, time.perf_counter() - started

    def json_fetch(self, path: str) -> tuple[int, dict, float]:
        status, body, elapsed = self.fetch(path)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{path} returned invalid JSON ({status})") from error
        return status, payload, elapsed


def _timed_gets(
    session: _Session, path: str, iterations: int, *, warm: bool = False
) -> tuple[dict[str, float], dict]:
    samples: list[float] = []
    payload: dict = {}
    if warm:
        # Discard one cold hit so the recorded p50/p95 is steady-state latency.
        session.json_fetch(path)
    for _ in range(iterations):
        status, payload, elapsed = session.json_fetch(path)
        if status != 200:
            raise RuntimeError(f"{path} returned {status}, expected 200")
        samples.append(elapsed)
    return _summary(samples), payload


def _peak_rss_mb(pid: int) -> float:
    status_path = Path(f"/proc/{pid}/status")
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024.0, 2)
    raise RuntimeError(f"VmHWM is unavailable for benchmark server pid {pid}")


def _develop_open(session: _Session, image_id: int) -> tuple[float, list[int]]:
    started = time.perf_counter()
    statuses: list[int] = []
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        status, _body, _elapsed = session.fetch(f"/api/develop/{image_id}/base.jpg")
        statuses.append(status)
        if status == 200:
            if 202 not in statuses:
                raise RuntimeError("fixture Develop base was already warm; expected 202 before 200")
            return _ms(time.perf_counter() - started), statuses
        if status != 202:
            raise RuntimeError(f"Develop base returned {status}, expected 202 or 200")
        time.sleep(0.025)
    raise RuntimeError("Develop base did not become ready within 120 seconds")


def _suggestions(session: _Session) -> tuple[float, float, int]:
    status, payload, cold = session.json_fetch("/api/collections/suggestions")
    if status != 200:
        raise RuntimeError(f"collection suggestions returned {status}")
    status, cached_payload, cached = session.json_fetch("/api/collections/suggestions")
    if status != 200 or cached_payload != payload:
        raise RuntimeError("cached collection suggestions changed response semantics")
    return _ms(cold), _ms(cached), len(payload.get("suggestions") or [])


def _prepare_cold_fixture(database: Path, develop_cache: Path) -> None:
    shutil.rmtree(develop_cache, ignore_errors=True)
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            "DELETE FROM cache_entries WHERE size = 'sm' AND image_id BETWEEN ? AND ?",
            (COLD_THUMB_START, COLD_THUMB_START + THUMB_SAMPLE_COUNT - 1),
        )
        conn.commit()
    finally:
        conn.close()


def _fixture_context(scratch: Path) -> tuple[dict, Any]:
    os.environ["AZIMUTH_QA_SCRATCH"] = str(scratch)
    os.environ["AZIMUTH_QA_ACTIVE_IMAGE_COUNT"] = str(BENCH_IMAGE_COUNT)

    # Imports intentionally follow the environment override: qa.config owns all
    # fixture paths and ProbeServer owns the isolated uvicorn lifecycle.
    from qa.fixture import reset_fixture
    from qa.server import ProbeServer

    manifest = reset_fixture()
    database = Path(manifest["database"])
    _prepare_cold_fixture(database, scratch / "fixture-home" / "cache" / "develop")
    return manifest, ProbeServer(log_path=scratch / "bench-server.log")


def _fixture_metrics(*, scratch: Path, iterations: int) -> tuple[dict[str, float], dict]:
    manifest, probe_server = _fixture_context(scratch)
    boot_started = time.perf_counter()
    with probe_server as server:
        with _Session(server.base_url) as session:
            status, first_page, _elapsed = session.json_fetch(
                "/api/rankings?limit=100&sort=elo"
            )
            if status != 200:
                raise RuntimeError(f"first Library page returned {status}")
            boot_ms = _ms(time.perf_counter() - boot_started)

            # Cold boot is captured above. Let startup callbacks release the event
            # loop before measuring the separate steady-state first-page budget.
            time.sleep(0.2)
            session.fetch("/api/rankings?limit=100&sort=elo")
            images, _payload = _timed_gets(
                session, "/api/rankings?limit=100&sort=elo", iterations
            )
            cached_thumbs = []
            for image_id in range(CACHED_THUMB_START, CACHED_THUMB_START + THUMB_SAMPLE_COUNT):
                status, _body, elapsed = session.fetch(f"/api/thumb/sm/{image_id}")
                if status != 200:
                    raise RuntimeError(f"cached sm thumbnail {image_id} returned {status}")
                cached_thumbs.append(elapsed)
            cold_thumbs = []
            for image_id in range(COLD_THUMB_START, COLD_THUMB_START + THUMB_SAMPLE_COUNT):
                status, _body, elapsed = session.fetch(f"/api/thumb/sm/{image_id}")
                if status != 200:
                    raise RuntimeError(f"cold sm thumbnail {image_id} returned {status}")
                cold_thumbs.append(elapsed)

            text_search, search_payload = _timed_gets(
                session,
                "/api/search?" + urllib.parse.urlencode({"q": "nebula", "limit": 50}),
                max(3, min(iterations, 5)),
            )
            semantic_search: dict[str, float] | None = None
            sources = {str(source).lower() for source in search_payload.get("search_sources") or []}
            if "semantic" in sources or "embedding" in sources:
                semantic_search, _payload = _timed_gets(
                    session,
                    "/api/search?" + urllib.parse.urlencode({"q": "nebula", "limit": 50, "deep": "true"}),
                    max(3, min(iterations, 5)),
                )

            develop_ms, develop_statuses = _develop_open(session, int(manifest["raw_image_id"]))
            # Prime persisted suggestion inputs before the measured fresh
            # process.  This keeps the KPI about an ordinary cold response,
            # not a once-per-parser-version migration backfill.
            status, _payload, _elapsed = session.json_fetch("/api/collections/suggestions")
            if status != 200:
                raise RuntimeError(f"collection suggestions priming returned {status}")
            sync_status, _payload = _timed_gets(
                session, "/api/sync/status", max(3, min(iterations, 5))
            )

            # A second page completes the standard browse script before reading
            # the process high-water mark.
            session.fetch("/")
            session.fetch("/api/rankings?limit=100&offset=100&sort=date_taken")
            peak_rss_mb = _peak_rss_mb(server.process.pid)

    # A process restart clears the response TTL without discarding the fixture
    # database, so this is a true cold request after lazy derived-data backfill.
    with type(probe_server)(log_path=scratch / "bench-server.log") as suggestion_server:
        with _Session(suggestion_server.base_url) as session:
            suggestions_cold, suggestions_cached, suggestion_count = _suggestions(session)

    metrics = {
        "server_boot_first_200_ms": boot_ms,
        "images_first_page_p50_ms": images["p50_ms"],
        "images_first_page_p95_ms": images["p95_ms"],
        "thumb_sm_cached_p50_ms": _summary(cached_thumbs)["p50_ms"],
        "thumb_sm_cached_p95_ms": _summary(cached_thumbs)["p95_ms"],
        "thumb_sm_cold_p50_ms": _summary(cold_thumbs)["p50_ms"],
        "thumb_sm_cold_p95_ms": _summary(cold_thumbs)["p95_ms"],
        "search_text_p50_ms": text_search["p50_ms"],
        "develop_open_202_to_200_ms": develop_ms,
        "collection_suggestions_cold_ms": suggestions_cold,
        "collection_suggestions_cached_ms": suggestions_cached,
        "sync_status_p50_ms": sync_status["p50_ms"],
        "peak_rss_mb": peak_rss_mb,
    }
    if semantic_search is not None:
        metrics["search_semantic_p50_ms"] = semantic_search["p50_ms"]
    details = {
        "profile": "qa-5000-visible",
        "active_images": int(manifest["active_images"]),
        "visible_images": int(manifest["visible_images"]),
        "suggestion_count": suggestion_count,
        "develop_statuses": develop_statuses,
        "semantic_available": semantic_search is not None,
        "first_page_images": len(first_page.get("images") or []),
    }
    return metrics, details


def _real_metrics(base_url: str, *, iterations: int) -> tuple[dict[str, float], dict]:
    """Run GET-only probes against an explicitly supplied instance."""

    base_url = base_url.rstrip("/")
    with _Session(base_url) as session:
        images, first_page = _timed_gets(session, "/api/rankings?limit=100&sort=elo", iterations, warm=True)
        image_ids = [int(image["id"]) for image in first_page.get("images") or []]
        thumb_samples = []
        for image_id in image_ids[:iterations]:
            status, _body, elapsed = session.fetch(f"/api/thumb/sm/{image_id}?cached=true")
            if status not in (200, 204):
                raise RuntimeError(f"read-only cached thumbnail probe returned {status}")
            thumb_samples.append(elapsed)
        text_search, payload = _timed_gets(session, "/api/search?q=photo&limit=50", min(iterations, 5), warm=True)
        sources = {str(source).lower() for source in payload.get("search_sources") or []}
        semantic_search: dict[str, float] | None = None
        if "semantic" in sources or "embedding" in sources:
            semantic_search, _payload = _timed_gets(
                session,
                "/api/search?q=photo&limit=50&deep=true",
                min(iterations, 5),
            )
        suggestions, _payload = _timed_gets(session, "/api/collections/suggestions", 2, warm=True)
        sync, _payload = _timed_gets(session, "/api/sync/status", min(iterations, 5), warm=True)
        # Heavy catalog surfaces — the interactions that must feel instant.
        counts, _payload = _timed_gets(session, "/api/counts", min(iterations, 5), warm=True)
        histogram, _payload = _timed_gets(session, "/api/date-histogram", min(iterations, 5), warm=True)
        filter_options, _payload = _timed_gets(session, "/api/filter-options", min(iterations, 5), warm=True)
        people, _payload = _timed_gets(session, "/api/people?limit=24", 2, warm=True)
        map_markers, _payload = _timed_gets(session, "/api/map/markers", 2, warm=True)
    metrics = {
        "images_first_page_p50_ms": images["p50_ms"],
        "images_first_page_p95_ms": images["p95_ms"],
        "thumb_sm_cached_p50_ms": _summary(thumb_samples)["p50_ms"],
        "thumb_sm_cached_p95_ms": _summary(thumb_samples)["p95_ms"],
        "search_text_p50_ms": text_search["p50_ms"],
        "collection_suggestions_cached_ms": suggestions["p50_ms"],
        "sync_status_p50_ms": sync["p50_ms"],
        "counts_p50_ms": counts["p50_ms"],
        "date_histogram_p50_ms": histogram["p50_ms"],
        "filter_options_p50_ms": filter_options["p50_ms"],
        "people_p50_ms": people["p50_ms"],
        "map_markers_p50_ms": map_markers["p50_ms"],
    }
    if semantic_search is not None:
        metrics["search_semantic_p50_ms"] = semantic_search["p50_ms"]
    return metrics, {
        "profile": "real-read-only",
        "base_url": base_url,
        "semantic_available": semantic_search is not None,
        "get_only": True,
    }


def run(*, scratch: Path, iterations: int = DEFAULT_ITERATIONS) -> tuple[dict[str, float], dict]:
    real_url = os.environ.get("AZIMUTH_BENCH_URL", "").strip()
    if real_url:
        return _real_metrics(real_url, iterations=iterations)
    return _fixture_metrics(scratch=scratch, iterations=iterations)
