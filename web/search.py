"""Search: one query, ranked fusion, and it never refuses to answer.

Two ways of finding a photograph, combined by rank rather than by score.

**Lexical** — the filename, the folder it sits in, the camera, the lens, the
date, and any keyword you gave it. Always available, because every one of those
facts is in the catalog the moment the photo is.

**Semantic** — the embedding space, when the photograph has a vector and the
query can be turned into one. 42,937 of 157,064 have vectors today.

> It degrades, never blocks, and never says *not enough photos*.

That last clause is a rule, not a nicety. A search that refuses until coverage
is complete is a search that is useless for the eighteen months of computing
that coverage takes — and this library will never be at 100%, because new
photographs arrive faster than embeddings are made for them.

**Fusion is by rank, not by score.** Reciprocal rank fusion — `1/(60 + rank)`
summed across the lists a result appears in — because a cosine similarity and
an FTS relevance are not on the same scale and never will be. Normalising them
against each other requires a constant that is wrong for some query, and the
symptom is a filename match losing to a vaguely-related photo. Ranks have no
units, so there is nothing to calibrate.
"""

from __future__ import annotations

import struct

from model import cache, decisions

# The constant from the reciprocal-rank-fusion paper. It flattens the
# difference between rank 1 and rank 2 just enough that agreement between two
# lists beats a single list's confidence.
RRF_K = 60

EMBEDDING = "embedding"


def _lexical(conn, query: str, limit: int) -> list[int]:
    """Filename, folder, camera, lens — everything that is words about a photo.

    `LIKE` is deliberate here and safe: this is a human's substring search over
    a text column, not a path prefix test. Where a *prefix* is meant — folder
    browsing — `library.photos` uses `substr()` instead, because `LIKE` is
    ASCII-case-insensitive and a bracket in a folder name would become a
    character class.
    """

    like = f"%{query.strip()}%"
    return [row["id"] for row in conn.execute(
        """
        SELECT i.id FROM images i
        WHERE i.status != 'trashed' AND (
              i.tail LIKE ? OR i.camera_model LIKE ? OR i.lens LIKE ?
              OR i.date_taken LIKE ?)
        ORDER BY i.date_taken DESC LIMIT ?
        """,
        (like, like, like, like, int(limit)),
    )]


def _by_keyword(conn, query: str, limit: int) -> list[int]:
    """Photographs you named. A keyword is a decision, so this reads the log."""

    wanted = query.strip().lower()
    hits = [
        subject for subject, value in decisions.current(conn, "keyword").items()
        if isinstance(value, str) and wanted in value.lower()
    ]
    if not hits:
        return []
    holes = ",".join("?" for _ in hits[:900])
    return [row["id"] for row in conn.execute(
        f"SELECT id FROM images WHERE content_hash IN ({holes}) AND status != 'trashed' LIMIT ?",
        (*hits[:900], int(limit)),
    )]


def _vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _semantic(conn, query_vector, limit: int) -> list[int]:
    """Nearest photographs in the embedding space, or nothing at all.

    Reads every vector as one matrix. That is why embeddings live in the cache
    row's `value` and not as files on disk: 42,937 file opens is a different
    kind of operation from one read, and reclaim would have mistaken them for
    previews.
    """

    if query_vector is None:
        return []
    import numpy as np

    rows = conn.execute(
        "SELECT c.hash, c.value FROM cache c WHERE c.kind = ? AND c.state = 'ready'",
        (EMBEDDING,),
    ).fetchall()
    if not rows:
        return []

    matrix = np.array([_vector(r["value"]) for r in rows], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    q = np.asarray(query_vector, dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9

    order = np.argsort(matrix @ q)[::-1][:limit]
    hashes = [rows[i]["hash"] for i in order]
    holes = ",".join("?" for _ in hashes)
    found = {
        row["content_hash"]: row["id"]
        for row in conn.execute(
            f"SELECT id, content_hash FROM images WHERE content_hash IN ({holes}) AND status != 'trashed'",
            hashes,
        )
    }
    return [found[h] for h in hashes if h in found]


def fuse(*lists: list[int], limit: int = 200) -> list[int]:
    """Reciprocal rank fusion. Ranks have no units, so nothing needs calibrating."""

    score: dict[int, float] = {}
    for ranked in lists:
        for position, image_id in enumerate(ranked):
            score[image_id] = score.get(image_id, 0.0) + 1.0 / (RRF_K + position + 1)
    return [image_id for image_id, _ in sorted(score.items(), key=lambda kv: -kv[1])][:limit]


def search(conn, query: str, *, query_vector=None, limit: int = 200) -> list[dict]:
    """Find photographs. Always answers, with whatever it has."""

    query = (query or "").strip()
    if not query:
        return []

    ids = fuse(
        _lexical(conn, query, limit * 2),
        _by_keyword(conn, query, limit * 2),
        _semantic(conn, query_vector, limit * 2),
        limit=limit,
    )
    if not ids:
        return []

    holes = ",".join("?" for _ in ids)
    found = {
        row["id"]: dict(row)
        for row in conn.execute(
            f"SELECT id, tail, date_taken, stars, elo, content_hash AS hash, width, height"
            f" FROM images WHERE id IN ({holes})",
            ids,
        )
    }
    return [found[i] for i in ids if i in found]


# Embeddings are a cache kind with one rider: never evict. They are small, they
# take hours to remake, and a machine that cannot make them (no model, no GPU)
# must record nothing rather than a failure -- so the helper that can is not
# looking at a row saying this photograph could not be embedded.
def _needs_a_helper() -> bool:
    return False


EMBEDDING_KIND = cache.register(cache.Kind(
    name=EMBEDDING,
    compute=lambda source: cache.Made(),
    cost=2.0,
    evictable=False,
    here=_needs_a_helper,
))
