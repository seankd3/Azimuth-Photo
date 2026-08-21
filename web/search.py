"""Search: one query, ranked fusion, and it never refuses to answer.

Three ways of finding a photograph, combined by rank rather than by score.

**Lexical** — the filename, the folder it sits in, the camera, the lens, the
date. Always available, because every one of those facts is in the catalog the
moment the photo is read.

**Named** — the sets you made. A keyword and a collection are one thing
(`model/sets`), so a query that matches a set's name finds its members:
"japan" finds everything you keyworded `travel/japan`, and "wedding" finds the
collection so named, through the same door.

**Semantic** — the embedding space, when it is loaded and the query can be
turned into a vector. The caller passes both; this module never loads a model
and never reads the space off disk, because the typing path must never wait
for either. A caller that has them passes them, a caller that does not passes
None and gets words-only results immediately. Nothing in here blocks, and
nothing in here should learn how.

> It degrades, never blocks, and never says *not enough photos*.

That last clause is a rule, not a nicety. A search that refuses until coverage
is complete is useless for the months that coverage takes — and the library is
never at 100%, because new photographs arrive faster than vectors are made.

**Fusion is by rank, not by score.** Reciprocal rank fusion — `1/(60 + rank)`
summed across the lists a result appears in — because a cosine similarity and
a LIKE match are not on the same scale and never will be. Normalising them
against each other requires a constant that is wrong for some query, and the
symptom is a filename match losing to a vaguely related photo. Ranks have no
units, so there is nothing to calibrate.

The answer is a ranked list of image ids and nothing more. Fetching rows,
tiles and pages is `library.photos` over an ids scope, the same as every
other surface; search only decides which ids and in what order.
"""

from __future__ import annotations

from model import sets
from model.scope import EVERYTHING, Scope, where as scope_where

# The constant from the reciprocal-rank-fusion paper. It flattens the
# difference between rank 1 and rank 2 just enough that agreement between two
# lists beats a single list's confidence.
RRF_K = 60

IN_LIBRARY = "i.status != 'trashed' AND i.tail IS NOT NULL"


def _lexical(conn, query: str, limit: int, scope: Scope) -> list[int]:
    """Filename, folder, camera, lens, date — everything that is words about
    a photo.

    `LIKE` is deliberate here and safe: this is a human's substring search
    over text columns, not a path prefix test. Where a *prefix* is meant —
    folder browsing — `library.photos` uses `substr()` instead, because
    `LIKE` is ASCII-case-insensitive and a bracket in a folder name would
    become a character class.
    """

    like = f"%{query}%"
    clause, args = scope_where(scope)
    return [row["id"] for row in conn.execute(
        f"""
        SELECT i.id FROM images i
        WHERE {IN_LIBRARY} AND ({clause}) AND (
              i.tail LIKE ? OR i.camera_make LIKE ? OR i.camera_model LIKE ?
              OR i.lens LIKE ? OR i.date_taken LIKE ?)
        ORDER BY i.date_taken DESC, i.id DESC LIMIT ?
        """,
        (*args, like, like, like, like, like, int(limit)),
    )]


def _named(conn, query: str, limit: int, scope: Scope) -> list[int]:
    """Members of every set whose name contains the query.

    A keyword and a collection are the same thing with different shelves, so
    one match rule serves both, and a photograph in two matching sets is in
    the list once, newest first.
    """

    wanted = query.lower()
    members: set[str] = set()
    for entry in sets.all(conn):
        if wanted in str(entry.get("name", "")).lower():
            members.update(sets.members(conn, entry["id"]))
    if not members:
        return []
    marks = ",".join("?" for _ in members)
    clause, args = scope_where(scope)
    return [row["id"] for row in conn.execute(
        f"SELECT i.id FROM images i WHERE {IN_LIBRARY} AND ({clause})"
        f" AND i.content_hash IN ({marks})"
        f" ORDER BY i.date_taken DESC, i.id DESC LIMIT ?",
        (*args, *sorted(members), int(limit)),
    )]


def _semantic(conn, space, query_vector, limit: int, scope: Scope) -> list[int]:
    """Nearest photographs in the embedding space, or nothing at all."""

    if query_vector is None or space is None:
        return []
    subjects, matrix = space
    if matrix is None or not len(subjects):
        return []

    import numpy as np

    q = np.asarray(query_vector, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    similarity = matrix @ q
    keep = min(int(limit), len(subjects))
    top = np.argpartition(similarity, -keep)[-keep:]
    ranked = [subjects[i] for i in top[np.argsort(similarity[top])[::-1]]]
    marks = ",".join("?" for _ in ranked)
    clause, args = scope_where(scope)
    found: dict[str, int] = {}
    for row in conn.execute(
        f"SELECT i.id, i.content_hash AS hash FROM images i"
        f" WHERE {IN_LIBRARY} AND ({clause}) AND i.content_hash IN ({marks})"
        f" ORDER BY i.id DESC",
        (*args, *ranked),
    ):
        found.setdefault(row["hash"], row["id"])  # one row per identity
    return [found[h] for h in ranked if h in found]


def fuse(*lists: list[int], limit: int) -> list[int]:
    """Reciprocal rank fusion. Ranks have no units, so nothing needs
    calibrating."""

    score: dict[int, float] = {}
    for ranked in lists:
        for position, image_id in enumerate(ranked):
            score[image_id] = score.get(image_id, 0.0) + 1.0 / (RRF_K + position + 1)
    ordered = sorted(score.items(), key=lambda pair: -pair[1])
    return [image_id for image_id, _ in ordered[:int(limit)]]


def search(conn, query: str, *, space=None, query_vector=None,
           scope: Scope = EVERYTHING, limit: int = 500) -> list[int]:
    """Find photographs: one ranked list of ids, from whatever is available.

    `scope` is the view being searched -- a folder, a collection, the chips
    -- so searching inside a collection is this same function and not a
    second one."""

    query = (query or "").strip()
    if not query:
        return []
    ranked = fuse(
        _lexical(conn, query, limit, scope),
        _named(conn, query, limit, scope),
        _semantic(conn, space, query_vector, limit, scope),
        limit=limit * 2,
    )
    if not ranked:
        return []
    # One photograph once. The same identity filed in two folders is two
    # rows, and two doors may answer with different rows of it; a search is
    # about photographs, so an identity keeps only its best rank. A row not
    # yet identified has nothing to collapse on and stands alone.
    marks = ",".join("?" for _ in ranked)
    identity = {int(row["id"]): row["hash"] for row in conn.execute(
        f"SELECT id, content_hash AS hash FROM images WHERE id IN ({marks})", ranked)}
    seen: set[str] = set()
    out: list[int] = []
    for image_id in ranked:
        digest = identity.get(image_id)
        if digest:
            if digest in seen:
                continue
            seen.add(digest)
        out.append(image_id)
        if len(out) == int(limit):
            break
    return out
