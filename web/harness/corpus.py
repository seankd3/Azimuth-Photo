"""The files the pipeline is measured against.

Small, synthetic and deterministic, so the harness runs anywhere. Two of them
are liars on purpose: a JPEG named ``.CR2`` and a JPEG named ``.ARW``. The live
archive holds 1,306 of the first kind, and every rule that decides RAW-ness by
file name gets them wrong.

This module is the one home for writing test images. ``qa/seed_worker.py``
imports from here rather than keeping a second copy.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from tifffile import imwrite

from harness.env import CORPUS, REAL_CORPUS

REAL_SUFFIXES = {".cr2", ".cr3", ".dng", ".arw", ".nef", ".orf", ".raf", ".rw2", ".jpg"}


def write_jpeg(path: Path, index: int, *, size: tuple[int, int] = (160, 106)) -> Path:
    """A small valid image with enough texture to exercise Develop."""

    path.parent.mkdir(parents=True, exist_ok=True)
    base = ((index * 41) % 210 + 20, (index * 73) % 210 + 20, (index * 97) % 210 + 20)
    image = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(image)
    for step in range(0, size[0], 16):
        shade = ((base[0] + step) % 255, (base[1] + step * 2) % 255, (base[2] + step * 3) % 255)
        draw.rectangle((step, 0, min(step + 7, size[0]), size[1]), fill=shade)
    draw.ellipse((36, 20, 124, 88), outline=(245, 245, 245), width=4)
    image.save(path, "JPEG", quality=88)
    return path


def write_bayer_dng(path: Path, index: int, *, size: tuple[int, int] = (160, 108)) -> Path:
    """A tiny standards-readable Bayer DNG that LibRaw will actually demosaic."""

    width, height = size
    y, x = np.mgrid[:height, :width]
    mosaic = ((x / width * 0.7 + y / height * 0.3) * 12_000 + 512).astype(np.uint16)
    mosaic += (((x // 8 + y // 8 + index) % 2) * 1_800).astype(np.uint16)
    path.parent.mkdir(parents=True, exist_ok=True)
    imwrite(
        path,
        mosaic,
        photometric=32803,
        metadata=None,
        extratags=[
            (50706, "B", 4, (1, 4, 0, 0), False),
            (50707, "B", 4, (1, 3, 0, 0), False),
            (50708, "s", 0, "Azimuth QA Camera", False),
            (33421, "H", 2, (2, 2), False),
            (33422, "B", 4, (0, 1, 1, 2), False),
            (50713, "H", 2, (1, 1), False),
            (50714, "I", 1, 512, False),
            (50717, "I", 1, 16_383, False),
            (50718, "2I", 2, ((1, 1), (1, 1)), False),
            (50719, "I", 2, (0, 0), False),
            (50720, "I", 2, (width, height), False),
            (50721, "2i", 9, tuple((value, 10_000) for value in (10_000, 0, 0, 0, 10_000, 0, 0, 0, 10_000)), False),
            (50728, "2I", 3, ((1, 2), (1, 1), (2, 3)), False),
            (50778, "H", 1, 21, False),
        ],
    )
    return path


def build() -> list[Path]:
    """Write the corpus and return it in a stable order."""

    files = [
        write_jpeg(CORPUS / "plain.jpg", 1),
        write_bayer_dng(CORPUS / "bayer.dng", 2),
        write_bayer_dng(CORPUS / "upper.DNG", 3),
        # Liars: real JPEG bytes under a RAW file name. LibRaw refuses them.
        write_jpeg(CORPUS / "liar.CR2", 4),
        write_jpeg(CORPUS / "liar.ARW", 5),
    ]
    return files + real_files()


def real_files() -> list[Path]:
    """Camera files from AZIMUTH_HARNESS_REAL_CORPUS, if the operator set it.

    Read-only. These are the operator's own photos; the harness never writes to
    this directory and never moves anything out of it.
    """

    if not REAL_CORPUS:
        return []
    root = Path(REAL_CORPUS)
    if not root.is_dir():
        return []
    found = [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in REAL_SUFFIXES]
    return found[:12]
