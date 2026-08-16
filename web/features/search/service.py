"""Search feature vector helpers and response caches."""

from core.catalog_path import catalog_path
import heapq
import tiles

from data.repositories import cache_entries as cache_entry_repository
from data.repositories import images as image_repository


_duplicates_cache = {"key": None, "data": None}




def _configured_cache_root() -> str:
    return tiles.CACHE_DIR



async def visible_embedding_page(
    image_ids,
    similarities,
    limit: int,
    size: str = "sm",
    *,
    exclude_id: int | None = None,
    model_key: str | None = None,
) -> tuple[list[dict], int, int]:
    db_path = catalog_path()
    candidate_ids = {int(image_id) for image_id in image_ids}
    if exclude_id is not None:
        candidate_ids.discard(int(exclude_id))
    if size:
        # Targeted visibility for the embedding candidates only — do not load the
        # whole ~87k cached-thumbnail set to intersect (see perf: search fix).
        candidate_ids &= await cache_entry_repository.cached_image_ids(
            db_path,
            list(candidate_ids),
            size,
            _configured_cache_root(),
        )
    if not candidate_ids:
        total = max(0, len(image_ids) - (1 if exclude_id is not None else 0))
        return [], 0, total

    id_to_idx = {}
    try:
        import embed_cache
        id_to_idx = embed_cache.get_index(model_key)
    except Exception:
        id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}
    if not id_to_idx:
        id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}

    visible_pairs = []
    for image_id in candidate_ids:
        image_id = int(image_id)
        if exclude_id is not None and image_id == exclude_id:
            continue
        idx = id_to_idx.get(image_id)
        if idx is None:
            continue
        visible_pairs.append((image_id, float(similarities[idx])))

    visible_count = len(visible_pairs)
    if len(visible_pairs) > limit:
        visible_pairs = heapq.nlargest(limit, visible_pairs, key=lambda item: item[1])
    else:
        visible_pairs.sort(key=lambda item: item[1], reverse=True)
    selected_ids = [image_id for image_id, _score in visible_pairs[:limit]]

    rows_by_id = await image_repository.get_active_images_by_ids(db_path, selected_ids)
    visible_rows = []
    for image_id in selected_ids:
        row = rows_by_id.get(image_id)
        if row is not None:
            visible_rows.append(row)
    total = max(0, len(image_ids) - (1 if exclude_id is not None else 0))
    return visible_rows, visible_count, total
