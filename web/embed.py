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

# The model is a constant of the build, measured in, not a setting: nothing
# else runs on this hardware. Changing it is a new recipe -- the space
# re-fills from the tiles and the old rows are dead weight to delete -- so
# the KEY names repository, revision and width, and the RECIPE is that name
# spelled exactly as `model.cache.canonical` spells it. The live backfill has
# been writing rows under this string since 08-17; a test pins all three
# spellings together.
MODEL = "google/siglip2-so400m-patch14-384"
KEY = "google--siglip2-so400m-patch14-384@main:1152"
RECIPE = '{"model":"' + KEY + '"}'

_lock = threading.Lock()
_loaded: tuple[object, object] | None = None


def kind(tiles):
    """The embedding as a cache capability, composed over the tile store.

    Everything about it is a consequence of one sentence -- **a vector is
    computed from the grid tile** -- and the sentence was measured before it
    was believed: identical model input at 8x less work, and the tile is
    local, so the archive drive may be away. So it *wants* exactly what the
    grid can show, its *source* is the tile file rather than the original,
    it is *here* only where the model runs, and it is never evicted, because
    a vector is hours of GPU to remake and 4.6 KB to keep.
    """

    import render
    from model import cache

    def compute(source, hash, model):
        made = vector(source)
        return cache.Made(value=made.tobytes(), bytes=made.nbytes)

    return cache.Kind(
        name="embedding",
        compute=compute,
        cost=0.15,
        params=("model",),
        ahead=lambda: ({"model": KEY},),
        evictable=False,
        wants=tiles.ready.sql,
        # Late-bound so a harness that stands the model down (a suite, a proof
        # on a card the backfill owns) patches `ready` and the kind follows.
        here=lambda: ready(),
        source=lambda conn, row: tiles.path(row["hash"], render.GRID),
    )


def ready() -> bool:
    """Can this machine make one right now?

    Asked, never remembered — a laptop with the model and a hub without it are
    the same code. False means the work loop simply skips embeddings and
    records nothing, so a machine that *can* embed does not later find a row
    claiming this photograph could not be.

    "Right now" includes the weights being on this disk. The first draft
    answered from the GPU alone, and the cost was measured the same day: a
    machine with a card and no local weights sent every worker lane into a
    2.4 GB download behind the model lock, and identity and tiles starved for
    seven minutes on a fresh library. Fetching weights is work you can watch
    (`fetch()`), never a surprise inside a background lane — so `_model()`
    loads with `local_files_only` and can never touch the network.
    """

    try:
        import torch
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False
    if not torch.cuda.is_available():
        return False
    return isinstance(try_to_load_from_cache(MODEL, "model.safetensors"), str)


def fetch() -> None:
    """Bring the weights to this machine, deliberately and in the open.

    The one place the network is allowed. Everything else reads the local
    cache: run this once on a new machine and `ready()` starts saying yes.
    """

    from huggingface_hub import snapshot_download

    snapshot_download(MODEL)


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

                model = AutoModel.from_pretrained(
                    MODEL, dtype=torch.float16, local_files_only=True
                ).to("cuda").eval()
                _loaded = (model, AutoProcessor.from_pretrained(MODEL, local_files_only=True))
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


def vectors(sources: list[str]):
    """Several photographs at once. Same answer as `vector`, far less overhead.

    The GPU is idle most of a single-image call: decode, one small forward
    pass, copy back. Handing it eight at a time keeps it busy and costs the
    same memory, because a 384px batch of eight is small beside the 2.4 GB of
    weights already resident.

    Returns one row per source, with None where the file would not decode --
    positional, so the caller can still tell which photograph failed.
    """

    import numpy as np
    import torch
    import render

    model, processor = _model()
    images, keep = [], []
    for index, source in enumerate(sources):
        try:
            images.append(render.decode(source, INPUT))
            keep.append(index)
        except Exception:
            continue
    out: list = [None] * len(sources)
    if not images:
        return out
    try:
        with torch.inference_mode():
            batch = processor(images=images, return_tensors="pt").to("cuda", torch.float16)
            got = _tensor(model.get_image_features(**batch))
            got = torch.nn.functional.normalize(got.float(), dim=-1).cpu().numpy()
        for slot, index in enumerate(keep):
            out[index] = got[slot].astype(np.float32)
        return out
    finally:
        for image in images:
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
