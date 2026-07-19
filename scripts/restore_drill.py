#!/usr/bin/env python3
"""CLI: restore-drill the newest sealed Azimuth Photo catalog snapshot."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
VENV_PYTHON = WEB / ".venv" / "bin" / "python"

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])

sys.path.insert(0, str(WEB))

from features.system.restore_drill import (  # noqa: E402
    DEFAULT_LOG_PATH,
    DEFAULT_NTFY_URL,
    DEFAULT_SPOT_ROWS,
    DEFAULT_STATE_PATH,
    RestoreDrillError,
    ScratchUnsafeError,
    notify_transition,
    run_restore_drill,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore the newest sealed catalog snapshot into scratch and verify it.",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="Directory of sealed photoarchive-*.db.gz snapshots (default: runtime backup_dir)",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Explicit snapshot path (default: newest sealed under --backup-root)",
    )
    parser.add_argument(
        "--scratch-parent",
        type=Path,
        default=None,
        help="Parent directory for a fresh throwaway scratch folder (default: system temp)",
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=None,
        help="Exact scratch directory (must not contain owner marker or live db)",
    )
    parser.add_argument(
        "--spot-rows",
        type=int,
        default=DEFAULT_SPOT_ROWS,
        help=f"Random image rows to spot-check (default: {DEFAULT_SPOT_ROWS})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for deterministic spot-check sampling",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Write state/log and ntfy only on ok↔bad transitions (systemd mode)",
    )
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--ntfy-url", default=DEFAULT_NTFY_URL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_restore_drill(
            backup_root=args.backup_root,
            snapshot=args.snapshot,
            scratch_parent=args.scratch_parent,
            scratch=args.scratch,
            spot_rows=args.spot_rows,
            seed=args.seed,
        )
    except ScratchUnsafeError as exc:
        detail = f"refused: {exc}"
        print(detail, file=sys.stderr)
        if args.notify:
            notify_transition(
                ok=False,
                detail=detail,
                state_path=args.state_file,
                ntfy_url=args.ntfy_url,
                log_path=args.log_file,
            )
        return 2
    except RestoreDrillError as exc:
        detail = str(exc)
        print(detail, file=sys.stderr)
        if args.notify:
            notify_transition(
                ok=False,
                detail=detail,
                state_path=args.state_file,
                ntfy_url=args.ntfy_url,
                log_path=args.log_file,
            )
        return 1
    except Exception as exc:  # noqa: BLE001
        detail = f"unexpected: {exc}"
        print(detail, file=sys.stderr)
        if args.notify:
            notify_transition(
                ok=False,
                detail=detail,
                state_path=args.state_file,
                ntfy_url=args.ntfy_url,
                log_path=args.log_file,
            )
        return 1

    print(result.message)
    if args.notify:
        notify_transition(
            ok=True,
            detail=result.message,
            state_path=args.state_file,
            ntfy_url=args.ntfy_url,
            log_path=args.log_file,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
