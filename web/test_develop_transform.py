"""Focused contracts for §28 Transform/Upright geometry."""

import numpy as np

from features.develop import transform


def test_identity_at_adobe_defaults_is_pixel_exact():
    image = np.arange(9 * 11 * 3, dtype=np.float32).reshape(9, 11, 3) / 255
    output = transform.apply_transform(image, {})
    np.testing.assert_allclose(output, image, atol=1e-6)
    np.testing.assert_allclose(transform.homography_from_settings({}), np.eye(3), atol=1e-6)


def test_known_angle_rotate_maps_screen_coordinates_clockwise():
    source = np.asarray([[[.75, .5]]], dtype=np.float32)
    mapped = transform.map_uv(source, transform.homography_from_settings({"PerspectiveRotate": 45}))
    np.testing.assert_allclose(mapped[0, 0], [.6767767, .6767767], atol=1e-6)


def test_horizon_hough_returns_opposite_rotate_for_sloping_line():
    image = np.zeros((96, 128, 3), dtype=np.float32)
    for x in range(8, 120):
        y = 36 + round(x * .16)
        image[max(0, y - 1):y + 2, x] = 1
    result = transform.auto_level_settings(image)
    assert result is not None
    assert result["PerspectiveUpright"] == "Auto"
    assert result["PerspectiveRotate"] < -4


def test_guided_upright_uses_two_or_four_lines_and_native_key():
    guides = [((.1, .2), (.9, .34)), ((.1, .6), (.9, .74))]
    result = transform.guided_upright(guides)
    assert result["PerspectiveUpright"] == "Guided"
    assert result["PerspectiveRotate"] < -5
