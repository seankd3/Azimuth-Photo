"""Small test-harness isolation helpers shared across unittest families."""

from __future__ import annotations

from core import memory_pressure


_TEST_PRESSURE_BYTES = 1024


def install_bulk_memory_isolation(test_case) -> None:
    """Keep bulk-work contract tests independent of live host pressure."""

    memory_pressure.reset_for_tests()
    memory_pressure.set_memory_reader(
        lambda: memory_pressure.MemoryReading(
            rss_bytes=_TEST_PRESSURE_BYTES,
            swap_bytes=0,
            pressure_bytes=_TEST_PRESSURE_BYTES,
            source="test-harness",
        )
    )
    memory_pressure.set_host_available_reader(
        lambda: max(
            memory_pressure.HOST_RESUME_AVAILABLE_BYTES,
            memory_pressure.HOST_SOFT_AVAILABLE_BYTES,
        )
        + memory_pressure._GIB
    )
    test_case.addCleanup(memory_pressure.reset_for_tests)


class BulkMemoryIsolatedTestCase:
    """Opt-in mixin for contracts that are not testing memory-pressure policy."""

    def setUp(self):
        super().setUp()
        install_bulk_memory_isolation(self)
