"""Keywords, as sets. And IPTC, which is not one.

A keyword is a named group of photographs, which is what a set is — so the
storage half of `keywords.py` (285 lines of table, tree, ancestor walk and
cascade over `keywords` 1 row and `image_keywords` 77, while the log carried
`keyword` 296) is `model.sets` with `kind="keyword"`.

**The hierarchy is the name.** `Animals > Cats` has depth 2 and parent `Animals`,
so there is no parent column, no ancestor walk, no cycle check and no re-parent
operation — renaming a branch is renaming a prefix. This is the trick
`library.folders` already plays on tails, and it is why neither needs a tree.

`parent_id` and `move` are gone from the API for the same reason. Nothing in the
desktop sent them: it posts a path to `/resolve` and reads back `{id, path,
depth, direct}`.

IPTC stays where it was. Title, caption, copyright and creator are fields *on a
photograph*, not a group of them, and they belong to the XMP half of
`keywords.py` that writes into the file.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.catalog_path import catalog_path
from data import connection
from features.library import keywords
from model import photos, sets

router = APIRouter(tags=["keywords"])


def db():
    return connection.reading(catalog_path())


class KeywordCreate(BaseModel):
    name: str = Field(max_length=180)


class KeywordPath(BaseModel):
    path: str = Field(max_length=2000)


class KeywordUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=180)


class KeywordAssignment(BaseModel):
    keyword_id: str
    image_ids: list[int] = Field(min_length=1, max_length=10_000)


class IptcFields(BaseModel):
    title: str = Field(default="", max_length=10_000)
    caption: str = Field(default="", max_length=10_000)
    copyright: str = Field(default="", max_length=10_000)
    creator: str = Field(default="", max_length=10_000)


def _card(said: dict, tally: dict) -> dict:
    """`{id, path, depth, direct}` — the four fields the panel reads.

    `depth` and the hierarchy come out of the path because the path *is* the
    hierarchy. `tally` is passed in rather than looked up: counting inside here
    would be one query per keyword.
    """

    path = said["name"]
    return {
        "id": said["id"],
        "path": path,
        "depth": path.count(">") + 1,
        "direct": int(tally.get(said["id"], 0)),
    }


def _resolve(conn, path: str) -> dict:
    """The keyword at this path, minted if it is new. Idempotent by name."""

    path = keywords.clean_path(path)
    for said in sets.all(conn, kind=sets.KEYWORD):
        if said["name"] == path:
            return said
    return {"id": sets.create(conn, path, kind=sets.KEYWORD), "name": path, "kind": sets.KEYWORD}


@router.get("/api/keywords")
async def api_keywords(q: str = ""):
    conn = db()
    found = sets.all(conn, kind=sets.KEYWORD)
    if q:
        needle = q.strip().lower()
        found = [s for s in found if needle in str(s["name"]).lower()]
    tally = sets.counts(conn)
    return {"keywords": [_card(s, tally) for s in found]}


@router.post("/api/keywords", status_code=201)
async def api_create_keyword(body: KeywordCreate):
    try:
        def job(conn):
            said = _resolve(conn, body.name)
            conn.commit()
            return _card(said, sets.counts(conn))

        return {"keyword": await connection.writing(catalog_path(), job)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/api/keywords/resolve", status_code=201)
async def api_resolve_keyword(body: KeywordPath):
    return await api_create_keyword(KeywordCreate(name=body.path))


@router.patch("/api/keywords/{keyword_id}")
async def api_update_keyword(keyword_id: str, body: KeywordUpdate):
    try:
        def job(conn):
            said = sets.amend(conn, keyword_id, name=keywords.clean_path(body.name or ""))
            if said is None:
                return None
            conn.commit()
            return _card(said, sets.counts(conn))

        card = await connection.writing(catalog_path(), job)
        if card is None:
            raise HTTPException(status_code=404, detail="No such keyword")
        return {"keyword": card}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/api/keywords/{keyword_id}", status_code=204)
async def api_delete_keyword(keyword_id: str):
    def job(conn):
        if sets.describe(conn, keyword_id) is None:
            return False
        sets.forget(conn, keyword_id)
        conn.commit()
        return True

    if not await connection.writing(catalog_path(), job):
        raise HTTPException(status_code=404, detail="No such keyword")


def _ancestors(path: str) -> list[str]:
    """`Animals > Cats > Maine Coon` -> Animals, Animals > Cats. From the name."""

    parts = path.split(keywords.SEPARATOR)
    return [keywords.SEPARATOR.join(parts[:i]) for i in range(1, len(parts))]


@router.get("/api/images/{image_id}/keywords")
async def api_image_keywords(image_id: int):
    """Direct keywords, plus the ancestors they imply.

    A photograph keyworded `Animals > Cats` is a cat and is also an animal, so
    the panel is told both — `direct` says which it actually carries, and it
    filters chips on exactly that. The ancestors are read off the path, so
    nothing stores them and they cannot disagree with the leaf.
    """

    conn = db()
    digest = photos.hashes(conn, [image_id])
    if not digest:
        return {"keywords": []}
    tally = sets.counts(conn)
    by_name = {s["name"]: s for s in sets.all(conn, kind=sets.KEYWORD)}
    direct = [sets.describe(conn, i) for i in sets.sets_of(conn, digest[0], kind=sets.KEYWORD)]

    shown: dict[str, dict] = {}
    for said in (s for s in direct if s):
        for name in _ancestors(said["name"]):
            if name in by_name:
                shown.setdefault(name, {**_card(by_name[name], tally), "direct": 0})
        shown[said["name"]] = {**_card(said, tally), "direct": 1}
    return {"keywords": [shown[name] for name in sorted(shown)]}


@router.get("/api/keywords/{keyword_id}/images")
async def api_keyword_images(keyword_id: str):
    """Everything under this keyword, not only what carries it exactly.

    Asking for `Animals` means the cats too. The hierarchy is the name, so the
    descendants are a prefix match — the same shape `library.folders` uses on
    tails, and the reason neither needs a closure table.
    """

    conn = db()
    said = sets.describe(conn, keyword_id)
    if said is None:
        return {"image_ids": []}
    under = said["name"] + keywords.SEPARATOR
    digests = set()
    for other in sets.all(conn, kind=sets.KEYWORD):
        if other["name"] == said["name"] or other["name"].startswith(under):
            digests.update(sets.members(conn, other["id"]))
    return {"image_ids": photos.ids(conn, sorted(digests))}


def _identified(conn, image_ids) -> list[str]:
    """The identities of these photographs, or a refusal naming the shortfall.

    A keyword is a decision, and a decision is about a photograph's identity —
    so one that has not been hashed yet cannot carry one. Returning "assigned:
    0" would be the silent kind of wrong: the panel would show the chip, the
    keyword would never exist, and nothing would say why.
    """

    digests = photos.hashes(conn, image_ids)
    if len(digests) < len(set(int(i) for i in image_ids if int(i) > 0)):
        raise HTTPException(
            status_code=409,
            detail="Some photographs are not identified yet; they cannot carry a keyword.",
        )
    return digests


@router.post("/api/keywords/assign")
async def api_assign_keyword(body: KeywordAssignment):
    def job(conn):
        if sets.describe(conn, body.keyword_id) is None:
            return None
        written = sets.add(conn, body.keyword_id, _identified(conn, body.image_ids))
        conn.commit()
        return written

    written = await connection.writing(catalog_path(), job)
    if written is None:
        raise HTTPException(status_code=404, detail="No such keyword")
    return {"assigned": written}


@router.post("/api/keywords/unassign")
async def api_unassign_keyword(body: KeywordAssignment):
    def job(conn):
        written = sets.remove(conn, body.keyword_id, _identified(conn, body.image_ids))
        conn.commit()
        return written

    return {"unassigned": await connection.writing(catalog_path(), job)}


@router.get("/api/images/{image_id}/iptc")
async def api_get_iptc(image_id: int):
    return {"iptc": await keywords.get_iptc(image_id)}


@router.put("/api/images/{image_id}/iptc")
async def api_save_iptc(image_id: int, body: IptcFields):
    try:
        return {"iptc": await keywords.save_iptc(image_id, **body.model_dump())}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
