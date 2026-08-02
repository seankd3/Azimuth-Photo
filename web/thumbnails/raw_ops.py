"""Spawn-safe RAW demosaic + thumbnail JPEG ops.

Kept outside the ``thumbnails`` package so ProcessPoolExecutor workers can
import this module without running ``thumbnails.__init__`` (thread pools,
app state). Settings must stay identical to the library-thumb path in
``thumbnails.generation`` / historical dngthumbs (LINEAR + half_size).
"""

from __future__ import annotations

import io
import json
import os
import subprocess

from PIL import Image

from core import pil_limits  # noqa: F401  # process-wide Pillow policy


def apply_raw_orientation(img: Image.Image, flip: int) -> Image.Image:
    """Apply libraw's container orientation to an untagged RAW image."""
    transforms = {
        1: Image.Transpose.FLIP_LEFT_RIGHT,
        2: Image.Transpose.FLIP_TOP_BOTTOM,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.TRANSPOSE,
        5: Image.Transpose.ROTATE_90,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
    }
    transform = transforms.get(int(flip or 0))
    return img.transpose(transform) if transform is not None else img


def exiftool_raw_flip(filepath: str) -> int:
    """Map the container Orientation tag to libraw's flip values when needed."""
    try:
        result = subprocess.run(
            ["exiftool", "-n", "-j", "-Orientation", filepath],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        orientation = int((json.loads(result.stdout) or [{}])[0].get("Orientation", 1))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, IndexError):
        return 0
    return {2: 1, 3: 3, 4: 2, 5: 4, 6: 6, 7: 7, 8: 5}.get(orientation, 0)


def _raw_open_target(filepath: str, source_data: bytes | None = None):
    """Path or BytesIO for rawpy.imread — caller must keep BytesIO alive."""
    if source_data is not None:
        return io.BytesIO(source_data)
    return filepath


def demosaic_raw_for_thumbnail(
    filepath: str,
    max_target: int,
    *,
    source_data: bytes | None = None,
) -> Image.Image:
    """Fast LibRaw (or lossy-DNG) demosaic for library thumbs — not Develop.

    One demosaic per source. Prefers half_size whenever the half frame is
    within ~25% of the target (mild upscale beats a multi-second full
    demosaic for grid/loupe previews).
    """
    import rawpy

    try:
        target = _raw_open_target(filepath, source_data)
        with rawpy.imread(target) as raw:
            sizes = raw.sizes
            long_side = max(sizes.width, sizes.height)
            half_size = (long_side // 2) >= int(max_target * 0.75)
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                half_size=half_size,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            )
        img = Image.fromarray(rgb)
        del rgb
        return img
    except Exception:
        from features.develop.lossydng import decode_lossy_dng, is_lossy_dng

        lossy_path = filepath
        tmp_path = None
        try:
            if source_data is not None:
                import tempfile

                tmp = tempfile.NamedTemporaryFile(
                    suffix=os.path.splitext(filepath)[1] or ".dng",
                    delete=False,
                )
                try:
                    tmp.write(source_data)
                    tmp.flush()
                finally:
                    tmp.close()
                tmp_path = tmp.name
                lossy_path = tmp_path
            if not is_lossy_dng(lossy_path):
                raise
            import numpy as _np

            arr, _meta = decode_lossy_dng(lossy_path, max_px=max(max_target, 512))
            linear = arr.astype(_np.float32) / 65535.0
            del arr
            encoded = _np.where(
                linear <= 0.0031308,
                linear * 12.92,
                1.055 * _np.power(_np.clip(linear, 0.0, 1.0), 1.0 / 2.4) - 0.055,
            )
            del linear
            image = Image.fromarray((_np.clip(encoded, 0.0, 1.0) * 255.0 + 0.5).astype(_np.uint8))
            del encoded
            return apply_raw_orientation(image, exiftool_raw_flip(lossy_path))
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


def resize_to_long_side(img: Image.Image, target_long_side: int) -> Image.Image:
    long_side = max(img.width, img.height)
    if long_side <= target_long_side:
        return img.copy()

    factor = max(1, long_side // (target_long_side * 2))
    if factor > 1:
        img = img.reduce(factor)
        long_side = max(img.width, img.height)

    scale = target_long_side / long_side
    new_size = (
        max(1, int(round(img.width * scale))),
        max(1, int(round(img.height * scale))),
    )
    resample = Image.BILINEAR if target_long_side >= 1920 else Image.LANCZOS
    return img.resize(new_size, resample)


def resize_to_long_side_exact(img: Image.Image, target_long_side: int) -> Image.Image:
    """Like ``resize_to_long_side`` but upscales when the source is smaller."""
    long_side = max(img.width, img.height)
    if long_side == target_long_side:
        return img.copy()
    if long_side > target_long_side:
        return resize_to_long_side(img, target_long_side)
    scale = target_long_side / long_side
    new_size = (
        max(1, int(round(img.width * scale))),
        max(1, int(round(img.height * scale))),
    )
    return img.resize(new_size, Image.BILINEAR)


def thumbnail_jpeg_bytes(variant: Image.Image, size: str, quality: int) -> bytes:
    buf = io.BytesIO()
    variant.save(
        buf,
        "JPEG",
        quality=quality,
        progressive=(size != "sm"),
    )
    return buf.getvalue()


def demosaic_tier_jpegs(
    filepath: str,
    uncovered_sizes: list[str],
    sizes: dict[str, int],
    thumb_quality: int,
    source_data: bytes | None = None,
) -> dict[str, object]:
    """Demosaic once, resize+JPEG-encode uncovered tiers. Spawn-pool entrypoint.

    Returns ``{"jpegs": {size: bytes}, "width": int, "height": int}``.
    Only JPEG bytes cross the process boundary — never the decoded frame.
    """
    if not uncovered_sizes:
        return {"jpegs": {}, "width": 0, "height": 0}

    max_target = max(int(sizes[size]) for size in uncovered_sizes)
    img = demosaic_raw_for_thumbnail(filepath, max_target, source_data=source_data)
    jpegs: dict[str, bytes] = {}
    current = img
    try:
        ordered = sorted(uncovered_sizes, key=lambda tier: sizes[tier], reverse=True)
        for size in ordered:
            variant = resize_to_long_side_exact(current, int(sizes[size]))
            if variant.mode != "RGB":
                converted = variant.convert("RGB")
                if variant is not current and variant is not img:
                    variant.close()
                variant = converted
            jpegs[size] = thumbnail_jpeg_bytes(variant, size, int(thumb_quality))
            if current is not img:
                current.close()
            current = variant
        return {
            "jpegs": jpegs,
            "width": int(img.width),
            "height": int(img.height),
        }
    finally:
        if current is not None and current is not img:
            try:
                current.close()
            except Exception:
                pass
        try:
            img.close()
        except Exception:
            pass
