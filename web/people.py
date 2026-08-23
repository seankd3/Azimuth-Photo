"""A person is a name the owner gives a group of faces.

The group is derived — greedy clustering over every stored face vector,
rewritten whole on the rank lane's rhythm, like tags and stars — and the
name is a fact: one decision whose subject is the group's exemplar face
(`hash:index`), so it survives every re-clustering by landing wherever
that face lands. Named groups write per-photo rows under their own kind,
which the same ≈ chips, shelf and search cards already read; unnamed
groups wait in a summary row as "Someone", holding the faces of the people
you have not introduced yet.
"""

from __future__ import annotations

import json

import faces
from model import decisions

# Two faces closer than this are the same person. ArcFace cosine for the
# same person runs ~0.5-0.7 and for strangers ~0.1-0.25; 0.5 favours
# precision — a split person is one Name away from whole, a merged pair of
# strangers is a lie.
SAME = 0.5
# A person seen in this many photographs is worth a row.
FLOOR = 3
FAMILY = "person"
GROUPS = "@groups"


def _faces(conn):
    """Every stored face: (photo hash, index, score, vector-matrix rows)."""

    import numpy as np

    rows = conn.execute(
        "SELECT hash, value FROM cache WHERE kind = 'faces' AND recipe = ? AND state = 'ready'"
        " AND hash != ?",
        (faces.RECIPE, GROUPS),
    ).fetchall()
    owners, scores, stacks, crops = [], [], [], []
    for row in rows:
        boxes, det, matrix = faces.unpack(row["value"])
        for index in range(len(boxes)):
            owners.append((row["hash"], index))
            scores.append(det[index])
            stacks.append(matrix[index])
            crops.append(boxes[index])
    if not stacks:
        return [], [], np.zeros((0, 512), dtype=np.float32), []
    return owners, scores, np.stack(stacks), crops


def _cluster(owners, scores, matrix):
    """Greedy centroid clustering, strongest detections first: join the
    nearest group above SAME or start one. Deterministic for one answer."""

    import numpy as np

    order = np.argsort(scores)[::-1]
    centroids: list = []
    members: list[list[int]] = []
    held = np.zeros((0, matrix.shape[1]), dtype=np.float32)
    for face in order:
        vec = matrix[face]
        if len(centroids):
            sims = held @ vec
            best = int(sims.argmax())
            if float(sims[best]) >= SAME:
                members[best].append(int(face))
                centroids[best] = centroids[best] + vec
                held[best] = centroids[best] / (np.linalg.norm(centroids[best]) or 1.0)
                continue
        centroids.append(vec.copy())
        members.append([int(face)])
        held = np.vstack([held, vec[None, :]])
    return members


def _names(conn) -> dict[str, str]:
    """Every exemplar the owner has named: `hash:index` -> name, latest
    decision winning, an empty value meaning the name was taken back."""

    out: dict[str, str] = {}
    for row in conn.execute(
        f"SELECT subject, value FROM decisions WHERE family = ? "
        f"ORDER BY {decisions.AUTHORITY_SQL} ASC, at ASC, id ASC",
        (FAMILY,),
    ):
        said = decisions.loaded(row)
        out[str(row["subject"])] = str(said or "")
    return {subject: name for subject, name in out.items() if name}


def repeople(conn) -> int:
    """The whole people answer, rewritten: named groups become per-photo
    rows, every group at least FLOOR strong waits in the summary."""

    owners, scores, matrix, crops = _faces(conn)
    conn.execute("DELETE FROM cache WHERE kind = 'people'")
    conn.execute("DELETE FROM cache WHERE kind = 'faces' AND hash = ?", (GROUPS,))
    if not owners:
        conn.commit()
        return 0
    members = _cluster(owners, scores, matrix)
    named = _names(conn)

    worn: dict[str, set] = {}
    summary = []
    someone = 0
    for group in sorted(members, key=len, reverse=True):
        photos = sorted({owners[face][0] for face in group})
        name = next((named[f"{owners[face][0]}:{owners[face][1]}"] for face in group
                     if f"{owners[face][0]}:{owners[face][1]}" in named), None)
        settled = name is not None
        if not settled and len(photos) >= FLOOR:
            # An interim handle, so the person is browsable before they are
            # introduced — seeing their photographs is how you know who they
            # are. It renumbers when the groups rewrite; naming settles it.
            someone += 1
            name = f"Someone {someone}"
        if name:
            for photo in photos:
                worn.setdefault(photo, set()).add(name)
        if len(photos) < FLOOR:
            continue
        strongest = max(group, key=lambda face: scores[face])
        exemplar = f"{owners[strongest][0]}:{owners[strongest][1]}"
        # The faces shown are the person's most typical large faces: among
        # the group's larger half, nearest the identity centroid first. A
        # poorly lit or blurred face embeds *atypically* — quality pushes a
        # vector away from the mean — so centrality quietly filters bad
        # light without ever metering it.
        import numpy as np

        centre = matrix[group].mean(axis=0)
        centre = centre / (np.linalg.norm(centre) or 1.0)
        typical = {face: float(matrix[face] @ centre) for face in group}
        big = float(np.median([crops[face][2] * crops[face][3] for face in group]))
        best = sorted(group, key=lambda face: (
            crops[face][2] * crops[face][3] >= big, typical[face]), reverse=True)
        sample, seen = [], set()
        for face in best:
            if owners[face][0] in seen:
                continue
            seen.add(owners[face][0])
            sample.append({"hash": owners[face][0], "box": crops[face]})
            if len(sample) == 3:
                break
        summary.append({"name": name, "settled": settled, "exemplar": exemplar,
                        "photos": len(photos), "faces": len(group), "sample": sample})

    conn.executemany(
        "INSERT OR REPLACE INTO cache (hash, kind, recipe, state, value, at)"
        " VALUES (?, 'people', ?, 'ready', ?, unixepoch())",
        [(photo, faces.RECIPE, json.dumps(sorted(names))) for photo, names in worn.items()],
    )
    conn.execute(
        "INSERT OR REPLACE INTO cache (hash, kind, recipe, state, value, at)"
        " VALUES (?, 'faces', ?, 'ready', ?, unixepoch())",
        (GROUPS, faces.RECIPE, json.dumps(summary)),
    )
    conn.commit()
    return len(summary)


def groups(conn) -> list[dict]:
    """The summary as last written: every group worth a row, named or not."""

    row = conn.execute(
        "SELECT value FROM cache WHERE kind = 'faces' AND hash = ? AND recipe = ?",
        (GROUPS, faces.RECIPE),
    ).fetchone()
    if row is None:
        return []
    held = row["value"]
    return json.loads(held if isinstance(held, str) else bytes(held).decode("utf-8"))


def name(conn, exemplar: str, called: str) -> dict:
    """The owner introduces someone: one decision on the exemplar face.

    The same *person* name on a second group is welcome: a split person
    heals by being introduced twice, because both groups then answer to
    the one name.
    """

    called = str(called).strip()
    if not called:
        raise ValueError("a person needs a name")
    if called.lower().startswith("someone"):
        raise ValueError("that is the library's word for the unintroduced — give them their own name")
    if ":" not in str(exemplar):
        raise ValueError("that face is not one the groups know")
    decisions.decide(conn, str(exemplar), FAMILY, called)
    conn.commit()
    return {"named": called}
