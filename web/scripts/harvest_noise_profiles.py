#!/usr/bin/env python3
"""Distill the Canon EOS R noise-calibration rows used by Develop.

The source data is darktable's measured calibration dataset.  This script is
kept with the generated payload so an update is deliberate and reproducible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DARKTABLE_REPOSITORY = "https://github.com/darktable-org/darktable"
DARKTABLE_COMMIT = "9afc58a34dfd79b6e6a41f0c6277a7189b46ac62"
LICENSE = "GPL-3.0-or-later"
MODELS = frozenset({"EOS R3", "EOS R5", "EOS R6", "EOS R7"})
DEFAULT_SOURCE = Path("/home/sean/Projects/darktable/data/noiseprofiles.json")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "features" / "develop" / "noise_profiles.json"


def harvest(source: Path) -> list[dict[str, object]]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for maker in payload.get("noiseprofiles", []):
        if maker.get("maker") != "Canon":
            continue
        for model in maker.get("models", []):
            if model.get("model") not in MODELS:
                continue
            for profile in model.get("profiles", []):
                if profile.get("skip"):
                    continue
                rows.append(
                    {
                        "maker": maker["maker"],
                        "model": model["model"],
                        "iso": profile["iso"],
                        "a": profile["a"],
                        "b": profile["b"],
                    }
                )
    return sorted(rows, key=lambda row: (str(row["model"]), float(row["iso"])))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = harvest(args.source)
    payload = {
        "provenance": {
            "source_repo": DARKTABLE_REPOSITORY,
            "source_commit": DARKTABLE_COMMIT,
            "license": LICENSE,
            "note": "Measured camera calibration data distilled from darktable; review GPL compatibility before any public release.",
        },
        "profiles": rows,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
