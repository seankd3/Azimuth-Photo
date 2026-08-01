"""Shared rules for merging synchronized develop settings."""

import json

# Lightroom writes its camera-profile lookup tables into a photo's develop
# settings as `Table_<digest>` entries. They describe a profile, not a photo,
# so the same twenty-five kilobytes repeat across every photo shot on that
# profile — and this app's render path ignores them outright (see
# features/develop/looks.py: "LookTable/RGBTable data is intentionally retained
# but ignored"). The hub keeps them so an XMP round-trip stays faithful; a
# satellite has no use for them and should not pay to receive them.
_PROFILE_TABLE_PREFIX = "Table_"


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


def without_profile_tables(settings: dict | None) -> dict:
    """Drop the profile lookup tables a satellite never renders from."""

    if not isinstance(settings, dict):
        return {}
    return {
        key: value
        for key, value in settings.items()
        if not key.startswith(_PROFILE_TABLE_PREFIX)
    }


def preserve_profile_tables(incoming_settings: dict, current_settings_json: str | None) -> dict:
    """Keep the tables this side holds when the other side never received them.

    The satellite is sent develop settings with the profile tables removed, so
    an edit pushed back would otherwise erase them here. Whatever tables are
    already stored win, because only this side has ever held them.
    """

    settings = dict(incoming_settings)
    try:
        current_settings = json.loads(current_settings_json)
    except (TypeError, ValueError):
        return settings
    if not isinstance(current_settings, dict):
        return settings
    for key, value in current_settings.items():
        if key.startswith(_PROFILE_TABLE_PREFIX):
            settings[key] = value
    return settings
