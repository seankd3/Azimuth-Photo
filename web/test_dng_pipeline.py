"""Parity goldens for the Adobe DNG profile stages in §30.2."""

from __future__ import annotations

import hashlib

import numpy as np

from features.develop import dng_pipeline as dng


IDENTITY_PROFILE = {
    "forward_matrix1": np.eye(3).tolist(),
    "calibration_illuminant1": 17,
    "calibration_illuminant2": 21,
}


def _identity_table(dims=(2, 2, 1)):
    hue, saturation, value = dims
    data = np.tile(np.array([0.0, 1.0, 1.0], dtype=np.float32), hue * saturation * value)
    return {"dims": list(dims), "data": data.tolist()}


def test_acr3_default_curve_is_exact_sdk_table_golden():
    curve = dng.ADOBE_ACR3_DEFAULT_TONE_CURVE
    assert curve.shape == (1025,)
    assert curve.dtype == np.dtype("float32")
    assert not curve.flags.writeable
    np.testing.assert_allclose(
        curve[[0, 1, 64, 128, 512, 1023, 1024]],
        [0.0, 0.00078, 0.10433, 0.25961, 0.80486, 0.99987, 1.0],
        atol=1e-8,
    )
    assert hashlib.sha256(curve.astype("<f4", copy=False).tobytes()).hexdigest() == "1ae4726011b5bf18a806c1c029a68fe3c56c30b0c9f289d95f23073a86c6fa27"


def test_baseline_exposure_precedence_and_exact_exp2():
    source = np.array([[[0.125, 0.25, 0.5]]], dtype=np.float32)
    profile = {"baseline_exposure": 1.25}
    np.testing.assert_allclose(dng.apply_baseline_exposure(source, profile), source * np.exp2(1.25), rtol=1e-7)
    np.testing.assert_allclose(
        dng.apply_baseline_exposure(source, profile, file_baseline_exposure=-1.0),
        source * 0.5,
        rtol=1e-7,
    )


def test_dual_illuminant_weight_uses_reciprocal_temperature():
    assert dng.dual_illuminant_weight(2856.0, 17, 21) == 1.0
    assert dng.dual_illuminant_weight(6504.0, 17, 21) == 0.0
    middle_mired = 2.0 / ((1.0 / 2856.0) + (1.0 / 6504.0))
    assert abs(dng.dual_illuminant_weight(middle_mired, 17, 21) - 0.5) < 1e-12
    assert abs(dng.dual_illuminant_weight(middle_mired, 21, 17) - 0.5) < 1e-12


def test_forward_matrix_interpolation_matches_map_weight():
    profile = {
        "forward_matrix1": np.eye(3).tolist(),
        "forward_matrix2": (np.eye(3) * 3.0).tolist(),
        "calibration_illuminant1": 17,
        "calibration_illuminant2": 21,
    }
    middle_mired = 2.0 / ((1.0 / 2856.0) + (1.0 / 6504.0))
    np.testing.assert_allclose(dng.interpolated_forward_matrix(profile, middle_mired), np.eye(3) * 2.0)


def test_hsv_identity_is_exact_for_2d_and_3d_tables():
    source = np.array([[[0.8, 0.3, 0.1], [0.1, 0.4, 0.7], [0.25, 0.25, 0.25]]], dtype=np.float32)
    for dims in ((6, 4, 1), (6, 4, 3)):
        result = dng.apply_hsv_delta_table(source, _identity_table(dims), cct=5000, profile=IDENTITY_PROFILE)
        np.testing.assert_allclose(result, source, atol=2e-7)


def test_hsv_table_trilinear_golden_and_hue_wrap():
    dims = (2, 2, 2)
    data = []
    # File order: value, hue, saturation. Encode a value scale that is affine
    # in every axis, so the expected trilinear result is analytic.
    for value in range(2):
        for hue in range(2):
            for saturation in range(2):
                data.extend([30.0 * hue, 1.0, 0.5 + 0.1 * hue + 0.2 * saturation + 0.2 * value])
    table = {"dims": list(dims), "data": data}
    source = np.array([[[0.4, 0.2, 0.2]]], dtype=np.float32)  # HSV6=(0, .5, .4)
    result = dng.apply_hsv_delta_table(source, table, cct=5000, profile=IDENTITY_PROFILE)
    # h=0 samples hue cell 0, s=.5, v=.4: valScale=.5 + .1 + .08=.68.
    np.testing.assert_allclose(result, [[[0.272, 0.136, 0.136]]], atol=2e-6)

    near_wrap = np.array([[[1.0, 0.0, 0.001]]], dtype=np.float32)
    identity = dng.apply_hsv_delta_table(near_wrap, _identity_table((6, 2, 1)), cct=5000, profile=IDENTITY_PROFILE)
    np.testing.assert_allclose(identity, near_wrap, atol=2e-6)


def test_dual_hue_sat_tables_interpolate_before_application():
    first = _identity_table((2, 2, 1))["data"]
    second = np.asarray(first, dtype=np.float32).reshape(-1, 3)
    second[:, 2] = 0.5
    table = {"dims": [2, 2, 1], "data1": first, "data2": second.reshape(-1).tolist()}
    middle_mired = 2.0 / ((1.0 / 2856.0) + (1.0 / 6504.0))
    source = np.array([[[0.8, 0.4, 0.2]]], dtype=np.float32)
    result = dng.apply_hsv_delta_table(source, table, cct=middle_mired, profile=IDENTITY_PROFILE)
    np.testing.assert_allclose(result, source * 0.75, atol=2e-6)


def test_rgb_ratio_tone_matches_sdk_reference_golden():
    square = np.linspace(0.0, 1.0, 1025, dtype=np.float32) ** 2
    source = np.array([[[0.8, 0.5, 0.2], [0.2, 0.2, 0.2], [0.1, 0.7, 0.4]]], dtype=np.float32)
    result = dng.apply_rgb_ratio_tone(source, square)
    expected = np.array([[[0.64, 0.34, 0.04], [0.04, 0.04, 0.04], [0.01, 0.49, 0.25]]], dtype=np.float32)
    np.testing.assert_allclose(result, expected, atol=2e-6)
    assert abs((result[0, 0, 1] - result[0, 0, 2]) / (result[0, 0, 0] - result[0, 0, 2]) - 0.5) < 1e-6


def test_profile_tone_points_override_default_curve():
    identity = dng.tone_curve_lut([[0.0, 0.0], [1.0, 1.0]])
    np.testing.assert_allclose(identity, np.linspace(0.0, 1.0, 1025), atol=1e-7)
    assert not np.array_equal(identity, dng.ADOBE_ACR3_DEFAULT_TONE_CURVE)


def test_profile_tone_curve_uses_sdk_natural_cubic_not_linear_segments():
    curve = dng.tone_curve_lut([[0.0, 0.0], [0.25, 0.1], [1.0, 1.0]])
    # Golden from dng_spline_solver: C2-continuous with zero second derivative
    # at both endpoints. A linear interpolation would be exactly 0.05 here.
    assert abs(float(curve[128]) - 0.040625) < 1e-7
    assert abs(float(curve[256]) - 0.1) < 1e-7


def test_user_ops_interleave_after_look_before_tone():
    profile = {**IDENTITY_PROFILE, "hue_sat_map": _identity_table(), "look_table": _identity_table(), "tone_curve": [[0, 0], [1, 1]]}
    source = np.full((1, 1, 3), 0.1, dtype=np.float32)
    seen = []

    def user_ops(value):
        seen.append(value.copy())
        return value * 2.0

    result = dng.apply_adobe_style(source, profile, cct=5000, user_ops=user_ops)
    np.testing.assert_allclose(seen[0], source, atol=1e-7)
    expected = dng.prophoto_to_display_srgb(source * 2.0)
    np.testing.assert_allclose(result, expected, atol=2e-6)


def test_scene_linear_tap_is_immediately_after_baseline_exposure():
    source = np.array([[[0.1, 0.2, 0.3]]], dtype=np.float32)
    scene = dng.prepare_scene_linear(source, {"baseline_exposure": 1.0}, cct=5000, input_space="prophoto")
    np.testing.assert_allclose(scene, source * 2.0, atol=1e-7)


def test_resolver_clean_seam_accepts_embedded_profile():
    profile = {"profile_name": "Adobe Standard"}
    assert dng.resolve_adobe_profile({"adobe_profile": profile}) == profile
    assert dng.resolve_adobe_profile(None) is None


def test_dngprof_grouped_loader_shape_normalizes_for_rendering():
    grouped = {
        "matrices": {"forward_matrix1": np.eye(3).tolist()},
        "illuminants": {"calibration_illuminant1": 17, "calibration_illuminant2": 21},
    }
    normalized = dng.normalize_adobe_profile(grouped)
    np.testing.assert_array_equal(normalized["forward_matrix1"], np.eye(3))
    assert normalized["calibration_illuminant1"] == 17
