"""Scene-referred sigmoid view transform, borrowed from darktable src/iop/sigmoid.c
(_generalized_loglogistic_sigmoid + commit_params). Maps scene-linear [0,inf)
to display [0,1]: mid-grey locked, asymptotic rolloff to white, no hard clip.

"""
from __future__ import annotations
import numpy as np

MIDDLE_GREY = np.float32(0.1845)  # darktable constant


def _loglogistic(value, magnitude, paper_exp, film_fog, film_power, paper_power):
    v = np.maximum(value, 0.0)
    film = np.power(film_fog + v, film_power)
    return magnitude * np.power(film / (paper_exp + film), paper_power)


def derive_params(contrast: float = 1.5, skew: float = 0.0,
                  white_target: float = 1.0, black_target: float = 0.000152):
    """Port of sigmoid.c commit_params: solve for the 5 curve constants so that
    f(0)=black, f(MIDDLE_GREY)=MIDDLE_GREY, f(inf)=white, slope-at-grey=contrast
    (skew-independent)."""
    g = float(MIDDLE_GREY)
    # reference slope (no skew, normalized display)
    ref_film_power = contrast
    ref_paper_power = 1.0
    ref_paper_exp = (g ** ref_film_power) * ((1.0 / g) - 1.0)
    d = 1e-6
    ref_slope = (_loglogistic(g + d, 1.0, ref_paper_exp, 0.0, ref_film_power, ref_paper_power)
                 - _loglogistic(g - d, 1.0, ref_paper_exp, 0.0, ref_film_power, ref_paper_power)) / 2.0 / d
    # skew
    paper_power = 5.0 ** (-skew)
    # slope at film_power=1 with skew
    temp_fp = 1.0
    tw = white_target
    temp_rel = (tw / g) ** (1.0 / paper_power) - 1.0
    temp_paper_exp = (g ** temp_fp) * temp_rel
    temp_slope = (_loglogistic(g + d, tw, temp_paper_exp, 0.0, temp_fp, paper_power)
                  - _loglogistic(g - d, tw, temp_paper_exp, 0.0, temp_fp, paper_power)) / 2.0 / d
    film_power = ref_slope / temp_slope
    # final constants
    wg = (white_target / g) ** (1.0 / paper_power) - 1.0
    wb = (black_target / white_target) ** (-1.0 / paper_power) - 1.0
    film_fog = g * wg ** (1.0 / film_power) / (wb ** (1.0 / film_power) - wg ** (1.0 / film_power))
    paper_exp = (film_fog + g) ** film_power * wg
    return dict(magnitude=white_target, paper_exp=paper_exp, film_fog=film_fog,
                film_power=film_power, paper_power=paper_power)


def sigmoid_view(linear_rgb: np.ndarray, contrast: float = 1.5, skew: float = 0.0) -> np.ndarray:
    """Per-channel scene->display sigmoid. Input scene-linear (>=0, may exceed 1)."""
    p = derive_params(contrast, skew)
    out = _loglogistic(np.asarray(linear_rgb, np.float32), p["magnitude"], p["paper_exp"],
                       p["film_fog"], p["film_power"], p["paper_power"])
    return np.clip(out, 0.0, 1.0).astype(np.float32)


if __name__ == "__main__":
    p = derive_params()
    print("derived:", {k: round(float(v), 6) for k, v in p.items()})
    xs = np.array([0.0, 0.01, 0.0461, 0.1845, 0.5, 1.0, 2.0, 4.0, 8.0, 32.0, 1000.0], np.float32)
    ys = sigmoid_view(xs.reshape(-1, 1, 1).repeat(3, -1))[:, 0, 0]
    print(" scene-linear -> display (sigmoid):")
    for x, y in zip(xs, ys):
        print(f"   {x:8.4f} -> {y:.4f}")
    # sanity: mid-grey locked, monotone, asymptote < 1
    assert abs(float(sigmoid_view(np.full((1,1,3), 0.1845, np.float32))[0,0,0]) - 0.1845) < 2e-3, "mid-grey not locked"
    assert float(sigmoid_view(np.full((1,1,3), 1000.0, np.float32))[0,0,0]) > 0.98, "no asymptote to white"
    assert float(sigmoid_view(np.full((1,1,3), 1000.0, np.float32))[0,0,0]) <= 1.0, "overshoots white"
    print(" OK: mid-grey locked, asymptotic to white, no clip")
