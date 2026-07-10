from collections.abc import Awaitable, Callable

from core.responses import (
    METADATA_FIELDS,
    image_card,
    metadata_payload,
)
from data.repositories import rankings as ranking_repository


CachedImageIdsProvider = Callable[[list[int], str, str], Awaitable[set[int]]]
_cached_image_ids_provider: CachedImageIdsProvider | None = None
_star_thresholds: dict[int, int] = ranking_repository.STAR_THRESHOLDS


def configure(
    *,
    cached_image_ids: CachedImageIdsProvider | None = None,
    star_thresholds: dict[int, int] | None = None,
) -> None:
    global _cached_image_ids_provider, _star_thresholds
    if cached_image_ids is not None:
        _cached_image_ids_provider = cached_image_ids
    if star_thresholds is not None:
        _star_thresholds = star_thresholds


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"helpers is missing configured dependency: {name}")
    return provider


def _get(image, key: str, default=None):
    if hasattr(image, "get"):
        return image.get(key, default)
    try:
        return image[key]
    except (KeyError, IndexError, TypeError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ranking_signal_count(image: dict) -> int:
    return _as_int(_get(image, "comparisons")) + _as_int(_get(image, "propagated_updates"))


def has_ranking_signal(image: dict) -> bool:
    return (
        ranking_signal_count(image) > 0
        or abs(_as_float(_get(image, "elo"), 1200.0) - 1200.0) > 0.0001
    )


def date_group_for_image(image: dict) -> str:
    date_taken = str(_get(image, "date_taken") or "")
    return date_taken[:7] if len(date_taken) >= 7 else ""


def camera_label(image: dict) -> str:
    return " ".join(
        str(part).strip()
        for part in (_get(image, "camera_make"), _get(image, "camera_model"))
        if part
    ).strip()


def filter_by_metadata(
    images: list[dict],
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
) -> list[dict]:
    if date_taken:
        if date_taken == "undated":
            images = [img for img in images if not img.get("date_taken")]
        else:
            date_range = ranking_repository.date_taken_filter_range(date_taken)
            if date_range is not None:
                start, end = date_range
                images = [
                    img for img in images
                    if start <= str(img.get("date_taken") or "") < end
                ]

    if file_type:
        normalized_type = file_type.lower().lstrip(".")
        images = [
            img for img in images
            if (img.get("file_ext") or "").lower().lstrip(".") == normalized_type
        ]

    if camera:
        images = [img for img in images if camera_label(img) == camera]

    if lens:
        images = [img for img in images if (img.get("lens") or "") == lens]

    return images


def filter_compare_mosaic_candidates(
    images,
    *,
    exclude_ids: set[int] | None = None,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
) -> list[dict]:
    exclude_ids = exclude_ids or set()
    candidates = [dict(img) for img in images if _as_int(_get(img, "id")) not in exclude_ids]

    if orientation in ("landscape", "portrait"):
        candidates = [c for c in candidates if c.get("orientation") == orientation]
    if compared == "compared":
        candidates = [c for c in candidates if has_ranking_signal(c)]
    elif compared == "uncompared":
        candidates = [c for c in candidates if not has_ranking_signal(c)]
    elif compared == "confident":
        candidates = [c for c in candidates if _as_int(c.get("comparisons")) >= 10]
    if min_stars > 0:
        threshold = _star_thresholds.get(min_stars, 0)
        candidates = [c for c in candidates if _as_float(c.get("elo"), 1200.0) >= threshold]
    if folder:
        candidates = [c for c in candidates if f"/{folder}/" in c.get("filepath", "")]
    if flag in ("picked", "unflagged", "rejected"):
        candidates = [c for c in candidates if (c.get("flag") or "unflagged") == flag]

    return filter_by_metadata(candidates, date_taken, file_type, camera, lens)


def _unique_int_ids(values) -> list[int]:
    ids = []
    seen = set()
    for value in values:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        ids.append(normalized)
    return ids


async def cached_image_ids(image_ids, size: str, cache_root: str) -> set[int]:
    ids = _unique_int_ids(image_ids)
    return await _configured(_cached_image_ids_provider, "cached_image_ids")(ids, size, cache_root)


async def filter_visible_candidates(candidates: list[dict], size: str, cache_root: str) -> list[dict]:
    if not candidates:
        return []
    cached_ids = await cached_image_ids([c.get("id") for c in candidates], size, cache_root)
    return [c for c in candidates if _as_int(c.get("id")) in cached_ids]
