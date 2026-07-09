"""Routes for publishing collections to the public portfolio site."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import settings
from features.publish.builder import build_public_gallery_bundle
from features.publish.deployer import GalleryDeployer, HookStatus, PublishConflict, PublishDeployError, PublishSetupError


router = APIRouter()

GetCollection = Callable[..., Awaitable[dict | None]]
GetImagesByIds = Callable[[list[int]], Awaitable[dict[int, dict]]]
CollectionImageIds = Callable[[int], Awaitable[list[int] | None]]
ResolveSmartImageIds = Callable[[dict], Awaitable[list[int]]]
GetPublish = Callable[[int], Awaitable[dict | None]]
ListPublishes = Callable[[], Awaitable[list[dict]]]
UpsertPublish = Callable[..., Awaitable[dict]]
DeletePublish = Callable[[int], Awaitable[bool]]
SlugAvailable = Callable[..., Awaitable[bool]]
TrackBackgroundTask = Callable[[Awaitable], object]

_templates: Jinja2Templates | None = None
_get_collection: GetCollection | None = None
_get_images_by_ids: GetImagesByIds | None = None
_collection_image_ids: CollectionImageIds | None = None
_resolve_smart_image_ids: ResolveSmartImageIds | None = None
_get_publish: GetPublish | None = None
_list_publishes: ListPublishes | None = None
_upsert_publish: UpsertPublish | None = None
_delete_publish: DeletePublish | None = None
_slug_available: SlugAvailable | None = None
_track_background_task: TrackBackgroundTask | None = None
_deployer: GalleryDeployer | None = None
_thumbnails = None
_jobs: dict[int, dict] = {}


class PublishBody(BaseModel):
    slug: str | None = Field(default=None, max_length=96)
    title: str | None = Field(default=None, max_length=160)


def configure(
    *,
    templates: Jinja2Templates,
    get_collection: GetCollection,
    get_images_by_ids: GetImagesByIds,
    collection_image_ids: CollectionImageIds,
    resolve_smart_image_ids: ResolveSmartImageIds,
    get_publish: GetPublish,
    list_publishes: ListPublishes,
    upsert_publish: UpsertPublish,
    delete_publish: DeletePublish,
    slug_available: SlugAvailable,
    thumbnails,
    deployer: GalleryDeployer | None = None,
    track_background_task: TrackBackgroundTask | None = None,
) -> None:
    global _templates, _get_collection, _get_images_by_ids, _collection_image_ids
    global _resolve_smart_image_ids, _get_publish, _list_publishes
    global _upsert_publish, _delete_publish, _slug_available, _track_background_task
    global _deployer, _thumbnails
    _templates = templates
    _get_collection = get_collection
    _get_images_by_ids = get_images_by_ids
    _collection_image_ids = collection_image_ids
    _resolve_smart_image_ids = resolve_smart_image_ids
    _get_publish = get_publish
    _list_publishes = list_publishes
    _upsert_publish = upsert_publish
    _delete_publish = delete_publish
    _slug_available = slug_available
    _track_background_task = track_background_task
    _deployer = deployer or GalleryDeployer()
    _thumbnails = thumbnails


def _configured() -> None:
    if (
        _templates is None
        or _get_collection is None
        or _get_images_by_ids is None
        or _collection_image_ids is None
        or _resolve_smart_image_ids is None
        or _get_publish is None
        or _list_publishes is None
        or _upsert_publish is None
        or _delete_publish is None
        or _slug_available is None
        or _deployer is None
        or _thumbnails is None
    ):
        raise RuntimeError("Publish routes are not configured")


@router.post("/api/user-collections/{collection_id}/publish")
async def api_publish_collection(collection_id: int, payload: PublishBody):
    _configured()
    if not _publish_enabled():
        return JSONResponse({"error": "Choose a publishing folder before publishing this gallery."}, status_code=409)
    collection = await _get_collection(collection_id, limit=1, offset=0)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    existing = await _get_publish(collection_id)
    try:
        slug = _clean_slug(payload.slug) if payload.slug else existing.get("slug") if existing else _slugify(collection["name"])
        slug = await _unique_slug(slug, collection_id=collection_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    title = (payload.title or existing.get("title") if existing else payload.title or collection["name"]).strip()
    if not title:
        title = collection["name"] or slug
    if not await _slug_available(slug, collection_id=collection_id):
        return JSONResponse({"error": "Slug is already published"}, status_code=409)
    _start_job(collection_id, "publishing", slug=slug, title=title)
    _schedule(_run_publish_job(collection_id, slug, title))
    return JSONResponse({"job": "publishing", "slug": slug, "title": title}, status_code=202)


@router.get("/api/user-collections/{collection_id}/publish")
async def api_get_collection_publish(collection_id: int):
    _configured()
    publish = await _get_publish(collection_id)
    job = _jobs.get(int(collection_id))
    return {
        "publish": _publish_payload(publish),
        "url": _public_url(publish["slug"]) if publish else None,
        "job": job,
        "in_progress": bool(job and job.get("state") in {"publishing", "revoking"}),
        "publishing": _publishing_config_payload(),
    }


@router.post("/api/user-collections/{collection_id}/publish/revoke")
async def api_revoke_collection_publish(collection_id: int):
    _configured()
    publish = await _get_publish(collection_id)
    if publish is None:
        return JSONResponse({"error": "Publish not found"}, status_code=404)
    _start_job(collection_id, "revoking", slug=publish["slug"], title=publish["title"])
    _schedule(_run_revoke_job(collection_id, publish["slug"]))
    return JSONResponse({"job": "revoking", "slug": publish["slug"]}, status_code=202)


@router.get("/api/publishes")
async def api_list_publishes():
    _configured()
    publishes = await _list_publishes()
    return {"publishes": [_publish_payload(row) for row in publishes], "publishing": _publishing_config_payload()}


async def _run_publish_job(collection_id: int, slug: str, title: str) -> None:
    assert _deployer is not None
    try:
        def progress(phase: str) -> None:
            _update_job(collection_id, phase=phase)

        def published_rows():
            return asyncio.run(_list_publishes())

        def write_bundle(target):
            return asyncio.run(
                build_public_gallery_bundle(
                    slug=slug,
                    title=title,
                    destination=target,
                    templates=_templates,
                    get_collection=_get_collection,
                    collection_id=collection_id,
                    get_images_by_ids=_get_images_by_ids,
                    collection_image_ids=_collection_image_ids,
                    resolve_smart_image_ids=_resolve_smart_image_ids,
                    thumbnails=_thumbnails,
                )
            )

        def persist_publish(summary, hook):
            return asyncio.run(
                _upsert_publish(
                    collection_id=collection_id,
                    slug=slug,
                    title=title,
                    image_count=summary.photo_count if summary else 0,
                    bundle_bytes=summary.bundle_bytes if summary else 0,
                    last_commit=None,
                    hook_exit_code=hook.returncode if hook and hook.configured else None,
                    hook_output=hook.output if hook and hook.configured else "",
                    hook_ran_at=hook.ran_at if hook and hook.configured else None,
                )
            )

        result = await _deployer.publish(
            slug=slug,
            title=title,
            collection_id=collection_id,
            published_rows=published_rows,
            write_bundle=write_bundle,
            persist_publish=persist_publish,
            progress=progress,
        )
        row = result.publish_row
        state = "hook_failed" if result.hook and not result.hook.ok else "live"
        _finish_job(
            collection_id,
            state,
            publish=_publish_payload(row),
            push_error=result.push_error,
            hook=result.hook,
        )
    except (PublishConflict, PublishDeployError, PublishSetupError) as exc:
        _fail_job(collection_id, exc)
    except Exception as exc:
        _fail_job(collection_id, exc)


async def _run_revoke_job(collection_id: int, slug: str) -> None:
    assert _deployer is not None
    try:
        def progress(phase: str) -> None:
            _update_job(collection_id, phase=phase)

        def published_rows():
            return asyncio.run(_list_publishes())

        def persist_revoke(_hook):
            return asyncio.run(_delete_publish(collection_id))

        result = await _deployer.revoke(
            slug=slug,
            collection_id=collection_id,
            published_rows=published_rows,
            persist_revoke=persist_revoke,
            progress=progress,
        )
        state = "revoked_hook_failed" if result.hook and not result.hook.ok else "revoked"
        _finish_job(collection_id, state, publish=None, push_error=result.push_error, hook=result.hook)
    except (PublishConflict, PublishDeployError, PublishSetupError) as exc:
        _fail_job(collection_id, exc)
    except Exception as exc:
        _fail_job(collection_id, exc)


def _schedule(coro) -> None:
    if _track_background_task is not None:
        _track_background_task(coro)
    else:
        asyncio.create_task(coro)


def _start_job(collection_id: int, state: str, *, slug: str, title: str) -> None:
    _jobs[int(collection_id)] = {
        "state": state,
        "phase": "queued",
        "slug": slug,
        "title": title,
        "started_at": time.time(),
        "completed_at": None,
        "error": None,
        "status_code": None,
        "hook": None,
    }


def _update_job(collection_id: int, **fields) -> None:
    job = _jobs.get(int(collection_id))
    if job:
        job.update(fields)


def _finish_job(
    collection_id: int,
    state: str,
    *,
    publish: dict | None,
    push_error: str | None,
    hook: HookStatus | None,
) -> None:
    _update_job(
        collection_id,
        state=state,
        phase="published locally, hook failed" if state == "hook_failed" else state,
        publish=publish,
        url=_public_url(publish["slug"]) if publish else None,
        completed_at=time.time(),
        push_error=push_error,
        hook=_hook_payload(hook),
    )


def _fail_job(collection_id: int, exc: Exception) -> None:
    status_code = getattr(exc, "status_code", 500)
    payload = {
        "state": "error",
        "phase": "error",
        "completed_at": time.time(),
        "error": str(exc),
        "status_code": status_code,
    }
    paths = getattr(exc, "paths", None)
    tail = getattr(exc, "tail", None)
    if paths:
        payload["paths"] = paths
    if tail:
        payload["tail"] = tail
    _update_job(collection_id, **payload)


def _publish_payload(row: dict | None) -> dict | None:
    if row is None:
        return None
    payload = dict(row)
    payload["url"] = _public_url(row["slug"])
    payload["hook_status"] = _hook_payload(row)
    return payload


def _public_url(slug: str) -> str | None:
    base = str(settings.get_settings().get("publish_site_base_url") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/g/{slug}/"


def _publish_enabled() -> bool:
    return bool(str(settings.get_settings().get("publish_dir") or "").strip())


def _publishing_config_payload() -> dict:
    config = settings.get_settings()
    publish_dir = str(config.get("publish_dir") or "").strip()
    return {
        "enabled": bool(publish_dir),
        "publish_dir": publish_dir,
        "hook_configured": bool(str(config.get("publish_hook") or "").strip()),
        "site_base_url": str(config.get("publish_site_base_url") or "").strip(),
        "setup_prompt": "" if publish_dir else "Choose a folder for published galleries before publishing.",
    }


def _hook_payload(value) -> dict:
    if value is None:
        return {
            "configured": False,
            "returncode": None,
            "output": "",
            "ran_at": None,
            "timed_out": False,
            "ok": True,
        }
    if isinstance(value, HookStatus):
        return value.payload()
    exit_code = value.get("hook_exit_code")
    configured = exit_code is not None or bool(value.get("hook_output") or value.get("hook_ran_at"))
    return {
        "configured": configured,
        "returncode": int(exit_code) if exit_code is not None else None,
        "output": value.get("hook_output") or "",
        "ran_at": value.get("hook_ran_at"),
        "timed_out": False,
        "ok": not configured or exit_code == 0,
    }


def _clean_slug(value: str) -> str:
    slug = _slugify(value)
    if not slug:
        raise ValueError("slug is required")
    return slug


async def _unique_slug(base: str, *, collection_id: int) -> str:
    slug = _slugify(base) or "gallery"
    if await _slug_available(slug, collection_id=collection_id):
        return slug
    for index in range(2, 200):
        candidate = f"{slug}-{index}"
        if await _slug_available(candidate, collection_id=collection_id):
            return candidate
    raise ValueError("Could not create a unique slug")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return slug.strip("-")[:96]
