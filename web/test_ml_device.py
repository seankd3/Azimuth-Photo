"""Contracts for the shared ML device helper."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from core import ml_device


class MlDeviceHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get(ml_device.ENV_NAME)
        os.environ.pop(ml_device.ENV_NAME, None)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop(ml_device.ENV_NAME, None)
        else:
            os.environ[ml_device.ENV_NAME] = self._old

    def test_cpu_env_override_wins_even_when_cuda_exists(self) -> None:
        os.environ[ml_device.ENV_NAME] = "cpu"
        with mock.patch.object(ml_device, "cuda_available", return_value=True):
            self.assertEqual(ml_device.preferred_device(), "cpu")
            self.assertEqual(ml_device.sentence_transformers_device(), "cpu")
            self.assertEqual(ml_device.transformers_device_map(quantized=True), {"": "cpu"})
            self.assertEqual(ml_device.insightface_ctx_id(), -1)

    def test_cuda_env_falls_back_when_unavailable(self) -> None:
        os.environ[ml_device.ENV_NAME] = "cuda"
        with mock.patch.object(ml_device, "cuda_available", return_value=False):
            self.assertEqual(ml_device.preferred_device(), "cpu")

    def test_default_prefers_cuda_when_available(self) -> None:
        with mock.patch.object(ml_device, "cuda_available", return_value=True):
            self.assertEqual(ml_device.preferred_device(), "cuda")
            self.assertEqual(ml_device.transformers_device_map(quantized=True), {"": 0})
            self.assertEqual(ml_device.transformers_device_map(quantized=False), "auto")

    def test_onnx_providers_prefer_cuda_only_when_installed(self) -> None:
        with (
            mock.patch.object(ml_device, "cuda_available", return_value=True),
            mock.patch(
                "onnxruntime.get_available_providers",
                return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
            ),
        ):
            self.assertEqual(
                ml_device.onnx_providers(),
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            self.assertEqual(ml_device.insightface_ctx_id(), 0)

        with (
            mock.patch.object(ml_device, "cuda_available", return_value=True),
            mock.patch(
                "onnxruntime.get_available_providers",
                return_value=["CPUExecutionProvider"],
            ),
        ):
            self.assertEqual(ml_device.onnx_providers(), ["CPUExecutionProvider"])
            self.assertEqual(ml_device.insightface_ctx_id(), -1)

    def test_empty_cuda_cache_never_raises(self) -> None:
        with (
            mock.patch("torch.cuda.is_available", return_value=True),
            mock.patch("torch.cuda.empty_cache", side_effect=RuntimeError("boom")),
        ):
            ml_device.empty_cuda_cache()  # must not raise


if __name__ == "__main__":
    unittest.main()
