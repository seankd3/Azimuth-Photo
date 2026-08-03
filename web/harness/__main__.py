"""python -m harness --record | --check [--only STAGE]"""

from __future__ import annotations

import argparse
import sys
import time

from harness import env

env.apply()  # before any app module resolves a runtime path

from harness import goldens  # noqa: E402
from harness.stages import STAGES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="write goldens; review the diff")
    mode.add_argument("--check", action="store_true", help="fail if any recorded fact moved")
    parser.add_argument("--only", action="append", choices=sorted(STAGES), help="run one stage")
    args = parser.parse_args()

    chosen = args.only or sorted(STAGES)
    moved = 0
    for name in chosen:
        started = time.monotonic()
        try:
            recorded = STAGES[name]()
        except Exception as exc:
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            moved += 1
            continue
        elapsed = time.monotonic() - started

        if args.record:
            goldens.write(name, recorded)
            print(f"rec  {name}  {len(recorded)} entries  {elapsed:.1f}s")
            continue

        expected = goldens.read(name)
        if expected is None:
            print(f"FAIL {name}: no golden recorded yet")
            moved += 1
            continue
        changes = goldens.differences(expected, recorded)
        if changes:
            moved += 1
            print(f"FAIL {name}  {len(changes)} changed  {elapsed:.1f}s")
            for line in changes[:20]:
                print(f"       {line}")
            if len(changes) > 20:
                print(f"       ... and {len(changes) - 20} more")
        else:
            print(f"ok   {name}  {len(recorded)} entries  {elapsed:.1f}s")

    if args.check and moved:
        print(f"\n{moved} stage(s) moved. If that was the point, --record and say why in the commit.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
