"""Routes for publishing collections to the public portfolio site."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import settings
from data.repositories import shares as share_repository
from features.publish.builder import build_public_gallery_bundle, export_website_tree
from features.publish.deployer import (
    GalleryDeployer,
    HookStatus,
    PublishConflict,
    PublishDeployError,
    PublishSetupError,
    configured_publish_hook,
)
from features.publish import nodes as published_nodes
from features.share import auth as share_auth


router = APIRouter()
log = logging.getLogger(__name__)

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
DbPath = Callable[[], str]
CreatePublishedNodeShare = Callable[..., Awaitable[dict | None]]

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
_db_path: DbPath | None = None
_create_published_node_share: CreatePublishedNodeShare | None = None
_scheduled_hook_retries: set[int] = set()

HOOK_RETRY_BASE_SECONDS = 30
HOOK_RETRY_MAX_SECONDS = 15 * 60


class PublishBody(BaseModel):
    slug: str | None = Field(default=None, max_length=96)
    title: str | None = Field(default=None, max_length=160)


class CreatePublishedNodeBody(BaseModel):
    area: str
    parent_id: int | None = None
    source_collection_id: int
    slug: str | None = Field(default=None, max_length=96)
    title: str | None = Field(default=None, max_length=160)


class PatchPublishedNodeBody(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    slug: str | None = Field(default=None, max_length=96)
    position: int | None = None
    parent_id: int | None = None


class UpdatePublishedNodeBody(BaseModel):
    add_image_ids: list[int] = Field(default_factory=list, max_length=10000)
    remove_image_ids: list[int] = Field(default_factory=list, max_length=10000)
    attach_child_collection_ids: list[int] = Field(default_factory=list, max_length=1000)


class PublishedNodeShareBody(BaseModel):
    password: str | None = Field(default=None, max_length=256)


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
    db_path: DbPath | None = None,
    create_published_node_share: CreatePublishedNodeShare | None = None,
) -> None:
    global _templates, _get_collection, _get_images_by_ids, _collection_image_ids
    global _resolve_smart_image_ids, _get_publish, _list_publishes
    global _upsert_publish, _delete_publish, _slug_available, _track_background_task
    global _deployer, _thumbnails, _db_path, _create_published_node_share
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
    _db_path = db_path
    _create_published_node_share = create_published_node_share


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


def _nodes_configured() -> None:
    _configured()
    if _db_path is None or _create_published_node_share is None:
        raise RuntimeError("Published node routes are not configured")


@router.get("/api/published/tree")
async def api_published_tree(area: str):
    _nodes_configured()
    try:
        return await published_nodes.published_tree(_db_path(), area)
    except published_nodes.PublishedNodeConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/api/published/nodes")
async def api_create_published_node(payload: CreatePublishedNodeBody):
    _nodes_configured()
    try:
        node = await published_nodes.create_snapshot_tree(
            _db_path(),
            area=payload.area,
            parent_id=payload.parent_id,
            source_collection_id=payload.source_collection_id,
            slug=payload.slug,
            title=payload.title,
            resolve_smart_image_ids=_resolve_smart_image_ids,
        )
    except published_nodes.PublishedNodeNotFound as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except published_nodes.PublishedNodeConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if node is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    share = None
    if node["area"] == "private":
        share = await _ensure_private_node_shares(root_node_id=int(node["id"]))
        node = await published_nodes.get_node(_db_path(), int(node["id"]))
    return {"ok": True, "node": node, "share": _published_share_payload(share)}


@router.patch("/api/published/nodes/{node_id}")
async def api_patch_published_node(node_id: int, payload: PatchPublishedNodeBody):
    _nodes_configured()
    fields = {
        name: getattr(payload, name)
        for name in _model_fields_set(payload)
        if name in {"title", "slug", "position", "parent_id"}
    }
    try:
        node = await published_nodes.patch_node(_db_path(), node_id, fields)
    except published_nodes.PublishedNodeNotFound as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except published_nodes.PublishedNodeConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if node is None:
        return JSONResponse({"error": "Published node not found"}, status_code=404)
    return {"ok": True, "node": node}


@router.delete("/api/published/nodes/{node_id}")
async def api_delete_published_node(node_id: int):
    _nodes_configured()
    if not await published_nodes.delete_node(_db_path(), node_id):
        return JSONResponse({"error": "Published node not found"}, status_code=404)
    return {"ok": True}


@router.get("/api/published/nodes/{node_id}/diff")
async def api_published_node_diff(node_id: int):
    _nodes_configured()
    diff = await published_nodes.node_diff(
        _db_path(),
        node_id,
        resolve_smart_image_ids=_resolve_smart_image_ids,
    )
    if diff is None:
        return JSONResponse({"error": "Published node not found"}, status_code=404)
    if diff["source_deleted"]:
        return JSONResponse(diff, status_code=410)
    return diff


@router.post("/api/published/nodes/{node_id}/update")
async def api_update_published_node(node_id: int, payload: UpdatePublishedNodeBody):
    _nodes_configured()
    try:
        node = await published_nodes.update_node(
            _db_path(),
            node_id,
            add_image_ids=payload.add_image_ids,
            remove_image_ids=payload.remove_image_ids,
            attach_child_collection_ids=payload.attach_child_collection_ids,
            resolve_smart_image_ids=_resolve_smart_image_ids,
        )
    except published_nodes.PublishedNodeSourceDeleted as exc:
        return JSONResponse({"error": str(exc)}, status_code=410)
    except published_nodes.PublishedNodeConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if node is None:
        return JSONResponse({"error": "Published node not found"}, status_code=404)
    return {"ok": True, "node": node}


@router.post("/api/published/nodes/{node_id}/share")
async def api_share_published_node(node_id: int, payload: PublishedNodeShareBody):
    _nodes_configured()
    node = await published_nodes.get_node(_db_path(), node_id)
    if node is None:
        return JSONResponse({"error": "Published node not found"}, status_code=404)
    if node["area"] != "private":
        return JSONResponse({"error": "Only private published nodes can be shared"}, status_code=409)
    password_supplied = "password" in _model_fields_set(payload)
    password_hash = share_auth.hash_password(payload.password) if payload.password else None
    try:
        share = await _create_published_node_share(
            node_id,
            password_hash=password_hash,
            update_password=password_supplied,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return {"ok": True, "share": _published_share_payload(share)}


@router.delete("/api/published/nodes/{node_id}/share")
async def api_revoke_published_node_share(node_id: int):
    _nodes_configured()
    node = await published_nodes.get_node(_db_path(), node_id)
    if node is None:
        return JSONResponse({"error": "Published node not found"}, status_code=404)
    revoked = await share_repository.revoke_share(
        _db_path(),
        published_node_id=node_id,
    )
    if not revoked:
        return JSONResponse({"error": "Share not found"}, status_code=404)
    return {"ok": True}


@router.post("/api/published/export")
async def api_export_published_area(area: str):
    _nodes_configured()
    if area != "website":
        return JSONResponse({"error": "area must be website"}, status_code=400)
    publish_dir = str(settings.get_settings().get("publish_dir") or "").strip()
    if not publish_dir:
        return JSONResponse(
            {"error": "Choose a publishing folder before publishing this gallery."},
            status_code=409,
        )
    manifest = await export_website_tree(
        db_path=_db_path(),
        destination=publish_dir,
        templates=_templates,
        thumbnails=_thumbnails,
    )
    return {"ok": True, "area": "website", "manifest": manifest}


@router.post("/api/user-collections/{collection_id}/publish")
async def api_publish_collection(collection_id: int, payload: PublishBody):
    _configured()
    if _job_in_progress(collection_id):
        return JSONResponse({"error": "A publish job is already in progress"}, status_code=409)
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
    if _job_in_progress(collection_id):
        return JSONResponse({"error": "A publish job is already in progress"}, status_code=409)
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
        "in_progress": _job_in_progress(collection_id),
        "publishing": _publishing_config_payload(),
    }


@router.post("/api/user-collections/{collection_id}/publish/revoke")
async def api_revoke_collection_publish(collection_id: int):
    _configured()
    if _job_in_progress(collection_id):
        return JSONResponse({"error": "A publish job is already in progress"}, status_code=409)
    publish = await _get_publish(collection_id)
    if publish is None:
        return JSONResponse({"error": "Publish not found"}, status_code=404)
    if _job_in_progress(collection_id):
        return JSONResponse({"error": "A publish job is already in progress"}, status_code=409)
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
            hook_failed = bool(hook and not hook.ok)
            attempts = 1 if hook_failed else 0
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
                    hook_pending=hook_failed,
                    hook_attempts=attempts,
                    hook_next_retry_at=_next_hook_retry_at(attempts) if hook_failed else None,
                    hook_pending_operation="publish" if hook_failed else "",
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
        if result.hook and not result.hook.ok:
            _queue_hook_retry(row, legacy_state="hook_failed", force=True)
        else:
            _finish_job(
                collection_id,
                "live",
                publish=_publish_payload(row),
                push_error=result.push_error,
                hook=result.hook,
            )
    except (PublishConflict, PublishDeployError, PublishSetupError) as exc:
        _fail_job(collection_id, exc, operation="publish")
    except Exception as exc:
        _fail_job(collection_id, exc, operation="publish")


async def _run_revoke_job(collection_id: int, slug: str) -> None:
    assert _deployer is not None
    try:
        def progress(phase: str) -> None:
            _update_job(collection_id, phase=phase)

        def published_rows():
            return asyncio.run(_list_publishes())

        def persist_revoke(hook):
            if hook is None or hook.ok:
                return asyncio.run(_delete_publish(collection_id))
            attempts = 1
            return asyncio.run(
                _upsert_publish(
                    collection_id=collection_id,
                    slug=publish["slug"],
                    title=publish["title"],
                    image_count=publish["image_count"],
                    bundle_bytes=publish["bundle_bytes"],
                    last_commit=publish.get("last_commit"),
                    hook_exit_code=hook.returncode if hook.configured else None,
                    hook_output=hook.output if hook.configured else "",
                    hook_ran_at=hook.ran_at if hook.configured else None,
                    hook_pending=True,
                    hook_attempts=attempts,
                    hook_next_retry_at=_next_hook_retry_at(attempts),
                    hook_pending_operation="revoke",
                )
            )

        result = await _deployer.revoke(
            slug=slug,
            collection_id=collection_id,
            published_rows=published_rows,
            persist_revoke=persist_revoke,
            progress=progress,
        )
        if result.hook and not result.hook.ok:
            row = await _get_publish(collection_id)
            _queue_hook_retry(row, legacy_state="revoked_hook_failed", force=True)
        else:
            _finish_job(collection_id, "revoked", publish=None, push_error=result.push_error, hook=result.hook)
    except (PublishConflict, PublishDeployError, PublishSetupError) as exc:
        _fail_job(collection_id, exc, operation="revoke")
    except Exception as exc:
        _fail_job(collection_id, exc, operation="revoke")


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


def _job_in_progress(collection_id: int) -> bool:
    job = _jobs.get(int(collection_id))
    return bool(
        job
        and (
            job.get("state") in {"publishing", "revoking"}
            or (job.get("state") == "hook_retrying" and job.get("executing"))
        )
    )


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
        retrying=False,
        executing=False,
        next_retry_at=None,
    )


def _next_hook_retry_at(attempts: int, *, now: float | None = None) -> float:
    delay = min(HOOK_RETRY_MAX_SECONDS, HOOK_RETRY_BASE_SECONDS * (2 ** max(0, int(attempts) - 1)))
    return (time.time() if now is None else float(now)) + delay


def _queue_hook_retry(row: dict | None, *, legacy_state: str, force: bool = False) -> None:
    if not row or not row.get("hook_pending"):
        return
    collection_id = int(row["collection_id"])
    if _job_in_progress(collection_id) and not force:
        return
    next_retry_at = float(row.get("hook_next_retry_at") or time.time())
    _jobs[collection_id] = {
        "state": "hook_retrying",
        "legacy_state": legacy_state,
        "hook_failure_state": legacy_state,
        "phase": "waiting to retry hook",
        "slug": row["slug"],
        "title": row["title"],
        "started_at": time.time(),
        "completed_at": None,
        "error": None,
        "status_code": None,
        "hook": _hook_payload(row),
        "retrying": True,
        "executing": False,
        "next_retry_at": next_retry_at,
        "attempts": int(row.get("hook_attempts") or 0),
        "publish": _publish_payload(row),
        "url": _public_url(row["slug"]),
    }
    if collection_id in _scheduled_hook_retries:
        return
    _scheduled_hook_retries.add(collection_id)
    _schedule(_wait_for_hook_retry(collection_id, row["hook_pending_operation"], next_retry_at))


async def resume_pending_hook_retries() -> None:
    """Restore durable hook confirmations after the app process starts."""
    _configured()
    import sqlite3

    try:
        rows = await _list_publishes()
    except sqlite3.OperationalError:
        # Fresh library: the publishes table doesn't exist yet, so there are
        # no durable retries to resume. Crashing boot here bricked new installs.
        log.info("hook-retry resume skipped: publishes schema not initialized yet")
        return
    for row in rows:
        if row.get("hook_pending") and row.get("hook_pending_operation") in {"publish", "revoke"}:
            legacy_state = "hook_failed" if row["hook_pending_operation"] == "publish" else "revoked_hook_failed"
            _queue_hook_retry(row, legacy_state=legacy_state)


async def _wait_for_hook_retry(collection_id: int, operation: str, expected_retry_at: float) -> None:
    rescheduled = False
    try:
        await asyncio.sleep(max(0, expected_retry_at - time.time()))
        if _job_in_progress(collection_id):
            return
        row = await _get_publish(collection_id)
        if not _retry_is_current(row, operation, expected_retry_at):
            return
        _update_job(
            collection_id,
            state="hook_retrying",
            phase="retrying hook",
            executing=True,
            retrying=True,
        )
        assert _deployer is not None
        hook = await _deployer.retry_hook()
        if hook.ok:
            await _finalize_hook_retry(row, operation, hook)
            return
        _scheduled_hook_retries.discard(collection_id)
        await _record_hook_retry_failure(row, operation, hook)
        rescheduled = True
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(
            "worker=publish operation=hook_retry collection_id=%s failed unexpectedly",
            collection_id,
        )
        row = await _get_publish(collection_id)
        if _retry_is_current(row, operation, expected_retry_at):
            _scheduled_hook_retries.discard(collection_id)
            _queue_hook_retry(row, legacy_state=_legacy_hook_state(operation), force=True)
            rescheduled = True
    finally:
        if not rescheduled:
            _scheduled_hook_retries.discard(collection_id)


def _retry_is_current(row: dict | None, operation: str, expected_retry_at: float) -> bool:
    return bool(
        row
        and row.get("hook_pending")
        and row.get("hook_pending_operation") == operation
        and float(row.get("hook_next_retry_at") or 0) == float(expected_retry_at)
    )


async def _finalize_hook_retry(row: dict, operation: str, hook: HookStatus) -> None:
    collection_id = int(row["collection_id"])
    if operation == "revoke":
        await _delete_publish(collection_id)
        _finish_job(collection_id, "revoked", publish=None, push_error=None, hook=hook)
        return
    published = await _upsert_publish_from_row(row, hook=hook)
    _finish_job(collection_id, "live", publish=_publish_payload(published), push_error=None, hook=hook)


async def _record_hook_retry_failure(row: dict, operation: str, hook: HookStatus) -> None:
    attempts = int(row.get("hook_attempts") or 0) + 1
    pending = await _upsert_publish_from_row(
        row,
        hook=hook,
        hook_pending=True,
        hook_attempts=attempts,
        hook_next_retry_at=_next_hook_retry_at(attempts),
        hook_pending_operation=operation,
    )
    _queue_hook_retry(pending, legacy_state=_legacy_hook_state(operation), force=True)


async def _upsert_publish_from_row(
    row: dict,
    *,
    hook: HookStatus,
    hook_pending: bool = False,
    hook_attempts: int = 0,
    hook_next_retry_at: float | None = None,
    hook_pending_operation: str = "",
) -> dict:
    return await _upsert_publish(
        collection_id=int(row["collection_id"]),
        slug=row["slug"],
        title=row["title"],
        image_count=int(row.get("image_count") or 0),
        bundle_bytes=int(row.get("bundle_bytes") or 0),
        last_commit=row.get("last_commit"),
        hook_exit_code=hook.returncode if hook.configured else None,
        hook_output=hook.output if hook.configured else "",
        hook_ran_at=hook.ran_at if hook.configured else None,
        hook_pending=hook_pending,
        hook_attempts=hook_attempts,
        hook_next_retry_at=hook_next_retry_at,
        hook_pending_operation=hook_pending_operation,
    )


def _legacy_hook_state(operation: str) -> str:
    return "hook_failed" if operation == "publish" else "revoked_hook_failed"


def _fail_job(collection_id: int, exc: Exception, *, operation: str) -> None:
    status_code = getattr(exc, "status_code", 500)
    expected = isinstance(exc, (PublishConflict, PublishDeployError, PublishSetupError))
    if expected:
        log.warning(
            "worker=publish operation=%s collection_id=%s failed status_code=%s: %s",
            operation,
            collection_id,
            status_code,
            exc,
        )
    else:
        log.error(
            "worker=publish operation=%s collection_id=%s failed unexpectedly",
            operation,
            collection_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    payload = {
        "state": "error",
        "phase": "error",
        "completed_at": time.time(),
        "error": (
            str(exc)
            if expected
            else f"{operation.title()} failed unexpectedly. Check the server log and try again."
        ),
        "status_code": status_code,
    }
    paths = getattr(exc, "paths", None)
    tail = getattr(exc, "tail", None)
    if expected and paths:
        payload["paths"] = paths
    if expected and tail:
        payload["tail"] = tail
    _update_job(collection_id, **payload)


def _publish_payload(row: dict | None) -> dict | None:
    if row is None:
        return None
    payload = dict(row)
    payload["url"] = _public_url(row["slug"])
    payload["hook_status"] = _hook_payload(row)
    payload["retrying"] = bool(row.get("hook_pending"))
    payload["next_retry_at"] = row.get("hook_next_retry_at")
    payload["attempts"] = int(row.get("hook_attempts") or 0)
    if payload["retrying"]:
        payload["hook_failure_state"] = _legacy_hook_state(row.get("hook_pending_operation") or "publish")
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
        "hook_configured": bool(configured_publish_hook()),
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


def _model_fields_set(payload) -> set[str]:
    fields = getattr(payload, "model_fields_set", None)
    if fields is None:
        fields = getattr(payload, "__fields_set__", set())
    return set(fields)


def _published_share_payload(share: dict | None) -> dict | None:
    if share is None:
        return None
    return {
        "id": int(share["id"]),
        "published_node_id": int(share["published_node_id"]),
        "token": share["token"],
        "protected": bool(share.get("password_hash")),
        "created_at": float(share["created_at"]),
    }


async def _ensure_private_node_shares(*, root_node_id: int) -> dict | None:
    return await _create_published_node_share(
        int(root_node_id),
        password_hash=None,
        update_password=False,
    )
