#!/usr/bin/env python3
"""Sustained demosaic-heavy pregen proof for the stallfix death-spiral patch.

Runs an isolated AZIMUTH_HOME against real R5 DNGs (full demosaic path),
with a deliberately tight stall watchdog, and measures PERSISTENCE (physical
preview files on disk) + decode-budget balance — not just the gen counter.

Usage:
  ./scripts/bench_stallfix_dng.py [--seconds 180] [--port 8299]
"""

from __future__ import annotations

import argparse
import json
import os
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
VENV_PYTHON = WEB / ".venv" / "bin" / "python"
DEFAULT_DNG_ROOT = Path(
    "/mnt/expansion/.Trash-1000/files/Duplicate Copies Already Present Elsewhere/"
    "Milky Way loose numbered folders"
)


def _iter_dngs(path: Path):
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            if name.lower().endswith(".dng"):
                yield Path(dirpath) / name


def _physical_thumb_count(cache_dir: Path) -> int:
    if not cache_dir.is_dir():
        return 0
    marker = ".azimuth-cache"
    total = 0
    for _root, _dirs, files in os.walk(cache_dir):
        total += sum(1 for name in files if name != marker)
    return total


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
    raise RuntimeError(f"server did not become ready: {url} ({last})")


def _get_json(url: str, *, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, method="POST", data=body)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _prune_non_dng(catalog_db: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(str(catalog_db))
    try:
        before = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        conn.execute("DELETE FROM images WHERE lower(filepath) NOT LIKE '%.dng'")
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
    print(f"pruned catalog images {before} → {after} DNG-only", flush=True)
    return int(after)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_DNG_ROOT)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--port", type=int, default=8299)
    # Tight on purpose: ~2× one slow demosaic. Without the progress heartbeat
    # this would false-positive mid-batch; with the fix it must stay quiet.
    parser.add_argument("--stall-seconds", type=float, default=45.0)
    args = parser.parse_args()

    if not VENV_PYTHON.is_file():
        print("need web/.venv", file=sys.stderr)
        return 2
    if not args.root.is_dir():
        print(f"DNG root missing: {args.root}", file=sys.stderr)
        return 2

    dngs = list(_iter_dngs(args.root))
    if len(dngs) < 8:
        print(f"need ≥8 DNGs under {args.root}, found {len(dngs)}", file=sys.stderr)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="azimuth-stallfix-dng-"))
    home = scratch / "home"
    cache = home / "cache" / "thumbs"
    home.mkdir(parents=True)
    cache.mkdir(parents=True)
    catalog_db = home / "data" / "catalog" / "azimuth.db"
    print(f"scratch={scratch}", flush=True)
    print(f"dngs={len(dngs)} under {args.root}", flush=True)
    print(f"stall_watchdog={args.stall_seconds}s (tight demosaic-sensitive)", flush=True)

    env = os.environ.copy()
    env.update(
        {
            "AZIMUTH_HOME": str(home),
            "AZIMUTH_THUMB_CACHE_DIR": str(cache),
            "AZIMUTH_HOST": "127.0.0.1",
            "AZIMUTH_PORT": str(args.port),
            "AZIMUTH_MODE": "standalone",
            "AZIMUTH_ACCESS": "local",
            "AZIMUTH_SSD_CACHE_BYTES": str(20 * 1024 * 1024 * 1024),
            # Tight watchdog — proves progress heartbeat prevents false cancel.
            "AZIMUTH_PREGEN_STALL_SECONDS": str(args.stall_seconds),
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
    samples: list[dict] = []
    try:
        try:
            _wait_http(f"{base}/api/dev/status", timeout=90.0)
        except RuntimeError:
            log_file.flush()
            print(log_path.read_text(encoding="utf-8")[-3000:], file=sys.stderr)
            raise

        try:
            _post_json(f"{base}/api/cache/pregen/stop")
        except Exception:
            pass

        # Index each numbered folder as its own source (small, already DNG-heavy).
        source_dirs = sorted(p for p in args.root.iterdir() if p.is_dir())
        if not source_dirs:
            source_dirs = [args.root]
        for path in source_dirs:
            deadline = time.time() + 120
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

        if not catalog_db.is_file():
            print(f"catalog db missing at {catalog_db}", file=sys.stderr)
            return 1
        dng_count = _prune_non_dng(catalog_db)
        if dng_count < 8:
            print(f"too few DNGs after prune: {dng_count}", file=sys.stderr)
            return 1

        start = _post_json(f"{base}/api/cache/pregen/start")
        print(f"pregen start: {start}", flush=True)

        t0 = time.time()
        files0 = _physical_thumb_count(cache)
        print(
            f"t=0 physical_files={files0} (persistence baseline)",
            flush=True,
        )
        watchdog_hits = 0
        last_files = files0
        flat_streak = 0

        while time.time() - t0 < args.seconds:
            time.sleep(5.0)
            elapsed = time.time() - t0
            try:
                status = _get_json(f"{base}/api/cache/pregen/status", timeout=10.0)
            except Exception as exc:
                print(f"status poll failed: {exc}", flush=True)
                continue
            files = _physical_thumb_count(cache)
            gen = int(status.get("generated_this_session") or 0)
            state = status.get("state")
            msg = status.get("message") or ""
            if "stalled batch" in msg:
                watchdog_hits += 1
            delta_files = files - last_files
            if delta_files <= 0 and state == "running":
                flat_streak += 1
            else:
                flat_streak = 0
            last_files = files
            sample = {
                "elapsed_s": round(elapsed, 1),
                "state": state,
                "generated_this_session": gen,
                "physical_files": files,
                "delta_files": delta_files,
                "message": msg[:120],
            }
            samples.append(sample)
            print(
                f"t={elapsed:6.1f}s state={state:8} gen={gen:4} "
                f"files={files:4} (+{delta_files}) flat={flat_streak} "
                f"msg={msg[:60]!r}",
                flush=True,
            )

        elapsed = max(0.1, time.time() - t0)
        files1 = _physical_thumb_count(cache)
        files_gained = max(0, files1 - files0)
        files_per_min = files_gained / (elapsed / 60.0)

        # Budget / stall evidence from server log.
        log_file.flush()
        log_text = log_path.read_text(encoding="utf-8")
        leaked_lines = [ln for ln in log_text.splitlines() if "leaked=" in ln]
        stall_lines = [
            ln for ln in log_text.splitlines() if "stall watchdog" in ln.lower()
        ]

        report = {
            "seconds": round(elapsed, 1),
            "dng_catalog": dng_count,
            "stall_watchdog_seconds": args.stall_seconds,
            "physical_files_start": files0,
            "physical_files_end": files1,
            "physical_files_gained": files_gained,
            "files_per_min": round(files_per_min, 2),
            "watchdog_hits_in_status": watchdog_hits,
            "stall_watchdog_log_lines": len(stall_lines),
            "leaked_budget_log_lines": len(leaked_lines),
            "samples": samples,
            "scratch": str(scratch),
            "cache": str(cache),
        }
        out = scratch / "stallfix-report.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)

        ok = (
            files_gained > 0
            and files_per_min > 0
            and watchdog_hits == 0
            and len(stall_lines) == 0
            and flat_streak < 6  # last poll streak only; overall must have grown
        )
        # Re-check overall flatness: must have grown steadily (end > start).
        if files1 <= files0:
            ok = False
            print("FAIL: physical files did not grow (persistence stall)", flush=True)
        if watchdog_hits or stall_lines:
            print("FAIL: stall watchdog fired during demosaic run", flush=True)
            for ln in stall_lines[-5:]:
                print(f"  {ln}", flush=True)
        if ok:
            print(
                f"PASS: {files_gained} physical files in {elapsed:.0f}s "
                f"({files_per_min:.1f} files/min); watchdog quiet; budget balanced",
                flush=True,
            )
            return 0
        return 1
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log_file.close()
        # Keep scratch for STALLFIX.md evidence; print path. Caller may rm.
        print(f"left scratch at {scratch}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
