#!/usr/bin/env python3
"""Pin the development library: which public-domain photographs, from where,
with what bytes. Writes scripts/test_library.tsv, which make_test_library.py
reads. A maintainer runs this once when the set changes; the fixture is then
the same on every machine because the manifest is.

    python scripts/pin_test_library.py            # rewrite the large tier
    python scripts/pin_test_library.py --cache D   # keep the downloads in D
    python scripts/pin_test_library.py --redate    # reassign dates, no network

Every photograph is from a Library of Congress collection whose items carry
"No known restrictions on publication" -- the FSA/OWI transparencies and
negatives (works of the United States government), the Detroit Publishing
glass negatives, the Bain News Service and Harris & Ewing prints. The small
tier (the thirty-one the quick build uses, with its controlled cases) is kept
as it is; this writes the large tier around it.

Each collection is sampled across its whole span so the dates spread. The
Library catalogs a month or a year; the day and time are the fixture's, a
function of the manifest alone: frames of one photographer's catalogued
month fall in fives, three seconds apart, so the fixture has bursts the way
a card does, and `--redate` reassigns them without a download.
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
# record names an organisation instead of a person, and the years to sample by when the
# collection is too deep to page through (the API refuses pages past about a thousand).
COLLECTIONS = (
    ("fsa-owi-color-photographs", 1700, "split", "", None, ()),
    ("fsa-owi-black-and-white-negatives", 2400, "split", "", None, tuple(range(1935, 1945))),
    ("detroit-publishing-company", 1200, "Raws/Film Scans", "Detroit Publishing", "Detroit Publishing Co.", ()),
    ("bain", 800, "Raws/Film Scans", "Bain News Service", "Bain News Service", ()),
    ("harris-ewing", 800, "Raws/Film Scans", "Harris & Ewing", "Harris & Ewing", ()),
)
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
YEAR = re.compile(r"\b(18\d\d|19\d\d)\b")
# Frames per burst; four on one beat is what web/stacks.py calls a set.
BURST = 5
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


def listing(slug: str, facet: str, page: int) -> dict:
    return json.loads(get(f"https://www.loc.gov/collections/{slug}/?fo=json&c={PER_PAGE}&sp={page}"
                          f"&fa=online-format:image{facet}"))


def sample(slug: str, wanted: int, years: tuple[int, ...]) -> list[dict]:
    """Items spread across the collection: whole pages, so consecutive frames
    stay together, every k-th page across the span -- or, for a collection
    too deep to page, the first pages of each year."""

    facets = [f"|dates:{year}" for year in years] or [""]
    share = -(-wanted // len(facets))
    chosen: list[dict] = []
    for facet in facets:
        time.sleep(0.6)   # the Library asks for a gentle pace on its API
        total = int(listing(slug, facet, 1)["pagination"]["total"])
        need = max(1, -(-share // PER_PAGE))
        step = max(1, min(total, 1000) // need) if not years else 1
        taken = 0
        for page in range(1, min(total, 1000) + 1, step):
            if taken >= share:
                break
            time.sleep(0.6)
            try:
                body = listing(slug, facet, page)
            except RuntimeError as error:   # one page refused is not the collection lost
                print(f"  {slug}{facet}: page {page} skipped: {error}", file=sys.stderr)
                continue
            for result in body.get("results", []):
                row = record(result)
                if row is not None:
                    chosen.append(row)
                    taken += 1
            print(f"  {slug}{facet}: page {page}/{total}, {len(chosen)} so far", file=sys.stderr)
    return chosen[:wanted]


def record(result: dict) -> dict | None:
    item = result.get("item") or {}
    service = [u.split("#")[0] for u in result.get("image_url", []) if u.split("#")[0].endswith("v.jpg")]
    if not service or not service[0].startswith(TILE):
        return None
    catalogued = item.get("created_published") or item.get("date") or result.get("date") or ""
    if isinstance(catalogued, list):
        catalogued = catalogued[0] if catalogued else ""
    catalogued = str(catalogued).strip()
    if not YEAR.search(catalogued):
        return None
    by = (result.get("contributor") or [""])[0]
    return {
        "id": service[0].rsplit("/", 1)[1][:-5],
        "item": result["id"].rstrip("/").rsplit("/", 1)[1],
        "photographer": _person(by),
        "catalogued": catalogued,
        "title": re.sub(r"\s+", " ", result.get("title", "")).strip().rstrip(".").replace('"', "'"),
        "path": service[0][len(TILE):],
    }


def _person(by: str) -> str:
    """'delano, jack' as the Library files it becomes 'Jack Delano'."""

    parts = [p.strip() for p in by.split(",")]
    if len(parts) >= 2 and parts[1] and not parts[1][0].isdigit():
        return f"{parts[1].title()} {parts[0].title()}".strip()
    return by.title()


def dated(rows: list[dict]) -> None:
    """Assign every row its capture date from the manifest alone. Rows of one
    root, roll, photographer and catalogued month are ordered by id and fall
    in fives (`BURST`), three seconds apart -- four on one beat is a set to
    `web/stacks.py`, so every five is a stack proposal -- and each five has
    its own day, hour and minute inside the catalogued month."""

    rows.sort(key=lambda r: (r["root"], r["roll"], r["photographer"], r["catalogued"], r["id"]))
    position: dict[tuple, int] = {}
    for row in rows:
        key = (row["root"], row["roll"], row["photographer"], row["catalogued"])
        n = position.get(key, 0)
        position[key] = n + 1
        row["taken"] = _when(row["catalogued"], "/".join((*key, str(n // BURST))), n % BURST)


def _when(catalogued: str, burst: str, place: int) -> str:
    """The year the Library catalogs (the first of a range) and the month when
    it names one; the day, hour and minute from the burst's own name, the
    seconds from the frame's place in it."""

    years = [int(y) for y in YEAR.findall(catalogued)]
    year = years[0]
    month = next((n for word, n in MONTHS.items() if word in catalogued.lower()), None)
    digits = int(hashlib.sha256(burst.encode()).hexdigest()[:8], 16)
    if month is None:
        month = digits % 12 + 1
    day = digits % 28 + 1
    hour = 8 + (digits // 28) % 10
    minute = (digits // 280) % 60
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{place * 3:02d}"


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
    parser.add_argument("--redate", action="store_true", help="reassign the large tier's dates from the manifest, no network")
    args = parser.parse_args()
    cache = Path(args.cache) if args.cache else None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        manifest = list(csv.DictReader(handle, delimiter="\t"))
    small = [row for row in manifest if row["tier"] == "small"]
    known = {row["id"] for row in small}

    if args.redate:
        rows = [row for row in manifest if row["tier"] == "large"]
        dated(rows)
        return write(small, rows)

    rows: list[dict] = []
    for slug, wanted, root, roll, org, years in COLLECTIONS:
        print(f"{slug}: sampling {wanted}", file=sys.stderr)
        for row in sample(slug, wanted, years):
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

    dated(rows)
    return write(small, rows)


def write(small: list[dict], rows: list[dict]) -> int:
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
