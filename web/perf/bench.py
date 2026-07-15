#!/usr/bin/env python3
"""Repeatable, production-scale performance probes for Azimuth Photo.

The benchmark never opens the production catalog. Point ``--db`` at a copied
catalog and it will run an isolated smoke server, SQL plans, RAW-stage timing,
and representative thumbnail-pregeneration probes.

Example (from ``web/``)::

    .venv/bin/python perf/bench.py \
      --db /mnt/expansion/tmp/perf.db \
      --port 8081 --output /tmp/dev8/perf-baseline.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import socket
import sqlite3
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


WEB_DIR = Path(__file__).resolve().parents[1]
PYTHON = WEB_DIR / ".venv" / "bin" / "python"
DEFAULT_DB = Path("/mnt/expansion/tmp/perf.db")
DEFAULT_DEVELOP_CACHE = Path("/mnt/expansion/tmp/photoarchive-perf-develop")
DEFAULT_THUMB_CACHE = Path("/mnt/expansion/tmp/photoarchive-perf-thumbs")
BASE_HEADER = struct.Struct("<8sII")


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 2)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def _timing_summary(values: list[float]) -> dict[str, float]:
    return {
        "min_ms": _ms(min(values)),
        "median_ms": _ms(statistics.median(values)),
        "p95_ms": _ms(_percentile(values, 0.95)),
        "max_ms": _ms(max(values)),
    }


def _fetch(url: str, timeout: float = 90.0) -> tuple[int, bytes, dict[str, str], float]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read()
        status = response.status
        headers = dict(response.headers.items())
    return status, body, headers, time.perf_counter() - started


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def _server(
    db_path: Path,
    port: int,
    *,
    develop_cache: Path,
    thumb_cache: Path,
) -> Iterator[tuple[str, float]]:
    if _port_open(port):
        raise RuntimeError(f"port {port} is already in use; refusing to touch that process")

    environment = os.environ.copy()
    environment.update(
        {
            "PHOTOARCHIVE_SMOKE_MODE": "1",
            "PHOTOARCHIVE_DEVELOP_CACHE_DIR": str(develop_cache),
            "PHOTOARCHIVE_THUMB_CACHE_DIR": str(thumb_cache),
            "PYTHONUNBUFFERED": "1",
        }
    )
    bootstrap = (
        "import db; "
        f"db.DB_PATH={str(db_path)!r}; "
        "import uvicorn; from app import app; "
        "from features.library import service as library_service; "
        "library_service._schedule_thumbnail_prefetch=None; "
        "library_service._schedule_result_thumbnail_memory_warm=None; "
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')"
    )
    log = tempfile.NamedTemporaryFile(prefix="photoarchive-perf-server-", suffix=".log", delete=False)
    log_path = Path(log.name)
    log.close()
    output = log_path.open("wb")
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(PYTHON), "-c", bootstrap],
        cwd=WEB_DIR,
        env=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                message = log_path.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"benchmark server exited early:\n{message[-4000:]}")
            try:
                _fetch(f"{base_url}/api/dev/status", timeout=0.5)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.025)
        else:
            raise RuntimeError("benchmark server did not become ready within 45 seconds")
        yield base_url, time.perf_counter() - started
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        output.close()
        log_path.unlink(missing_ok=True)


def _http_case(
    db_path: Path,
    port: int,
    path: str,
    iterations: int,
    develop_cache: Path,
    thumb_cache: Path,
) -> tuple[dict[str, Any], float]:
    with _server(
        db_path,
        port,
        develop_cache=develop_cache,
        thumb_cache=thumb_cache,
    ) as (base_url, startup):
        status, body, _headers, cold = _fetch(base_url + path)
        warm = [_fetch(base_url + path)[3] for _ in range(iterations)]
    return (
        {
            "path": path,
            "status": status,
            "bytes": len(body),
            "cold_ms": _ms(cold),
            "warm": _timing_summary(warm),
        },
        startup,
    )


def _representative_dates(conn: sqlite3.Connection) -> tuple[str, str]:
    year = conn.execute(
        "SELECT substr(date_taken, 1, 4) AS year, COUNT(*) AS n "
        "FROM images WHERE date_taken IS NOT NULL AND length(date_taken) >= 4 "
        "GROUP BY year ORDER BY n DESC LIMIT 1"
    ).fetchone()[0]
    month = conn.execute(
        "SELECT substr(date_taken, 1, 7) AS month, COUNT(*) AS n "
        "FROM images WHERE date_taken LIKE ? "
        "GROUP BY month ORDER BY n DESC LIMIT 1",
        (f"{year}-%",),
    ).fetchone()[0]
    return str(year), str(month)


def _remap_cache_metadata(db_path: Path, thumb_cache: Path) -> dict[str, Any]:
    """Make copied cache metadata visible to the isolated benchmark server.

    The physical production cache is never read or written. Background warming
    is disabled in ``_server``; only the cache-root identity in the copied DB is
    changed so rankings exercise the same visible-row counts and sorts.
    """
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT cache_root, COUNT(*) AS n FROM cache_entries "
            "WHERE size='sm' GROUP BY cache_root ORDER BY n DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"from": "", "to": str(thumb_cache), "rows": 0}
        source_root = str(row[0])
        target_root = str(thumb_cache)
        if source_root == target_root:
            return {"from": source_root, "to": target_root, "rows": int(row[1])}
        cursor = conn.execute(
            "UPDATE cache_entries SET cache_root=? WHERE cache_root=?",
            (target_root, source_root),
        )
        conn.commit()
        return {"from": source_root, "to": target_root, "rows": int(cursor.rowcount or 0)}
    finally:
        conn.close()


def benchmark_http(
    db_path: Path,
    port: int,
    iterations: int,
    raw_image_id: int,
    develop_cache: Path,
    thumb_cache: Path,
) -> dict[str, Any]:
    cache_remap = _remap_cache_metadata(db_path, thumb_cache)
    conn = sqlite3.connect(db_path)
    try:
        year, month = _representative_dates(conn)
    finally:
        conn.close()

    cases = {
        "grid_default": "/api/rankings?limit=100&sort=elo",
        "grid_year": f"/api/rankings?limit=100&sort=elo&date_taken={year}",
        "grid_month": f"/api/rankings?limit=100&sort=elo&date_taken={month}",
        "date_histogram": "/api/date-histogram",
    }
    results: dict[str, Any] = {}
    startups: list[float] = []
    for name, path in cases.items():
        result, startup = _http_case(
            db_path,
            port,
            path,
            iterations,
            develop_cache,
            thumb_cache,
        )
        results[name] = result
        startups.append(startup)

    shutil.rmtree(develop_cache, ignore_errors=True)
    shutil.rmtree(thumb_cache, ignore_errors=True)
    raw_result, startup = _http_case(
        db_path,
        port,
        f"/api/full/{raw_image_id}",
        max(1, min(iterations, 3)),
        develop_cache,
        thumb_cache,
    )
    results["raw_full"] = raw_result
    startups.append(startup)

    with _server(
        db_path,
        port,
        develop_cache=develop_cache,
        thumb_cache=thumb_cache,
    ) as (base_url, startup):
        _status, html, _headers, shell_cold = _fetch(base_url + "/")
        shell_warm = [_fetch(base_url + "/")[3] for _ in range(iterations)]
        initial_assets = sorted(
            asset.decode("utf-8")
            for asset in set(re.findall(rb"(?:src|href)=[\"']([^\"']*/static/[^\"'?]+)", html))
        )
        pending = list(initial_assets)
        assets: set[str] = set()
        asset_bytes = 0
        asset_seconds = 0.0
        js_bytes = 0
        while pending:
            asset = pending.pop()
            if asset in assets:
                continue
            assets.add(asset)
            if asset.startswith("http://") or asset.startswith("https://"):
                url = asset
            else:
                url = base_url + (asset if asset.startswith("/") else "/" + asset)
            _asset_status, body, _asset_headers, elapsed = _fetch(url)
            asset_bytes += len(body)
            asset_seconds += elapsed
            if asset.split("?", 1)[0].endswith(".js"):
                js_bytes += len(body)
                imports = re.findall(
                    rb"(?:from\s+|import\s*)[\"']([^\"']+\.js)[\"']",
                    body,
                )
                for raw_import in imports:
                    imported = urllib.parse.urljoin(asset, raw_import.decode("utf-8"))
                    if imported.startswith("/static/") and imported not in assets:
                        pending.append(imported)
    startups.append(startup)
    results["page_shell"] = {
        "path": "/",
        "bytes": len(html),
        "cold_ms": _ms(shell_cold),
        "warm": _timing_summary(shell_warm),
        "static_asset_count": len(assets),
        "static_asset_bytes": asset_bytes,
        "javascript_bytes": js_bytes,
        "serial_asset_fetch_ms": _ms(asset_seconds),
    }
    results["startup"] = _timing_summary(startups)
    results["date_filter_samples"] = {"year": year, "month": month}
    results["cache_metadata_remap"] = cache_remap
    return results


def _query_plan(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
    return [str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def benchmark_sql(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        cache_root_row = conn.execute(
            "SELECT cache_root, COUNT(*) AS n FROM cache_entries "
            "WHERE size = 'sm' GROUP BY cache_root ORDER BY n DESC LIMIT 1"
        ).fetchone()
        cache_root = str(cache_root_row[0]) if cache_root_row else ""
        year, _month = _representative_dates(conn)
        queries = {
            "grid_default": (
                "SELECT i.id FROM images i INDEXED BY idx_images_active_elo "
                "WHERE i.status IN ('kept','maybe') AND i.missing_at IS NULL AND i.vc_of IS NULL "
                "AND EXISTS (SELECT 1 FROM cache_entries c WHERE c.cache_root=? AND c.size='sm' AND c.image_id=i.id) "
                "ORDER BY i.elo DESC LIMIT 100",
                (cache_root,),
            ),
            "grid_year": (
                "SELECT i.id FROM images i INDEXED BY idx_images_active_elo "
                "WHERE i.status IN ('kept','maybe') AND i.missing_at IS NULL AND i.vc_of IS NULL "
                "AND i.date_taken>=? AND i.date_taken<? "
                "AND EXISTS (SELECT 1 FROM cache_entries c WHERE c.cache_root=? AND c.size='sm' AND c.image_id=i.id) "
                "ORDER BY i.elo DESC LIMIT 100",
                (f"{year}-01-01 00:00:00", f"{int(year)+1}-01-01 00:00:00", cache_root),
            ),
            "grid_visible_count": (
                "SELECT COUNT(*) FROM images i WHERE i.status IN ('kept','maybe') "
                "AND i.missing_at IS NULL AND i.vc_of IS NULL "
                "AND EXISTS (SELECT 1 FROM cache_entries c WHERE c.cache_root=? AND c.size='sm' AND c.image_id=i.id)",
                (cache_root,),
            ),
            "date_histogram": (
                "SELECT substr(i.date_taken,1,7) AS month, COUNT(*) "
                "FROM images i JOIN catalog_sources s ON s.id=i.source_id "
                "WHERE s.included=1 AND i.status IN ('kept','maybe') "
                "AND i.missing_at IS NULL AND i.vc_of IS NULL GROUP BY month",
                (),
            ),
        }
        results = {}
        for name, (sql, params) in queries.items():
            started = time.perf_counter()
            rows = conn.execute(sql, params).fetchall()
            elapsed = time.perf_counter() - started
            results[name] = {
                "elapsed_ms": _ms(elapsed),
                "rows": len(rows),
                "plan": _query_plan(conn, sql, params),
            }
        results["catalog"] = {
            "images": conn.execute("SELECT COUNT(*) FROM images").fetchone()[0],
            "dng_images": conn.execute("SELECT COUNT(*) FROM images WHERE lower(file_ext)='.dng'").fetchone()[0],
            "cache_root": cache_root,
        }
        return results
    finally:
        conn.close()


def _image_path(db_path: Path, image_id: int) -> Path:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT filepath FROM images WHERE id=?", (image_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"image {image_id} is absent from the copied catalog")
    path = Path(row[0])
    if not path.is_file():
        raise RuntimeError(f"source for image {image_id} is unavailable: {path}")
    return path


def benchmark_raw(
    db_path: Path,
    raw_image_id: int,
    develop_cache: Path,
) -> dict[str, Any]:
    os.environ["PHOTOARCHIVE_DEVELOP_CACHE_DIR"] = str(develop_cache)
    sys.path.insert(0, str(WEB_DIR))
    import numpy as np
    from features.develop.pipeline import apply_pipeline
    from features.develop import rawproc

    path = _image_path(db_path, raw_image_id)
    shutil.rmtree(develop_cache, ignore_errors=True)

    started = time.perf_counter()
    rgb, meta = rawproc.decode_base(path)
    decode_seconds = time.perf_counter() - started

    linear = rgb.astype(np.float32) / np.float32(65535.0)
    as_shot = meta.get("as_shot") or {}
    color = dict(meta.get("color") or {})
    if meta.get("base_kind"):
        color["base_kind"] = meta["base_kind"]
    started = time.perf_counter()
    developed = apply_pipeline(
        linear,
        {},
        asshot_temperature=as_shot.get("temperature"),
        asshot_tint=as_shot.get("tint"),
        color_profile=color or None,
    )
    pipeline_seconds = time.perf_counter() - started

    payload = BASE_HEADER.pack(b"PABASE1\0", rgb.shape[1], rgb.shape[0]) + np.ascontiguousarray(
        rgb.astype("<u2", copy=False)
    ).tobytes()
    gzip_results = {}
    for level in (1, 6, 9):
        started = time.perf_counter()
        compressed = gzip.compress(payload, compresslevel=level, mtime=0)
        gzip_results[str(level)] = {
            "elapsed_ms": _ms(time.perf_counter() - started),
            "bytes": len(compressed),
            "ratio": round(len(compressed) / len(payload), 4),
        }

    return {
        "image_id": raw_image_id,
        "path": str(path),
        "source_bytes": path.stat().st_size,
        "dimensions": [int(rgb.shape[1]), int(rgb.shape[0])],
        "base_bytes": len(payload),
        "decode_ms": _ms(decode_seconds),
        "pipeline_default_ms": _ms(pipeline_seconds),
        "gzip": gzip_results,
        "developed_dimensions": [int(developed.shape[1]), int(developed.shape[0])],
    }


def benchmark_pregen(
    db_path: Path,
    fast_image_id: int,
    slow_image_id: int,
    develop_cache: Path,
    thumb_cache: Path,
) -> dict[str, Any]:
    os.environ["PHOTOARCHIVE_DEVELOP_CACHE_DIR"] = str(develop_cache)
    os.environ["PHOTOARCHIVE_THUMB_CACHE_DIR"] = str(thumb_cache)
    sys.path.insert(0, str(WEB_DIR))
    import db
    import settings
    import thumbnails

    db.DB_PATH = str(db_path)
    thumbnails.configure_data_providers(
        db_path=lambda: db.DB_PATH,
        get_db=lambda: db.get_db(),
        batch_set_orientations=lambda updates: db.batch_set_orientations(updates),
        mark_image_missing_sync=lambda image_id: db.mark_image_missing_sync(image_id),
        invalidate_cached_image_ids_cache=lambda cache_root=None, size=None: db.invalidate_cached_image_ids_cache(
            cache_root=cache_root,
            size=size,
        ),
        note_cached_image_ids_added=lambda cache_root, size, image_ids: db.note_cached_image_ids_added(
            cache_root,
            size,
            image_ids,
        ),
    )
    config = dict(settings.DEFAULT_SETTINGS)
    config.update(
        {
            "ssd_cache_dir": str(thumb_cache),
            "ssd_cache_gb": 10,
            "memory_cache_gb": 0.25,
            "background_thumb_workers": 1,
            "pregen_generate_batch": 1,
        }
    )
    shutil.rmtree(develop_cache, ignore_errors=True)
    shutil.rmtree(thumb_cache, ignore_errors=True)
    thumbnails.configure(config)

    cases = {
        "embedded_sm_md": (fast_image_id, ("sm", "md")),
        "small_preview_lg": (slow_image_id, ("lg",)),
    }
    results = {}
    for name, (image_id, sizes) in cases.items():
        path = _image_path(db_path, image_id)
        signatures = {
            size: thumbnails._build_source_signature(str(path), size, image_id)
            for size in sizes
        }
        started = time.perf_counter()
        outcome = thumbnails._generate_thumbnail_set_sync(
            str(path),
            image_id,
            signatures,
            source_bytes=path.stat().st_size,
            hot=False,
        )
        elapsed = time.perf_counter() - started
        thumbnails._flush_write_queue()
        results[name] = {
            "image_id": image_id,
            "sizes": list(sizes),
            "elapsed_ms": _ms(elapsed),
            "items_per_minute": round(60.0 / elapsed, 2),
            "outcome": outcome,
        }
    return results


def _print_table(results: dict[str, Any]) -> None:
    print("\nHTTP / startup")
    print("| Probe | Cold ms | Warm median ms | Warm p95 ms | Bytes |")
    print("|---|---:|---:|---:|---:|")
    for name, value in results.get("http", {}).items():
        if not isinstance(value, dict) or "cold_ms" not in value:
            continue
        warm = value["warm"]
        print(
            f"| {name} | {value['cold_ms']:.2f} | {warm['median_ms']:.2f} | "
            f"{warm['p95_ms']:.2f} | {value.get('bytes', 0):,} |"
        )
    startup = results.get("http", {}).get("startup", {})
    if startup:
        print(
            f"\nServer startup median {startup['median_ms']:.2f} ms "
            f"(p95 {startup['p95_ms']:.2f} ms)."
        )

    raw = results.get("raw", {})
    if raw:
        print("\nRAW fallback breakdown")
        print("| Stage | Time ms | Bytes |")
        print("|---|---:|---:|")
        print(f"| LibRaw half-size decode | {raw['decode_ms']:.2f} | {raw['base_bytes']:,} |")
        print(f"| Default develop pipeline | {raw['pipeline_default_ms']:.2f} | — |")
        for level, data in raw["gzip"].items():
            print(f"| gzip level {level} | {data['elapsed_ms']:.2f} | {data['bytes']:,} |")

    pregen = results.get("pregen", {})
    if pregen:
        print("\nPregen")
        print("| Probe | Time ms | Items/min |")
        print("|---|---:|---:|")
        for name, value in pregen.items():
            print(f"| {name} | {value['elapsed_ms']:.2f} | {value['items_per_minute']:.2f} |")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--raw-image-id", type=int, default=111259)
    parser.add_argument("--fast-image-id", type=int, default=64988)
    parser.add_argument("--develop-cache", type=Path, default=DEFAULT_DEVELOP_CACHE)
    parser.add_argument("--thumb-cache", type=Path, default=DEFAULT_THUMB_CACHE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--skip-raw", action="store_true")
    parser.add_argument("--skip-pregen", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"copied benchmark database does not exist: {args.db}")
    if args.port == 8022:
        raise SystemExit("port 8022 is reserved and must never be probed by this benchmark")
    if args.iterations < 1:
        raise SystemExit("--iterations must be at least 1")

    results: dict[str, Any] = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "database": str(args.db),
        "sql": benchmark_sql(args.db),
    }
    if not args.skip_raw:
        results["raw"] = benchmark_raw(args.db, args.raw_image_id, args.develop_cache)
    if not args.skip_pregen:
        results["pregen"] = benchmark_pregen(
            args.db,
            args.fast_image_id,
            args.raw_image_id,
            args.develop_cache,
            args.thumb_cache,
        )
    if not args.skip_http:
        results["http"] = benchmark_http(
            args.db,
            args.port,
            args.iterations,
            args.raw_image_id,
            args.develop_cache,
            args.thumb_cache,
        )

    _print_table(results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
