"""Run exactly one satellite sync pass for cron and field recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _run_sync_pass(hub_url: str, *, dry_run: bool) -> dict:
    """Reuse the satellite worker's real one-pass pipeline for CLI use."""

    import db
    from features.sync import satellite
    from features.sync.sync_worker import SyncWorker

    if dry_run:
        # Identity discovery is the same first worker stage.  It writes hashes
        # locally when absent, but deliberately makes no hub request or upload.
        items = await satellite.record_local_images(db.DB_PATH)
        pending = [item for item in items if not int(item.get("uploaded") or 0)]
        return {
            "ok": True,
            "dry_run": True,
            "queue_depth": len(pending),
            "bytes_remaining": sum(int(item.get("bytes") or 0) for item in pending),
        }

    worker = SyncWorker(db_path=db.DB_PATH, hub=hub_url)
    await worker.sync_once()
    return {"ok": True, "dry_run": False, "status": worker.status()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Azimuth Photo satellite sync pass")
    parser.add_argument("--hub", required=True, help="Hub base URL, e.g. http://100.102.150.104:8000")
    parser.add_argument("--dry-run", action="store_true", help="Calculate local sync candidates without contacting the hub")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run_sync_pass(args.hub.rstrip("/"), dry_run=bool(args.dry_run)))
    except RuntimeError as exc:
        print(f"field_sync: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
