#!/usr/bin/env python3
"""Build a small archive of public-domain photographs to develop against.

Working against the real library means every check waits on a spinning disk
holding 150,000 photos, so a single verification costs most of an hour. This
writes thirty-one photographs from the Library of Congress into the same
three-root shape on fast local disk, on any machine, with nothing of the
owner's in it. Everything the app does to the real archive it does here in
seconds.

    python scripts/make_test_library.py                 # ~/Azimuth Test/Photos
    python scripts/make_test_library.py --dest /tmp/az  # anywhere else
    AZIMUTH_TEST_LIBRARY=/mnt/fast/az python scripts/make_test_library.py
    python scripts/make_test_library.py --reset         # throw it away and rebuild

The photographs are FSA/OWI Kodachrome transparencies of 1939-1942, work of
the United States government with no known restrictions on publication. Each
is pinned by URL and SHA-256, so the fixture is the same on every machine and
every day, and PROVENANCE.md in the library root names each one's source,
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

ROOTS = ("Edits", "Raws/Digital", "Snapshots")
MAKE, MODEL = "Kodak", "Kodachrome"          # the medium, as the camera the catalog shows
RIGHTS = "https://www.loc.gov/rr/print/res/071_fsab.html"
# Every file's modification time (2020-09-13 12:26:40 UTC): the same on every
# disk, and after 1970 so every platform can store and read it.
STAMP = 1_600_000_000

# root, taken, LOC digital id, LOC item, photographer, the date as catalogued,
# SHA-256 of the download, the Library's title.
PHOTOGRAPHS = (
    ("Edits", "1940-11-16 10:30:00", "1a33828", "2017877377", "Jack Delano", "1940 Nov.",
     "81d1a5ddf51f905656969f4fd72d8e9064b2ef6432cb7da52653d3a41492fc94",
     "A view of the old sea town, Stonington, Conn"),
    ("Edits", "1940-11-16 10:42:00", "1a33829", "2017877378", "Jack Delano", "1940 Nov.",
     "b728dc5fc33badc748e05e50e2d40b92fe91ebba92132cff4789aaa168c4ca37",
     "Connecticut town, probably Stonington, on the sea"),
    ("Edits", "1940-12-21 14:05:00", "1a33851", "2017877353", "Jack Delano", "1940 Dec.",
     "c572524aae32886cabe06ca2eda57417eb20929d6fbed129d5908badf060c20b",
     "Brockton, Mass., Dec. 1940, second-hand plumbing store"),
    ("Edits", "1940-09-12 11:20:00", "1a33830", "2017877379", "Jack Delano", "1940 Sept.",
     "3a256b5a4602cf1b8d2d31f6b0bb1877a30f97a49c3a37ca46dc306105021921",
     "Cheesecloth covering used in growing shade grown tobacco; the stalks lying on the ground are left after the tobacco is cut; Suffield, Conn"),
    ("Edits", "1941-09-06 15:10:00", "1a33912", "2017877391", "Jack Delano", "1941 Sept.",
     "8f41bdbc35e3938a1cdd0f2849064cd47fa72518443b52a333b52b4595d96131",
     "At the Vermont state fair, Rutland"),
    ("Edits", "1941-12-15 09:40:00", "1a33952", "2017877848", "Jack Delano", "1941 Dec.",
     "09a941acd1948082cb471776198e2aaac9e72b25baad79a85ae408a0d77bd5a6",
     "Street in Christiansted, St. Croix? Virgin Islands"),
    ("Edits", "1941-12-15 17:25:00", "1a33963", "2017877858", "Jack Delano", "1941 Dec.",
     "aca498cd29e0a991bd3448fad6284cf552d7af57ccae4104bdedddd1ce3b8970",
     "The harbor, Frederiksted, Saint Croix island, Virgin Islands"),
    ("Edits", "1939-09-20 08:15:00", "1a34348", "2017877494", "Marion Post Wolcott", "1939 Sept.",
     "f1ec07e3a197828f81b8df1efc7639a4a6bcc40ab6cf8dbd4bef31e0f43bbafe",
     "Marcella Plantation, Mileston, Miss"),
    ("Edits", "1940-09-25 13:00:00", "1a34368", "2017877532", "Marion Post Wolcott", "1940 Sept.",
     "49414b1d941088e7dac74d0b6142433289eef613cf44627122170daa5278b713",
     "Field of Burley tobacco on farm of Russell Spears, drying and curing barn in the background, vicinity of Lexington, Ky"),
    ("Edits", "1941-06-10 10:00:00", "1a33882", "2017877508", "Jack Delano", "1941 June",
     "ed12b554313ed0b225de6c7c5ae4b78f84e801eb28174dbfab1790894c25c016",
     "Chopping cotton on rented land near White Plains, Greene County, Ga"),
    ("Edits", "1941-01-04 08:30:00", "1a33863", "2017877364", "Jack Delano", "1940 Dec. or 1941 Jan.",
     "78b2c8a483824aaa59e314ab23843066d150b6b431510a137531c7b85c1b6a8f",
     "[Train and several sets of railroad tracks in the snow, Massachusetts]"),
    ("Edits", "1942-01-24 15:45:00", "1a34257", "2017877652", "Arthur Rothstein", "1942 Jan.",
     "3b94721e6854415eed037f700d78408d545c5621a81e4345c43b7dec5e8987ba",
     "Boys flying a kite in front of community center, FSA ... camp, Robstown, Tex"),
    ("Edits", "1942-01-24 16:02:00", "1a34254", "2017877649", "Arthur Rothstein", "1942 Jan.",
     "0abcb2ad9b35184811c114e76fe94ebd65168adafc2e6dc3b160c3eb2551cf86",
     "Boys playing marbles, FSA ... labor camp, Robstown, Texas"),
    ("Raws/Digital", "1942-01-17 09:05:00", "1a34243", "2017877638", "Arthur Rothstein", "1942 Jan.",
     "1bd7eccc514bca96bab992348e8e1909977c2d6b8800381101d7a6a68503c596",
     "Instructor explaining the operation of a parachute to student pilots, Meacham Field, Fort Worth, Tex"),
    ("Raws/Digital", "1942-01-17 09:20:00", "1a34244", "2017877639", "Arthur Rothstein", "1942 Jan.",
     "3e6feaad77f0331deefa2011409b46e8408d4a798341d32e52592b196db2d257",
     "Student pilots, Meacham Field, Fort Worth, Tex"),
    ("Raws/Digital", "1942-01-17 10:10:00", "1a34250", "2017877645", "Arthur Rothstein", "1942 Jan.",
     "02d39c077f6e93ff6a139f042efbd507bf6490930baee462a3d719be2339735a",
     "Instructor and students studying a map, Meacham Field, Fort Worth, Tex"),
    ("Raws/Digital", "1942-01-17 11:30:00", "1a34249", "2017877644", "Arthur Rothstein", "1942 Jan.",
     "2bc11812195ca177de236c69f2fe97c2919f517d05ff8ac1be4a017ac3686f53",
     "[Civilian pilot training school], returning from practice flight, Meacham Field, Fort Worth, Tex"),
    ("Raws/Digital", "1941-12-19 12:15:00", "1a34020", "2017877778", "Jack Delano", "1941 Dec.",
     "2d3dd678386987c72cc468e4da0c3074cd8e7adbdd17050ce2f972e8ab25badc",
     "Sugar cane workers resting, Rio Piedras, Puerto Rico"),
    ("Raws/Digital", "1941-12-19 12:40:00", "1a34028", "2017877786", "Jack Delano", "1941 Dec.",
     "899b54d7f2387d99a26d306b10191583596eaba0748caa276366a88a00ab26ce",
     "Sugar cane land, vicinity of Rio Piedras, Puerto Rico"),
    ("Raws/Digital", "1941-12-12 10:20:00", "1a33956", "2017877852", "Jack Delano", "1941 Dec.",
     "35e4a27a9d1760d058cc368c987c13728d667633b0eee06f274a649ac7bfc549",
     "Cultivating sugar cane of the Virgin Islands Company land, vicinity of Bethlehem, St. Croix"),
    ("Raws/Digital", "1941-12-12 14:00:00", "1a33958", "2017877854", "Jack Delano", "1941 Dec.",
     "53dba85778adad848c411e9d5c0d0dee98d409a0c5c7fba41fda6fec7c42822a",
     "A cattle farm, vicinity of Christiansted, St. Croix, Virgin Islands"),
    ("Snapshots", "1939-11-08 09:30:00", "1a34337", "2017877483", "Marion Post Wolcott", "1939 Nov.",
     "3d6e2335017fc664e8ba0251a51010af94df454958e1b85643babdd4e661f248",
     "Day-laborers picking cotton near Clarksdale, Miss"),
    ("Snapshots", "1939-11-08 09:33:00", "1a34340", "2017877486", "Marion Post Wolcott", "1939 Nov.",
     "0387951d1456bd0de81474a7e61a012b77e9912df780d50f64e07e6566cfc282",
     "Day laborers picking cotton near Clarksdale, Miss"),
    ("Snapshots", "1939-11-08 09:35:00", "1a34344", "2017877490", "Marion Post Wolcott", "1939 Nov.",
     "be0fa5b466c3c447feb157c29269411912adea48b6fafc414e51cca9a322dada",
     "Day laborers picking cotton near Clarksdale, Miss"),
    ("Snapshots", "1939-09-20 16:20:00", "1a34349", "2017877495", "Marion Post Wolcott", "1939 Sept.",
     "f339e5b853f3e16069bea875adb681dfc24df666e39ff704faba828aca7265e6",
     "Backyard of Negro tenant's home, Marcella Plantation, Mileston, Miss. Delta"),
    ("Snapshots", "1939-09-20 16:24:00", "1a34350", "2017877496", "Marion Post Wolcott", "1939 Sept.",
     "9211c73db2c893ee4b0e426edbe5e96515c9cb0dce4616932e4031056afb008d",
     "Marcella Plantation, Mileston, Miss"),
    ("Snapshots", "1940-09-25 13:10:00", "1a34369", "2017877533", "Marion Post Wolcott", "1940 Sept.",
     "3d1190e609540f50daa636f004710a9c28a5641ec4c9753eae637d1806c02820",
     "Taking Burley tobacco in from the fields after it had been cut, to dry and cure in the barn, on the Russell Spears' farm, vicinity of Lexington, Ky"),
    ("Snapshots", "1940-09-25 13:18:00", "1a34370", "2017877534", "Marion Post Wolcott", "1940 Sept.",
     "04318bfcd0cf05f83c823ce037bd1c88140d675ee75d230d87386ea9fdbe7dd3",
     "Cutting Burley tobacco and putting it on sticks to wilt before taking it into the curing and drying barn on the Russell Spears' farm, vicinity of Lexington, Ky"),
    ("Snapshots", "1942-01-24 14:05:00", "1a34259", "2017877654", "Arthur Rothstein", "1942 Jan.",
     "c90c9bbe284a541f3ec0a495e5e6e3c8853befe1bf2d41b6d0e49c6d46adba4f",
     "Young woman at the community laundry on Saturday afternoon, FSA ... camp, Robstown, Tex"),
    ("Snapshots", "1942-01-24 14:12:00", "1a34262", "2017877657", "Arthur Rothstein", "1942 Jan.",
     "663d003a078c8020c49f709aab758505ee898be22985ac0c65e2b5e046838339",
     "Community clothesline, FSA ... camp, Robstown, Tex"),
    ("Snapshots", "1940-12-21 15:30:00", "1a33864", "2017877365", "Jack Delano", "ca. 1940 Dec.",
     "3699e61802ec3179e68b8e4b5f44fdab3ee0da566ae639a13450be210b7cb60c",
     "Massachusetts farm, possibly around Brockton, Mass"),
)
NESTED = ("1a33912", "Fair")   # the state fair, in a folder inside its day
DUPLICATE = "1a33851"          # the plumbing store, again under Snapshots and a day older
UNDATED = "1a33864"            # "ca. 1940 Dec.": no EXIF date, so the folder alone says when


def source_url(digital_id: str) -> str:
    """Where the Library keeps the 1024-pixel JPEG of one transparency."""

    return (f"https://tile.loc.gov/storage-services/service/pnp/fsac/"
            f"{digital_id[:4]}000/{digital_id[:5]}00/{digital_id}v.jpg")


def fetch(digital_id: str, sha256: str) -> bytes:
    """The bytes the table promises, or a loud failure: never a different photograph."""

    url = source_url(digital_id)
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
    """Root/YYYY/YYYY-MM-DD/name: the archive's own shape, from the photograph's own date."""

    day = taken[:10]
    return dest / root / day[:4] / day / inside / name


def write(path: Path, body: bytes, *, days_older: int = 0) -> tuple[Path, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    stamp = STAMP - days_older * 86400
    os.utime(path, (stamp, stamp))
    return path, len(body)


def build(dest: Path) -> list[tuple[Path, int]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        downloads = {row[2]: pool.submit(fetch, row[2], row[6]) for row in PHOTOGRAPHS}
    written = []
    for root, taken, digital_id, _item, _by, _catalogued, _sha256, title in PHOTOGRAPHS:
        data = downloads[digital_id].result()
        inside = NESTED[1] if digital_id == NESTED[0] else ""
        if root == "Raws/Digital":
            path = place(dest, root, taken, f"{digital_id}.dng", inside)
            body = dng(data, title, taken)
        else:
            path = place(dest, root, taken, f"{digital_id}.jpg", inside)
            body = jpeg(data, title, None if digital_id == UNDATED else taken)
        written.append(write(path, body))
        if digital_id == DUPLICATE:
            written.append(write(place(dest, "Snapshots", taken, path.name), body, days_older=1))
    return written


def provenance(dest: Path) -> None:
    lines = [
        "# Provenance",
        "",
        "Every photograph here is a Farm Security Administration / Office of War",
        "Information Kodachrome transparency of 1939-1942 from the Library of Congress",
        "Prints and Photographs Division. They are works of the United States",
        f"government with no known restrictions on publication: {RIGHTS}",
        "",
        "Each JPEG is the Library's 1024-pixel service file, pinned by the SHA-256",
        "below with its pixels untouched, plus one EXIF segment: make and model name",
        "the medium, the description is the Library's title, and the capture date is",
        "assigned by `scripts/make_test_library.py` -- the Library catalogs the month;",
        "the day and time are the fixture's, so the catalog's dates are known exactly.",
        "The DNGs under Raws/Digital hold the same frames as uncompressed linear raw.",
        f"Downloads come from {source_url('<id>')}.",
        "",
        "| file | photographed | catalogued | photographer | item | SHA-256 of the download | title |",
        "|---|---|---|---|---|---|---|",
    ]
    for _root, taken, digital_id, item, by, catalogued, sha256, title in PHOTOGRAPHS:
        lines.append(f"| {digital_id} | {taken} | {catalogued} | {by} "
                     f"| https://www.loc.gov/item/{item}/ | {sha256} | {title} |")
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

    print(f"fetching {len(PHOTOGRAPHS)} photographs from the Library of Congress...")
    try:
        written = build(dest)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    provenance(dest)

    total = sum(size for _, size in written)
    print(f"\nwrote {len(written)} photographs ({total / 1e6:.1f} MB) into {dest}")
    for root in ROOTS:
        count = sum(1 for path, _ in written if path.is_relative_to(dest / root))
        print(f"  {root:<14} {count}")
    print("\nrun the app against it with an isolated home, and attach the folder:")
    print("  AZIMUTH_HOME=<an empty folder> python web/desktop.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
