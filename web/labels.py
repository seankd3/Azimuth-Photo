"""A label is a word the owner taught.

Vocabulary is born by teaching: the first anchor or exclusion on a word
creates it as a set (kind='label'), and its yes/no membership rows ARE the
teachings — the same true/false rows every set already stores. A small
head — the word's own text vector pulled toward the anchors and away from
the exclusions — scores the space, and the label extends down to its
weakest anchor: everything scoring at least that much wears the word,
tilde-first, until calibration earns a real threshold. Anchors always wear
it; exclusions never do — the same (derived ∪ pinned) ∖ denied formula a
smart album answers with. The whole answer rewrites on the rank lane, like
stars and people and everything else derived.

The word's text vector is kept as a cache row the moment the model is warm
enough to make one, so the lane itself never loads a model; a label taught
entirely by example works with no vector at all.
"""

from __future__ import annotations

import json

from model import decisions, sets

# The stored kind predates the Labels vocabulary: adopted V1 keywords
# already live under it, and they surface as anchor-only labels — words
# the owner taught once, in another tool.
KIND = "label"        # the per-photo rows' cache kind
SEED = "label-seed"   # cache kind holding one text vector per word


def vocabulary(conn) -> list[dict]:
    """Every word the owner has taught: id and name."""

    return [{"id": entry["id"], "name": entry["name"]}
            for entry in sets.all(conn, kind=sets.LABEL)]


def _find(conn, word: str) -> str | None:
    held = word.strip().lower()
    for entry in vocabulary(conn):
        if entry["name"].lower() == held:
            return entry["id"]
    return None


def teach(conn, word: str, identities, yes: bool) -> dict:
    """One answer: these photographs are (or are not) this word.

    The first answer is what creates the word. The seed vector rides along
    when the model happens to be warm; a label without one learns from its
    examples alone.
    """

    import embed

    word = str(word).strip()
    if not word:
        raise ValueError("a label needs a word")
    wanted = [str(h) for h in identities if str(h)]
    if not wanted:
        raise ValueError("nothing to teach with")
    held = _find(conn, word)
    if held is None:
        held = sets.create(conn, word, kind=sets.LABEL)
    if yes:
        sets.add(conn, held, wanted)
    else:
        sets.remove(conn, held, wanted)
    if embed.warm():
        have = conn.execute(
            "SELECT 1 FROM cache WHERE kind = ? AND hash = ?", (SEED, word)).fetchone()
        if have is None:
            vec = embed.text(word)
            conn.execute(
                "INSERT OR REPLACE INTO cache (hash, kind, recipe, state, value, at)"
                " VALUES (?, ?, '', 'ready', ?, unixepoch())",
                (word, SEED, vec.tobytes()))
    conn.commit()
    return {"word": word, "taught": len(wanted), "yes": bool(yes)}


def _teachings(conn, set_id: str) -> tuple[list[str], list[str]]:
    """The latest answer per photograph: (anchored, excluded)."""

    said: dict[str, bool] = {}
    for row in conn.execute(
        f"SELECT subject, value FROM decisions WHERE family = ? "
        f"ORDER BY {decisions.AUTHORITY_SQL} ASC, at ASC, id ASC",
        (sets.family(set_id),),
    ):
        said[str(row["subject"])] = decisions.loaded(row) is True
    yes = [subject for subject, verdict in said.items() if verdict]
    no = [subject for subject, verdict in said.items() if not verdict]
    return yes, no


def _fit(matrix, seed, ys, ns):
    """The head: the seed pulled toward the anchors, away from the
    exclusions — the measured probe's ridge-logistic, nothing fancier."""

    import numpy as np

    if seed is None and not ys:
        return None
    base = seed if seed is not None else matrix[ys].mean(axis=0)
    norm = np.linalg.norm(base)
    if not norm:
        return None
    base = base / norm
    if not ys and not ns:
        return base
    rows = [matrix[ys], matrix[ns]]
    truth = [1.0] * len(ys) + [0.0] * len(ns)
    if seed is not None:
        rows.append(base[None, :])
        truth.append(1.0)
    X = np.vstack([r for r in rows if len(r)])
    y = np.array(truth, dtype=np.float32)
    w = base.copy()
    for _ in range(200):
        p = 1 / (1 + np.exp(-(X @ w) * 4))
        grad = X.T @ ((p - y) * 4) / len(y) + 0.05 * (w - base)
        w -= 0.5 * grad
    return w


def relabel(conn, subjects, matrix) -> int:
    """Every taught word's answer, rewritten whole. A photograph wears a
    word when it was anchored, or when it scores at least as high as the
    word's weakest anchor — and never when it was excluded."""

    import numpy as np

    vocab = vocabulary(conn)
    conn.execute("DELETE FROM cache WHERE kind = ?", (KIND,))
    if not vocab:
        conn.commit()
        return 0
    where = {subject: i for i, subject in enumerate(subjects)}
    worn: dict[str, set] = {}
    for entry in vocab:
        yes, no = _teachings(conn, entry["id"])
        word = entry["name"]
        for subject in yes:
            worn.setdefault(subject, set()).add(word)
        if not len(subjects):
            continue
        seed_row = conn.execute(
            "SELECT value FROM cache WHERE kind = ? AND hash = ? AND state = 'ready'",
            (SEED, word)).fetchone()
        seed = np.frombuffer(seed_row["value"], dtype=np.float32).copy() if seed_row else None
        ys = [where[subject] for subject in yes if subject in where]
        ns = [where[subject] for subject in no if subject in where]
        head = _fit(matrix, seed, ys, ns)
        if head is None or not ys:
            continue
        scores = matrix @ head
        floor = min(float(scores[i]) for i in ys) - 1e-6
        denied = set(no)
        for i in np.flatnonzero(scores >= floor):
            subject = subjects[int(i)]
            if subject not in denied:
                worn.setdefault(subject, set()).add(word)
    conn.executemany(
        "INSERT OR REPLACE INTO cache (hash, kind, recipe, state, value, at)"
        " VALUES (?, ?, '', 'ready', ?, unixepoch())",
        [(subject, KIND, json.dumps(sorted(words))) for subject, words in worn.items()],
    )
    conn.commit()
    return len(worn)
