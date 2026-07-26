#!/usr/bin/env python3
"""Distill measured noise-calibration rows used by Develop.

Source data is darktable's measured calibration dataset.  Cameras are the
bodies present in the azimuth-photo fixture library that darktable already
calibrated — adding another body is a data change (MODEL_SPECS / aliases),
not a code change in the lookup path.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


DARKTABLE_REPOSITORY = "https://github.com/darktable-org/darktable"
DARKTABLE_COMMIT = "9afc58a34dfd79b6e6a41f0c6277a7189b46ac62"
LICENSE = "GPL-3.0-or-later"
DEFAULT_SOURCE = Path(
    os.environ.get("DARKTABLE_NOISE_PROFILES", str(Path.home() / "src/darktable/data/noiseprofiles.json"))
)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "features" / "develop" / "noise_profiles.json"

# (maker, model) pairs to ship.  Aliases map EXIF / catalog spellings onto these.
MODEL_SPECS = (
    ("Canon", "EOS 2000D"),
    ("Canon", "EOS R3"),
    ("Canon", "EOS R5"),
    ("Canon", "EOS R5 Mark II"),
    ("Canon", "EOS R6"),
    ("Canon", "EOS R6 Mark II"),
    ("Canon", "EOS R7"),
    ("Canon", "EOS RP"),
    ("DJI", "FC3170"),
    ("Sony", "ILCE-7RM4"),
)

# Catalog / EXIF model strings → canonical profile model (data, not code).
ALIASES = {
    "EOS R5m2": "EOS R5 Mark II",
    "EOS R6m2": "EOS R6 Mark II",
    "ILCE-7RM4A": "ILCE-7RM4",
    "EOS Rebel T7": "EOS 2000D",
    "Rebel T7": "EOS 2000D",
    "Canon EOS Rebel T7": "EOS 2000D",
}


def harvest(source: Path) -> list[dict[str, object]]:
    wanted = {(maker, model) for maker, model in MODEL_SPECS}
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for maker in payload.get("noiseprofiles", []):
        maker_name = maker.get("maker")
        for model in maker.get("models", []):
            key = (maker_name, model.get("model"))
            if key not in wanted:
                continue
            for profile in model.get("profiles", []):
                if profile.get("skip"):
                    continue
                rows.append(
                    {
                        "maker": maker_name,
                        "model": model["model"],
                        "iso": profile["iso"],
                        "a": profile["a"],
                        "b": profile["b"],
                    }
                )
    missing = wanted - {(row["maker"], row["model"]) for row in rows}
    if missing:
        raise SystemExit(f"missing darktable profiles for: {sorted(missing)}")
    return sorted(rows, key=lambda row: (str(row["maker"]), str(row["model"]), float(row["iso"])))


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
            "fixture_cameras": [f"{maker} {model}" for maker, model in MODEL_SPECS],
        },
        "aliases": ALIASES,
        "profiles": rows,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} profiles for {len(MODEL_SPECS)} models → {args.output}")


if __name__ == "__main__":
    main()
