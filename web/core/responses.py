import copy


METADATA_FIELDS = (
    "date_taken",
    "date_source",
    "camera_make",
    "camera_model",
    "lens",
    "file_ext",
    "file_size",
    "file_modified_at",
    "width",
    "height",
    "latitude",
    "longitude",
    "created_at",
)

_MISSING = object()


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


def metadata_payload(image: dict) -> dict:
    return {field: _get(image, field) for field in METADATA_FIELDS}


def _rounded_elo(value) -> float:
    return round(_as_float(value, 1200.0), 1)


def image_card(
    image: dict,
    thumb_size: str = "sm",
    *,
    elo_value=_MISSING,
    similarity=_MISSING,
    taste_score=_MISSING,
    date_group=_MISSING,
) -> dict:
    image_id = _as_int(_get(image, "id"))
    card = {
        "id": image_id,
        "filename": _get(image, "filename", ""),
        "elo": _rounded_elo(_get(image, "elo") if elo_value is _MISSING else elo_value),
        "comparisons": _as_int(_get(image, "comparisons")),
        "propagated_updates": _as_int(_get(image, "propagated_updates")),
        "status": _get(image, "status") or "kept",
        "flag": _get(image, "flag") or "unflagged",
        "aspect_ratio": _as_float(_get(image, "aspect_ratio"), 1.5) or 1.5,
        **metadata_payload(image),
        "thumb_url": f"/api/thumb/{thumb_size}/{image_id}",
    }
    if similarity is not _MISSING:
        card["similarity"] = None if similarity is None else round(_as_float(similarity), 4)
    if taste_score is not _MISSING:
        card["taste_score"] = None if taste_score is None else round(_as_float(taste_score), 4)
    if date_group is not _MISSING:
        card["date_group"] = date_group or ""
    return card


def visibility_counts(total_images: int, visible_images: int) -> dict:
    total = max(0, int(total_images or 0))
    visible = max(0, int(visible_images or 0))
    return {
        "visible_images": visible,
        "total_images": total,
        "hidden_pending_thumbnails": max(total - visible, 0),
    }


def interaction_pool_stats(total_images: int, visible_images: int) -> dict:
    total = max(0, int(total_images or 0))
    visible = max(0, int(visible_images or 0))
    return {
        "total_images": total,
        "active_images": total,
        "kept": total,
        "maybe": 0,
        "filtered_pool": visible,
        "filtered_pool_visible": visible,
        "filtered_pool_total": total,
    }


def compare_response_rows(response: dict) -> list[dict]:
    rows = []
    for pair in response.get("pairs") or ():
        left = pair.get("left") if isinstance(pair, dict) else None
        right = pair.get("right") if isinstance(pair, dict) else None
        if isinstance(left, dict):
            rows.append(left)
        if isinstance(right, dict):
            rows.append(right)
    return rows


def copy_dict_of_dicts(value: dict | None) -> dict:
    return {
        key: dict(item) if isinstance(item, dict) else item
        for key, item in (value or {}).items()
    }


def copy_cache_status_response(status: dict) -> dict:
    copied = dict(status)
    if isinstance(status.get("memory"), dict):
        copied["memory"] = dict(status["memory"])
    if isinstance(status.get("disk"), dict):
        disk = dict(status["disk"])
        disk["tiers"] = copy_dict_of_dicts(disk.get("tiers"))
        copied["disk"] = disk
    if isinstance(status.get("thumbnail_config"), dict):
        copied["thumbnail_config"] = dict(status["thumbnail_config"])
    if isinstance(status.get("recommendations"), dict):
        recommendations = dict(status["recommendations"])
        if isinstance(recommendations.get("budget"), dict):
            recommendations["budget"] = dict(recommendations["budget"])
        recommendations["tiers"] = copy_dict_of_dicts(recommendations.get("tiers"))
        copied["recommendations"] = recommendations
    if isinstance(status.get("pregen"), dict):
        pregen = dict(status["pregen"])
        pregen["phases"] = copy_dict_of_dicts(pregen.get("phases"))
        for key in ("preview", "originals"):
            if isinstance(pregen.get(key), dict):
                pregen[key] = dict(pregen[key])
        copied["pregen"] = pregen
    if isinstance(status.get("system_resources"), dict):
        resources = dict(status["system_resources"])
        if isinstance(resources.get("disk"), dict):
            resources["disk"] = dict(resources["disk"])
        if isinstance(resources.get("memory"), dict):
            resources["memory"] = dict(resources["memory"])
        copied["system_resources"] = resources
    return copied


def copy_interaction_response(response: dict) -> dict:
    copied = dict(response)
    if isinstance(response.get("images"), list):
        copied["images"] = [dict(image) for image in response["images"]]
    if isinstance(response.get("pairs"), list):
        copied["pairs"] = [
            {
                "left": dict(pair.get("left") or {}),
                "right": dict(pair.get("right") or {}),
            }
            for pair in response["pairs"]
        ]
    if isinstance(response.get("stats"), dict):
        copied["stats"] = dict(response["stats"])
    return copied


def copy_rankings_response(response: dict) -> dict:
    copied = dict(response)
    copied["images"] = list(response.get("images") or [])
    return copied


def copy_settings_response(response: dict, *, copy_ai_status, copy_cache_status) -> dict:
    copied = dict(response)
    for key in ("settings", "model_status", "catalog", "defaults", "metadata_status"):
        if isinstance(response.get(key), dict):
            copied[key] = copy.deepcopy(response[key])
    if isinstance(response.get("ai_status"), dict):
        copied["ai_status"] = copy_ai_status(response["ai_status"])
    if isinstance(response.get("people_status"), dict):
        copied["people_status"] = copy.deepcopy(response["people_status"])
    if isinstance(response.get("cache_stats"), dict):
        copied["cache_stats"] = copy_cache_status(response["cache_stats"])
    if isinstance(response.get("catalog"), dict):
        catalog = dict(response["catalog"])
        catalog["sources"] = [dict(source) for source in catalog.get("sources") or []]
        if isinstance(catalog.get("stats"), dict):
            catalog["stats"] = dict(catalog["stats"])
        copied["catalog"] = catalog
    if isinstance(response.get("cache_profiles"), list):
        copied["cache_profiles"] = list(response["cache_profiles"])
    if isinstance(response.get("embedding_model_presets"), list):
        copied["embedding_model_presets"] = [
            dict(preset) for preset in response["embedding_model_presets"]
        ]
    return copied


def copy_ai_status_response(response: dict) -> dict:
    copied = dict(response)
    if isinstance(response.get("last_batch_stage_seconds"), dict):
        copied["last_batch_stage_seconds"] = dict(response["last_batch_stage_seconds"])
    if isinstance(response.get("embedding_indexes"), dict):
        copied["embedding_indexes"] = copy.deepcopy(response["embedding_indexes"])
    if isinstance(response.get("embedding_index"), dict):
        copied["embedding_index"] = dict(response["embedding_index"])
    return copied
