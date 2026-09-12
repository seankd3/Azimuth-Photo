#!/usr/bin/env python3
"""Pin the development library: which public-domain photographs, from where,
with what bytes. Writes scripts/test_library.tsv, which make_test_library.py
reads. A maintainer runs this once when the set changes; the fixture is then
the same on every machine because the manifest is.

    python scripts/pin_test_library.py            # rewrite the large tier
    python scripts/pin_test_library.py --cache D   # keep the downloads in D

Every photograph is from a Library of Congress collection whose items carry
"No known restrictions on publication" -- the FSA/OWI transparencies and
negatives (works of the United States government), the Detroit Publishing
glass negatives, the Bain News Service and Harris & Ewing prints. The small
tier (the thirty-one the quick build uses, with its controlled cases) is kept
as it is; this writes the large tier around it.

Each collection is sampled across its whole span so the dates spread, and
frames that the Library numbered consecutively are given consecutive seconds,
so the fixture has bursts the way a card does. The Library catalogs a month
or a year; the day and time are the fixture's, assigned from the item's own
id so they never change.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "test_library.tsv"
COLUMNS = ("tier", "root", "roll", "taken", "id", "item", "photographer", "catalogued", "title", "path", "sha256")
AGENT = {"User-Agent": "azimuth-photo/test-library"}
TILE = "https://tile.loc.gov/storage-services/service/pnp/"

# collection slug, how many, the root (or Film Scans with a roll), photographer when the
# record names an organisation instead of a person.
COLLECTIONS = (
    ("fsa-owi-color-photographs", 1700, "split", "", None),
    ("fsa-owi-black-and-white-negatives", 2400, "split", "", None),
    ("detroit-publishing-company", 1200, "Raws/Film Scans", "Detroit Publishing", "Detroit Publishing Co."),
    ("bain", 800, "Raws/Film Scans", "Bain News Service", "Bain News Service"),
    ("harris-ewing", 800, "Raws/Film Scans", "Harris & Ewing", "Harris & Ewing"),
)
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
PER_PAGE = 100


def get(url: str, tries: int = 4) -> bytes:
    failure = ""
    for attempt in range(tries):
        if attempt:
            time.sleep(3 * attempt)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=90) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001 -- retried, then reported
            failure = f"{type(error).__name__}: {error}"
    raise RuntimeError(f"{url}: {failure}")


def pages(slug: str) -> int:
    first = json.loads(get(f"https://www.loc.gov/collections/{slug}/?fo=json&c={PER_PAGE}&fa=online-format:image"))
    return int(first["pagination"]["total"])


def sample(slug: str, wanted: int) -> list[dict]:
    """Items spread across the collection: every k-th page, whole pages, so
    consecutive frames stay together."""

    total = pages(slug)
    need = max(1, -(-wanted // PER_PAGE))
    step = max(1, total // need)
    chosen: list[dict] = []
    for page in range(1, total + 1, step):
        if len(chosen) >= wanted:
            break
        time.sleep(0.6)   # the Library asks for a gentle pace on its API
        body = json.loads(get(f"https://www.loc.gov/collections/{slug}/?fo=json&c={PER_PAGE}&sp={page}&fa=online-format:image"))
        for rank, result in enumerate(body.get("results", [])):
            row = record(result, rank)
            if row is not None:
                chosen.append(row)
        print(f"  {slug}: page {page}/{total}, {len(chosen)} so far", file=sys.stderr)
    return chosen[:wanted]


def record(result: dict, rank: int) -> dict | None:
    item = result.get("item") or {}
    service = [u.split("#")[0] for u in result.get("image_url", []) if u.split("#")[0].endswith("v.jpg")]
    if not service or not service[0].startswith(TILE):
        return None
    catalogued = item.get("created_published") or item.get("date") or result.get("date") or ""
    if isinstance(catalogued, list):
        catalogued = catalogued[0] if catalogued else ""
    catalogued = str(catalogued).strip()
    when = _when(catalogued, result["id"], rank)
    if when is None:
        return None
    by = (result.get("contributor") or [""])[0]
    return {
        "id": service[0].rsplit("/", 1)[1][:-5],
        "item": result["id"].rstrip("/").rsplit("/", 1)[1],
        "photographer": _person(by),
        "catalogued": catalogued,
        "title": re.sub(r"\s+", " ", result.get("title", "")).strip().rstrip(".").replace('"', "'"),
        "path": service[0][len(TILE):],
        "taken": when,
    }


def _person(by: str) -> str:
    """'delano, jack' as the Library files it becomes 'Jack Delano'."""

    parts = [p.strip() for p in by.split(",")]
    if len(parts) >= 2 and parts[1] and not parts[1][0].isdigit():
        return f"{parts[1].title()} {parts[0].title()}".strip()
    return by.title()


def _when(catalogued: str, item_url: str, rank: int) -> str | None:
    """A capture date the fixture assigns inside what the Library says: the
    year (the first of a range), the month when named, the day from the
    item's own digits, the seconds from its place on the page so consecutive
    frames are consecutive seconds."""

    years = [int(y) for y in re.findall(r"\b(18\d\d|19\d\d)\b", catalogued)]
    if not years:
        return None
    year = years[0]
    month = next((n for word, n in MONTHS.items() if word in catalogued.lower()), None)
    digits = int(re.sub(r"\D", "", item_url) or "0")
    if month is None:
        month = digits % 12 + 1
    day = digits % 28 + 1
    hour = 8 + (digits // 28) % 10
    minute = (digits // 280) % 60
    second = (rank * 2) % 60
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"


def pinned(row: dict, cache: Path | None) -> dict:
    target = cache / row["id"] if cache else None
    if target is not None and target.is_file():
        data = target.read_bytes()
    else:
        data = get(TILE + row["path"])
        if target is not None:
            target.write_bytes(data)
    return {**row, "sha256": hashlib.sha256(data).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description="pin the large tier of the development library")
    parser.add_argument("--cache", help="keep the downloaded bytes here, so a rerun does not fetch again")
    args = parser.parse_args()
    cache = Path(args.cache) if args.cache else None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        small = [row for row in csv.DictReader(handle, delimiter="\t") if row["tier"] == "small"]
    known = {row["id"] for row in small}

    rows: list[dict] = []
    for slug, wanted, root, roll, org in COLLECTIONS:
        print(f"{slug}: sampling {wanted}", file=sys.stderr)
        for row in sample(slug, wanted):
            if row["id"] in known:
                continue
            known.add(row["id"])
            if org and not row["photographer"]:
                row["photographer"] = org
            digits = int(re.sub(r"\D", "", row["id"]) or "0")
            row["root"] = ("Edits" if digits % 2 else "Snapshots") if root == "split" else root
            row["roll"] = roll
            row["tier"] = "large"
            rows.append(row)

    print(f"pinning {len(rows)} downloads", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda r: pinned(r, cache), rows))

    rows.sort(key=lambda r: (r["root"], r["taken"], r["id"]))
    with MANIFEST.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in small + rows:
            writer.writerow(row)
    print(f"wrote {len(small)} small + {len(rows)} large rows to {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
