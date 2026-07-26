"""Harvest embedded Adobe DNG profiles from an Azimuth Photo SQLite catalog.

The database is opened read-only and used only as a DNG path index.  Every DNG
is identity-scanned, then at most three files per (UniqueCameraModel,
ProfileName) are fully read to verify the profile content hash is stable.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


WEB_ROOT = Path(__file__).resolve().parents[1]
if str(WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_ROOT))

from features.develop.adobe_profiles import (  # noqa: E402
    extract_embedded_profile,
    slugify,
)


DEFAULT_DB = Path(
    os.environ.get("AZIMUTH_PROFILE_CATALOG", str(Path.home() / ".local/share/azimuth-photo/catalog/azimuth.db"))
)
DEFAULT_OUTPUT = WEB_ROOT / "features" / "develop" / "profiles" / "adobe"
RAW_EXTENSIONS = (".dng", ".cr2", ".cr3")
MAX_PROFILE_BYTES = 2 * 1024 * 1024


def _identity(path: str) -> tuple[str, str] | None:
    """Read only DNG identity ASCII tags without materializing large map tables.

    Full extraction deliberately goes through tifffile in ``adobe_profiles``.
    This small TIFF IFD reader is only the corpus index pass: tifffile's page
    construction eagerly visits enough DNG structures to turn an 82k-file
    scalar scan into needless multi-gigabyte I/O.
    """
    try:
        with open(path, "rb") as source:
            header = source.read(16)
            if len(header) < 8 or header[:2] not in (b"II", b"MM"):
                return None
            order = "<" if header[:2] == b"II" else ">"
            magic = struct.unpack(f"{order}H", header[2:4])[0]
            if magic == 42:
                offset = struct.unpack(f"{order}I", header[4:8])[0]
                count_size, entry_size, offset_size = 2, 12, 4
            elif magic == 43 and len(header) >= 16 and struct.unpack(f"{order}H", header[4:6])[0] == 8:
                offset = struct.unpack(f"{order}Q", header[8:16])[0]
                count_size, entry_size, offset_size = 8, 20, 8
            else:
                return None
            source.seek(offset)
            count_bytes = source.read(count_size)
            if len(count_bytes) != count_size:
                return None
            count = struct.unpack(f"{order}{'H' if count_size == 2 else 'Q'}", count_bytes)[0]
            if count > 4096:
                return None
            found: dict[int, str] = {}
            for _ in range(count):
                entry = source.read(entry_size)
                if len(entry) != entry_size:
                    return None
                tag, field_type = struct.unpack(f"{order}HH", entry[:4])
                if tag not in (50708, 50936) or field_type != 2:  # UniqueCameraModel, ProfileName, ASCII
                    continue
                value_count = struct.unpack(f"{order}{'I' if offset_size == 4 else 'Q'}", entry[4 : 4 + offset_size])[0]
                if not value_count or value_count > 4096:
                    continue
                value_field = entry[4 + offset_size :]
                if value_count <= offset_size:
                    raw = value_field[:value_count]
                else:
                    value_offset = struct.unpack(f"{order}{'I' if offset_size == 4 else 'Q'}", value_field[:offset_size])[0]
                    position = source.tell()
                    source.seek(value_offset)
                    raw = source.read(value_count)
                    source.seek(position)
                found[tag] = raw.decode("utf-8", "replace").strip("\0 ")
            model_text = found.get(50708, "")
            name_text = found.get(50936, "")
            return (model_text, name_text) if model_text and name_text else None
    except (OSError, ValueError, struct.error):
        return None


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _write_profile(output_dir: Path, profile: dict[str, Any]) -> tuple[Path | None, int]:
    filename = f"{slugify(profile['camera_model'])}--{slugify(profile['profile_name'])}.json"
    path = output_dir / filename
    encoded = (json.dumps(profile, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_PROFILE_BYTES:
        path.unlink(missing_ok=True)
        return None, len(encoded)
    path.write_bytes(encoded)
    return path, len(encoded)


def _has_profile_for_catalog_model(catalog_model: str, profiled_models: set[str]) -> bool:
    """Match catalog labels such as ``EOS R5`` to DNG's ``Canon EOS R5``."""
    normalized = "".join(char for char in catalog_model.casefold() if char.isalnum())
    aliases = {
        "eosr5m2": "canoneosr5markii",
        "eosr6m2": "canoneosr6markii",
        "eosrebelt7": "canoneos1500d",
    }
    normalized = aliases.get(normalized, normalized)
    for profile_model in profiled_models:
        candidate = "".join(char for char in profile_model.casefold() if char.isalnum())
        if normalized == candidate or candidate.endswith(normalized) or normalized.endswith(candidate) or (
            len(normalized) >= 6 and normalized in candidate
        ):
            return True
    return False


def harvest(database: Path, output_dir: Path, workers: int = 8) -> dict[str, Any]:
    """Harvest the corpus and return a compact, serializable coverage report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with _readonly_connection(database) as connection:
        dng_rows = list(
            connection.execute(
                "SELECT filepath FROM images WHERE lower(file_ext) = '.dng' AND filepath IS NOT NULL ORDER BY id"
            )
        )
        raw_rows = list(
            connection.execute(
                "SELECT COALESCE(NULLIF(TRIM(camera_model), ''), '<unknown>'), COUNT(*) "
                "FROM images WHERE lower(file_ext) IN ('.dng', '.cr2', '.cr3') "
                "GROUP BY 1 ORDER BY 1 COLLATE NOCASE"
            )
        )

    groups: Counter[tuple[str, str]] = Counter()
    samples: dict[tuple[str, str], list[str]] = defaultdict(list)
    unreadable = 0
    dng_without_profile: Counter[str] = Counter()
    paths = [path for (path,) in dng_rows]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        identities = executor.map(_identity, paths)
        for index, (path, identity) in enumerate(zip(paths, identities), start=1):
            if index % 2000 == 0 or index == len(paths):
                print(f"Identity scan: {index}/{len(paths)} DNGs", flush=True)
            if identity is None:
                unreadable += 1
                continue
            groups[identity] += 1
            if len(samples[identity]) < 3:
                samples[identity].append(path)

    harvested: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    oversize: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda item: (item[0].casefold(), item[1].casefold())):
        candidates = [profile for path in samples[key] if (profile := extract_embedded_profile(path)) is not None]
        if not candidates:
            dng_without_profile[key[0]] += groups[key]
            continue
        hashes = {profile["content_hash"] for profile in candidates}
        chosen = candidates[0]
        chosen["source_count"] = groups[key]
        if len(hashes) > 1:
            conflicts.append(
                {
                    "camera_model": key[0],
                    "profile_name": key[1],
                    "hashes": sorted(hashes),
                    "sample_files": [profile["source_file"] for profile in candidates],
                }
            )
        path, size = _write_profile(output_dir, chosen)
        if path is None:
            oversize.append(
                {
                    "camera_model": chosen["camera_model"],
                    "profile_name": chosen["profile_name"],
                    "bytes": size,
                    "look_table_dims": (chosen.get("look_table") or {}).get("dims"),
                }
            )
            continue
        harvested.append(
            {
                "camera_model": chosen["camera_model"],
                "profile_name": chosen["profile_name"],
                "source_count": chosen["source_count"],
                "content_hash": chosen["content_hash"],
                "path": str(path),
                "bytes": size,
            }
        )

    profiled_models = {item["camera_model"] for item in harvested}
    coverage = [
        {
            "catalog_model": model,
            "raw_count": count,
            "profile": _has_profile_for_catalog_model(model, profiled_models),
        }
        for model, count in raw_rows
    ]
    return {
        "dng_rows": len(dng_rows),
        "unreadable_or_unprofiled_dng_rows": unreadable,
        "profiles": harvested,
        "coverage": coverage,
        "hash_conflicts": conflicts,
        "oversize_profiles": oversize,
        "dng_without_profile": dict(sorted(dng_without_profile.items())),
    }


def _print_report(report: dict[str, Any]) -> None:
    profiles = report["profiles"]
    standard_models = sorted({item["camera_model"] for item in profiles if item["profile_name"].casefold().startswith("adobe standard")})
    print(f"Scanned {report['dng_rows']} DNG rows; {report['unreadable_or_unprofiled_dng_rows']} unavailable or without profile identity.")
    print(f"Harvested {len(profiles)} profile files across {len({item['camera_model'] for item in profiles})} camera models.")
    print(f"Adobe Standard models ({len(standard_models)}): {', '.join(standard_models) or 'none'}")
    print("Profiles:")
    for item in profiles:
        print(f"  {item['camera_model']} | {item['profile_name']} | {item['source_count']} DNGs | {item['bytes']} bytes | {Path(item['path']).name}")
    print("Raw-model coverage (catalog label):")
    for item in report["coverage"]:
        print(f"  {item['catalog_model']} | {item['raw_count']} raws | {'profile' if item['profile'] else 'no exact profile'}")
    if report["hash_conflicts"]:
        print("Content-hash conflicts:")
        for conflict in report["hash_conflicts"]:
            print(f"  {conflict['camera_model']} | {conflict['profile_name']} | {', '.join(conflict['hashes'])}")
    else:
        print("Content-hash conflicts: none in up-to-three-file samples.")
    if report["oversize_profiles"]:
        print(f"Oversize profiles skipped (>{MAX_PROFILE_BYTES} bytes; frozen-spec conflict):")
        for profile in report["oversize_profiles"]:
            print(f"  {profile['camera_model']} | {profile['profile_name']} | {profile['bytes']} bytes | LookTable {profile['look_table_dims']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB, help="Azimuth Photo SQLite DB (opened read-only).")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directory for harvested JSON profiles.")
    parser.add_argument("--report-json", type=Path, help="Optional machine-readable report path.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent scalar identity reads (default: 8).")
    args = parser.parse_args()
    report = harvest(args.database, args.output, workers=args.workers)
    _print_report(report)
    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
