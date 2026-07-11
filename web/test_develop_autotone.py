"""Golden contracts for deterministic Auto tone settings."""

import numpy as np

from features.develop.autotone import auto_tone_settings, should_skip_batch_origin


def _scene(values):
    luma = np.asarray(values, dtype=np.float32).reshape(4, 4, 1)
    return np.repeat(luma, 3, axis=2)


def test_auto_tone_is_deterministic_and_does_not_mutate_input():
    base = _scene([.01, .02, .03, .04, .05, .06, .07, .08, .09, .10, .11, .12, .13, .14, .15, .16])
    original = base.copy()
    first = auto_tone_settings(base, {"Exposure2012": -1.0})
    second = auto_tone_settings(base, {"Exposure2012": 2.0})
    assert first == second
    np.testing.assert_array_equal(base, original)
    assert first == {
        "Exposure2012": 1.08,
        "Contrast2012": 39,
        "Highlights2012": 0,
        "Shadows2012": 0,
        "Whites2012": 100,
        "Blacks2012": -100,
    }


def test_auto_tone_moves_under_and_over_exposed_scenes_in_the_right_direction():
    under = _scene(np.linspace(.01, .08, 16))
    over = _scene(np.linspace(.5, 1.0, 16))
    under_settings = auto_tone_settings(under, {})
    over_settings = auto_tone_settings(over, {})
    assert under_settings["Exposure2012"] > 0
    assert over_settings["Exposure2012"] < 0
    assert under_settings == {
        "Exposure2012": 1.99,
        "Contrast2012": 41,
        "Highlights2012": 0,
        "Shadows2012": 0,
        "Whites2012": 100,
        "Blacks2012": -100,
    }
    assert over_settings == {
        "Exposure2012": -2.06,
        "Contrast2012": 49,
        "Highlights2012": 0,
        "Shadows2012": 0,
        "Whites2012": 100,
        "Blacks2012": -100,
    }


def test_highlight_protection_backs_exposure_off_before_clipping_mass_limit():
    luma = np.concatenate([np.full(95, .08), np.full(5, 1.0)]).reshape(10, 10, 1).astype(np.float32)
    base = np.repeat(luma, 3, axis=2)
    settings = auto_tone_settings(base, {})
    adjusted = luma[..., 0] * np.exp2(settings["Exposure2012"])
    assert np.mean(adjusted > .95) <= .005
    assert settings == {
        "Exposure2012": -0.08,
        "Contrast2012": 55,
        "Highlights2012": 0,
        "Shadows2012": 0,
        "Whites2012": -31,
        "Blacks2012": -100,
    }


def test_batch_skips_only_user_origin_unless_forced():
    assert should_skip_batch_origin("user")
    assert not should_skip_batch_origin("xmp")
    assert not should_skip_batch_origin(None)
    assert not should_skip_batch_origin("user", force=True)
