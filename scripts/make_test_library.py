#!/usr/bin/env python3
"""Build a small archive of public-domain photographs to develop against.

Working against the real library means every check waits on a spinning disk
holding 150,000 photos, so a single verification costs most of an hour. This
writes public-domain photographs from the Library of Congress into the same
root shape on fast local disk, on any machine, with nothing of the owner's in
it. Everything the app does to the real archive it does here.

    python scripts/make_test_library.py                 # the small tier, ~/Azimuth Test/Photos, 2 s
    python scripts/make_test_library.py --large         # the whole manifest: thousands, minutes
    python scripts/make_test_library.py --dest /tmp/az  # anywhere else
    AZIMUTH_TEST_LIBRARY=/mnt/fast/az python scripts/make_test_library.py
    python scripts/make_test_library.py --reset         # throw it away and rebuild

Two tiers of one manifest, scripts/test_library.tsv (written by
scripts/pin_test_library.py): the small tier is thirty-one FSA/OWI Kodachrome
transparencies with the controlled cases below, what a session start and a
quick check want; the large tier adds thousands more -- the FSA/OWI colour and
black-and-white files, Detroit Publishing glass negatives, Bain and Harris &
Ewing prints -- across 1890-1944, with bursts, for development, the proofs and
scripts/bench.py. All are works with no known restrictions on publication;
each is pinned by URL and SHA-256, so the fixture is the same on every machine
and every day, and PROVENANCE.md in the library root names each one's source,
photographer and rights.

What the fixture knows for certain, so a check can be exact:

- Capture dates. The Library catalogs the month; the fixture assigns the day
  and time, writes it as EXIF DateTimeOriginal, and files the photograph
  under that day. The catalog's date_taken is known before the first sweep.
  (File times stay modern: Windows cannot hold a 1939 modification time.)
- A camera's own files. Raws/Digital holds DNGs made from the same frames
  (uncompressed linear raw with a linearisation table), so the RAW decode and
  the RAW header walk run on every machine, not only where a card is.
- A duplicate pair. One Edits photograph is copied byte for byte under
  Snapshots, a day older on disk, so the exact-duplicate rule (keep the
  oldest filesystem-modified file) has one right answer.
- A nested folder. One photograph sits in a folder inside its day folder,
  which the real archive has.
- An undated photograph. One carries no EXIF date, so only its folder and
  its modification time say when.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import http.client
import io
import os
import shutil
import struct
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOTS = ("Edits", "Raws/Digital", "Raws/Film Scans", "Snapshots")
MANIFEST = Path(__file__).resolve().with_name("test_library.tsv")
TILE = "https://tile.loc.gov/storage-services/service/pnp/"
MAKE, MODEL = "Kodak", "Kodachrome"          # the medium, as the camera the catalog shows
RIGHTS = "https://www.loc.gov/rr/print/res/071_fsab.html"
# Every file's modification time (2020-09-13 12:26:40 UTC): the same on every
# disk, and after 1970 so every platform can store and read it.
STAMP = 1_600_000_000

def manifest(large: bool) -> list[dict]:
    """The rows this build makes: the small tier, or every row."""

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return rows if large else [row for row in rows if row["tier"] == "small"]


NESTED = ("1a33912", "Fair")   # the state fair, in a folder inside its day
DUPLICATE = "1a33851"          # the plumbing store, again under Snapshots and a day older
UNDATED = "1a33864"            # "ca. 1940 Dec.": no EXIF date, so the folder alone says when


def fetch(path: str, sha256: str) -> bytes:
    """The bytes the manifest promises, or a loud failure: never a different photograph."""

    url = TILE + path
    failure = ""
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        request = urllib.request.Request(url, headers={"User-Agent": "azimuth-photo/test-library"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except (OSError, http.client.HTTPException) as error:   # a cut body is the latter
            failure = str(error)
            continue
        if hashlib.sha256(data).hexdigest() == sha256:
            return data
        failure = f"SHA-256 mismatch ({len(data)} bytes)"
    raise RuntimeError(f"{url}: {failure}")


# One small TIFF writer. The EXIF a JPEG carries and a DNG's own directories
# are the same structure, so one writer says everything a fixture file says.
SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 10: 8}   # BYTE, ASCII, SHORT, LONG, RATIONAL, SRATIONAL


def ascii_(tag: int, text: str) -> tuple:
    return (tag, 2, text.encode("utf-8") + b"\0")


def byte(tag: int, *values: int) -> tuple:
    return (tag, 1, bytes(values))


def short(tag: int, *values: int) -> tuple:
    return (tag, 3, struct.pack(f"<{len(values)}H", *values))


def long_(tag: int, *values: int) -> tuple:
    return (tag, 4, struct.pack(f"<{len(values)}I", *values))


def rational(tag: int, *values: int) -> tuple:
    return (tag, 5, struct.pack(f"<{len(values)}I", *values))


def srational(tag: int, *values: int) -> tuple:
    return (tag, 10, struct.pack(f"<{len(values)}i", *values))


def _ifd(entries: list, at: int) -> bytes:
    """One directory placed at `at`: entries by tag, values over four bytes spilled after it."""

    entries = sorted(entries)
    body = struct.pack("<H", len(entries))
    spill = at + 2 + 12 * len(entries) + 4
    extra = b""
    for tag, kind, payload in entries:
        count = len(payload) // SIZE[kind]
        if len(payload) <= 4:
            body += struct.pack("<HHI", tag, kind, count) + payload.ljust(4, b"\0")
        else:
            body += struct.pack("<HHII", tag, kind, count, spill + len(extra))
            extra += payload + b"\0" * (len(payload) % 2)
    return body + b"\0\0\0\0" + extra


def tiff(ifd0: list, exif: list, pixels: bytes = b"") -> bytes:
    """A little-endian TIFF: IFD0, the Exif directory it points to, then the strip of pixels."""

    def whole(exif_at: int, pixels_at: int) -> list:
        return [*ifd0, long_(0x8769, exif_at), *([long_(0x0111, pixels_at)] if pixels else [])]

    exif_at = 8 + len(_ifd(whole(0, 0), 0))
    pixels_at = exif_at + len(_ifd(exif, 0))
    return (b"II*\0" + struct.pack("<I", 8) + _ifd(whole(exif_at, pixels_at), 8)
            + _ifd(exif, exif_at) + pixels)


def header(title: str, taken: str | None) -> tuple[list, list]:
    """What every fixture file says of itself: the medium as its camera, the
    Library's title as its description, and the assigned capture date."""

    ifd0 = [ascii_(0x010F, MAKE), ascii_(0x0110, MODEL), ascii_(0x010E, title)]
    exif = [ascii_(0x9003, taken.replace("-", ":", 2))] if taken else []
    return ifd0, exif


def jpeg(data: bytes, title: str, taken: str | None) -> bytes:
    """The downloaded JPEG, pixels untouched, with one Exif segment after its JFIF header."""

    ifd0, exif = header(title, taken)
    blob = b"Exif\0\0" + tiff(ifd0, exif)
    segment = b"\xff\xe1" + struct.pack(">H", len(blob) + 2) + blob
    cut = 4 + struct.unpack(">H", data[4:6])[0] if data[2:4] == b"\xff\xe0" else 2
    return data[:cut] + segment + data[cut:]


XYZ_TO_SRGB = (32406, -15372, -4986, -9689, 18758, 415, 557, -2040, 10570)   # over 10000


def _linear(code: int) -> int:
    """sRGB's transfer curve: one 8-bit code to a 16-bit linear level."""

    c = code / 255.0
    return round(65535 * (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4))


def dng(data: bytes, title: str, taken: str) -> bytes:
    """The same frame as a camera's own file: one uncompressed strip of the
    JPEG's 8-bit pixels, a linearisation table so a raw decoder sees light,
    and sRGB's primaries as the camera's matrix, so it renders as the photograph."""

    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        width, height = image.size
        pixels = image.tobytes()
    ifd0, exif = header(title, taken)
    ifd0 += [
        long_(0x00FE, 0), long_(0x0100, width), long_(0x0101, height), short(0x0102, 8, 8, 8),
        short(0x0103, 1), short(0x0106, 34892), short(0x0112, 1), short(0x0115, 3),   # LinearRaw
        long_(0x0116, height), long_(0x0117, width * height * 3), short(0x011C, 1),
        byte(50706, 1, 4, 0, 0), byte(50707, 1, 1, 0, 0), ascii_(50708, MODEL),   # DNG 1.4
        short(50712, *(_linear(code) for code in range(256))), long_(50717, 65535),
        srational(50721, *(n for m in XYZ_TO_SRGB for n in (m, 10000))),   # ColorMatrix1
        rational(50728, 1, 1, 1, 1, 1, 1), short(50778, 21),   # neutral as shot, under D65
    ]
    return tiff(ifd0, exif, pixels)


def place(dest: Path, root: str, taken: str, name: str, inside: str = "") -> Path:
    """Root/YYYY/YYYY-MM-DD/[roll/]name: the archive's own shape, from the
    photograph's own date; a film scan sits in its roll's folder inside the day."""

    day = taken[:10]
    return dest / root / day[:4] / day / inside / name


def write(path: Path, body: bytes, *, days_older: int = 0) -> tuple[Path, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    stamp = STAMP - days_older * 86400
    os.utime(path, (stamp, stamp))
    return path, len(body)


def build(dest: Path, rows: list[dict]) -> list[tuple[Path, int]]:
    """Download and write, one photograph at a time as its bytes arrive, so a
    large tier never sits whole in memory."""

    written = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        bodies = pool.map(lambda row: fetch(row["path"], row["sha256"]), rows)
        for row, data in zip(rows, bodies):
            root, taken, digital_id, title = row["root"], row["taken"], row["id"], row["title"]
            inside = NESTED[1] if digital_id == NESTED[0] else row["roll"]
            if root == "Raws/Digital":
                path = place(dest, root, taken, f"{digital_id}.dng", inside)
                body = dng(data, title, taken)
            else:
                path = place(dest, root, taken, f"{digital_id}.jpg", inside)
                body = jpeg(data, title, None if digital_id == UNDATED else taken)
            written.append(write(path, body))
            if digital_id == DUPLICATE:
                written.append(write(place(dest, "Snapshots", taken, path.name), body, days_older=1))
            if len(written) % 500 == 0:
                print(f"  {len(written)} written...")
    return written


def provenance(dest: Path, rows: list[dict]) -> None:
    lines = [
        "# Provenance",
        "",
        "Every photograph here is from the Library of Congress Prints and Photographs",
        "Division, from a collection whose items carry no known restrictions on",
        "publication: the FSA/OWI colour transparencies and black-and-white negatives",
        f"(works of the United States government: {RIGHTS}), the Detroit Publishing",
        "Company glass negatives, and the Bain News Service and Harris & Ewing prints.",
        "",
        "Each JPEG is the Library's 1024-pixel service file, pinned by the SHA-256",
        "below with its pixels untouched, plus one EXIF segment: make and model name",
        "the medium, the description is the Library's title, and the capture date is",
        "assigned by the fixture -- the Library catalogs a month or a year; the day",
        "and time are the fixture's, so the catalog's dates are known exactly. The",
        "DNGs under Raws/Digital hold the same frames as uncompressed linear raw.",
        f"Downloads come from {TILE}<path>; scripts/test_library.tsv is the manifest.",
        "",
        "| file | root | photographed | catalogued | photographer | item | SHA-256 of the download | title |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(f"| {row['id']} | {row['root']} | {row['taken']} | {row['catalogued']} | {row['photographer']} "
                     f"| https://www.loc.gov/item/{row['item']}/ | {row['sha256']} | {row['title']} |")
    lines += [
        "",
        f"Controlled cases: {DUPLICATE} is also under Snapshots, byte for byte and a day",
        (f"older on disk; {NESTED[0]} sits in a `{NESTED[1]}` folder inside its day; "
         f"{UNDATED} carries no EXIF date."),
    ]
    (dest / "PROVENANCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="build the development archive of public-domain photographs")
    parser.add_argument(
        "--dest", help="where to build it (default: $AZIMUTH_TEST_LIBRARY, else ~/Azimuth Test/Photos)")
    parser.add_argument("--large", action="store_true",
                        help="build the whole manifest (thousands of photographs) instead of the small tier")
    parser.add_argument("--reset", action="store_true", help="delete the archive first")
    args = parser.parse_args()
    dest = Path(args.dest or os.environ.get("AZIMUTH_TEST_LIBRARY")
                or Path.home() / "Azimuth Test" / "Photos")

    # PROVENANCE.md is written last, so its presence means the whole library
    # is there; a folder without it is a build that stopped short, or not
    # this script's at all -- which --reset must never remove.
    built = dest / "PROVENANCE.md"
    if args.reset and dest.exists():
        if not built.is_file():
            print(f"{dest} was not built by this script; leaving it alone", file=sys.stderr)
            return 1
        shutil.rmtree(dest)
        print(f"removed {dest}")
    if built.is_file():
        print(f"{dest} is already built; --reset throws it away and rebuilds")
        return 0

    rows = manifest(args.large)
    print(f"fetching {len(rows)} photographs from the Library of Congress...")
    try:
        written = build(dest, rows)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    provenance(dest, rows)

    total = sum(size for _, size in written)
    print(f"\nwrote {len(written)} photographs ({total / 1e6:.1f} MB) into {dest}")
    for root in ROOTS:
        count = sum(1 for path, _ in written if path.is_relative_to(dest / root))
        print(f"  {root:<16} {count}")
    print("\nrun the app against it with an isolated home, and attach the folder:")
    print("  AZIMUTH_HOME=<an empty folder> python web/desktop.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
