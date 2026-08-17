"""How much memory this process is using, and how much the host has left.

Two readings, best source first: `psutil` where it is installed, the cgroup's
own accounting or `/proc/self/status` otherwise, and `MemAvailable` for the
host. Either can answer `None`, because "nothing here can read it" is a real
answer and a zero pretending to be one is not. Nothing here decides anything.

It used to. Soft, hard and resume watermarks with hysteresis, host floors, a
120-second startup calm, a pause flag, a model-unload request, and a
`gate_bulk_work` this docstring described as the one path every bulk driver
consults before taking a batch: *"pregen, captions, embeddings, people ... not
a pregen-only special case."*

Those four drivers were consolidated into `work.step()`, and none of the
deciding functions ever acquired a caller afterwards. `work.workers()` answers
the same question in twelve lines — half the machine's memory divided by what
one decode may cost, bounded by cores minus one, halved while the app is in use
— so this had become a second opinion nobody asked for. What made it worth
finding rather than leaving: the health panel still read the verdict and told
the owner *"bulk work paused"*, which had not been true for as long as the
watermarks had had no callers.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()
_memory_reader: Callable[[], "MemoryReading"] | None = None
_host_available_reader: Callable[[], int | None] | None = None


@dataclass(frozen=True)
class MemoryReading:
    """One sample: what this process holds, and where the number came from."""

    rss_bytes: int
    swap_bytes: int
    pressure_bytes: int
    source: str  # psutil | cgroup | proc | injected


def _self_cgroup_dir() -> Path | None:
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split(":", 2)
                if len(parts) == 3 and parts[0] == "0":
                    candidate = Path("/sys/fs/cgroup") / parts[2].lstrip("/")
                    return candidate if candidate.is_dir() else None
    except OSError:
        return None
    return None


def _read_proc_status_kb(keys: tuple[str, ...]) -> dict[str, int]:
    found = {key: 0 for key in keys}
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                for key in keys:
                    if line.startswith(f"{key}:"):
                        found[key] = max(0, int(line.split()[1]))
                        break
    except (OSError, ValueError, IndexError):
        pass
    return found


def read_memory() -> MemoryReading | None:
    """What this process holds, or None where nothing can read it.

    psutil first because it is the only source that works on every platform
    this app runs on. `/proc` and the cgroup files do not exist on Windows, so
    a reader that tried them first reported **0 B in use** on the machine the
    owner actually looks at — and the health panel printed the zero as a fact.
    A number that means "could not read" is the same lie as a wrong number.
    """

    if _memory_reader is not None:
        try:
            reading = _memory_reader()
            return MemoryReading(
                rss_bytes=max(0, int(reading.rss_bytes)),
                swap_bytes=max(0, int(reading.swap_bytes)),
                pressure_bytes=max(0, int(reading.pressure_bytes)),
                source=str(reading.source or "injected"),
            )
        except Exception:
            return MemoryReading(0, 0, 0, "injected")

    try:
        import psutil

        info = psutil.Process().memory_full_info()
        rss = max(0, int(info.rss))
        swap = max(0, int(getattr(info, "swap", 0) or 0))
        return MemoryReading(rss, swap, rss + swap, "psutil")
    except Exception:
        pass

    cgroup = _self_cgroup_dir()
    if cgroup is not None:
        try:
            current = max(0, int((cgroup / "memory.current").read_text(encoding="utf-8").strip()))
            swap_path = cgroup / "memory.swap.current"
            swap = max(0, int(swap_path.read_text(encoding="utf-8").strip())) if swap_path.is_file() else 0
            return MemoryReading(current, swap, current + swap, "cgroup")
        except (OSError, ValueError):
            pass

    kb = _read_proc_status_kb(("VmRSS", "VmSwap"))
    rss, swap = kb["VmRSS"] * 1024, kb["VmSwap"] * 1024
    if rss or swap:
        return MemoryReading(rss, swap, rss + swap, "proc")
    return None


def read_host_available_bytes() -> int | None:
    """What the whole machine has left, or None where it cannot be read.

    The host figure matters because this process is not alone: an indexing
    grind or an agent on the same box spends the same memory.
    """

    if _host_available_reader is not None:
        try:
            value = _host_available_reader()
            return None if value is None else max(0, int(value))
        except Exception:
            return None
    try:
        import psutil

        return max(0, int(psutil.virtual_memory().available))
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return max(0, int(line.split()[1]) * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def set_memory_reader(reader: Callable[[], MemoryReading] | None) -> None:
    """Inject a reading (tests). ``None`` restores live reads."""

    global _memory_reader
    _memory_reader = reader


def set_host_available_reader(reader: Callable[[], int | None] | None) -> None:
    """Inject the host figure (tests). ``None`` restores live reads."""

    global _host_available_reader
    _host_available_reader = reader


def reset_for_tests() -> None:
    global _memory_reader, _host_available_reader
    with _lock:
        _memory_reader = None
        _host_available_reader = None
