from core.catalog_path import catalog_path
from core.requests import parse_exclude_sources
from collections.abc import Awaitable, Callable
from typing import Any

import db
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.collections import graph
from features.collections import smart
from features.collections import suggestions as collection_suggestions
from features.sync import oplog


from core import query_constraints
from features.collections import smart as smart_collections


async def _smart_image_ids(query):
    return await smart_collections.resolve_image_ids(
        query,
        resolve_library_constraints=query_constraints.resolve_configured_library_constraints,
    )


router = APIRouter()
ResolveSmartImageIds = Callable[[dict], Awaitable[list[int]]]

MAX_IMAGE_IDS_PER_REQUEST = 10000
MAX_COLLECTION_NAME_LENGTH = 160


class CreateCollectionBody(BaseModel):
    name: str = ""
    description: str = ""
    image_ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)
    visibility: str = "private"
    status: str = "draft"
    query: dict[str, Any] | None = None


class CollectionImagesBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)


class CollectionLinkBody(BaseModel):
    child_id: int
    position: int = 0


class RenameCollectionBody(BaseModel):
    name: str = ""
    query: dict[str, Any] | None = None
    materialize: bool = False


class UpdateCollectionBody(BaseModel):
    name: str | None = None
    query: dict[str, Any] | None = None
    materialize: bool = False


def _clean_collection_name(name: str) -> str | None:
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > MAX_COLLECTION_NAME_LENGTH:
        return None
    return clean_name


def _fields_set(payload) -> set[str]:
    model_fields = getattr(payload, "model_fields_set", None)
    if model_fields is not None:
        return set(model_fields)
    return set(getattr(payload, "__fields_set__", set()))


def _invalid_query_response(exc: smart.SmartCollectionQueryError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


def _invalidate_suggestions_cache() -> None:
    collection_suggestions.invalidate_cache()


async def _collection_oplog_payload(collection_id: int) -> dict | None:
    return await oplog.collection_meta_payload(catalog_path(), collection_id)


async def _append_collection_meta(collection_id: int, *, deleted: bool = False, payload: dict | None = None) -> None:
    payload = payload or await _collection_oplog_payload(collection_id)
    if payload is None:
        return
    await oplog.append_collection_meta(catalog_path(), {**payload, "deleted": deleted})


async def _append_collection_memberships(collection_id: int, image_ids: list[int], *, member: bool) -> None:
    await oplog.append_collection_memberships(catalog_path(), collection_id, image_ids, member=member)


async def _smart_collection_conflict(collection_id: int) -> JSONResponse | None:
    smart_state = await db.collection_is_smart(collection_id)
    if smart_state is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if smart_state:
        return JSONResponse(
            {"error": "Smart collections are live queries; materialize before editing membership."},
            status_code=409,
        )
    return None


async def _with_smart_summary(collection: dict) -> dict:
    if not collection.get("smart"):
        return collection
    summary = await smart_collections.resolve_summary(collection["query"] or {}, resolve_library_constraints=query_constraints.resolve_configured_library_constraints)
    return {**collection, **summary, "smart": True}


async def _collection_update(
    collection_id: int,
    payload,
    *,
    require_name: bool,
):
    fields = _fields_set(payload)
    clean_name = None
    if "name" in fields or require_name:
        clean_name = _clean_collection_name(payload.name or "")
        if clean_name is None:
            return JSONResponse({"error": "Collection name is required"}, status_code=400)

    query_json = None
    query_supplied = "query" in fields
    if query_supplied:
        try:
            query_json = smart.query_to_json(payload.query)
        except smart.SmartCollectionQueryError as exc:
            return _invalid_query_response(exc)

    materialize_ids = None
    if bool(payload.materialize):
        current = await db.get_collection(collection_id, limit=1, offset=0)
        if current is None:
            return JSONResponse({"error": "Collection not found"}, status_code=404)
        if current.get("smart"):
            try:
                materialize_ids = await smart_collections.resolve_materialized_image_ids(current["query"] or {}, resolve_library_constraints=query_constraints.resolve_configured_library_constraints)
            except smart.SmartCollectionMaterializeTooLarge as exc:
                return JSONResponse(
                    {
                        "error": "Smart collection is too large to materialize",
                        "image_count": exc.count,
                        "limit": exc.limit,
                    },
                    status_code=409,
                )

    collection = await db.rename_collection(
        collection_id,
        name=clean_name,
        query=query_json if query_supplied else None,
        query_supplied=query_supplied,
        materialize_image_ids=materialize_ids,
    )
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if materialize_ids is None:
        await _append_collection_meta(collection_id)
    if materialize_ids is not None:
        _invalidate_suggestions_cache()
    return {"ok": True, "collection": await _with_smart_summary(collection)}


@router.get("/api/user-collections")
async def api_user_collections():
    collections = []
    for collection in await db.list_collections():
        collections.append(await _with_smart_summary(collection))
    return {"collections": collections}


@router.post("/api/user-collections")
async def api_create_collection(payload: CreateCollectionBody):
    try:
        query_json = smart.query_to_json(payload.query)
    except smart.SmartCollectionQueryError as exc:
        return _invalid_query_response(exc)
    collection = await db.create_collection(
        name=payload.name,
        description=payload.description,
        image_ids=payload.image_ids,
        visibility=payload.visibility,
        status=payload.status,
        query=query_json,
    )
    await _append_collection_meta(collection["id"])
    if not collection.get("smart"):
        await _append_collection_memberships(collection["id"], payload.image_ids, member=True)
    _invalidate_suggestions_cache()
    return {"ok": True, "collection": await _with_smart_summary(collection)}


@router.get("/api/collections/suggestions")
async def api_collection_suggestions(exclude_sources: str = ""):
    payload = await collection_suggestions.collection_suggestions(
        catalog_path(), db_signature=catalog_path()
    )
    excluded = parse_exclude_sources(exclude_sources)
    if not excluded:
        return payload
    return await collection_suggestions.filter_suggestions_excluding_sources(
        catalog_path(),
        payload,
        excluded,
    )


@router.post("/api/collections/{collection_id}/links")
async def api_add_collection_link(collection_id: int, payload: CollectionLinkBody):
    try:
        link = await graph.add_link(
            catalog_path(),
            collection_id,
            payload.child_id,
            payload.position,
        )
    except graph.CollectionGraphConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if link is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_meta(payload.child_id)
    return {"ok": True, "link": link}


@router.delete("/api/collections/{collection_id}/links")
async def api_delete_collection_link(
    collection_id: int,
    payload: CollectionLinkBody | None = None,
    child_id: int | None = None,
):
    target_child_id = child_id if child_id is not None else payload.child_id if payload else None
    if target_child_id is None:
        return JSONResponse({"error": "child_id is required"}, status_code=422)
    deleted = await graph.delete_link(catalog_path(), collection_id, target_child_id)
    if deleted is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if not deleted:
        return JSONResponse({"error": "Collection link not found"}, status_code=404)
    await _append_collection_meta(target_child_id)
    return {"ok": True}


@router.get("/api/collections/tree")
async def api_collection_tree():
    return await graph.workspace_tree(catalog_path())


@router.get("/api/collections/{collection_id}/images")
async def api_collection_graph_images(collection_id: int, recursive: int = 0):
    try:
        result = await graph.recursive_images(
            catalog_path(),
            collection_id,
            recursive=bool(recursive),
            resolve_smart_image_ids=_smart_image_ids,
            get_images_by_ids=db.get_active_images_by_ids,
        )
    except graph.CollectionGraphConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    image_ids, images = result
    return {
        "collection_id": int(collection_id),
        "recursive": bool(recursive),
        "count": len(image_ids),
        "image_ids": image_ids,
        "images": images,
    }


@router.get("/api/user-collections/{collection_id}")
async def api_collection(collection_id: int, limit: int = 200, offset: int = 0):
    collection = await db.get_collection(collection_id, limit=limit, offset=offset)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if collection.get("smart"):
        detail = await smart_collections.resolve_detail(collection["query"] or {}, limit=limit, offset=offset, resolve_library_constraints=query_constraints.resolve_configured_library_constraints)
        collection = {**collection, **detail, "smart": True}
    return {"collection": collection}


@router.post("/api/user-collections/{collection_id}/rename")
async def api_rename_collection(collection_id: int, payload: RenameCollectionBody):
    return await _collection_update(collection_id, payload, require_name=True)


@router.post("/api/user-collections/{collection_id}")
async def api_update_collection(collection_id: int, payload: UpdateCollectionBody):
    return await _collection_update(collection_id, payload, require_name=False)


@router.post("/api/user-collections/{collection_id}/delete")
async def api_delete_collection(collection_id: int):
    oplog_payload = await _collection_oplog_payload(collection_id)
    deleted = await db.delete_collection(collection_id)
    if not deleted:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_meta(collection_id, deleted=True, payload=oplog_payload)
    _invalidate_suggestions_cache()
    return {"ok": True}


@router.post("/api/user-collections/{collection_id}/images")
async def api_add_collection_images(collection_id: int, payload: CollectionImagesBody):
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    conflict = await _smart_collection_conflict(collection_id)
    if conflict is not None:
        return conflict
    collection = await db.add_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_memberships(collection_id, payload.image_ids, member=True)
    _invalidate_suggestions_cache()
    return {"ok": True, "collection": collection}


@router.post("/api/user-collections/{collection_id}/images/remove")
async def api_remove_collection_images_post(collection_id: int, payload: CollectionImagesBody):
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    conflict = await _smart_collection_conflict(collection_id)
    if conflict is not None:
        return conflict
    collection = await db.remove_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_memberships(collection_id, payload.image_ids, member=False)
    _invalidate_suggestions_cache()
    return {"ok": True, "collection": collection}


@router.delete("/api/user-collections/{collection_id}/images")
async def api_remove_collection_images(collection_id: int, payload: CollectionImagesBody):
    # Kept for compatibility; both clients call POST /images/remove, and proxies
    # that strip DELETE bodies must.
    return await api_remove_collection_images_post(collection_id, payload)
