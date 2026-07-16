from __future__ import annotations

import numpy as np

from features.develop.highlights_recon import reconstruct_highlights


def test_unclipped_image_is_byte_exact() -> None:
    rng = np.random.default_rng(42)
    source = rng.uniform(0.0, 0.99, size=(12, 9, 3)).astype(np.float32)

    reconstructed = reconstruct_highlights(source, (1.0, 1.0, 1.0))

    assert reconstructed.tobytes() == source.tobytes()


def test_clipped_sky_channel_is_recovered_without_magenta_shift() -> None:
    sensor = np.empty((32, 32, 3), dtype=np.float32)
    sensor[...] = np.array([0.85, 0.90, 1.00], dtype=np.float32)
    sensor[:4] = np.array([0.42, 0.52, 0.78], dtype=np.float32)
    sensor[-4:] = np.array([0.42, 0.52, 0.78], dtype=np.float32)

    reconstructed = reconstruct_highlights(sensor, (1.0, 1.0, 1.0))

    center = reconstructed[16, 16]
    assert center[2] > 1.05
    assert center[2] >= center[1] >= center[0]
    assert reconstructed[0, 0].tobytes() == sensor[0, 0].tobytes()
