import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import helpers as app_helpers
import photo_metadata
from features.search.similarity import scan_duplicate_pairs
from features.sync import satellite


router = APIRouter()
log = logging.getLogger(__name__)
ApiRankings = Callable[..., Awaitable[dict]]
VisibleEmbeddingPage = Callable[..., Awaitable[tuple[list[dict], int, int]]]
CachedImageIds = Callable[[list[int], str], Awaitable[set[int]]]
MetadataUpdateTuple = Callable[[int, dict], tuple]
InvalidatePairing = Callable[..., None]
NormalizeSearchQuery = Callable[[str], str]
ClampInt = Callable[[object, int, int, int], int]
VisibilityCounts = Callable[[int, int], dict]
ActiveEmbeddingModelKey = Callable[[], str]
DbSignature = Callable[[], str]
GetActiveSourceIdSet = Callable[[], Awaitable[frozenset[int]]]
GetActiveImagesByIds = Callable[[list[int]], Awaitable[dict[int, dict]]]
GetImageById = Callable[[int], Awaitable[dict | None]]
BatchUpdateMetadata = Callable[[list[tuple]], Awaitable[None]]

_api_rankings: ApiRankings | None = None
_visible_embedding_page: VisibleEmbeddingPage | None = None
_cached_image_ids: CachedImageIds | None = None
_metadata_update_tuple: MetadataUpdateTuple | None = None
_invalidate_pairing_cache: InvalidatePairing | None = None
_normalize_search_query: NormalizeSearchQuery | None = None
_clamp_int: ClampInt | None = None
_visibility_counts: VisibilityCounts | None = None
_active_embedding_model_key: ActiveEmbeddingModelKey | None = None
_db_signature: DbSignature | None = None
_get_active_source_id_set: GetActiveSourceIdSet | None = None
_get_active_images_by_ids: GetActiveImagesByIds | None = None
_get_image_by_id: GetImageById | None = None
_batch_update_metadata: BatchUpdateMetadata | None = None
_duplicates_cache: dict | None = None
_exif_cache: dict[int, dict] = {}
_EXIF_CACHE_MAX = 2000


def configure(
    *,
    api_rankings: ApiRankings,
    visible_embedding_page: VisibleEmbeddingPage,
    cached_image_ids: CachedImageIds,
    metadata_update_tuple: MetadataUpdateTuple,
    invalidate_pairing_cache: InvalidatePairing,
    normalize_search_query: NormalizeSearchQuery,
    clamp_int: ClampInt,
    visibility_counts: VisibilityCounts,
    active_embedding_model_key: ActiveEmbeddingModelKey,
    db_signature: DbSignature,
    get_active_source_id_set: GetActiveSourceIdSet,
    get_active_images_by_ids: GetActiveImagesByIds,
    get_image_by_id: GetImageById,
    batch_update_metadata: BatchUpdateMetadata,
    duplicates_cache: dict,
) -> None:
    global _api_rankings, _visible_embedding_page, _cached_image_ids, _metadata_update_tuple
    global _invalidate_pairing_cache, _normalize_search_query, _clamp_int, _visibility_counts
    global _active_embedding_model_key, _db_signature, _get_active_source_id_set
    global _get_active_images_by_ids, _get_image_by_id, _batch_update_metadata
    global _duplicates_cache
    _api_rankings = api_rankings
    _visible_embedding_page = visible_embedding_page
    _cached_image_ids = cached_image_ids
    _metadata_update_tuple = metadata_update_tuple
    _invalidate_pairing_cache = invalidate_pairing_cache
    _normalize_search_query = normalize_search_query
    _clamp_int = clamp_int
    _visibility_counts = visibility_counts
    _active_embedding_model_key = active_embedding_model_key
    _db_signature = db_signature
    _get_active_source_id_set = get_active_source_id_set
    _get_active_images_by_ids = get_active_images_by_ids
    _get_image_by_id = get_image_by_id
    _batch_update_metadata = batch_update_metadata
    _duplicates_cache = duplicates_cache


def _configured() -> None:
    values = (
        _api_rankings,
        _visible_embedding_page,
        _cached_image_ids,
        _metadata_update_tuple,
        _invalidate_pairing_cache,
        _normalize_search_query,
        _clamp_int,
        _visibility_counts,
        _active_embedding_model_key,
        _db_signature,
        _get_active_source_id_set,
        _get_active_images_by_ids,
        _get_image_by_id,
        _batch_update_metadata,
        _duplicates_cache,
    )
    if any(value is None for value in values):
        raise RuntimeError("Search routes are not configured")


@router.get("/api/search")
async def api_search(
    q: str = "", limit: int = 50, deep: bool = False, people: str = "",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "",
):
    """Search images by text query using embedding similarity."""
    _configured()
    query = _normalize_search_query(q)
    if not query:
        return {
            "images": [],
            "query": query,
            "search_mode": "",
            "search_sources": [],
            "ai_unavailable": False,
            "fallback_reason": "",
            **_visibility_counts(0, 0),
        }
    limit = _clamp_int(limit, 50, 1, 500)
    response = await _api_rankings(
        limit=limit,
        offset=0,
        sort="similarity",
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        q=query,
        deep=deep,
        people=people,
    )
    if isinstance(response, dict):
        response["query"] = query
        for image in response.get("images") or []:
            if isinstance(image, dict):
                image.setdefault("similarity", None)
    return response


def _matvec(matrix, vec):
    return matrix @ vec


@router.get("/api/similar/{image_id}")
async def api_similar(image_id: int, limit: int = 50):
    """Find visually similar images using embedding cosine similarity."""
    _configured()
    limit = max(1, min(int(limit), 500))
    try:
        import embed_cache
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None:
        return {"images": [], "source_id": image_id, **_visibility_counts(0, 0)}

    source_vec = embed_cache.get_vector(image_id)
    if source_vec is None:
        return JSONResponse({"error": "Image not embedded yet"}, status_code=404)

    # The matvec is CPU-bound numpy work; keep it off the event loop.
    similarities = await asyncio.to_thread(_matvec, matrix, source_vec)
    visible_rows, visible_images, total_images = await _visible_embedding_page(
        image_ids,
        similarities,
        limit,
        "" if satellite.is_satellite_mode() else "sm",
        exclude_id=image_id,
        model_key=_active_embedding_model_key(),
    )
    results = []
    id_to_idx = embed_cache.get_index()
    for img in visible_rows:
        img_id = int(img["id"])
        idx = id_to_idx.get(img_id)
        score = float(similarities[idx]) if idx is not None else 0.0
        card = app_helpers.image_card(img, "sm", similarity=score)
        card["preview_ready"] = True
        results.append(card)
    if satellite.is_satellite_mode() and results:
        cached_ids = await _cached_image_ids([int(result["id"]) for result in results], "sm")
        for result in results:
            ready = int(result["id"]) in cached_ids
            result["preview_ready"] = ready
            if not ready:
                result.pop("thumb_url", None)

    return {
        "images": results,
        "source_id": image_id,
        **_visibility_counts(total_images, visible_images),
    }


@router.get("/api/duplicates")
async def api_duplicates(threshold: float = 0.95, limit: int = 100):
    """Find near-duplicate image pairs using embedding similarity."""
    _configured()
    if not await _get_active_source_id_set():
        return {"pairs": [], "visible_pairs": 0, "total_pairs": 0, "hidden_pending_thumbnails": 0}
    try:
        import embed_cache
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)

    image_ids, matrix = await embed_cache.get_matrix()
    if image_ids is None or len(image_ids) < 2:
        return {"pairs": [], "visible_pairs": 0, "total_pairs": 0, "hidden_pending_thumbnails": 0}

    batch_size = 500
    n = len(image_ids)
    cached_sm_ids = await _cached_image_ids([int(image_id) for image_id in image_ids], "sm")
    cache_key = (
        _db_signature(),
        round(float(threshold), 4),
        int(limit),
        len(image_ids),
        len(cached_sm_ids),
    )
    if _duplicates_cache["key"] == cache_key and _duplicates_cache["data"] is not None:
        return json.loads(_duplicates_cache["data"])

    # The pairwise similarity sweep is O(n^2) CPU/numpy work that can take
    # seconds on a large archive; run it off the event loop.
    pairs, total_pairs, hidden_pairs = await asyncio.to_thread(
        scan_duplicate_pairs,
        matrix,
        image_ids,
        cached_sm_ids,
        threshold=threshold,
        batch_size=batch_size,
        limit=limit,
        n=n,
    )

    all_ids = list({p[0] for p in pairs} | {p[1] for p in pairs})
    images = await _get_active_images_by_ids(all_ids) if all_ids else {}

    result = []
    for id_a, id_b, sim in pairs[:limit]:
        a, b = images.get(id_a), images.get(id_b)
        if not a or not b:
            continue
        result.append({
            "similarity": round(sim, 4),
            "a": {"id": id_a, "filename": a["filename"], "elo": round(a["elo"], 1), "thumb_url": f"/api/thumb/sm/{id_a}"},
            "b": {"id": id_b, "filename": b["filename"], "elo": round(b["elo"], 1), "thumb_url": f"/api/thumb/sm/{id_b}"},
        })

    response = {
        "pairs": result,
        "visible_pairs": len(result),
        "total_pairs": total_pairs,
        "hidden_pending_thumbnails": hidden_pairs,
    }
    _duplicates_cache["key"] = cache_key
    _duplicates_cache["data"] = json.dumps(response, separators=(",", ":"))
    return response


@router.get("/api/image/{image_id}/exif")
async def api_exif(image_id: int):
    """Extract EXIF metadata from an image (cached per image)."""
    _configured()
    if image_id in _exif_cache:
        return _exif_cache[image_id]
    image = await _get_image_by_id(image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    try:
        # Full EXIF parsing reads the original file (possibly a large RAW on
        # a slow disk); keep it off the event loop.
        exif = await asyncio.to_thread(photo_metadata.extract_image_metadata, image["filepath"])
    except Exception:
        log.exception("worker=exif_reader image_id=%s extraction failed", image_id)
        exif = {}

    row = dict(image)
    for key in (
        "date_taken", "date_source", "camera_make", "camera_model", "lens", "file_ext",
        "file_size", "file_modified_at", "latitude", "longitude",
    ):
        if not exif.get(key) and row.get(key) is not None:
            exif[key] = row.get(key)
    if not exif.get("dimensions") and row.get("width") and row.get("height"):
        exif["dimensions"] = f"{row['width']} x {row['height']}"
    if not exif.get("filepath"):
        exif["filepath"] = image["filepath"]

    try:
        await _batch_update_metadata([_metadata_update_tuple(image_id, exif)])
        _invalidate_pairing_cache()
    except Exception:
        log.exception("worker=exif_backfill image_id=%s metadata update failed", image_id)

    result = {"exif": exif}
    _exif_cache[image_id] = result
    if len(_exif_cache) > _EXIF_CACHE_MAX:
        to_remove = list(_exif_cache.keys())[:_EXIF_CACHE_MAX // 2]
        for key in to_remove:
            del _exif_cache[key]
    return result
