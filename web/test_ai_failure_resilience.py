from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from core.ai_failures import caption_ledger_status, is_gpu_resource_error
from core import model_pool
from data.repositories import captions


class AiFailureResilienceTests(unittest.TestCase):
    def test_cuda_oom_is_a_retryable_system_failure(self):
        error = RuntimeError("CUDA out of memory while allocating tensor")

        self.assertTrue(is_gpu_resource_error(error))
        self.assertEqual(
            caption_ledger_status(requested_status="error", error=error),
            "pending",
        )

    def test_media_failure_remains_an_image_error(self):
        error = ValueError("cannot identify image file")

        self.assertFalse(is_gpu_resource_error(error))
        self.assertEqual(
            caption_ledger_status(requested_status="error", error=error),
            "error",
        )

    def test_model_pool_uses_safe_host_budgets_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                model_pool._env_budget_bytes(model_pool.ENV_VRAM_BUDGET),
                model_pool.DEFAULT_VRAM_BUDGET_BYTES,
            )
            self.assertEqual(
                model_pool._env_budget_bytes(model_pool.ENV_RAM_BUDGET),
                model_pool.DEFAULT_RAM_BUDGET_BYTES,
            )

    def test_model_pool_allows_explicit_unlimited_opt_out(self):
        with mock.patch.dict(
            os.environ,
            {
                model_pool.ENV_VRAM_BUDGET: "unlimited",
                model_pool.ENV_RAM_BUDGET: "0",
            },
            clear=True,
        ):
            self.assertIsNone(model_pool._env_budget_bytes(model_pool.ENV_VRAM_BUDGET))
            self.assertIsNone(model_pool._env_budget_bytes(model_pool.ENV_RAM_BUDGET))


class CaptionLedgerResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_oom_is_persisted_as_retryable_pending_work(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as database:
            conn = sqlite3.connect(database.name)
            conn.executescript(
                """
                CREATE TABLE image_captions (
                    image_id INTEGER,
                    model_key TEXT,
                    caption TEXT,
                    tags TEXT,
                    quality TEXT,
                    user_edited INTEGER DEFAULT 0,
                    created_at REAL,
                    UNIQUE(model_key, image_id)
                );
                CREATE TABLE caption_scan_images (
                    image_id INTEGER,
                    model_key TEXT,
                    status TEXT,
                    last_error TEXT,
                    scanned_at REAL,
                    UNIQUE(image_id, model_key)
                );
                """
            )
            conn.close()

            await captions.store_caption_result(
                database.name,
                image_id=7,
                model_key="caption@test",
                caption="",
                tags=[],
                status="error",
                error="CUDA out of memory",
            )

            conn = sqlite3.connect(database.name)
            row = conn.execute(
                "SELECT status, last_error FROM caption_scan_images"
            ).fetchone()
            conn.close()

        self.assertEqual(row, ("pending", "CUDA out of memory"))


if __name__ == "__main__":
    unittest.main()
