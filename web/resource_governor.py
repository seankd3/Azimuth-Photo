from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BackgroundDecision:
    mode: str
    intensity: float
    pause: bool
    sleep_seconds: float
    thumbnail_batch_size: int
    thumbnail_pause_seconds: float
    embedding_pause_seconds: float
    reason: str
    load_1m: float
    cpu_count: int
    available_memory_gb: float
    swap_used_pct: float
    idle_seconds: float
    checked_at: float
    work_mode: str = "balanced"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["can_start_heavy_work"] = self.can_start_heavy_work
        return data

    @property
    def can_start_heavy_work(self) -> bool:
        if self.work_mode == "browse":
            return False
        load_ratio = self.load_1m / max(1, self.cpu_count)
        if self.work_mode == "max":
            return (
                self.mode in {"normal", "manual"}
                and load_ratio < 1.15
                and self.available_memory_gb >= 4.0
                and self.swap_used_pct < 50.0
            )
        if self.work_mode == "balanced":
            return (
                self.mode == "light"
                and load_ratio < 0.75
                and self.available_memory_gb >= 4.0
                and self.swap_used_pct < 35.0
            )
        return False


def _read_meminfo() -> dict[str, int]:
    values = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, raw_value = line.split(":", 1)
                parts = raw_value.strip().split()
                if parts:
                    values[key] = int(parts[0]) * 1024
    except Exception:
        pass
    return values


def _read_load_1m() -> float:
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def _normalize_work_mode(value) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"browse", "balanced", "max"}:
        return mode
    return "balanced"


def _current_work_mode() -> str:
    try:
        import settings
        return _normalize_work_mode(settings.get_settings().get("background_work_mode"))
    except Exception:
        return "balanced"


def get_background_decision(idle_seconds: float = 999.0, work_mode: str | None = None) -> BackgroundDecision:
    work_mode = _normalize_work_mode(work_mode or _current_work_mode())
    cpu_count = max(1, os.cpu_count() or 1)
    load_1m = _read_load_1m()
    meminfo = _read_meminfo()

    available = meminfo.get("MemAvailable", 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used_pct = (
        round(((swap_total - swap_free) / swap_total) * 100, 1)
        if swap_total > 0
        else 0.0
    )
    available_gb = round(available / (1024 ** 3), 2)
    load_ratio = load_1m / cpu_count
    idle_seconds = max(0.0, float(idle_seconds))
    checked_at = time.time()

    if swap_used_pct >= 65:
        return BackgroundDecision(
            work_mode=work_mode,
            mode="paused",
            intensity=0.0,
            pause=True,
            sleep_seconds=15.0,
            thumbnail_batch_size=0,
            thumbnail_pause_seconds=5.0,
            embedding_pause_seconds=5.0,
            reason="swap pressure",
            load_1m=load_1m,
            cpu_count=cpu_count,
            available_memory_gb=available_gb,
            swap_used_pct=swap_used_pct,
            idle_seconds=round(idle_seconds, 2),
            checked_at=checked_at,
        )

    if available_gb < 2.0:
        return BackgroundDecision(
            work_mode=work_mode,
            mode="paused",
            intensity=0.0,
            pause=True,
            sleep_seconds=10.0,
            thumbnail_batch_size=0,
            thumbnail_pause_seconds=5.0,
            embedding_pause_seconds=5.0,
            reason="low available memory",
            load_1m=load_1m,
            cpu_count=cpu_count,
            available_memory_gb=available_gb,
            swap_used_pct=swap_used_pct,
            idle_seconds=round(idle_seconds, 2),
            checked_at=checked_at,
        )

    if work_mode == "browse":
        return BackgroundDecision(
            work_mode=work_mode,
            mode="paused",
            intensity=0.0,
            pause=True,
            sleep_seconds=10.0,
            thumbnail_batch_size=0,
            thumbnail_pause_seconds=5.0,
            embedding_pause_seconds=5.0,
            reason="browse mode",
            load_1m=load_1m,
            cpu_count=cpu_count,
            available_memory_gb=available_gb,
            swap_used_pct=swap_used_pct,
            idle_seconds=round(idle_seconds, 2),
            checked_at=checked_at,
        )

    busy_threshold = 1.20 if work_mode == "max" else 0.95
    if load_ratio >= busy_threshold or swap_used_pct >= 40:
        return BackgroundDecision(
            work_mode=work_mode,
            mode="gentle",
            intensity=0.55 if work_mode == "max" else 0.2,
            pause=False,
            sleep_seconds=0.0,
            thumbnail_batch_size=8 if work_mode == "max" else 1,
            thumbnail_pause_seconds=0.5 if work_mode == "max" else 3.0,
            embedding_pause_seconds=0.5 if work_mode == "max" else 3.0,
            reason="system busy",
            load_1m=load_1m,
            cpu_count=cpu_count,
            available_memory_gb=available_gb,
            swap_used_pct=swap_used_pct,
            idle_seconds=round(idle_seconds, 2),
            checked_at=checked_at,
        )

    return BackgroundDecision(
        work_mode=work_mode,
        mode="normal" if work_mode == "max" else "light",
        intensity=0.85 if work_mode == "max" else 0.25,
        pause=False,
        sleep_seconds=0.0,
        thumbnail_batch_size=16 if work_mode == "max" else 2,
        thumbnail_pause_seconds=0.0 if work_mode == "max" else 1.5,
        embedding_pause_seconds=0.05 if work_mode == "max" else 1.5,
        reason="system healthy" if work_mode == "max" else "light background",
        load_1m=load_1m,
        cpu_count=cpu_count,
        available_memory_gb=available_gb,
        swap_used_pct=swap_used_pct,
        idle_seconds=round(idle_seconds, 2),
        checked_at=checked_at,
    )
