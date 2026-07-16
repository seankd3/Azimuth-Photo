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
    raw.white_level = 12000
    raw.black_level_per_channel = [0, 0, 0, 0]
    raw.color_matrix = None
    raw.rgb_xyz_matrix = None
    raw.postprocess.return_value = decoded

    monkeypatch.setattr(rawproc.rawpy, "imread", mock.Mock(return_value=raw))
    monkeypatch.setattr(rawproc, "_enrich_source_metadata", lambda meta, _path: meta)

    rgb, _meta = rawproc.decode_base(source)

    np.testing.assert_array_equal(rgb, np.full_like(decoded, 2000))
    assert raw.postprocess.call_args.kwargs["user_sat"] == 12000
    assert raw.postprocess.call_args.kwargs["adjust_maximum_thr"] == 0.0


def test_libraw_clip_levels_follow_white_range_and_wb_gain():
    clips = rawproc.derive_libraw_clip_levels(
        [12000, 11000, 10000, 11000],
        [2000, 1000, 1000, 1000],
        [2.0, 1.0, 1.5, 1.0],
        saturation_level=12000,
    )

    np.testing.assert_allclose(
        clips / 65535.0,
        [2.0, 10.0 / 11.0, 13.5 / 11.0],
        rtol=1e-6,
    )


def test_raw_cache_v5_moves_native_and_linear_dng_bases(tmp_path, monkeypatch):
    monkeypatch.setattr(rawproc, "BASE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(rawproc, "BASE_CACHE_DIR", tmp_path / "base" / "v3")

    assert rawproc.base_paths(1, "native.cr3").metadata.parent.name == "v5"
    assert rawproc.base_paths(2, "native.dng").metadata.parent.name == "v5"
    assert rawproc.base_paths(3, "lossy.dng").metadata.parent.name == "v5"
    assert rawproc.base_paths(4, "display.jpg").metadata.parent.name == "v3"
