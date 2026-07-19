#!/usr/bin/env python3
"""Real-host ModelPool smoke: caption → embeddings → people under a tight VRAM budget.

Proves LRU eviction + nvidia-smi staying under the configured budget.
Uses the 3B captioner + 2B embedding model (real weights on this host) so the
run fits beside other GPU clients (e.g. voxtype) on the 8GB card. Declared
pool costs still force caption→embedding eviction.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

# Tight host budget: one large GPU model at a time on the 8GB card.
os.environ.setdefault("PHOTOARCHIVE_MODEL_BUDGET_VRAM_BYTES", "default")
os.environ.setdefault("PHOTOARCHIVE_MODEL_BUDGET_RAM_BYTES", "default")
os.environ.setdefault("PHOTOARCHIVE_MODEL_PIN_SECONDS", "0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("model_pool_smoke")


def _nvidia_used_mib() -> int | None:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        ).strip()
        return int(float(out.splitlines()[0]))
    except Exception:
        return None


def main() -> int:
    from core import model_pool
    from core.model_pool import (
        DEFAULT_VRAM_BUDGET_BYTES,
        ModelPool,
        reset_model_pool_for_tests,
    )
    import caption_worker
    import embedding_worker
    import face_worker

    budget_mib = DEFAULT_VRAM_BUDGET_BYTES // (1024 * 1024)
    pool = reset_model_pool_for_tests(
        ModelPool(
            vram_budget_bytes=DEFAULT_VRAM_BUDGET_BYTES,
            ram_budget_bytes=model_pool.DEFAULT_RAM_BUDGET_BYTES,
            pin_seconds=0.0,
        )
    )
    pool.clear_eviction_log()

    caption_dir = Path("/home/sean/Projects/photo-archive/web/.models/Qwen--Qwen2.5-VL-3B-Instruct")
    embed_dir = Path("/home/sean/Projects/photo-archive/web/.models/Qwen--Qwen3-VL-Embedding-2B")
    face_dir = Path("/home/sean/Projects/photo-archive/web/.models/insightface")
    for path, label in (
        (caption_dir, "caption"),
        (embed_dir, "embeddings"),
        (face_dir, "people"),
    ):
        if not path.exists():
            log.error("missing %s model dir: %s", label, path)
            return 2

    caption_config = {
        "model_dir": str(caption_dir),
        "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
        "revision": "main",
        "quantization": "bnb-4bit",
    }
    face_config = {
        "face_model_id": "buffalo_l",
        "face_model_dir": str(face_dir),
        "face_detection_size": 640,
    }

    samples: list[dict] = []

    def snapshot(step: str) -> None:
        used = _nvidia_used_mib()
        samples.append(
            {
                "step": step,
                "residents": pool.resident_names(),
                "evictions": pool.eviction_log(),
                "nvidia_used_mib": used,
                "budget_mib": budget_mib,
            }
        )
        log.info(
            "smoke step=%s residents=%s evictions=%s nvidia_used_mib=%s budget_mib=%s",
            step,
            pool.resident_names(),
            pool.eviction_log(),
            used,
            budget_mib,
        )
        if used is not None and used > budget_mib + 512:
            # Allow small overhead above declared budget for drivers / other procs.
            log.warning(
                "nvidia used %s MiB exceeds budget %s MiB (+512 slack)",
                used,
                budget_mib,
            )

    snapshot("start")
    t0 = time.perf_counter()

    log.info("loading captions…")
    caption_worker._load_model(caption_config, interactive=False)
    snapshot("after_captions")

    log.info("loading embeddings (should evict captions)…")
    embedding_worker._load_model(
        str(embed_dir),
        "Qwen/Qwen3-VL-Embedding-2B",
        False,
    )
    snapshot("after_embeddings")

    log.info("loading people…")
    face_worker._load_face_app(face_config, interactive=False)
    snapshot("after_people")

    caption_worker._unload_model()
    embedding_worker._unload_model()
    face_worker._unload_face_app()
    snapshot("after_unload_all")

    elapsed = round(time.perf_counter() - t0, 1)
    evictions = pool.eviction_log()
    print("\n=== ModelPool smoke summary ===")
    print(f"elapsed_s={elapsed}")
    print(f"budget_mib={budget_mib}")
    print(f"evictions={evictions}")
    for row in samples:
        print(row)

    if "captions" not in evictions:
        log.error("expected captions to be evicted when embeddings loaded")
        return 1

    peak = max((r["nvidia_used_mib"] or 0) for r in samples)
    # Peak must stay under card size; budget gate is the declared pool budget
    # plus other GPU users (voxtype etc.) observed at start.
    start_used = samples[0]["nvidia_used_mib"] or 0
    other = max(0, start_used - 200)  # baseline other processes
    if peak > budget_mib + other + 768:
        log.error(
            "peak nvidia %s MiB too high for budget %s + other ~%s",
            peak,
            budget_mib,
            other,
        )
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
