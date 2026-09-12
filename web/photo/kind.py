"""What kind of file this is.

Nine modules used to answer "is this a RAW?" over six different extension sets.
Reading them showed the sets were not six copies of one answer — they were
three different questions, two of which had borrowed the wrong name:

* **Is it RAW data?** — a format question, and the only one the bytes can
  settle. :data:`RAW_FORMATS`, :func:`is_raw`.
* **Can Develop render it well?** — a capability question. Azimuth ships fitted
  colour for three formats and refuses the rest honestly rather than rendering
  them badly. :data:`DEVELOP_FITTED`, :func:`develop_is_fitted`.
* **Did a camera make it, or could a phone have?** — a provenance question that
  import uses to decide where a file is filed. Phones shoot DNG, so DNG cannot
  settle it and stays marker-driven. :data:`CAMERA_ONLY`.

A name is not a format. This archive holds 1,306 files named ``.CR2`` that are
really full-resolution JPEGs: they open fine in a viewer, LibRaw refuses them as
"not a raw file", and every branch chosen by extension alone gets them wrong.
The extension only says which files are worth asking about; the first three
bytes give the answer.
"""

from __future__ import annotations

import os

RAW_FORMATS = frozenset(
    {".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2"}
)

# Develop's colour pipeline is fitted for these. Widen it per vendor once
# acceptance coverage proves the whole chain, not before: scanner, catalog,
# thumbnail decode, Develop and XMP write-back all widen together.
DEVELOP_FITTED = frozenset({".dng", ".cr2", ".cr3"})

# RAW formats no phone produces. DNG is excluded deliberately — Pixel and others
# shoot it — so a DNG's provenance has to come from its source, not its name.
CAMERA_ONLY = RAW_FORMATS - {".dng"}

DISPLAY_FORMATS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})

_JPEG_MAGIC = b"\xff\xd8\xff"


def extension(path: str | os.PathLike[str]) -> str:
    """The lowercase suffix, the one way this codebase asks for it."""

    return os.path.splitext(os.fspath(path))[1].lower()


def is_raw(path: str | os.PathLike[str], *, data: bytes | None = None) -> bool:
    """Whether RAW decoding applies — by content, not by file name.

    ``data`` is the original's bytes when they are already in RAM, which makes
    this free. Otherwise it costs a 3-byte read of a file that is about to be
    read in full anyway.
    """

    if extension(path) not in RAW_FORMATS:
        return False
    if data is not None:
        head = data[:3]
    else:
        try:
            with open(path, "rb") as handle:
                head = handle.read(3)
        except OSError:
            head = b""  # unreadable: let the RAW path report the real error
    return not head.startswith(_JPEG_MAGIC)


def develop_is_fitted(path: str | os.PathLike[str]) -> bool:
    """Whether Develop has fitted colour for this format."""

    return extension(path) in DEVELOP_FITTED
