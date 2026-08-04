"""Deterministic query understanding for archive search retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass


PLAN_VERSION = 1

_VISUAL_TERMS = {
    "aesthetic", "backlit", "black and white", "blurry", "bokeh", "bright",
    "candid", "close up", "colorful", "dark", "foggy", "golden hour",
    "landscape", "moody", "night", "portrait", "reflection", "silhouette",
    "snowy", "sunrise", "sunset", "vintage",
}
_TEXTUAL_TERMS = {
    "document", "handwriting", "letter", "menu", "poster", "receipt",
    "screen", "screenshot", "sign", "text", "whiteboard",
}
_RECENCY_TERMS = {
    "latest", "newest", "recent", "recently", "this month", "this week",
    "this year", "today", "yesterday",
}
_FILE_QUERY = re.compile(r"^\.?[a-z0-9]{2,5}$", re.IGNORECASE)


@dataclass(frozen=True)
class SearchPlan:
    intent: str
    source_weights: dict[str, float]
    recency_weight: float = 0.0
    version: int = PLAN_VERSION

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "intent": self.intent,
            "source_weights": dict(self.source_weights),
            "recency_weight": self.recency_weight,
        }


def _contains_phrase(query: str, phrases: set[str]) -> bool:
    padded = f" {query.casefold()} "
    return any(f" {phrase} " in padded for phrase in phrases)


def plan_search(query: str) -> SearchPlan:
    """Choose retrieval emphasis from user language without a model-control UI."""

    normalized = " ".join(str(query or "").split()).casefold()
    wants_recency = _contains_phrase(normalized, _RECENCY_TERMS)
    recency_weight = 0.08 if wants_recency else 0.0

    if _FILE_QUERY.fullmatch(normalized.lstrip(".")):
        return SearchPlan(
            intent="file",
            source_weights={"metadata": 0.8, "captions": 0.05, "embedding": 0.15},
            recency_weight=recency_weight,
        )
    if _contains_phrase(normalized, _TEXTUAL_TERMS):
        return SearchPlan(
            intent="textual",
            source_weights={"metadata": 0.15, "captions": 0.5, "embedding": 0.35},
            recency_weight=recency_weight,
        )
    if _contains_phrase(normalized, _VISUAL_TERMS):
        return SearchPlan(
            intent="visual",
            source_weights={"metadata": 0.1, "captions": 0.3, "embedding": 0.6},
            recency_weight=recency_weight,
        )
    return SearchPlan(
        intent="memory",
        source_weights={"metadata": 0.2, "captions": 0.35, "embedding": 0.45},
        recency_weight=recency_weight,
    )
