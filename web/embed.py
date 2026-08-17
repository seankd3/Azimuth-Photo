"""Turn a photograph into a vector. One model, loaded once.

This is the half of search and ranking that costs hours, and everything about
it was decided by measurement on this machine rather than by reputation:

* **SigLIP-2 so400m.** Not a compromise — the only candidate that runs. It
  needs 2,352 MB of the card's 4,096 and does 6.6 img/s. Qwen3-VL-Embedding-2B
  at fp16 needs 3,901 MB, makes no forward progress even at batch 1 with inputs
  capped to 384x384, and the 8B will not load at all. The CPU is not a tier:
  20x slower for the *smallest* candidate, 2.9 s for a single query.
* **It is not a worse space for ranking, either.** Against the 8B's own 42,937
  stored vectors on identical photographs, SigLIP-2 predicts the owner's Elo
  slightly better (+0.714 against +0.687) at a third of the dimensions.
* **Embed the preview, not the original.** Identical model input, 8x less
  work: 4.8 hours for the library against 37.7. It also means the backfill does
  not need the archive drive plugged in, because previews live on the laptop.
* **The pooled vector, not the patch grid.** Reading the attention-pooled
  output beats averaging the same tokens (+0.555 against +0.485 on ranking
  like-for-like) and beats quadrant pooling. The pooling head is a learned
  summary, not a flattening.
"""

from __future__ import annotations

import threading

# 384 is the model's own input size. Decoding larger and letting the processor
# shrink it costs time and changes nothing it sees.
INPUT = 384

_lock = threading.Lock()
_loaded: tuple[object, object] | None = None


def ready() -> bool:
    """Can this machine make one right now?

    Asked, never remembered — a laptop with the model and a hub without it are
    the same code. False means the work loop simply skips embeddings and
    records nothing, so a machine that *can* embed does not later find a row
    claiming this photograph could not be.
    """

    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available())


def _model():
    """The model and its processor, loaded once per process.

    Loading costs 10-17 s and 2.4 GB. Doing it per photograph would make the
    backfill a week, so it is held for the life of the process and guarded
    because the work loop has lanes.
    """

    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                import torch
                from transformers import AutoModel, AutoProcessor
                import settings

                repo = settings.active_embedding_config()["model_id"]
                model = AutoModel.from_pretrained(repo, dtype=torch.float16).to("cuda").eval()
                _loaded = (model, AutoProcessor.from_pretrained(repo))
    return _loaded


def _tensor(out):
    """The vectors, whatever wrapper this version of transformers returned.

    `get_image_features` has returned a bare tensor, a `BaseModelOutputWithPooling`
    and a plain tuple across the versions this has run on, and the failure mode
    is `'tuple' object has no attribute 'float'` at the first photograph — late,
    loud, and only after the model has finished loading.
    """

    import torch

    if torch.is_tensor(out):
        return out
    if isinstance(out, (tuple, list)):
        for item in out:
            if torch.is_tensor(item) and item.dim() == 2:
                return item
    for field in ("pooler_output", "image_embeds", "text_embeds", "embeddings"):
        value = getattr(out, field, None)
        if torch.is_tensor(value):
            return value
    hidden = getattr(out, "last_hidden_state", None)
    if torch.is_tensor(hidden):
        return hidden.mean(dim=1)
    raise TypeError(f"no vector in {type(out).__name__}")


def vector(source: str):
    """One photograph as a unit vector, ready to store.

    Normalised here rather than at read time so every consumer — search,
    ranking, clustering, dedup — compares by dot product without having to
    remember to. A vector that is not unit length is a bug that only shows up
    as slightly wrong ordering, which is the kind that survives for months.
    """

    import numpy as np
    import torch
    import render

    model, processor = _model()
    image = render.decode(source, INPUT)
    try:
        with torch.inference_mode():
            batch = processor(images=[image], return_tensors="pt").to("cuda", torch.float16)
            out = _tensor(model.get_image_features(**batch))
            out = torch.nn.functional.normalize(out.float(), dim=-1)
        return out[0].cpu().numpy().astype(np.float32)
    finally:
        image.close()


def text(query: str):
    """A query in the same space, so a dot product means something.

    The text tower is the cheap half — one short forward pass — which is why
    typed search can afford to embed the query on every keystroke's commit
    while the image side is an overnight job.
    """

    import numpy as np
    import torch

    model, processor = _model()
    with torch.inference_mode():
        batch = processor(text=[query], padding="max_length", return_tensors="pt").to("cuda")
        out = model.get_text_features(**batch)
        out = _tensor(out)
        out = torch.nn.functional.normalize(out.float(), dim=-1)
    return out[0].cpu().numpy().astype(np.float32)
