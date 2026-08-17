"""Collections, as sets.

Every route here was a repository call over `collections`, `collection_images`
and `collection_links` — three tables holding zero rows on the live catalog,
while the log beside them already carried the memberships. So the tables are
gone and this file is `model.sets` with HTTP on the front.

Two things the old shape got wrong, both fixed by the move rather than patched:

* **Membership survived nothing.** `judgements.membership` filed every collection
  under one `collection_member` family with the collection inside the value, and
  `decisions.current()` keeps the latest row per subject — so a photograph in two
  collections read back as being in one. One family per set makes them independent.
* **Deleting cascaded.** `collection_images` rows had to be swept when a
  collection went, and a photograph's removal had to be swept from every
  collection. Neither exists now: membership is a row saying no, and forgetting a
  set leaves its members readable.

The API speaks image ids because the desktop does; the log speaks content hashes
because a decision outlives a row. That translation is `model.photos.hashes` and
`model.photos.ids` — it belongs with identity, not with collections, and search
was already writing its own copy of half of it.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.catalog_path import catalog_path
from core.requests import parse_exclude_sources
from data import connection
from features.collections import suggestions as collection_suggestions
from model import photos, sets

router = APIRouter()


def db():
    return connection.inline_reader(catalog_path())


def _writer():
    return connection.open_sync(catalog_path())


class CreateCollectionBody(BaseModel):
    name: str = ""
    image_ids: list[int] = Field(default_factory=list)


class RenameCollectionBody(BaseModel):
    name: str = ""


class CollectionImagesBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list)


def _card(conn, set_id: str, name: str) -> dict:
    """The five fields the desktop reads off a collection, and no others.

    `cover_image_id` is the first member rather than a stored choice: a cover
    that is a column is a cover that can point at a photograph the collection no
    longer contains.
    """

    members = photos.ids(conn, sets.members(conn, set_id))
    return {
        "id": set_id,
        "name": name,
        "image_count": len(members),
        "cover_image_id": members[0] if members else None,
        "smart": False,
    }


@router.get("/api/user-collections")
async def api_user_collections():
    conn = db()
    return {"collections": [_card(conn, s["id"], s["name"]) for s in sets.all(conn)]}


@router.post("/api/user-collections")
async def api_create_collection(payload: CreateCollectionBody):
    name = (payload.name or "").strip()
    if not name:
        return JSONResponse({"error": "Collection name is required"}, status_code=400)
    conn = _writer()
    set_id = sets.create(conn, name)
    sets.add(conn, set_id, photos.hashes(conn, payload.image_ids))
    conn.commit()
    return {"ok": True, "collection": _card(conn, set_id, name)}


@router.get("/api/user-collections/{collection_id}")
async def api_collection(collection_id: str, limit: int = 200, offset: int = 0):
    conn = db()
    name = sets.name_of(conn, collection_id)
    if name is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    members = photos.ids(conn, sets.members(conn, collection_id))
    card = _card(conn, collection_id, name)
    return {"collection": {**card, "image_ids": members[offset:offset + limit]}}


@router.post("/api/user-collections/{collection_id}/rename")
async def api_rename_collection(collection_id: str, payload: RenameCollectionBody):
    name = (payload.name or "").strip()
    if not name:
        return JSONResponse({"error": "Collection name is required"}, status_code=400)
    conn = _writer()
    if sets.name_of(conn, collection_id) is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    sets.rename(conn, collection_id, name)
    conn.commit()
    return {"ok": True, "collection": _card(conn, collection_id, name)}


@router.post("/api/user-collections/{collection_id}")
async def api_update_collection(collection_id: str, payload: RenameCollectionBody):
    return await api_rename_collection(collection_id, payload)


@router.post("/api/user-collections/{collection_id}/delete")
async def api_delete_collection(collection_id: str):
    conn = _writer()
    if sets.name_of(conn, collection_id) is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    sets.forget(conn, collection_id)
    conn.commit()
    return {"ok": True}


async def _membership(collection_id: str, image_ids, *, member: bool):
    if not image_ids:
        return JSONResponse({"error": "image_ids is required"}, status_code=400)
    conn = _writer()
    name = sets.name_of(conn, collection_id)
    if name is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    say = sets.add if member else sets.remove
    say(conn, collection_id, photos.hashes(conn, image_ids))
    conn.commit()
    return {"ok": True, "collection": _card(conn, collection_id, name)}


@router.post("/api/user-collections/{collection_id}/images")
async def api_add_collection_images(collection_id: str, payload: CollectionImagesBody):
    return await _membership(collection_id, payload.image_ids, member=True)


@router.post("/api/user-collections/{collection_id}/images/remove")
async def api_remove_collection_images_post(collection_id: str, payload: CollectionImagesBody):
    return await _membership(collection_id, payload.image_ids, member=False)


@router.delete("/api/user-collections/{collection_id}/images")
async def api_remove_collection_images(collection_id: str, payload: CollectionImagesBody):
    # Both clients call POST /images/remove; this stays for proxies that strip
    # DELETE bodies.
    return await _membership(collection_id, payload.image_ids, member=False)


@router.get("/api/collections/tree")
async def api_collection_tree():
    """The workspace tree, which is flat.

    `collection_links` held zero rows, so there was never a tree — 254 lines of
    graph walking, cycle detection and a `CollectionGraphConflict` existed for a
    nesting nobody had created. Every set is a root until something asks
    otherwise, and the desktop already defaults this shape to empty.
    """

    conn = db()
    nodes = [_card(conn, s["id"], s["name"]) for s in sets.all(conn)]
    return {"nodes": nodes, "links": [], "root_ids": [n["id"] for n in nodes]}


@router.get("/api/collections/suggestions")
async def api_collection_suggestions(exclude_sources: str = ""):
    payload = await collection_suggestions.collection_suggestions(
        catalog_path(), db_signature=catalog_path()
    )
    excluded = parse_exclude_sources(exclude_sources)
    if not excluded:
        return payload
    return await collection_suggestions.filter_suggestions_excluding_sources(
        catalog_path(), payload, excluded
    )


@router.get("/api/collections/{collection_id}/images")
async def api_collection_images(collection_id: str, recursive: int = 0):
    """Was the collection graph: nesting, recursion, and a conflict class.

    Nothing in the desktop asks for a nested collection, and `collection_links`
    held zero rows, so recursion had no tree to walk. The parameter is accepted
    and ignored rather than 400ing a caller that still sends it.
    """

    conn = db()
    if sets.name_of(conn, collection_id) is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    image_ids = photos.ids(conn, sets.members(conn, collection_id))
    return {
        "collection_id": collection_id,
        "recursive": False,
        "count": len(image_ids),
        "image_ids": image_ids,
    }
