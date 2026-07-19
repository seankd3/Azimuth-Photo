#!/usr/bin/env python3
"""Prove idle pregen throughput on real CR3s with a 3s status poller.

Usage:
  ./scripts/bench_pregen_cr3_idle.py [--root DIR] [--count 200] [--seconds 90]

Creates an isolated PHOTOARCHIVE_HOME, indexes a real shoot folder that has
≥count CR3s, prunes non-CR3 rows from the catalog (shoot folders often mix in
DNG/TIFF that take the demosaic path), starts pregen, polls
/api/cache/pregen/status every 3s (the ops-board regression), and reports
sustained thumbs/min on the embedded-preview CR3 path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
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


def _iter_cr3s(path: Path):
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            if name.lower().endswith(".cr3"):
                yield Path(dirpath) / name


def _cr3_count(path: Path) -> int:
    return sum(1 for _ in _iter_cr3s(path))


def _find_cr3_dirs(root: Path, *, min_count: int) -> list[Path]:
    """Pick year folders under RAWS that together cover min_count CR3s."""
    years: list[tuple[int, Path]] = []
    if not root.is_dir():
        return []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        count = _cr3_count(child)
        if count:
            years.append((count, child))
    years.sort(key=lambda item: item[0], reverse=True)
    chosen: list[Path] = []
    total = 0
    for count, path in years:
        chosen.append(path)
        total += count
        if total >= min_count:
            break
    return chosen if total >= min_count else []


def _prune_non_cr3(catalog_db: Path) -> int:
    """Keep only CR3 rows so the bench measures embedded-preview throughput."""
    import sqlite3

    conn = sqlite3.connect(str(catalog_db))
    try:
        before = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        conn.execute(
            "DELETE FROM images WHERE lower(filepath) NOT LIKE '%.cr3'"
        )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        try:
            conn.execute(
                """
                UPDATE catalog_sources
                SET image_count = (
                    SELECT COUNT(*) FROM images WHERE images.source_id = catalog_sources.id
                )
                """
            )
            conn.commit()
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    print(f"pruned catalog images {before} → {after} CR3-only", flush=True)
    return int(after)


def _wait_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 — probe loop
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"server did not become ready: {url} ({last})")


def _get_json(url: str, *, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, method="POST", data=body)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_CR3_ROOT)
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        default=None,
        help="Explicit source directory (repeatable). Default: densest CR3 day under --root.",
    )
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument("--min-rate", type=float, default=40.0)
    args = parser.parse_args()

    if not VENV_PYTHON.is_file():
        print("need web/.venv", file=sys.stderr)
        return 2

    if args.source:
        source_dirs = list(args.source)
    else:
        # Dense-enough CR3 shoot day without multi-thousand-file scan cost.
        preferred = args.root / "2024" / "2024-09-11"
        if preferred.is_dir() and _cr3_count(preferred) >= args.count:
            source_dirs = [preferred]
        else:
            preferred = args.root / "2024" / "2024-10-24"
            if preferred.is_dir() and _cr3_count(preferred) >= args.count:
                source_dirs = [preferred]
            else:
                source_dirs = _find_cr3_dirs(args.root, min_count=args.count)

    if not source_dirs:
        print(
            f"could not find directories covering {args.count} CR3s",
            file=sys.stderr,
        )
        return 2

    cr3_total = sum(_cr3_count(path) for path in source_dirs)
    scratch = Path(tempfile.mkdtemp(prefix="pa-snappy-cr3-pregen-"))
    home = scratch / "home"
    cache = home / "cache" / "thumbs"
    home.mkdir(parents=True)
    cache.mkdir(parents=True)
    print(
        "using source dirs: "
        + ", ".join(f"{path} ({_cr3_count(path)} CR3s)" for path in source_dirs),
        flush=True,
    )
    catalog_db = home / "data" / "catalog" / "photoarchive.db"

    env = os.environ.copy()
    env.update(
        {
            "PHOTOARCHIVE_HOME": str(home),
            "PHOTOARCHIVE_THUMB_CACHE_DIR": str(cache),
            "PHOTOARCHIVE_HOST": "127.0.0.1",
            "PHOTOARCHIVE_PORT": str(args.port),
            "PHOTOARCHIVE_MODE": "standalone",
            "PHOTOARCHIVE_ACCESS": "local",
            # Do NOT set SMOKE_MODE — that skips init_db.
            "PHOTOARCHIVE_SSD_CACHE_BYTES": str(20 * 1024 * 1024 * 1024),
            "PYTHONPATH": str(WEB),
        }
    )

    base = f"http://127.0.0.1:{args.port}"
    log_path = scratch / "server.log"
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
            str(args.port),
            "--no-access-log",
        ],
        cwd=str(WEB),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        try:
            _wait_http(f"{base}/api/dev/status", timeout=90.0)
        except RuntimeError:
            log_file.flush()
            print(log_path.read_text(encoding="utf-8")[-2000:], file=sys.stderr)
            raise

        # Keep pregen off during scan so auto-warm cannot wedge the prefetch
        # pool on sibling DNG/TIFF files before we prune to CR3-only.
        try:
            _post_json(f"{base}/api/cache/pregen/stop")
        except Exception:
            pass

        # Wait out any in-flight scan before claiming sources.
        for path in source_dirs:
            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    status = _get_json(f"{base}/api/scan/status")
                except Exception:
                    status = {"scanning": False}
                if not status.get("scanning"):
                    break
                time.sleep(0.5)
            try:
                payload = _post_json(
                    f"{base}/api/catalog/sources",
                    {"path": str(path), "scan": True},
                )
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                print(f"add source failed for {path}: {exc.code} {body}", file=sys.stderr)
                return 1
            if not payload.get("ok"):
                print(f"add source failed for {path}: {payload}", file=sys.stderr)
                return 1
            deadline = time.time() + 600
            while time.time() < deadline:
                try:
                    status = _get_json(f"{base}/api/scan/status", timeout=10.0)
                except Exception:
                    time.sleep(1)
                    continue
                if not status.get("scanning"):
                    break
                time.sleep(1)

        # Shoot folders mix DNG/TIFF; keep only CR3s for the embedded-preview proof.
        if not catalog_db.is_file():
            print(f"catalog db missing at {catalog_db}", file=sys.stderr)
            return 1
        try:
            _post_json(f"{base}/api/cache/pregen/stop")
        except Exception:
            pass
        cr3_kept = _prune_non_cr3(catalog_db)
        if cr3_kept < args.count:
            print(f"catalog only kept {cr3_kept}/{args.count} CR3s", file=sys.stderr)
            return 1
        # Let the server drop any in-flight catalog snapshots after the prune.
        time.sleep(1)
        try:
            _get_json(f"{base}/api/counts", timeout=10.0)
        except Exception:
            pass
        time.sleep(1)

        deadline = time.time() + 180
        total = 0
        while time.time() < deadline:
            try:
                counts = _get_json(f"{base}/api/counts")
                total = int(counts.get("total") or 0)
            except Exception:
                total = 0
            if total >= args.count:
                break
            time.sleep(1)
        # Counts API may still reflect cached totals; trust the pruned DB.
        if total < args.count:
            total = cr3_kept
        if total < args.count:
            print(f"catalog only reached {total}/{args.count} images", file=sys.stderr)
            return 1

        # First-run import auto-starts pregen during scan; stop that so we
        # measure a clean idle wave after the catalog is settled.
        try:
            _post_json(f"{base}/api/cache/pregen/stop")
        except Exception:
            pass
        time.sleep(2)
        _post_json(f"{base}/api/cache/pregen/start")

        # Wait until the prefetch worker actually enters a bulk wave.
        armed_deadline = time.time() + 90
        status = {}
        while time.time() < armed_deadline:
            try:
                status = _get_json(f"{base}/api/cache/pregen/status", timeout=5.0)
            except Exception as exc:
                print(f"status probe retry: {exc}", flush=True)
                time.sleep(1)
                continue
            message = str(status.get("message") or "")
            generated = int(status.get("generated_this_session") or 0)
            preview_count = int((status.get("preview") or {}).get("count") or 0)
            if "Bulk warming" in message or generated > 0 or preview_count > 0:
                break
            time.sleep(1)
        else:
            print(
                f"pregen worker never left start state: {status}",
                file=sys.stderr,
            )
            return 1

        samples: list[tuple[float, int, int]] = []
        started = time.time()
        status = {}
        while time.time() - started < args.seconds:
            try:
                status = _get_json(f"{base}/api/cache/pregen/status", timeout=5.0)
            except Exception as exc:
                print(f"status sample skipped: {exc}", flush=True)
                time.sleep(3)
                continue
            generated = int(status.get("generated_this_session") or 0)
            # Prefer session counter; fall back to preview tier count if the
            # session counter lags (observed 0 while disk tiers advanced).
            preview_count = int((status.get("preview") or {}).get("count") or 0)
            samples.append((time.time(), generated, preview_count))
            # Ops-board regression: 3s status poller must not throttle idle waves.
            time.sleep(3)

        if len(samples) < 3:
            print("not enough status samples", file=sys.stderr)
            return 1

        mid = len(samples) // 3
        t0, g0, p0 = samples[mid]
        t1, g1, p1 = samples[-1]
        elapsed_min = max(1e-6, (t1 - t0) / 60.0)
        session_rate = (g1 - g0) / elapsed_min
        preview_rate = (p1 - p0) / elapsed_min
        rate = max(session_rate, preview_rate)
        print(
            json.dumps(
                {
                    "cr3_dirs": [str(path) for path in source_dirs],
                    "cr3_files_in_dirs": cr3_total,
                    "fixture_cr3s": cr3_kept,
                    "catalog_total": total,
                    "window_seconds": args.seconds,
                    "generated_start": g0,
                    "generated_end": g1,
                    "preview_start": p0,
                    "preview_end": p1,
                    "session_per_min": round(session_rate, 1),
                    "preview_per_min": round(preview_rate, 1),
                    "sustained_per_min": round(rate, 1),
                    "min_rate": args.min_rate,
                    "pass": rate >= args.min_rate,
                    "final_state": status.get("state"),
                    "final_message": status.get("message"),
                    "samples": len(samples),
                },
                indent=2,
            )
        )
        return 0 if rate >= args.min_rate else 1
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
