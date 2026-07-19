#!/usr/bin/env python3
"""Interactive latency under load — browse snappiness while bulk work grinds.

Points at a running server (default http://127.0.0.1:8000). GET-only; safe for
prod. See docs/PERF_BUDGETS.md for budgets and load policy.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
HISTORY = ROOT / "bench-runs" / "interactive-history.jsonl"
VENV_PYTHON = WEB / ".venv" / "bin" / "python"

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])

sys.path.insert(0, str(WEB))

import httpx  # noqa: E402

from perf import interactive  # noqa: E402


def _sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "url",
        nargs="?",
        default="http://127.0.0.1:8000",
        help="base URL of a running Azimuth Photo server (default: localhost:8000)",
    )
    parser.add_argument(
        "--loops",
        type=int,
        default=interactive.DEFAULT_LOOPS,
        help="measured browse cycles per phase after warm-up (default: %(default)s)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=interactive.DEFAULT_WARMUP,
        help="warm-up cycles excluded from stats (default: %(default)s)",
    )
    parser.add_argument(
        "--with-load",
        action="store_true",
        help=(
            "run a second phase observing natural pregen/caption state via GET "
            "status APIs (never injects load — safe for prod)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when initial budgets are missed (p95 grid/thumb_sm)",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=HISTORY,
        help=f"JSONL history path (default: {HISTORY})",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="skip appending a history line",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.loops < 1:
        print("--loops must be at least 1", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("--warmup must be >= 0", file=sys.stderr)
        return 2

    measured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        result = interactive.run_benchmark(
            args.url,
            loops=args.loops,
            warmup=args.warmup,
            with_load=args.with_load,
        )
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    result["measured_at"] = measured_at
    result["git_sha"] = _sha()

    print(interactive.format_summary_table(result))
    print()
    print(json.dumps({
        "measured_at": result["measured_at"],
        "git_sha": result["git_sha"],
        "budget_ok": result["budget_ok"],
        "phases": [
            {
                "name": phase["name"],
                "classes": phase["classes"],
                "bulk_state": {
                    k: phase["bulk_state"].get(k)
                    for k in (
                        "mode",
                        "any_bulk_active",
                        "active_snapshots",
                        "snapshots",
                    )
                    if k in (phase.get("bulk_state") or {})
                },
            }
            for phase in result["phases"]
        ],
    }, indent=2))

    if not args.no_history:
        interactive.append_history(args.history, result)
        try:
            display = args.history.relative_to(ROOT)
        except ValueError:
            display = args.history
        print(f"\nAppended {display}")

    if args.check and not result["budget_ok"]:
        print("\nFAIL: interactive latency budgets missed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
