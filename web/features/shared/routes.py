"""Aggregate read routes for collections that leave the private app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request

import settings


router = APIRouter()

ListShares = Callable[[], Awaitable[list[dict]]]
ListPublishes = Callable[[], Awaitable[list[dict]]]

_list_shares: ListShares | None = None
_list_publishes: ListPublishes | None = None


def configure(*, list_shares: ListShares, list_publishes: ListPublishes) -> None:
    global _list_shares, _list_publishes
    _list_shares = list_shares
    _list_publishes = list_publishes


def _configured() -> None:
    if _list_shares is None or _list_publishes is None:
        raise RuntimeError("Shared routes are not configured")


@router.get("/api/shares")
async def api_list_shared_surfaces(request: Request):
    _configured()
    shares = await _list_shares()
    publishes = await _list_publishes()
    by_collection: dict[int, dict] = {}

    for share in shares:
        collection_id = int(share["collection_id"])
        item = by_collection.setdefault(collection_id, _base_item(share))
        item["private_link"] = _private_link_payload(request, share)

    for publish in publishes:
        collection_id = int(publish["collection_id"])
        item = by_collection.setdefault(collection_id, _base_item(publish))
        item["website"] = _website_payload(publish)
        if not item.get("photo_count"):
            item["photo_count"] = int(publish.get("image_count") or 0)

    items = list(by_collection.values())
    items.sort(
        key=lambda item: max(
            float((item.get("private_link") or {}).get("created_at") or 0),
            float((item.get("website") or {}).get("updated_at") or 0),
        ),
        reverse=True,
    )
    return {"items": items}


def _base_item(row: dict) -> dict:
    cover_image_id = row.get("cover_image_id")
    return {
        "collection_id": int(row["collection_id"]),
        "name": row.get("collection_name") or row.get("title") or "Collection",
        "photo_count": int(row.get("photo_count") or row.get("image_count") or 0),
        "cover_image_id": int(cover_image_id) if cover_image_id is not None else None,
        "cover_thumb_url": f"/api/thumb/sm/{int(cover_image_id)}" if cover_image_id is not None else "",
        "private_link": None,
        "website": None,
    }


def _private_link_payload(request: Request, share: dict) -> dict:
    token = str(share.get("token") or "")
    return {
        "token": token,
        "url": str(request.url_for("public_share_gallery", token=token)) if token else "",
        "protected": bool(share.get("password_hash")),
        "view_count": int(share.get("view_count") or 0),
        "first_viewed_at": share.get("first_viewed_at"),
        "last_viewed_at": share.get("last_viewed_at"),
        "created_at": share.get("created_at"),
        "expires_at": share.get("expires_at"),
        "pick_count": int(share.get("pick_count") or 0),
    }


def _website_payload(publish: dict) -> dict:
    slug = str(publish.get("slug") or "")
    return {
        "slug": slug,
        "title": publish.get("title") or slug,
        "url": _public_url(slug),
        "published_at": publish.get("published_at"),
        "updated_at": publish.get("updated_at"),
        "image_count": int(publish.get("image_count") or 0),
        "bundle_bytes": int(publish.get("bundle_bytes") or 0),
        "hook_status": _hook_payload(publish),
    }


def _hook_payload(row: dict) -> dict:
    exit_code = row.get("hook_exit_code")
    configured = exit_code is not None or bool(row.get("hook_output") or row.get("hook_ran_at"))
    return {
        "configured": configured,
        "returncode": int(exit_code) if exit_code is not None else None,
        "output": row.get("hook_output") or "",
        "ran_at": row.get("hook_ran_at"),
        "ok": (exit_code is None and not configured) or exit_code == 0,
    }


def _public_url(slug: str) -> str:
    base = str(settings.get_settings().get("publish_site_base_url") or "").strip().rstrip("/")
    if not base or not slug:
        return ""
    return f"{base}/g/{slug}/"
