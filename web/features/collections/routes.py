from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter()
CreateCollection = Callable[..., Awaitable[dict]]
ListCollections = Callable[[], Awaitable[list[dict]]]
GetCollection = Callable[..., Awaitable[dict | None]]
MutateCollectionImages = Callable[[int, list[int]], Awaitable[dict | None]]

_create_collection: CreateCollection | None = None
_list_collections: ListCollections | None = None
_get_collection: GetCollection | None = None
_add_collection_images: MutateCollectionImages | None = None
_remove_collection_images: MutateCollectionImages | None = None


def configure(
    *,
    create_collection: CreateCollection,
    list_collections: ListCollections,
    get_collection: GetCollection,
    add_collection_images: MutateCollectionImages,
    remove_collection_images: MutateCollectionImages,
) -> None:
    global _create_collection, _list_collections, _get_collection
    global _add_collection_images, _remove_collection_images
    _create_collection = create_collection
    _list_collections = list_collections
    _get_collection = get_collection
    _add_collection_images = add_collection_images
    _remove_collection_images = remove_collection_images


def _configured() -> None:
    if (
        _create_collection is None
        or _list_collections is None
        or _get_collection is None
        or _add_collection_images is None
        or _remove_collection_images is None
    ):
        raise RuntimeError("Collection routes are not configured")


@router.get("/api/user-collections")
async def api_user_collections():
    _configured()
    return {"collections": await _list_collections()}


@router.post("/api/user-collections")
async def api_create_collection(payload: dict):
    _configured()
    collection = await _create_collection(
        name=payload.get("name") or "",
        description=payload.get("description") or "",
        image_ids=payload.get("image_ids") or [],
        visibility=payload.get("visibility") or "private",
        status=payload.get("status") or "draft",
    )
    return {"ok": True, "collection": collection}


@router.get("/api/user-collections/{collection_id}")
async def api_collection(collection_id: int, limit: int = 200, offset: int = 0):
    _configured()
    collection = await _get_collection(collection_id, limit=limit, offset=offset)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"collection": collection}


@router.post("/api/user-collections/{collection_id}/images")
async def api_add_collection_images(collection_id: int, payload: dict):
    _configured()
    collection = await _add_collection_images(collection_id, payload.get("image_ids") or [])
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "collection": collection}


@router.delete("/api/user-collections/{collection_id}/images")
async def api_remove_collection_images(collection_id: int, payload: dict):
    _configured()
    collection = await _remove_collection_images(collection_id, payload.get("image_ids") or [])
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "collection": collection}
