"""The shaping curves the develop pipeline blends with.

Both of these existed three times over. Worse than repetition, the copies had
drifted: one `smoothstep` divided by the gap between its edges without checking
it was non-zero, which hands a NaN to whatever it was about to weight — a
corrupt pixel, not a wrong one.

Guarding the degenerate case is not a special case. When the edges coincide the
ramp *is* a step, and that is what the guarded form produces.
"""

from __future__ import annotations

import numpy as np

from pixels import ops_constants as C


def smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    """A cubic ease from 0 at ``edge0`` to 1 at ``edge1``, clamped outside."""

    span = max(edge1 - edge0, C.LOCAL_RANGE_EPSILON)
    t = np.clip((value - edge0) / span, 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32, copy=False)


def gaussian_ev(ev: np.ndarray, center: float, sigma: float = C.TONE_EV_SIGMA) -> np.ndarray:
    """A bell centred on an exposure value, for weighting tonal regions."""

    z = (ev - center) / max(sigma, C.TONE_EPSILON)
    return np.exp(-0.5 * z * z).astype(np.float32, copy=False)
