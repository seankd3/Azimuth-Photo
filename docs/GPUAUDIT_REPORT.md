# gpuaudit — GPU utilization verify + fix

Host: omarchy · GPU: RTX 2060 SUPER 8GB · torch 2.12.0+cu130 · branch `gpuaudit` · 2026-07-19

## Verdict

Torch search/caption paths were **already on CUDA**. Face detection and Develop subject masks were **hardcoded CPU**, and installed `onnxruntime` has **no CUDA EP**, so they stay CPU until that package changes (non-goal). Phase 2 adds one shared device helper + wires every loader; env override `AZIMUTH_ML_DEVICE=cpu|cuda`.

## Phase 1 — audit (before code changes)

| Workload | Model | Loaded in | Device logic (before) | Measured device | Throughput | nvidia-smi |
|----------|-------|-----------|----------------------|-----------------|------------|------------|
| Embeddings | Qwen3-VL-Embedding-8B bnb-4bit | `embedding_worker._load_model` | No explicit device | **cuda:0** | 4 imgs · **2.21 img/s** | peak **7464 MiB**, util ≤10% |
| Captions | Qwen2.5-VL-3B-Instruct bnb-4bit | `caption_worker._load_model` | `device_map={"": 0}` | **cuda:0** | 2 sm · **0.043 img/s** | peak **6055 MiB**, util ≤**67%** |
| Faces | insightface buffalo_l | `face_worker._load_face_app` | Hardcoded CPU EP, `ctx_id=-1` | **CPU** | 4 md · **1.20 img/s** | util **0%** |
| Subject mask | rembg u2net.onnx | `ai_masks._subject_session_for_model` | Hardcoded CPU EP | **CPU** | 2 · **0.37 img/s** | util **0%** |
| Sky / pregen | heuristic / thumbs | — | N/A | CPU by design | — | — |

ORT providers in venv: `AzureExecutionProvider`, `CPUExecutionProvider` only.

Receipts: `receipts/gpuaudit-phase1-before.json`, `receipts/gpuaudit-phase1-before-captions.json`.

## Phase 2 — fix

- **`web/core/ml_device.py`**: single policy — prefer CUDA if available AND allowed; `AZIMUTH_ML_DEVICE=cpu|cuda`; ORT providers; InsightFace `ctx_id`; HF `device_map`; `empty_cuda_cache()`.
- Wired: `embedding_worker`, `caption_worker`, `face_worker`, `ai_masks`.
- `memory_pressure.request_model_unload` still drops all three residencies, then `empty_cuda_cache`.
- Lazy load/unload unchanged; no eager import loads; no new deps.

## Before / after (same inputs)

| Workload | Before | After | GPU residency after |
|----------|--------|-------|---------------------|
| Embeddings (4×512) | 2.21 img/s · cuda | **1.87 img/s** · cuda:0 | peak **7683 MiB**, util ≤18% |
| Captions (2 sm) | 0.043 img/s · cuda | **0.049 img/s** · cuda:0 | peak **6053 MiB**, util ≤49% |
| Faces (4 md) | 1.20 img/s · CPU | **1.30 img/s** · CPU EP | util **0%** (no CUDA EP) |
| Subject mask (2) | 0.37 img/s · CPU | **1.25 img/s** · CPU EP* | util **0%** |

\*Mask after is warmer (model already on disk / session path); still CPU — not a GPU win.

Receipt: `receipts/gpuaudit-phase2-after.json`.

## Left on CPU (deliberate)

1. **Faces + subject masks** — helper prefers CUDA, but installed `onnxruntime` has no `CUDAExecutionProvider`. Same CPU behavior as today. Enabling GPU needs `onnxruntime-gpu` (explicitly out of scope).
2. **Sky mask** — position/chroma heuristic, not ML.
3. **Pregen** — decode/resize only; no model load.

## Tests

```text
.venv/bin/python -m pytest -q \
  test_ml_device.py test_optional_ai.py test_memory_pressure.py \
  test_embedding_worker.py test_captions.py test_develop_ai_masks.py
→ 73 passed, exit 0
```

No new skips.

## Env

```bash
AZIMUTH_ML_DEVICE=cpu   # force CPU even if CUDA present
AZIMUTH_ML_DEVICE=cuda  # prefer CUDA; falls back to CPU if unavailable
```
