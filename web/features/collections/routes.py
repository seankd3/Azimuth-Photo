from core.requests import parse_exclude_sources
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.collections import graph
from features.collections import smart
from features.collections import suggestions as collection_suggestions
from features.sync import oplog


router = APIRouter()
CreateCollection = Callable[..., Awaitable[dict]]
ListCollections = Callable[[], Awaitable[list[dict]]]
GetCollection = Callable[..., Awaitable[dict | None]]
RenameCollection = Callable[..., Awaitable[dict | None]]
DeleteCollection = Callable[[int], Awaitable[bool]]
MutateCollectionImages = Callable[[int, list[int]], Awaitable[dict | None]]
GetSuggestions = Callable[[], Awaitable[dict]]
CollectionIsSmart = Callable[[int], Awaitable[bool | None]]
ResolveSmartDetail = Callable[..., Awaitable[dict]]
ResolveSmartSummary = Callable[[dict], Awaitable[dict]]
ResolveSmartImageIds = Callable[[dict], Awaitable[list[int]]]
DbPath = Callable[[], str]
GetImagesByIds = Callable[[list[int]], Awaitable[dict[int, dict]]]

MAX_IMAGE_IDS_PER_REQUEST = 10000
MAX_COLLECTION_NAME_LENGTH = 160

_create_collection: CreateCollection | None = None
_list_collections: ListCollections | None = None
_get_collection: GetCollection | None = None
_rename_collection: RenameCollection | None = None
_delete_collection: DeleteCollection | None = None
_add_collection_images: MutateCollectionImages | None = None
_remove_collection_images: MutateCollectionImages | None = None
_get_suggestions: GetSuggestions | None = None
_collection_is_smart: CollectionIsSmart | None = None
_resolve_smart_detail: ResolveSmartDetail | None = None
_resolve_smart_summary: ResolveSmartSummary | None = None
_resolve_smart_image_ids: ResolveSmartImageIds | None = None
_resolve_smart_materialized_image_ids: ResolveSmartImageIds | None = None
_db_path: DbPath | None = None
_get_images_by_ids: GetImagesByIds | None = None


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


def configure(
    *,
    create_collection: CreateCollection,
    list_collections: ListCollections,
    get_collection: GetCollection,
    rename_collection: RenameCollection,
    delete_collection: DeleteCollection,
    add_collection_images: MutateCollectionImages,
    remove_collection_images: MutateCollectionImages,
    get_suggestions: GetSuggestions | None = None,
    collection_is_smart: CollectionIsSmart | None = None,
    resolve_smart_detail: ResolveSmartDetail | None = None,
    resolve_smart_summary: ResolveSmartSummary | None = None,
    resolve_smart_image_ids: ResolveSmartImageIds | None = None,
    resolve_smart_materialized_image_ids: ResolveSmartImageIds | None = None,
    db_path: DbPath | None = None,
    get_images_by_ids: GetImagesByIds | None = None,
) -> None:
    global _create_collection, _list_collections, _get_collection
    global _rename_collection, _delete_collection
    global _add_collection_images, _remove_collection_images, _get_suggestions
    global _collection_is_smart, _resolve_smart_detail, _resolve_smart_summary
    global _resolve_smart_image_ids, _resolve_smart_materialized_image_ids
    global _db_path, _get_images_by_ids
    _create_collection = create_collection
    _list_collections = list_collections
    _get_collection = get_collection
    _rename_collection = rename_collection
    _delete_collection = delete_collection
    _add_collection_images = add_collection_images
    _remove_collection_images = remove_collection_images
    _get_suggestions = get_suggestions
    _collection_is_smart = collection_is_smart
    _resolve_smart_detail = resolve_smart_detail
    _resolve_smart_summary = resolve_smart_summary
    _resolve_smart_image_ids = resolve_smart_image_ids
    _resolve_smart_materialized_image_ids = resolve_smart_materialized_image_ids
    _db_path = db_path
    _get_images_by_ids = get_images_by_ids


def _configured() -> None:
    if (
        _create_collection is None
        or _list_collections is None
        or _get_collection is None
        or _rename_collection is None
        or _delete_collection is None
        or _add_collection_images is None
        or _remove_collection_images is None
    ):
        raise RuntimeError("Collection routes are not configured")


def _graph_configured() -> None:
    _configured()
    if _db_path is None or _get_images_by_ids is None or _resolve_smart_image_ids is None:
        raise RuntimeError("Collection graph routes are not configured")


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
    if _db_path is None:
        return None
    return await oplog.collection_meta_payload(_db_path(), collection_id)


async def _append_collection_meta(collection_id: int, *, deleted: bool = False, payload: dict | None = None) -> None:
    if _db_path is None:
        return
    payload = payload or await _collection_oplog_payload(collection_id)
    if payload is None:
        return
    await oplog.append_collection_meta(_db_path(), {**payload, "deleted": deleted})


async def _append_collection_memberships(collection_id: int, image_ids: list[int], *, member: bool) -> None:
    if _db_path is None:
        return
    await oplog.append_collection_memberships(_db_path(), collection_id, image_ids, member=member)


async def _smart_collection_conflict(collection_id: int) -> JSONResponse | None:
    if _collection_is_smart is None:
        return None
    smart_state = await _collection_is_smart(collection_id)
    if smart_state is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if smart_state:
        return JSONResponse(
            {"error": "Smart collections are live queries; materialize before editing membership."},
            status_code=409,
        )
    return None


async def _with_smart_summary(collection: dict) -> dict:
    if not collection.get("smart") or _resolve_smart_summary is None:
        return collection
    summary = await _resolve_smart_summary(collection["query"] or {})
    return {**collection, **summary, "smart": True}


async def _collection_update(
    collection_id: int,
    payload,
    *,
    require_name: bool,
):
    _configured()
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
        if _resolve_smart_materialized_image_ids is None:
            raise RuntimeError("Collection routes are not configured")
        current = await _get_collection(collection_id, limit=1, offset=0)
        if current is None:
            return JSONResponse({"error": "Collection not found"}, status_code=404)
        if current.get("smart"):
            try:
                materialize_ids = await _resolve_smart_materialized_image_ids(current["query"] or {})
            except smart.SmartCollectionMaterializeTooLarge as exc:
                return JSONResponse(
                    {
                        "error": "Smart collection is too large to materialize",
                        "image_count": exc.count,
                        "limit": exc.limit,
                    },
                    status_code=409,
                )

    collection = await _rename_collection(
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
    _configured()
    collections = []
    for collection in await _list_collections():
        collections.append(await _with_smart_summary(collection))
    return {"collections": collections}


@router.post("/api/user-collections")
async def api_create_collection(payload: CreateCollectionBody):
    _configured()
    try:
        query_json = smart.query_to_json(payload.query)
    except smart.SmartCollectionQueryError as exc:
        return _invalid_query_response(exc)
    collection = await _create_collection(
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
    _configured()
    if _get_suggestions is None:
        return {"suggestions": []}
    payload = await _get_suggestions()
    excluded = parse_exclude_sources(exclude_sources)
    if not excluded or _db_path is None:
        return payload
    return await collection_suggestions.filter_suggestions_excluding_sources(
        _db_path(),
        payload,
        excluded,
    )


@router.post("/api/collections/{collection_id}/links")
async def api_add_collection_link(collection_id: int, payload: CollectionLinkBody):
    _graph_configured()
    try:
        link = await graph.add_link(
            _db_path(),
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
    _graph_configured()
    target_child_id = child_id if child_id is not None else payload.child_id if payload else None
    if target_child_id is None:
        return JSONResponse({"error": "child_id is required"}, status_code=422)
    deleted = await graph.delete_link(_db_path(), collection_id, target_child_id)
    if deleted is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if not deleted:
        return JSONResponse({"error": "Collection link not found"}, status_code=404)
    await _append_collection_meta(target_child_id)
    return {"ok": True}


@router.get("/api/collections/tree")
async def api_collection_tree():
    _graph_configured()
    return await graph.workspace_tree(_db_path())


@router.get("/api/collections/{collection_id}/images")
async def api_collection_graph_images(collection_id: int, recursive: int = 0):
    _graph_configured()
    try:
        result = await graph.recursive_images(
            _db_path(),
            collection_id,
            recursive=bool(recursive),
            resolve_smart_image_ids=_resolve_smart_image_ids,
            get_images_by_ids=_get_images_by_ids,
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
    _configured()
    collection = await _get_collection(collection_id, limit=limit, offset=offset)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if collection.get("smart"):
        if _resolve_smart_detail is None:
            raise RuntimeError("Collection routes are not configured")
        detail = await _resolve_smart_detail(collection["query"] or {}, limit=limit, offset=offset)
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
    _configured()
    oplog_payload = await _collection_oplog_payload(collection_id)
    deleted = await _delete_collection(collection_id)
    if not deleted:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_meta(collection_id, deleted=True, payload=oplog_payload)
    _invalidate_suggestions_cache()
    return {"ok": True}


@router.post("/api/user-collections/{collection_id}/images")
async def api_add_collection_images(collection_id: int, payload: CollectionImagesBody):
    _configured()
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    conflict = await _smart_collection_conflict(collection_id)
    if conflict is not None:
        return conflict
    collection = await _add_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_memberships(collection_id, payload.image_ids, member=True)
    _invalidate_suggestions_cache()
    return {"ok": True, "collection": collection}


@router.post("/api/user-collections/{collection_id}/images/remove")
async def api_remove_collection_images_post(collection_id: int, payload: CollectionImagesBody):
    _configured()
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    conflict = await _smart_collection_conflict(collection_id)
    if conflict is not None:
        return conflict
    collection = await _remove_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_memberships(collection_id, payload.image_ids, member=False)
    _invalidate_suggestions_cache()
    return {"ok": True, "collection": collection}


@router.delete("/api/user-collections/{collection_id}/images")
async def api_remove_collection_images(collection_id: int, payload: CollectionImagesBody):
    # Kept for compatibility; proxies that strip DELETE bodies should use the
    # POST /images/remove route instead.
    _configured()
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    conflict = await _smart_collection_conflict(collection_id)
    if conflict is not None:
        return conflict
    collection = await _remove_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    await _append_collection_memberships(collection_id, payload.image_ids, member=False)
    _invalidate_suggestions_cache()
    return {"ok": True, "collection": collection}
