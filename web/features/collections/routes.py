from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


router = APIRouter()
CreateCollection = Callable[..., Awaitable[dict]]
ListCollections = Callable[[], Awaitable[list[dict]]]
GetCollection = Callable[..., Awaitable[dict | None]]
RenameCollection = Callable[..., Awaitable[dict | None]]
DeleteCollection = Callable[[int], Awaitable[bool]]
MutateCollectionImages = Callable[[int, list[int]], Awaitable[dict | None]]
GetSuggestions = Callable[[], Awaitable[dict]]

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


class CreateCollectionBody(BaseModel):
    name: str = ""
    description: str = ""
    image_ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)
    visibility: str = "private"
    status: str = "draft"


class CollectionImagesBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)


class RenameCollectionBody(BaseModel):
    name: str = ""


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
) -> None:
    global _create_collection, _list_collections, _get_collection
    global _rename_collection, _delete_collection
    global _add_collection_images, _remove_collection_images, _get_suggestions
    _create_collection = create_collection
    _list_collections = list_collections
    _get_collection = get_collection
    _rename_collection = rename_collection
    _delete_collection = delete_collection
    _add_collection_images = add_collection_images
    _remove_collection_images = remove_collection_images
    _get_suggestions = get_suggestions


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


def _clean_collection_name(name: str) -> str | None:
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > MAX_COLLECTION_NAME_LENGTH:
        return None
    return clean_name


@router.get("/api/user-collections")
async def api_user_collections():
    _configured()
    return {"collections": await _list_collections()}


@router.post("/api/user-collections")
async def api_create_collection(payload: CreateCollectionBody):
    _configured()
    collection = await _create_collection(
        name=payload.name,
        description=payload.description,
        image_ids=payload.image_ids,
        visibility=payload.visibility,
        status=payload.status,
    )
    return {"ok": True, "collection": collection}


@router.get("/api/collections/suggestions")
async def api_collection_suggestions():
    _configured()
    if _get_suggestions is None:
        return {"suggestions": []}
    return await _get_suggestions()


@router.get("/api/user-collections/{collection_id}")
async def api_collection(collection_id: int, limit: int = 200, offset: int = 0):
    _configured()
    collection = await _get_collection(collection_id, limit=limit, offset=offset)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"collection": collection}


@router.post("/api/user-collections/{collection_id}/rename")
async def api_rename_collection(collection_id: int, payload: RenameCollectionBody):
    _configured()
    clean_name = _clean_collection_name(payload.name)
    if clean_name is None:
        return JSONResponse({"error": "Collection name is required"}, status_code=400)
    collection = await _rename_collection(collection_id, name=clean_name)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "collection": collection}


@router.post("/api/user-collections/{collection_id}/delete")
async def api_delete_collection(collection_id: int):
    _configured()
    deleted = await _delete_collection(collection_id)
    if not deleted:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True}


@router.post("/api/user-collections/{collection_id}/images")
async def api_add_collection_images(collection_id: int, payload: CollectionImagesBody):
    _configured()
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    collection = await _add_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "collection": collection}


@router.post("/api/user-collections/{collection_id}/images/remove")
async def api_remove_collection_images_post(collection_id: int, payload: CollectionImagesBody):
    _configured()
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    collection = await _remove_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "collection": collection}


@router.delete("/api/user-collections/{collection_id}/images")
async def api_remove_collection_images(collection_id: int, payload: CollectionImagesBody):
    # Kept for compatibility; proxies that strip DELETE bodies should use the
    # POST /images/remove route instead.
    _configured()
    if not payload.image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    collection = await _remove_collection_images(collection_id, payload.image_ids)
    if collection is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "collection": collection}
