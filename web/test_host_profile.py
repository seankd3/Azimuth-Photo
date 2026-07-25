"""host_profile adaptive sizing."""
from __future__ import annotations

import unittest

from core import host_profile
from core.host_profile import HostProfile, _GIB


def _profile(**kwargs) -> HostProfile:
    base = dict(
        cpu_count=8,
        ram_total_bytes=16 * _GIB,
        ram_available_bytes=8 * _GIB,
        cgroup_high_bytes=None,
        cgroup_max_bytes=None,
        vram_total_bytes=8 * _GIB,
        vram_free_bytes=7 * _GIB,
        has_cuda=True,
        source="test",
    )
    base.update(kwargs)
    return HostProfile(**base)


class HostProfileTests(unittest.TestCase):
    def tearDown(self):
        host_profile.reset_host_profile_for_tests(None)

    def test_small_gpu_recommends_2b(self):
        p = _profile(vram_total_bytes=8 * _GIB, vram_free_bytes=7 * _GIB)
        self.assertEqual(p.recommended_embed_preset(), "qwen3-vl-embedding-2b")
        self.assertTrue(p.exclusive_gpu_models())

    def test_large_gpu_recommends_8b(self):
        p = _profile(vram_total_bytes=24 * _GIB, vram_free_bytes=20 * _GIB)
        self.assertEqual(p.recommended_embed_preset(), "qwen3-vl-embedding-8b")
        self.assertFalse(p.exclusive_gpu_models())

    def test_no_gpu_uses_compact_model(self):
        p = _profile(has_cuda=False, vram_total_bytes=None, vram_free_bytes=None)
        self.assertEqual(p.recommended_embed_preset(), "qwen3-vl-embedding-2b")
        self.assertEqual(p.model_vram_budget_bytes(), 0)

    def test_demosaic_workers_scale_with_ram(self):
        small = _profile(ram_total_bytes=8 * _GIB, cpu_count=8)
        big = _profile(ram_total_bytes=64 * _GIB, cpu_count=16)
        self.assertLessEqual(small.demosaic_workers(), 2)
        self.assertGreaterEqual(big.demosaic_workers(), 4)

    def test_watermarks_proportional_to_cgroup(self):
        p = _profile(
            cgroup_high_bytes=12 * _GIB,
            cgroup_max_bytes=14 * _GIB,
        )
        soft, hard, resume = p.memory_watermarks()
        self.assertLess(soft, 12 * _GIB)
        self.assertLess(hard, 14 * _GIB)
        self.assertLessEqual(resume, soft)
        self.assertGreater(soft, 4 * _GIB)


if __name__ == "__main__":
    unittest.main()
