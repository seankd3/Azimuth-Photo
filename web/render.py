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
* **The camera's own JPEG is the tile, whenever the RAW carries one big
  enough.** Every RAW embeds the camera's rendering of itself -- Canon and Sony
  at full size, phones at about 1,000 px -- and it is what the photographer
  saw on the back of the camera. Measured on this machine: a 3840 loupe and
  its 1024 grid tile from that preview cost ~0.3 s together; demosaicing the
  same CR3 for one size costs 0.5-1.1 s, and a phone DNG 2.6-3.1 s. Lightroom's
  embedded-preview mode and Photo Mechanic are this exact choice. Develop
  renders its own pixels when there is an edit; a tile of an unedited
  photograph is the camera's.
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

from PIL import Image, ImageOps

from photo import kind

# Truncated JPEGs are the norm here, not the exception.
from PIL import ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# Pillow refuses very large images as a decompression-bomb guard. This archive
# legitimately contains them, and the real defence is the memory refusal below,
# which knows what the machine can actually pay.
Image.MAX_IMAGE_PIXELS = None

# Two stored sizes, derived from what a cell and a window need in device
# pixels rather than inherited from V1's three. A grid cell on a 4K-class
# display at its densest is under 1,000 px; a full-window loupe on the same
# display wants ~3,500, and 4096 is also exactly half of the 8192-wide
# previews the common full-frame bodies embed, so the loupe is a draft read
# and no resample at all. Anything beyond that is the original, decoded on
# demand: a 1:1 tier for 157,000 photographs is 1.9 TB, which is not a cache.
GRID = 1024
LOUPE = 4096
FULL = 0  # native resolution

# What the archive's existing tiles were encoded at. Kept so re-rendering a
# photo does not visibly change it. Baseline, not progressive-and-optimised:
# measured at 3840 px, that pair cost 165 ms against 32 ms and saved 5%.
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


def _draft(image: Image.Image, longest: int) -> None:
    """Ask the JPEG decoder for no more pixels than `longest` needs.

    `draft()` scales by powers of two and returns a frame at least as large as
    the box it is given, so the box has to carry the image's own shape: asking
    for a square of side 2x on a 3:2 frame forces the *short* side past it and
    costs a scale step -- measured, 186 ms instead of ~60 for a 400 px tile.
    Below 1920 the box is twice the target so the resample has pixels to work
    with; at and above it the target itself is enough, and the difference
    between 4096 and 8192 decoded pixels is 150 ms per photograph.
    """

    if not longest:
        return
    margin = 2 if longest < 1920 else 1
    width, height = image.size
    long_side = max(width, height, 1)
    image.draft("RGB", (max(1, margin * longest * width // long_side),
                        max(1, margin * longest * height // long_side)))


def _flip(image: Image.Image, flip: int) -> Image.Image:
    """LibRaw's flip, applied to pixels it did not orient itself."""

    turn = {3: Image.ROTATE_180, 5: Image.ROTATE_90, 6: Image.ROTATE_270}.get(int(flip))
    return image.transpose(turn) if turn else image


def _preview(raw, longest: int) -> Image.Image | None:
    """The camera's embedded JPEG, when it is big enough to be the answer.

    Big enough is the same 25% margin the half-size demosaic uses. When a
    longest side of 0 means native resolution, no preview qualifies; export and
    Develop read the sensor.
    """

    import io

    import rawpy

    try:
        thumb = raw.extract_thumb()
    except rawpy.LibRawError:
        return None
    if thumb.format != rawpy.ThumbFormat.JPEG:
        return None
    image = Image.open(io.BytesIO(thumb.data))
    if not longest or max(image.size) < int(longest * 0.75):
        image.close()
        return None
    _draft(image, longest)
    image.load()
    # The preview's own EXIF usually repeats the camera's orientation, but the
    # RAW's flip is the authority and applying both would turn a portrait
    # twice; so the preview's tag is ignored and the flip alone is honoured.
    return _flip(image, raw.sizes.flip)


def _decode_raw(path: str, longest: int) -> Image.Image:
    import rawpy

    with rawpy.imread(path) as raw:
        preview = _preview(raw, longest)
        if preview is not None:
            return preview
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
    _draft(image, longest)
    _affordable(image.width, image.height, longest)
    image.load()
    return ImageOps.exif_transpose(image) or image


def decode(path: str, longest: int = FULL) -> Image.Image:
    """The pixels of `path`, at least `longest` on the long side if it can be.

    Orientation is applied here and nowhere else. A photograph that arrives
    sideways in one surface and upright in another is the classic symptom of
    two decoders, which is the thing this function exists to prevent.
    """

    image = _decode_raw(path, longest) if kind.is_raw(path) else _decode_display(path, longest)
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
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def render(source: str, size: int = GRID, rotate: int = 0) -> bytes:
    """One photograph, at one size. JPEG bytes.

    The whole surface. Grid asks for GRID, loupe for LOUPE, export for FULL,
    and Develop asks for whichever it is showing — same code, same pixels.

    `rotate` is the owner's correction for a file that is filed sideways — a
    lab that scans a portrait frame into a landscape TIFF and writes no
    orientation tag, which no renderer can guess and every renderer therefore
    gets "wrong" in the same honest way. It is applied after decode and before
    the resize, so the tile is the right shape rather than a rotated crop.

    There was an `edits` argument here that applied a develop recipe, reached
    by `from develop import apply_edits`. No module named `develop` exists, no
    function named `apply_edits` exists anywhere in the tree, and the sole
    caller passed a literal `None` — so the branch was three ways dead and
    could only ever have raised. Rendering an edited tile is a real thing to
    want; when it comes back it comes back as a `tile` recipe that names the
    edit, so an edited photograph's tile is a different cached answer rather
    than the same one rendered differently.
    """

    return encode(pixels(source, size, rotate))


def pixels(source: str, size: int = GRID, rotate: int = 0) -> Image.Image:
    """The oriented, turned, fitted pixels `render` encodes.

    Exposed so the tile store can pay one decode for two sizes: the loupe is
    cut from these pixels and the grid tile from the loupe's, and the original
    -- on an archive drive, the expensive read -- is opened exactly once.
    """

    image = decode(source, size)
    if rotate % 360:
        # PIL rotates counter-clockwise; the owner means clockwise.
        image = image.rotate(-int(rotate) % 360, expand=True)
    return fit(image, size)


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
        width, height = int(image.width), int(image.height)
        orientation = int(image.getexif().get(0x0112, 1) or 1)
        return (height, width) if orientation in (5, 6, 7, 8) else (width, height)
