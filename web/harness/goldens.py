"""Read, write and compare one stage's recorded facts."""

from __future__ import annotations

import json
from pathlib import Path

from harness.env import GOLDENS


def path_for(stage: str) -> Path:
    return GOLDENS / f"{stage}.json"


def write(stage: str, recorded: dict) -> Path:
    GOLDENS.mkdir(parents=True, exist_ok=True)
    target = path_for(stage)
    target.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read(stage: str) -> dict | None:
    try:
        return json.loads(path_for(stage).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def differences(expected: dict, actual: dict, prefix: str = "") -> list[str]:
    """Every leaf that moved, as one readable line each."""

    if isinstance(expected, dict) and isinstance(actual, dict):
        found: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            here = f"{prefix}.{key}" if prefix else str(key)
            if key not in expected:
                found.append(f"{here}: added -> {actual[key]!r}")
            elif key not in actual:
                found.append(f"{here}: removed (was {expected[key]!r})")
            else:
                found.extend(differences(expected[key], actual[key], here))
        return found
    if expected != actual:
        return [f"{prefix}: {expected!r} -> {actual!r}"]
    return []
