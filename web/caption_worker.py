"""Background VLM caption worker for cached previews."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import ai_models
import settings
from core import work_coordination


log = logging.getLogger("caption_worker")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler())

WORKER_SLEEP_SECONDS = 20
MODEL_LOAD_FAILURE_RETRY_SECONDS = 300
MODEL_LOAD_FAILURE_PAUSE_THRESHOLD = 3
CAPTION_PROMPT = (
    "Describe this photo for private photo-library search. Return only JSON with "
    "keys caption and tags. caption must be 2-3 rich sentences mentioning "
    "subjects, scene, setting, lighting, mood, notable objects/actions, and "
    "photographic style. tags must be 8-15 lowercase strings covering subjects, "
    "scene, style, lighting, and colors."
)

_caption_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption-gpu")
_model = None
_processor = None
_loaded_key: tuple[str, str, str, str] | None = None
_caption_manual_pause = True
_caption_manual_pause_message = "Captions are stopped until you start them from Background Work."
_model_load_failure_count = 0
_status = {
    "state": "idle",
    "message": "Captions have not scanned cached previews yet.",
    "ready": False,
    "running": False,
    "manual_pause": True,
    "model_id": "",
    "model_key": "",
    "model_dir": "",
    "quantization": "",
    "prompt_version": "",
    "last_error": "",
    "last_batch_size": 0,
    "last_batch_seconds": 0.0,
    "last_captioned_at": None,
    "pending_cached_images": 0,
    "session_captioned": 0,
    "session_started_at": None,
    "oom_backoffs": 0,
    "model_load_failures": 0,
    "source_files_preserved": True,
    "source_media_read": "app_owned_cached_previews_only",
}

AsyncDictProvider = Callable[..., Awaitable[dict[str, Any]]]
AsyncIntProvider = Callable[..., Awaitable[int]]
AsyncListProvider = Callable[..., Awaitable[list[dict[str, Any]]]]
AsyncNoneProvider = Callable[..., Awaitable[None]]
_count_images_needing_captions: AsyncIntProvider | None = None
_get_images_needing_captions: AsyncListProvider | None = None
_store_caption_result: AsyncNoneProvider | None = None


def configure(
    *,
    count_images_needing_captions: AsyncIntProvider | None = None,
    get_images_needing_captions: AsyncListProvider | None = None,
    store_caption_result: AsyncNoneProvider | None = None,
) -> None:
    global _count_images_needing_captions, _get_images_needing_captions, _store_caption_result
    if count_images_needing_captions is not None:
        _count_images_needing_captions = count_images_needing_captions
    if get_images_needing_captions is not None:
        _get_images_needing_captions = get_images_needing_captions
    if store_caption_result is not None:
        _store_caption_result = store_caption_result


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"caption_worker is missing configured dependency: {name}")
    return provider


def _set_status(**updates: Any) -> None:
    _status.update(updates)
    _status["manual_pause"] = _caption_manual_pause


def get_worker_status() -> dict[str, Any]:
    _status["manual_pause"] = _caption_manual_pause
    return dict(_status)


def manual_pause_active() -> bool:
    return _caption_manual_pause


def pause_caption_worker(message: str = "Captions are stopped.") -> dict[str, Any]:
    global _caption_manual_pause, _caption_manual_pause_message
    _caption_manual_pause = True
    _caption_manual_pause_message = message
    work_coordination.release_manual_owner("captions")
    _unload_model()
    _set_status(state="paused", ready=False, message=message)
    return get_worker_status()


def resume_caption_worker() -> dict[str, Any]:
    global _caption_manual_pause, _caption_manual_pause_message, _model_load_failure_count
    _caption_manual_pause = False
    _caption_manual_pause_message = ""
    _model_load_failure_count = 0
    work_coordination.claim_manual_owner("captions")
    _set_status(state="idle", message="Captions will scan cached previews.", model_load_failures=0)
    return get_worker_status()


def _reset_model_load_failures() -> None:
    global _model_load_failure_count
    if _model_load_failure_count:
        _model_load_failure_count = 0
        _set_status(model_load_failures=0)


def _record_model_load_failure(error: Exception) -> bool:
    global _model_load_failure_count
    _model_load_failure_count += 1
    _set_status(model_load_failures=_model_load_failure_count, last_error=str(error))
    if _model_load_failure_count < MODEL_LOAD_FAILURE_PAUSE_THRESHOLD:
        return False
    pause_caption_worker(
        "Captions paused after 3 consecutive model load failures. Check the local caption model and start again."
    )
    _set_status(model_load_failures=_model_load_failure_count, last_error=str(error))
    return True


def _is_cuda_oom_error(error) -> bool:
    name = type(error).__name__.lower()
    text = str(error).lower()
    return (
        "outofmemoryerror" in name
        or "cuda out of memory" in text
        or ("cuda" in text and "out of memory" in text)
    )


def _clear_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _unload_model() -> None:
    global _model, _processor, _loaded_key
    _model = None
    _processor = None
    _loaded_key = None
    _clear_cuda_cache()
    work_coordination.release_gpu_owner("captions")


def _load_model(config: dict[str, Any]):
    global _model, _processor, _loaded_key
    key = (
        str(config["model_dir"]),
        str(config["model_id"]),
        str(config.get("revision") or "main"),
        str(config.get("quantization") or ""),
    )
    if _model is not None and _processor is not None and _loaded_key == key:
        return _model, _processor

    from transformers import AutoProcessor, BitsAndBytesConfig

    try:
        from transformers import AutoModelForImageTextToText as AutoCaptionModel
    except ImportError:
        from transformers import AutoModelForVision2Seq as AutoCaptionModel

    kwargs = {
        "local_files_only": True,
        "revision": config.get("revision") or "main",
        "device_map": "auto",
        "trust_remote_code": True,
    }
    if str(config.get("quantization") or "") == "bnb-4bit":
        import torch

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        # Force a whole-model GPU load: accelerate's CPU-offload path crashes
        # on this transformers+bitsandbytes pairing, and a clean CUDA OOM is
        # handled by the worker's backoff. The 4-bit model fits when the card
        # isn't shared with another loaded model.
        kwargs["device_map"] = {"": 0}
    _processor = AutoProcessor.from_pretrained(
        config["model_dir"],
        local_files_only=True,
        revision=config.get("revision") or "main",
        trust_remote_code=True,
    )
    _model = AutoCaptionModel.from_pretrained(config["model_dir"], **kwargs)
    _loaded_key = key
    return _model, _processor


def parse_caption_response(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE).strip()
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        caption = str(data.get("caption") or "").strip()
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        tags = [str(tag or "").strip().lower() for tag in tags if str(tag or "").strip()]
        return {"caption": caption or raw, "tags": tags[:15], "raw": raw, "parsed": True}
    return {"caption": raw, "tags": [], "raw": raw, "parsed": False}


def _caption_cached_preview(cache_path: str, config: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image, ImageOps

    model, processor = _load_model(config)
    with Image.open(cache_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        # Cap visual tokens: Qwen-VL attention memory scales with input pixels,
        # and an uncapped md preview can demand >9 GiB on an 8GB card.
        image.thumbnail((1280, 1280))
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": CAPTION_PROMPT},
                ],
            }
        ]
        prompt = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        try:
            from qwen_vl_utils import process_vision_info

            image_inputs, video_inputs = process_vision_info(messages)
            inputs = processor(
                text=[prompt],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )
        except Exception:
            inputs = processor(text=[prompt], images=[image], padding=True, return_tensors="pt")
        device = getattr(model, "device", None)
        if device is not None:
            inputs = inputs.to(device)
        output_ids = model.generate(**inputs, max_new_tokens=384, do_sample=False, temperature=0.0)
        generated = output_ids[:, inputs.input_ids.shape[1]:]
        text = processor.batch_decode(
            generated,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
    return parse_caption_response(text)


async def run_caption_worker() -> None:
    _set_status(running=True, session_started_at=time.time())
    batch_size = 1
    while True:
        started = time.perf_counter()
        captioned = 0
        try:
            app_config = settings.get_settings()
            caption_config = settings.active_caption_config(app_config)
            _set_status(
                model_id=caption_config["model_id"],
                model_key=caption_config["model_key"],
                model_dir=caption_config["model_dir"],
                quantization=caption_config["quantization"],
                prompt_version=caption_config["prompt_version"],
            )
            if _caption_manual_pause or not bool(app_config.get("caption_scan_enabled", False)):
                _unload_model()
                _set_status(
                    state="paused",
                    ready=False,
                    message=_caption_manual_pause_message or "Captions are stopped.",
                    last_error="",
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            if not ai_models.model_files_present(caption_config["model_dir"]):
                _unload_model()
                _set_status(
                    state="waiting_for_model",
                    ready=False,
                    message=f"Install {caption_config['model_id']} before caption scanning.",
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            pending = await _configured(
                _count_images_needing_captions,
                "count_images_needing_captions",
            )(caption_config=caption_config, cache_root=str(app_config.get("ssd_cache_dir") or ""))
            _set_status(pending_cached_images=pending, last_error="")
            if pending <= 0:
                work_coordination.release_manual_owner("captions")
                _unload_model()
                _set_status(
                    state="idle",
                    ready=True,
                    message="Captions are caught up on cached previews.",
                    last_batch_size=0,
                    last_batch_seconds=0.0,
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            batch_size = max(1, min(int(caption_config.get("batch_size") or 1), batch_size, 4))
            rows = await _configured(
                _get_images_needing_captions,
                "get_images_needing_captions",
            )(
                caption_config=caption_config,
                cache_root=str(app_config.get("ssd_cache_dir") or ""),
                cache_size="md",
                limit=batch_size,
            )
            if not rows:
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            await work_coordination.wait_for_gpu_turn("captions")
            await work_coordination.wait_for_manual_turn("captions")
            loop = asyncio.get_running_loop()
            _set_status(
                state="captioning",
                ready=True,
                message=f"Captioning {len(rows)} cached previews.",
            )
            with work_coordination.manual_bulk("captions"):
                try:
                    await loop.run_in_executor(_caption_executor, _load_model, caption_config)
                    _reset_model_load_failures()
                except Exception as exc:
                    if _is_cuda_oom_error(exc):
                        batch_size = max(1, batch_size // 2)
                        _set_status(oom_backoffs=int(_status.get("oom_backoffs") or 0) + 1)
                    _unload_model()
                    paused = _record_model_load_failure(exc)
                    if paused:
                        log.error("Caption worker paused after repeated model load failures: %s", exc, exc_info=True)
                        await asyncio.sleep(WORKER_SLEEP_SECONDS)
                    else:
                        _set_status(
                            state="error",
                            ready=False,
                            message=(
                                "Caption model load failed "
                                f"({_model_load_failure_count}/{MODEL_LOAD_FAILURE_PAUSE_THRESHOLD})."
                            ),
                            last_batch_seconds=round(time.perf_counter() - started, 3),
                        )
                        log.error("Caption model load failed: %s", exc, exc_info=True)
                        await asyncio.sleep(MODEL_LOAD_FAILURE_RETRY_SECONDS)
                    continue
                for row in rows:
                    image_id = int(row["id"])
                    cache_path = str(row.get("cache_path") or "")
                    try:
                        parsed = await loop.run_in_executor(
                            _caption_executor,
                            _caption_cached_preview,
                            cache_path,
                            caption_config,
                        )
                        await _configured(_store_caption_result, "store_caption_result")(
                            image_id=image_id,
                            caption_config=caption_config,
                            caption=parsed["caption"],
                            tags=parsed["tags"],
                            quality="parsed" if parsed.get("parsed") else "raw_fallback",
                            status="done",
                        )
                        captioned += 1
                    except Exception as exc:
                        if _is_cuda_oom_error(exc):
                            batch_size = max(1, batch_size // 2)
                            _set_status(oom_backoffs=int(_status.get("oom_backoffs") or 0) + 1)
                            _clear_cuda_cache()
                        await _configured(_store_caption_result, "store_caption_result")(
                            image_id=image_id,
                            caption_config=caption_config,
                            caption="",
                            tags=[],
                            status="error",
                            error=str(exc),
                        )
                        _set_status(last_error=str(exc))
            elapsed = round(time.perf_counter() - started, 3)
            _set_status(
                state="ready",
                ready=True,
                message=f"Captioned {captioned} cached previews; {max(0, pending - captioned)} queued.",
                last_batch_size=captioned,
                last_batch_seconds=elapsed,
                last_captioned_at=time.time() if captioned else _status.get("last_captioned_at"),
                session_captioned=int(_status.get("session_captioned") or 0) + captioned,
            )
        except Exception as exc:
            _set_status(
                state="error",
                ready=False,
                message="Captions are unavailable.",
                last_error=str(exc),
                last_batch_seconds=round(time.perf_counter() - started, 3),
            )
            log.error("Caption worker error: %s", exc, exc_info=True)
            await asyncio.sleep(MODEL_LOAD_FAILURE_RETRY_SECONDS)
