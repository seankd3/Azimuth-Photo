"""Detect host resources and derive adaptive run-time budgets.

Azimuth must run well on an 8GB laptop, a 16GB hub with an 8GB GPU, or a
64GB workstation — without per-machine hardcoding. Every bulk/AI path
should consult this profile instead of baking in one developer's box.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

_GIB = 1024**3
_MIB = 1024**2

# Model cost estimates (peak residency). Used for pool accounting + preset pick.
# 4-bit Qwen3-VL embedding peaks measured ~2026-07 on consumer NVIDIA cards.
COST_EMBED_8B_VRAM = 6200 * _MIB
COST_EMBED_2B_VRAM = 2800 * _MIB
COST_CAPTION_7B_VRAM = 5500 * _MIB
COST_CAPTION_3B_VRAM = 3200 * _MIB
COST_EMBED_RAM = 512 * _MIB
COST_CAPTION_RAM = 512 * _MIB
COST_PEOPLE_RAM = 800 * _MIB
COST_SUBJECT_MASK_RAM = 200 * _MIB


@dataclass(frozen=True)
class HostProfile:
    """Snapshot of the machine Azimuth is running on."""

    cpu_count: int
    ram_total_bytes: int
    ram_available_bytes: int | None
    cgroup_high_bytes: int | None
    cgroup_max_bytes: int | None
    vram_total_bytes: int | None
    vram_free_bytes: int | None
    has_cuda: bool
    source: str

    @property
    def ram_total_gib(self) -> float:
        return self.ram_total_bytes / _GIB

    @property
    def vram_total_gib(self) -> float | None:
        if self.vram_total_bytes is None:
            return None
        return self.vram_total_bytes / _GIB

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ram_total_gib"] = round(self.ram_total_gib, 2)
        d["vram_total_gib"] = (
            round(self.vram_total_gib, 2) if self.vram_total_gib is not None else None
        )
        d["model_vram_budget_bytes"] = self.model_vram_budget_bytes()
        d["model_ram_budget_bytes"] = self.model_ram_budget_bytes()
        d["recommended_embed_preset"] = self.recommended_embed_preset()
        d["recommended_caption_preset"] = self.recommended_caption_preset()
        d["exclusive_gpu_models"] = self.exclusive_gpu_models()
        return d

    # ---- budgets ---------------------------------------------------------

    def effective_memory_ceiling_bytes(self) -> int:
        """Hard ceiling for process working set (cgroup max → high → RAM)."""
        if self.cgroup_max_bytes:
            return self.cgroup_max_bytes
        if self.cgroup_high_bytes:
            return self.cgroup_high_bytes
        return self.ram_total_bytes

    def model_vram_budget_bytes(self) -> int | None:
        """How much VRAM ModelPool may keep resident.

        None = unlimited (CPU-only hosts skip GPU budgeting entirely via 0).
        """
        if not self.has_cuda or not self.vram_total_bytes:
            return 0
        # Keep ~12% free for fragmentation / CUDA context.
        return max(256 * _MIB, int(self.vram_total_bytes * 0.88))

    def model_ram_budget_bytes(self) -> int:
        ceiling = self.effective_memory_ceiling_bytes()
        # Leave headroom for catalog, thumb cache, demosaic workers, OS.
        if ceiling <= 8 * _GIB:
            fraction = 0.45
        elif ceiling <= 16 * _GIB:
            fraction = 0.55
        else:
            fraction = 0.60
        return max(1 * _GIB, int(ceiling * fraction))

    def exclusive_gpu_models(self) -> bool:
        """Small GPUs should host only one heavy model at a time."""
        vram = self.vram_total_bytes or 0
        return vram > 0 and vram < 14 * _GIB

    def memory_watermarks(self) -> tuple[int, int, int]:
        """Return (soft, hard, resume) pressure bytes — proportional, not fixed GiB.

        Uses cgroup high/max when present so a systemd-limited service and a
        bare process on the same box share the same formula.
        """
        high = self.cgroup_high_bytes
        maximum = self.cgroup_max_bytes
        if high is not None or maximum is not None:
            # Headroom scales with the limit (12% high / 10% max), floor 512MB.
            high_head = max(512 * _MIB, int((high or maximum or 0) * 0.12))
            max_head = max(512 * _MIB, int((maximum or high or 0) * 0.10))
            soft = (high - high_head) if high is not None else None
            hard = (maximum - max_head) if maximum is not None else None
            if soft is None and hard is not None:
                soft = int(hard * 0.55 / 0.72)
            if hard is None and soft is not None:
                hard = int(soft * 0.72 / 0.55)
            soft = max(1 * _GIB, int(soft or 1 * _GIB))
            hard = max(soft + 256 * _MIB, int(hard or soft + 256 * _MIB))
        else:
            total = max(2 * _GIB, self.ram_total_bytes)
            soft = max(1 * _GIB, int(total * 0.55))
            hard = max(soft + 256 * _MIB, int(total * 0.72))
        resume = max(soft * 85 // 100, soft - 512 * _MIB)
        resume = min(resume, soft)
        return soft, hard, resume

    # ---- model recommendations -------------------------------------------

    def recommended_embed_preset(self) -> str:
        """The heaviest search model this host can actually run.

        Three branches used to sit here, all of them choosing between a 2B and
        an 8B on estimated cost. Measured on the 4 GB card both are fictional:
        the 8B never loads, and the 2B loads at 3,901 MB of 4,096 and then makes
        no forward progress at all — Windows spills the overflow to system RAM
        rather than erroring, so it crawls at 0.03 img/s instead of failing.
        A recommendation that returns something which cannot run is worse than
        no recommendation.

        SigLIP-2 is not the consolation prize either: on the owner's own
        comparisons it ranks slightly better than the 8B's stored vectors
        (+0.714 against +0.687) at a third of the dimensions.
        """
        vram = self.vram_total_bytes or 0
        if self.has_cuda and vram >= 20 * _GIB:
            return "qwen3-vl-embedding-8b"
        return "siglip2-so400m"

    def recommended_caption_preset(self) -> str:
        vram = self.vram_total_bytes or 0
        if not self.has_cuda or vram < 12 * _GIB:
            return "qwen2.5-vl-3b-instruct-bnb-4bit"
        if vram < COST_CAPTION_7B_VRAM + (1500 * _MIB):
            return "qwen2.5-vl-3b-instruct-bnb-4bit"
        return "qwen2.5-vl-7b-instruct-bnb-4bit"

    def embed_vram_cost_bytes(self, preset_or_model_id: str) -> int:
        key = (preset_or_model_id or "").lower()
        if "8b" in key:
            return COST_EMBED_8B_VRAM
        return COST_EMBED_2B_VRAM

    def caption_vram_cost_bytes(self, preset_or_model_id: str) -> int:
        key = (preset_or_model_id or "").lower()
        if "3b" in key:
            return COST_CAPTION_3B_VRAM
        return COST_CAPTION_7B_VRAM


    # ---- bulk concurrency ------------------------------------------------

    def demosaic_workers(self) -> int:
        """RAW demosaic process count from RAM + cores (not one box's N=4)."""
        ncores = max(1, self.cpu_count)
        # ~3GB peak per demosaic worker for 40–60MP RAW.
        per_worker = 3 * _GIB
        # Budget half of ceiling for demosaic children so parent+AI still fit.
        budget = max(per_worker, self.effective_memory_ceiling_bytes() // 2)
        memory_workers = max(1, int(budget // per_worker))
        return max(1, min(ncores - 1 if ncores > 1 else 1, memory_workers, 6))

    def pregen_batch_size(self) -> int:
        gib = self.ram_total_gib
        if gib < 8:
            return 4
        if gib < 16:
            return 8
        if gib < 32:
            return 12
        return 16

    def embed_batch_size(self, preset: str) -> int:
        if "8b" in (preset or "").lower():
            return 1
        gib = self.ram_total_gib
        if gib < 8:
            return 1
        if gib < 16:
            return 2
        return 4

    def hub_memory_cache_gb(self) -> float:
        """In-process thumb LRU for the hub — scales with RAM, stays modest."""
        gib = self.ram_total_gib
        if gib < 8:
            return 0.25
        if gib < 16:
            return 0.5
        if gib < 32:
            return 1.0
        return min(2.0, gib * 0.04)

    def background_thumb_workers(self) -> int:
        n = max(1, self.cpu_count - 1)
        if self.ram_total_gib < 8:
            return min(n, 2)
        if self.ram_total_gib < 16:
            return min(n, 4)
        return min(n, 8)


_lock = threading.Lock()
_cached: HostProfile | None = None


def _read_cgroup_bytes(name: str) -> int | None:
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split(":", 2)
                if len(parts) != 3:
                    continue
                rel = parts[2].lstrip("/")
                if not rel:
                    continue
                path = f"/sys/fs/cgroup/{rel}/{name}"
                try:
                    raw = open(path, encoding="utf-8").read().strip().lower()
                except OSError:
                    continue
                if not raw or raw == "max":
                    return None
                value = int(raw)
                return value if value > 0 else None
    except OSError:
        pass
    return None


def _detect_ram() -> tuple[int, int | None]:
    total = 0
    available: int | None = None
    try:
        import psutil

        vm = psutil.virtual_memory()
        total = int(vm.total)
        available = int(vm.available)
    except Exception:
        pass
    if total <= 0:
        try:
            total = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (AttributeError, OSError, ValueError):
            total = 16 * _GIB
    if available is None:
        try:
            # MemAvailable from /proc/meminfo
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        available = int(line.split()[1]) * 1024
                        break
        except OSError:
            pass
    return max(total, 1 * _GIB), available


def _detect_vram() -> tuple[bool, int | None, int | None]:
    """Return (has_cuda, total_bytes, free_bytes)."""
    # Prefer nvidia-smi — works before torch is imported (saves RAM at boot).
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        try:
            out = subprocess.check_output(
                [
                    nvsmi,
                    "--query-gpu=memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            ).strip()
            if out:
                # Multi-GPU: use the largest card (primary compute device).
                best_total = 0
                best_free = 0
                for line in out.splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 2:
                        continue
                    total_mib = int(float(parts[0]))
                    free_mib = int(float(parts[1]))
                    if total_mib > best_total:
                        best_total = total_mib
                        best_free = free_mib
                if best_total > 0:
                    return True, best_total * _MIB, best_free * _MIB
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    try:
        import torch

        if torch.cuda.is_available():
            idx = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(idx)
            total = int(props.total_memory)
            try:
                free, _total = torch.cuda.mem_get_info(idx)
                free_i = int(free)
            except Exception:
                free_i = None
            return True, total, free_i
    except Exception:
        pass
    return False, None, None


def detect_host_profile(*, force: bool = False) -> HostProfile:
    """Probe the machine. Cached for process lifetime unless ``force``."""
    global _cached
    with _lock:
        if _cached is not None and not force:
            return _cached
        ram_total, ram_avail = _detect_ram()
        has_cuda, vram_total, vram_free = _detect_vram()
        profile = HostProfile(
            cpu_count=max(1, os.cpu_count() or 1),
            ram_total_bytes=ram_total,
            ram_available_bytes=ram_avail,
            cgroup_high_bytes=_read_cgroup_bytes("memory.high"),
            cgroup_max_bytes=_read_cgroup_bytes("memory.max"),
            vram_total_bytes=vram_total,
            vram_free_bytes=vram_free,
            has_cuda=has_cuda,
            source="live",
        )
        _cached = profile
        log.info(
            "host_profile ram=%.1fGiB vram=%s cuda=%s cgroup_high=%s "
            "embed=%s caption=%s demosaic_workers=%s exclusive_gpu=%s",
            profile.ram_total_gib,
            f"{profile.vram_total_gib:.1f}GiB" if profile.vram_total_gib else "none",
            profile.has_cuda,
            profile.cgroup_high_bytes,
            profile.recommended_embed_preset(),
            profile.recommended_caption_preset(),
            profile.demosaic_workers(),
            profile.exclusive_gpu_models(),
        )
        return profile


def reset_host_profile_for_tests(profile: HostProfile | None = None) -> None:
    global _cached
    with _lock:
        _cached = profile


@lru_cache(maxsize=1)
def _boot_log_once() -> bool:
    return True
