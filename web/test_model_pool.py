"""ModelPool contracts: LRU, single-flight, pin-while-hot, pressure, pass-through."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from core import memory_pressure, model_pool
from core.model_pool import ModelPool, reset_model_pool_for_tests


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ModelPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.empty_cache_calls = 0

        def empty_cache() -> None:
            self.empty_cache_calls += 1

        self.pool = reset_model_pool_for_tests(
            ModelPool(
                vram_budget_bytes=100,
                ram_budget_bytes=1000,
                pin_seconds=10.0,
                empty_cache=empty_cache,
                clock=self.clock,
            )
        )

    def tearDown(self) -> None:
        reset_model_pool_for_tests(
            ModelPool(
                vram_budget_bytes=None,
                ram_budget_bytes=None,
                pin_seconds=0.0,
                empty_cache=lambda: None,
            )
        )
        memory_pressure.reset_for_tests()

    def _fake(self, name: str, *, vram: int = 40, ram: int = 10):
        def load():
            self.loads.append(name)
            return f"model:{name}"

        def unload():
            self.unloads.append(name)

        return dict(
            name=name,
            load_fn=load,
            unload_fn=unload,
            vram_bytes=vram,
            ram_bytes=ram,
        )

    def test_lru_eviction_at_budget(self) -> None:
        self.pool.acquire(**self._fake("a"), interactive=False)
        self.clock.advance(1)
        self.pool.acquire(**self._fake("b"), interactive=False)
        self.assertEqual(sorted(self.pool.resident_names()), ["a", "b"])

        self.clock.advance(1)
        # a+b = 80 VRAM; loading c (40) needs 120 → evict LRU (a).
        self.pool.acquire(**self._fake("c"), interactive=False)
        self.assertEqual(sorted(self.pool.resident_names()), ["b", "c"])
        self.assertEqual(self.unloads, ["a"])
        self.assertEqual(self.pool.eviction_log(), ["a"])
        self.assertGreaterEqual(self.empty_cache_calls, 1)

    def test_single_flight_concurrent_loads(self) -> None:
        started = threading.Event()
        release = threading.Event()
        load_count = 0
        load_lock = threading.Lock()

        def load():
            nonlocal load_count
            with load_lock:
                load_count += 1
            started.set()
            self.assertTrue(release.wait(timeout=2.0))
            return "shared"

        def unload():
            self.unloads.append("shared")

        results: list[object] = []
        errors: list[BaseException] = []

        def worker():
            try:
                results.append(
                    self.pool.acquire(
                        "shared",
                        load_fn=load,
                        unload_fn=unload,
                        vram_bytes=10,
                        ram_bytes=10,
                    )
                )
            except BaseException as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.assertTrue(started.wait(timeout=2.0))
        release.set()
        for thread in threads:
            thread.join(timeout=2.0)
        self.assertEqual(errors, [])
        self.assertEqual(results, ["shared", "shared", "shared", "shared"])
        self.assertEqual(load_count, 1)

    def test_pin_while_hot_blocks_bulk_eviction(self) -> None:
        # Budget 100: interactive search (70) + bulk captions (70) cannot both
        # fit. Pin blocks eviction so bulk load is refused (no load-anyway OOM).
        self.pool.acquire(**self._fake("search", vram=70), interactive=True)
        self.clock.advance(1)
        with self.assertRaises(RuntimeError):
            self.pool.acquire(**self._fake("captions", vram=70), interactive=False)
        self.assertEqual(self.pool.resident_names(), ["search"])
        self.assertEqual(self.unloads, [])

        # After pin window, bulk load can evict search and fit.
        self.clock.advance(11)
        self.pool.acquire(**self._fake("people", vram=70), interactive=False)
        self.assertNotIn("search", self.pool.resident_names())
        self.assertIn("people", self.pool.resident_names())
        self.assertIn("search", self.unloads)

    def test_pressure_unload_all(self) -> None:
        self.pool.acquire(**self._fake("a"), interactive=False)
        self.pool.acquire(**self._fake("b", vram=30), interactive=False)
        unloaded = self.pool.unload_all()
        self.assertEqual(sorted(unloaded), ["a", "b"])
        self.assertEqual(self.pool.resident_names(), [])
        self.assertEqual(sorted(self.unloads), ["a", "b"])

    def test_pressure_unload_all_skips_pinned(self) -> None:
        self.pool.acquire(**self._fake("search", vram=40), interactive=True)
        self.pool.acquire(**self._fake("captions", vram=30), interactive=False)
        unloaded = self.pool.unload_all(force=False)
        self.assertEqual(unloaded, ["captions"])
        self.assertEqual(self.pool.resident_names(), ["search"])
        self.assertEqual(self.unloads, ["captions"])

        forced = self.pool.unload_all(force=True)
        self.assertEqual(forced, ["search"])
        self.assertEqual(self.pool.resident_names(), [])

    def test_unload_if_idle_respects_ttl_and_pin(self) -> None:
        self.pool.acquire(**self._fake("embeddings"), interactive=True)
        self.assertFalse(self.pool.unload_if_idle("embeddings", ttl_seconds=5.0))
        self.assertIn("embeddings", self.pool.resident_names())

        self.clock.advance(11)  # pin expired
        self.assertFalse(self.pool.unload_if_idle("embeddings", ttl_seconds=30.0))
        self.clock.advance(30)
        self.assertTrue(self.pool.unload_if_idle("embeddings", ttl_seconds=30.0))
        self.assertEqual(self.pool.resident_names(), [])
        self.assertEqual(self.unloads, ["embeddings"])

    def test_pass_through_when_budget_unset_or_huge(self) -> None:
        empty_calls = {"n": 0}

        def empty_cache() -> None:
            empty_calls["n"] += 1

        # Unset budgets → pass-through (no eviction).
        unlimited = ModelPool(
            vram_budget_bytes=None,
            ram_budget_bytes=None,
            pin_seconds=0.0,
            empty_cache=empty_cache,
            clock=self.clock,
        )
        self.assertTrue(unlimited.pass_through)
        unlimited.acquire(**self._fake("a", vram=10**12), interactive=False)
        unlimited.acquire(**self._fake("b", vram=10**12), interactive=False)
        self.assertEqual(sorted(unlimited.resident_names()), ["a", "b"])
        self.assertEqual(self.unloads, [])

        # Huge budget → also pass-through for practical purposes.
        huge = ModelPool(
            vram_budget_bytes=10**18,
            ram_budget_bytes=10**18,
            pin_seconds=0.0,
            empty_cache=empty_cache,
            clock=self.clock,
        )
        huge.acquire(**self._fake("x", vram=10**9), interactive=False)
        huge.acquire(**self._fake("y", vram=10**9), interactive=False)
        self.assertEqual(sorted(huge.resident_names()), ["x", "y"])
        self.assertEqual(huge.eviction_log(), [])


class ModelPoolEnvTests(unittest.TestCase):
    def test_env_default_and_unlimited_tokens(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                model_pool.ENV_VRAM_BUDGET: "default",
                model_pool.ENV_RAM_BUDGET: "unlimited",
            },
            clear=False,
        ):
            self.assertEqual(
                model_pool._env_budget_bytes(model_pool.ENV_VRAM_BUDGET),
                model_pool.DEFAULT_VRAM_BUDGET_BYTES,
            )
            self.assertIsNone(model_pool._env_budget_bytes(model_pool.ENV_RAM_BUDGET))

        with mock.patch.dict("os.environ", {model_pool.ENV_VRAM_BUDGET: "6g"}, clear=False):
            self.assertEqual(
                model_pool._env_budget_bytes(model_pool.ENV_VRAM_BUDGET),
                6 * 1024**3,
            )


if __name__ == "__main__":
    unittest.main()
