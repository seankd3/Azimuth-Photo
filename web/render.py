"""Pixels. One decode, one resize, one encode — for everybody.

> A tile is the cached answer to *what does this photograph look like, at this
> size, with these edits*.

Grid, loupe, Develop and export all call `render()`. That is not tidiness, it
is the only way they cannot disagree: three pipelines produced three slightly
different images from one file, and every "why does the export look different
from the loupe" bug lived in the gap between them.

What used to be here was 9,157 lines across 23 modules — tiers, schedulers,
budgets, presence tables, memory stores, disk stores, harvesters, pregen
workers, job probes. Almost all of it was answering *when* to make a thumbnail
and *where to put it*, and both questions now belong to `work.owed` and
`model.cache`. What is left is the part that was always the actual work.

Everything below that looks like a magic number was paid for once:

* **RAW-ness is decided by the first three bytes.** This archive holds 1,306
  files named `.CR2` that are full-resolution JPEGs. LibRaw refuses them and
  every branch chosen by extension gets them wrong.
* **2,480 of 44,521 JPEGs end without an EOI marker.** Pillow refuses them all
  unless told not to. The picture is entirely there.
* **`draft()` before `load()`.** The archive holds frames up to 527 MP — 1.5 GB
  of RGB each. Full-decoding one for a 400 px tile is how the old server
  reached 6.6 GB resident.
* **Demosaic at half size** whenever the half frame is still within 25% of the
  target. A mild upscale is invisible in a grid tile and saves seconds.
* **A frame too big to afford is refused, never clamped.** Clamping an
  oversized weight to the ceiling is how one 4.2 GB panorama was charged 768 MB
  and killed the service four times in an hour.

Two things checked against the real archive rather than assumed, because both
would have been silent:

* **Orientation is applied exactly once.** `rawpy.postprocess` already honours
  the camera's flip, and `exif_transpose` is a no-op on an image built from an
  array, so the two do not compound. Verified on a `flip=5` frame: an 8191×5463
  sensor decodes to 2732×4096 portrait. Applying both would double-rotate every
  portrait RAW; applying neither would leave whole camera bodies sideways.
* **Some files genuinely will not decode, and that is an answer.** Measured
  over 119 TIFFs, two refuse — a 32-bit-float astronomy stack and one export —
  with Pillow's *unknown pixel mode*. They record as `failed` with the reason,
  and the second request costs 0.01 ms instead of 74 ms because nothing tries
  again. That is the `unreadable` word, and writing a decoder for 1.7% of the
  TIFFs is not the smaller codebase.
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageOps

from model import cache
from photo import kind

# Truncated JPEGs are the norm here, not the exception.
from PIL import ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# Pillow refuses very large images as a decompression-bomb guard. This archive
# legitimately contains them, and the real defence is the memory refusal below,
# which knows what the machine can actually pay.
Image.MAX_IMAGE_PIXELS = None

# The sizes, which are the sizes the UI already asks for. Not tiers with
# policies, budgets and allocation profiles -- just how long the long side is.
SIZES = {"sm": 400, "md": 1920, "lg": 3840, "full": 0}
GRID = SIZES["sm"]
LOUPE = SIZES["md"]
FULL = SIZES["full"]  # native resolution

# What the archive's existing tiles were encoded at. Kept so re-rendering a
# photo does not visibly change it.
QUALITY = 92

# What one decode may cost, in bytes of RGB. A frame whose full decode would
# exceed this is refused rather than attempted; 527 MP is 1.5 GB before rawpy's
# working copies, and the measured peak of a demosaic worker was 9 GB.
DECODE_CEILING_BYTES = 1_500_000_000


class TooBig(Exception):
    """This machine will not pay for this frame at this size."""


def _affordable(width: int, height: int, longest: int) -> None:
    """Refuse a frame we cannot decode, rather than pretending it is smaller.

    The distinction that matters: `draft`/`half_size` genuinely reduce what is
    decoded, so a big frame at a small target is cheap and allowed. What is
    refused is a *full* decode that would not fit — and it is refused outright,
    because a budget that clamps an oversized weight to its own ceiling has
    admitted a frame at a price the machine cannot pay.
    """

    pixels = int(width) * int(height)
    if longest:
        scale = min(1.0, (longest * longest) / max(pixels, 1))
        pixels = int(pixels * max(scale, 0.0625))  # draft/half_size floor
    if pixels * 3 > DECODE_CEILING_BYTES:
        raise TooBig(f"{width}x{height} at longest={longest or 'native'} exceeds the decode ceiling")


def _decode_raw(path: str, longest: int) -> Image.Image:
    import rawpy

    with rawpy.imread(path) as raw:
        sizes = raw.sizes
        long_side = max(sizes.width, sizes.height)
        # Half the sensor is plenty whenever it still lands near the target.
        half = bool(longest) and (long_side // 2) >= int(longest * 0.75)
        _affordable(sizes.width, sizes.height, longest if half else 0)
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            half_size=half,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
        )
    try:
        return Image.fromarray(rgb)
    finally:
        del rgb


def _decode_display(path: str, longest: int) -> Image.Image:
    """A JPEG/PNG/TIFF, decoded no larger than it needs to be.

    `draft()` asks the JPEG decoder for a DCT-scaled read — a 1/8 decode is
    eight times less work and eight times less memory, and it happens before
    any pixel is allocated. It is a no-op on formats that cannot do it, which
    is why it is safe to ask unconditionally.
    """

    image = Image.open(path)
    if longest:
        # Twice the target, not the target: `draft` only scales by powers of
        # two, so asking for exactly the target can land a 1/8 read just under
        # it and the upscale back is visibly soft. 2x always leaves a frame at
        # least as large as what is wanted.
        image.draft("RGB", (longest * 2, longest * 2))
    _affordable(image.width, image.height, longest)
    image.load()
    return image


def decode(path: str, longest: int = FULL) -> Image.Image:
    """The pixels of `path`, at least `longest` on the long side if it can be.

    Orientation is applied here and nowhere else. A photograph that arrives
    sideways in one surface and upright in another is the classic symptom of
    two decoders, which is the thing this function exists to prevent.
    """

    image = _decode_raw(path, longest) if kind.is_raw(path) else _decode_display(path, longest)
    image = ImageOps.exif_transpose(image) or image
    return image.convert("RGB") if image.mode not in ("RGB", "L") else image


def fit(image: Image.Image, longest: int) -> Image.Image:
    """Down to `longest` on the long side, cheaply and without softness.

    `reduce()` first: an integer-factor box reduction is far faster than a
    resample over millions of pixels, and doing it in two stages avoids the
    aliasing a single huge downscale produces. Above 1920 the difference
    between BILINEAR and LANCZOS is invisible and the cost is not.
    """

    if not longest:
        return image
    long_side = max(image.width, image.height)
    if long_side <= longest:
        return image

    factor = max(1, long_side // (longest * 2))
    if factor > 1:
        image = image.reduce(factor)
        long_side = max(image.width, image.height)

    scale = longest / long_side
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.BILINEAR if longest >= 1920 else Image.LANCZOS)


def encode(image: Image.Image, quality: int = QUALITY) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def render(source: str, size: int = GRID, edits: dict | None = None,
           rotate: int = 0) -> bytes:
    """One photograph, at one size, with one set of edits. JPEG bytes.

    The whole surface. Grid asks for GRID, loupe for LOUPE, export for FULL,
    and Develop asks for whichever it is showing — same code, same pixels.

    `rotate` is the owner's correction for a file that is filed sideways — a
    lab that scans a portrait frame into a landscape TIFF and writes no
    orientation tag, which no renderer can guess and every renderer therefore
    gets "wrong" in the same honest way. It is applied after decode and before
    the resize, so the tile is the right shape rather than a rotated crop.
    """

    image = decode(source, size)
    if rotate % 360:
        # PIL rotates counter-clockwise; the owner means clockwise.
        image = image.rotate(-int(rotate) % 360, expand=True)
    if edits:
        from develop import apply_edits  # imported late: the grid never needs it

        image = apply_edits(image, edits)
    return encode(fit(image, size))


def dimensions(path: str) -> tuple[int, int]:
    """The shape the photograph is *shown* at. A header read, not a picture.

    Shown, not stored, because every caller wants the former: the grid sizes
    each cell from these numbers, and a cell that disagrees with its tile is
    the letterboxed-portrait bug. Keeping the sensor's shape here would mean
    each caller had to re-derive the turn, which is how two decoders start.

    A raw's flip is the camera's own, already honoured by `postprocess`, so it
    has to be honoured here too or the two disagree. Measured across this
    archive: `flip=5` (8224x5490 sensor) and `flip=6` (8191x5463) both decode
    portrait, `flip=0` decodes landscape, and `flip=3` is a half turn that
    swaps nothing.
    """

    if kind.is_raw(path):
        import rawpy

        with rawpy.imread(path) as raw:
            width, height = int(raw.sizes.width), int(raw.sizes.height)
            return (height, width) if raw.sizes.flip in (5, 6) else (width, height)
    with Image.open(path) as image:
        return int(image.width), int(image.height)
