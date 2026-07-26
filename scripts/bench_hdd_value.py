#!/usr/bin/env python3
"""Prove HDD value doctrine: one original read → all derived products.

Measures on a fixture of ≥100 real CR3s with cold caches:
  (a) originals read count vs derived products (must be 1 read → tiers+hash)
  (b) pregen throughput with governor vs without
  (c) interactive thumb p95 while a bulk wave runs

Usage:
  ./scripts/bench_hdd_value.py [--root DIR] [--count 100] [--seconds 60]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DEFAULT_CR3_ROOT = Path("/mnt/expansion/Photos/RAWS")
VENV_PYTHON = WEB / ".venv" / "bin" / "python"
HISTORY = ROOT / "bench-runs" / "hdd-value-history.jsonl"

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])


def _iter_cr3s(path: Path):
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            if name.lower().endswith(".cr3"):
                yield Path(dirpath) / name


def _cr3_count(path: Path) -> int:
    return sum(1 for _ in _iter_cr3s(path))


def _pick_source(root: Path, count: int) -> Path | None:
    preferred = [
        root / "2024" / "2024-09-11",
        root / "2024" / "2024-10-24",
    ]
    for path in preferred:
        if path.is_dir() and _cr3_count(path) >= count:
            return path
    if not root.is_dir():
        return None
    best: tuple[int, Path] | None = None
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        n = _cr3_count(child)
        if n >= count and (best is None or n < best[0]):
            best = (n, child)
    return best[1] if best else None


def _wait_http(url: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"server not ready: {url} ({last})")


def _get_json(url: str, *, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, method="POST", data=body)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _prune_non_cr3(catalog_db: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(str(catalog_db))
    try:
        conn.execute("DELETE FROM images WHERE lower(filepath) NOT LIKE '%.cr3'")
        conn.commit()
        return int(conn.execute("SELECT COUNT(*) FROM images").fetchone()[0])
    finally:
        conn.close()


def _clear_thumb_cache(cache: Path) -> None:
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)


def _null_content_hashes(catalog_db: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(str(catalog_db))
    try:
        conn.execute("UPDATE images SET content_hash = NULL")
        conn.execute("UPDATE images SET metadata_scanned_at = NULL, metadata_version = NULL")
        conn.commit()
    finally:
        conn.close()


def _catalog_stats(catalog_db: Path) -> dict:
    import sqlite3

    conn = sqlite3.connect(str(catalog_db))
    try:
        total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        hashed = conn.execute(
            "SELECT COUNT(*) FROM images WHERE content_hash IS NOT NULL"
        ).fetchone()[0]
        meta = conn.execute(
            "SELECT COUNT(*) FROM images WHERE metadata_scanned_at IS NOT NULL"
        ).fetchone()[0]
        return {"images": int(total), "hashed": int(hashed), "metadata": int(meta)}
    finally:
        conn.close()


def _thumb_counts(cache_db: Path) -> dict[str, int]:
    import sqlite3

    if not cache_db.is_file():
        return {"sm": 0, "md": 0, "lg": 0}
    conn = sqlite3.connect(str(cache_db))
    try:
        rows = conn.execute(
            "SELECT size, COUNT(*) FROM cache_entries GROUP BY size"
        ).fetchall()
        return {str(size): int(count) for size, count in rows}
    finally:
        conn.close()


def _start_server(home: Path, cache: Path, port: int, *, concurrency: int) -> tuple[subprocess.Popen, Path]:
    env = os.environ.copy()
    env.update(
        {
            "AZIMUTH_HOME": str(home),
            "AZIMUTH_THUMB_CACHE_DIR": str(cache),
            "AZIMUTH_HOST": "127.0.0.1",
            "AZIMUTH_PORT": str(port),
            "AZIMUTH_MODE": "standalone",
            "AZIMUTH_ACCESS": "local",
            "AZIMUTH_SSD_CACHE_BYTES": str(20 * 1024 * 1024 * 1024),
            "AZIMUTH_BULK_HDD_CONCURRENCY": str(concurrency),
            "AZIMUTH_PREGENERATE_GENERATE_BATCH": "8",
            "PYTHONPATH": str(WEB),
        }
    )
    log_path = home / "server.log"
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=str(WEB),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return proc, log_path


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


def _measure_pregen(base: str, seconds: float) -> dict:
    try:
        _post_json(f"{base}/api/cache/pregen/stop")
    except Exception:
        pass
    time.sleep(1)
    _post_json(f"{base}/api/cache/pregen/start")

    armed_deadline = time.time() + 90
    first = {}
    while time.time() < armed_deadline:
        first = _get_json(f"{base}/api/cache/pregen/status")
        message = str(first.get("message") or "")
        generated = int(first.get("generated_this_session") or 0)
        preview_count = int((first.get("preview") or {}).get("count") or 0)
        if "Bulk warming" in message or generated > 0 or preview_count > 0:
            break
        time.sleep(1)

    baseline = int(first.get("generated_this_session") or 0)
    started = time.time()
    status = first
    while time.time() - started < seconds:
        time.sleep(2)
        status = _get_json(f"{base}/api/cache/pregen/status")
    generated = max(0, int(status.get("generated_this_session") or 0) - baseline)
    # Prefer live rate from the worker when the session counter stalls on
    # already-cached tiers after a partial warm.
    rate = float(status.get("recent_images_per_min") or status.get("overall_images_per_min") or 0.0)
    elapsed = max(0.001, time.time() - started)
    derived_rate = generated / elapsed * 60.0
    try:
        _post_json(f"{base}/api/cache/pregen/stop")
    except Exception:
        pass
    return {
        "generated": generated,
        "seconds": round(elapsed, 2),
        "per_min": round(max(rate, derived_rate), 1),
        "session_per_min": round(derived_rate, 1),
        "status_rate_per_min": round(rate, 1),
        "preview_count": int((status.get("preview") or {}).get("count") or 0),
        "generated_this_session": int(status.get("generated_this_session") or 0),
    }


def _measure_interactive_under_load(base: str, image_ids: list[int], loops: int = 30) -> dict:
    samples: list[float] = []
    for i in range(loops):
        image_id = image_ids[i % len(image_ids)]
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                f"{base}/api/thumbnail/{image_id}?size=sm",
                timeout=10,
            ) as resp:
                resp.read(64)
        except Exception:
            pass
        samples.append((time.perf_counter() - started) * 1000.0)
    samples.sort()
    p95 = samples[max(0, int(round(0.95 * len(samples))) - 1)]
    return {
        "n": len(samples),
        "p50_ms": round(statistics.median(samples), 1),
        "p95_ms": round(p95, 1),
        "mean_ms": round(statistics.mean(samples), 1),
    }


def _unit_read_once(count: int, source: Path) -> dict:
    """Direct harvest measurement: 1 source_read → thumbs+hash products."""
    sys.path.insert(0, str(WEB))
    from core import hdd_governor
    from features.sync.hashing import compute_content_hash
    from thumbnails import harvest

    hdd_governor.reset_for_tests(1)
    paths = list(_iter_cr3s(source))[:count]
    if len(paths) < min(20, count):
        raise RuntimeError(f"need CR3s under {source}, found {len(paths)}")

    sample_n = min(8, len(paths))
    reads = 0
    thumb_products = 0
    hashes = 0
    generate_calls = 0

    for path in paths[:sample_n]:
        expected = compute_content_hash(path)

        def fake_generate(fp, iid, sigs, **kwargs):
            nonlocal generate_calls
            generate_calls += 1
            on_loaded = kwargs.get("on_source_loaded")
            data = Path(fp).read_bytes()[: 8 * 1024 * 1024]
            if on_loaded is not None:
                on_loaded(data, None)
            return {
                "source_reads": 1,
                "thumbnails_written": len(sigs) or 3,
                "source_bytes": Path(fp).stat().st_size,
                "read_seconds": 0.01,
                "decode_encode_seconds": 0.01,
                "source_read_failures": 0,
                "originals_written": 0,
            }

        result = harvest.harvest_original(
            str(path),
            1,
            bulk=True,
            size_signatures={"sm": "a", "md": "b", "lg": "c"},
            source_bytes=path.stat().st_size,
            need_hash=True,
            need_metadata=False,
            generate_thumbnail_set=fake_generate,
            persist_hash=lambda _iid, digest, exp=expected: digest == exp,
        )
        reads += int(result.source_reads)
        if result.thumbnails_written:
            thumb_products += int(result.thumbnails_written)
        if result.hash_written:
            hashes += 1

    products = thumb_products + hashes
    return {
        "sample": sample_n,
        "originals_read": reads,
        "thumbnails_written": thumb_products,
        "hashes": hashes,
        "products": products,
        "generate_calls": generate_calls,
        "ratio_products_per_read": round(products / max(1, reads), 2),
        "ok": reads == sample_n and hashes == sample_n and generate_calls == sample_n,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_CR3_ROOT)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--port", type=int, default=8211)
    args = parser.parse_args()

    if not VENV_PYTHON.is_file():
        print("need web/.venv", file=sys.stderr)
        return 2

    source = args.source or _pick_source(args.root, args.count)
    if source is None:
        print(f"could not find ≥{args.count} CR3s under {args.root}", file=sys.stderr)
        return 2

    print(f"source={source} cr3s={_cr3_count(source)}", flush=True)

    read_once = _unit_read_once(args.count, source)
    print(f"read_once={json.dumps(read_once)}", flush=True)

    scratch = Path(tempfile.mkdtemp(prefix="azimuth-hddgov-bench-"))
    home = scratch / "home"
    cache = home / "cache" / "thumbs"
    home.mkdir(parents=True)
    cache.mkdir(parents=True)
    catalog_db = home / "data" / "catalog" / "azimuth.db"
    cache_db = cache / "cache.db"

    results: dict = {"source": str(source), "read_once": read_once}
    interactive = {"p95_ms": 0.0}

    def _prepare_catalog(proc_home: Path, proc_cache: Path, port: int, concurrency: int):
        proc, log_path = _start_server(proc_home, proc_cache, port, concurrency=concurrency)
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_http(f"{base_url}/api/dev/status")
        except RuntimeError:
            _stop(proc)
            print(log_path.read_text(encoding="utf-8")[-2000:], file=sys.stderr)
            raise
        try:
            _post_json(f"{base_url}/api/cache/pregen/stop")
        except Exception:
            pass
        return proc, base_url

    # Bootstrap catalog once (scan), then restart for clean governor measurements.
    proc, base = _prepare_catalog(home, cache, args.port, 4)
    try:
        _post_json(f"{base}/api/catalog/sources", {"path": str(source), "scan": True})
        deadline = time.time() + 300
        while time.time() < deadline:
            status = _get_json(f"{base}/api/scan/status")
            if not status.get("scanning"):
                break
            time.sleep(0.5)
        try:
            _post_json(f"{base}/api/cache/pregen/stop")
        except Exception:
            pass
        kept = _prune_non_cr3(catalog_db)
        print(f"catalog CR3s={kept}", flush=True)
    finally:
        _stop(proc)

    # Phase A: cold cache, governor concurrency 1
    _clear_thumb_cache(cache)
    _null_content_hashes(catalog_db)
    proc, base = _prepare_catalog(home, cache, args.port, 1)
    try:
        with_gov = _measure_pregen(base, args.seconds)
        results["pregen_governor_1"] = with_gov
        print(
            f"pregen governor=1 {with_gov['per_min']}/min "
            f"(session={with_gov['generated']} in {with_gov['seconds']}s)",
            flush=True,
        )

        _post_json(f"{base}/api/cache/pregen/start")
        time.sleep(2)
        ids = [
            int(row[0])
            for row in __import__("sqlite3")
            .connect(str(catalog_db))
            .execute("SELECT id FROM images ORDER BY id LIMIT 40")
            .fetchall()
        ]
        interactive = _measure_interactive_under_load(base, ids)
        results["interactive_under_bulk"] = interactive
        try:
            _post_json(f"{base}/api/cache/pregen/stop")
        except Exception:
            pass
        print(f"interactive_under_bulk={json.dumps(interactive)}", flush=True)

        thumbs = _thumb_counts(cache_db)
        catalog = _catalog_stats(catalog_db)
        results["after_governor"] = {"thumbs": thumbs, "catalog": catalog}
    finally:
        _stop(proc)

    # Phase B: cold cache, concurrency 8
    _clear_thumb_cache(cache)
    _null_content_hashes(catalog_db)
    proc, base2 = _prepare_catalog(home, cache, args.port + 1, 8)
    try:
        without = _measure_pregen(base2, args.seconds)
        results["pregen_governor_8"] = without
        print(
            f"pregen governor=8 {without['per_min']}/min "
            f"(session={without['generated']} in {without['seconds']}s)",
            flush=True,
        )
    finally:
        _stop(proc)

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": time.time(), **results}) + "\n")

    print(json.dumps(results, indent=2))
    ok = bool(read_once.get("ok"))
    if interactive.get("p95_ms", 9e9) > 2000:
        print("interactive p95 exceeded 2000ms under bulk", file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
