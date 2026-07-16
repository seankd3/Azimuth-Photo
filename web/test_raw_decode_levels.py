from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np

from features.develop import lossydng, rawproc


class _TagPage:
    def __init__(self, **tags):
        self.tags = {name: SimpleNamespace(value=value) for name, value in tags.items()}


def test_dng_tag_lookup_falls_back_to_full_resolution_level():
    target = _TagPage(OpcodeList2=b"reduced")
    full = _TagPage(WhiteLevel=(255, 255, 255))
    ifd0 = _TagPage(BaselineExposure=(61, 100))

    assert lossydng._tag("WhiteLevel", target, full, ifd0) == (255, 255, 255)
    assert lossydng._tag("BaselineExposure", target, full, ifd0) == (61, 100)
    assert lossydng._tag("Missing", target, full, ifd0, default=7) == 7


def test_libraw_decode_restores_common_camera_wb_gain(tmp_path, monkeypatch):
    source = tmp_path / "sample.cr3"
    source.touch()
    decoded = np.full((2, 3, 3), 1000, dtype=np.uint16)

    raw = mock.MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.camera_whitebalance = [2000.0, 1000.0, 1500.0, 1000.0]
    raw.daylight_whitebalance = [1.8, 1.0, 1.4, 0.0]
    raw.camera_white_level_per_channel = [12000, 12000, 12000, 12000]
    raw.color_matrix = None
    raw.rgb_xyz_matrix = None
    raw.postprocess.return_value = decoded

    monkeypatch.setattr(rawproc.rawpy, "imread", mock.Mock(return_value=raw))
    monkeypatch.setattr(rawproc, "_enrich_source_metadata", lambda meta, _path: meta)

    rgb, _meta = rawproc.decode_base(source)

    np.testing.assert_array_equal(rgb, np.full_like(decoded, 2000))
    assert raw.postprocess.call_args.kwargs["user_sat"] == 12000
    assert raw.postprocess.call_args.kwargs["adjust_maximum_thr"] == 0.0


def test_libraw_common_gain_scaling_preserves_rounding_and_saturation():
    decoded = np.array(
        [0, 1, 2, 10_000, 31_375, 31_376, 65_534, 65_535],
        dtype=np.uint16,
    )
    scale = np.float32(2.08984375)
    expected = np.asarray(
        np.clip(np.rint(np.asarray(decoded, dtype=np.float32) * scale), 0, 65_535),
        dtype=np.uint16,
    )

    np.testing.assert_array_equal(rawproc._scale_linear_uint16(decoded, scale), expected)


def test_native_cache_v4_does_not_move_display_or_jxl_bases(tmp_path, monkeypatch):
    monkeypatch.setattr(rawproc, "BASE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(rawproc, "BASE_CACHE_DIR", tmp_path / "base" / "v3")

    monkeypatch.setattr(lossydng, "is_lossy_dng", lambda path: str(path).endswith("lossy.dng"))

    assert rawproc.base_paths(1, "native.cr3").metadata.parent.name == "v4"
    assert rawproc.base_paths(2, "native.dng").metadata.parent.name == "v4"
    assert rawproc.base_paths(3, "lossy.dng").metadata.parent.name == "v3"
    assert rawproc.base_paths(4, "display.jpg").metadata.parent.name == "v3"
