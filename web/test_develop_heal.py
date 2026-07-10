"""Focused proof for the §23 circular clone/heal renderer."""

from __future__ import annotations

import numpy as np

from features.develop.heal import RETOUCH_SETTINGS_KEY, apply_retouch_spots, import_adobe_retouch_info, spots_from_settings


def _spot(**changes):
    return {
        "src_x": .15, "src_y": .5, "dst_x": .75, "dst_y": .5,
        "radius": .14, "feather": 0, "opacity": 1, "mode": "clone",
        **changes,
    }


def test_clone_copies_source_disc_at_destination():
    image = np.zeros((101, 101, 3), dtype=np.float32)
    image[:, :40] = (.2, .4, .8)
    output = apply_retouch_spots(image, [_spot()])
    assert np.allclose(output[50, 75], image[50, 15], atol=1e-6)
    assert np.allclose(output[50, 50], image[50, 50], atol=1e-6)


def test_heal_matches_destination_ring_mean():
    image = np.full((101, 101, 3), (.8, .2, .1), dtype=np.float32)
    image[:, :40] = (.15, .35, .65)
    output = apply_retouch_spots(image, [_spot(mode="heal")])
    assert np.allclose(output[50, 75], (.8, .2, .1), atol=1e-4)


def test_feather_blends_at_disc_edge():
    image = np.zeros((101, 101, 3), dtype=np.float32)
    image[:, :40] = 1
    spot = _spot(radius=.2, feather=.5)
    output = apply_retouch_spots(image, [spot])
    assert np.allclose(output[50, 75], 1, atol=1e-6)
    assert 0 < output[50, 88, 0] < 1
    assert np.allclose(output[50, 96], 0, atol=1e-6)


def test_settings_and_json_shaped_adobe_import_are_validated():
    settings = {RETOUCH_SETTINGS_KEY: [_spot(), {"mode": "invalid"}]}
    assert len(spots_from_settings(settings)) == 1
    assert import_adobe_retouch_info({"RetouchInfo": '{"spots": [{"src_x": 0.2, "src_y": 0.2, "dst_x": 0.8, "dst_y": 0.8, "radius": 0.1, "mode": "clone"}]}'})[0]["mode"] == "clone"
