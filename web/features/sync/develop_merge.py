"""Shared rules for merging synchronized develop settings."""

import json


def preserve_local_rating(incoming_settings: dict, current_settings_json: str | None) -> dict:
    """Return incoming develop settings without overwriting a local rating."""

    settings = dict(incoming_settings)
    try:
        current_settings = json.loads(current_settings_json)
    except (TypeError, ValueError):
        return settings
    if isinstance(current_settings, dict) and "_lr_rating" in current_settings:
        settings["_lr_rating"] = current_settings["_lr_rating"]
    return settings
