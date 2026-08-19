"""Every gate, one number each.

    python scripts/gates/check.py            measure, and fail on any drift
    python scripts/gates/check.py --list     print what each number counts
    python scripts/gates/check.py --write    write the numbers that fell

A number falls by measurement and rises only by hand. `--write` refuses to
raise one, so growth is a line somebody typed, in a commit, under a subject
that has to say why.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUDGET = HERE / "budget.txt"
NAMES = ("seam", "layers", "tables", "paths", "routes", "names", "collects", "imports")

sys.path.insert(0, str(HERE))


def budget() -> dict[str, int]:
    numbers = {}
    for line in BUDGET.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, count = line.split()
            numbers[name] = int(count)
    return numbers


def write(numbers: dict[str, int]) -> None:
    head = [line for line in BUDGET.read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    body = [f"{name} {numbers[name]}" for name in NAMES]
    BUDGET.write_text("\n".join(head + body) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    show, lower = "--list" in argv, "--write" in argv
    allowed, failed, started = budget(), 0, time.time()
    for name in NAMES:
        gate = importlib.import_module(name)
        found = gate.run(ROOT)
        cap = allowed[name]
        state = "ok" if len(found) == cap else ("ROSE" if len(found) > cap else "fell")
        print(f"{name:9} {len(found):4} of {cap:4}  {state}   {gate.__doc__.splitlines()[0]}")
        if show or state == "ROSE":
            for line in found:
                print(f"    {line}")
        if state == "ROSE":
            failed += 1
        elif state == "fell":
            if lower:
                allowed[name] = len(found)
            else:
                failed += 1
        if state == "fell" and not lower:
            print(f"    {name} fell to {len(found)}: run check.py --write")
    if lower:
        write(allowed)
    print(f"{'gates ok' if not failed else str(failed) + ' GATES FAILED'} in {time.time()-started:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
