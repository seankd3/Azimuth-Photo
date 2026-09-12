"""Simulate ranking sessions through the real code and measure each mode's
dynamics: how often photographs repeat, how evenly attention spreads, and
how quickly the fitted strength learns a hidden taste.

The clicker is softmax-consistent with the fit's own model: it picks
argmax(hidden + Gumbel noise), which is exactly the generative story
`fit()` assumes — so learning speed is measured fairly.
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
import model
import rank

RNG = np.random.default_rng(7)

PHOTOS = 2000
CLUSTERS = 20
DIM = 64
ROUNDS = 300
SET = 9
RECENT = 48          # the client's memory window, emulated


def build():
    path = os.path.join(tempfile.mkdtemp(), "sim.db")
    conn = model.connect(path)
    centers = RNG.normal(size=(CLUSTERS, DIM))
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    which = RNG.integers(0, CLUSTERS, PHOTOS)
    vectors = centers[which] + 0.35 * RNG.normal(size=(PHOTOS, DIM))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    # Hidden taste: a per-cluster inclination plus per-photo variation.
    hidden = 0.8 * RNG.normal(size=CLUSTERS)[which] + RNG.normal(size=PHOTOS)
    hashes = [f"h{i:05d}" for i in range(PHOTOS)]
    conn.executemany(
        "INSERT INTO images (id, filename, tail, content_hash, width, height, status)"
        " VALUES (?, ?, ?, ?, 6000, 4000, 'unflagged')",
        [(i + 1, f"p{i}.jpg", f"Raws/p{i}.jpg", hashes[i]) for i in range(PHOTOS)])
    conn.commit()
    space = (hashes, vectors.astype(np.float32))
    return conn, hashes, hidden, space


def session(mode, *, recent_window=RECENT, refit_every=25):
    conn, hashes, hidden, space = build()
    index = {h: i for i, h in enumerate(hashes)}
    shown_ever = set()
    recent = []
    repeats = shows = 0
    wear_of_shown = []
    for round_number in range(ROUNDS):
        got = rank.candidates(conn, SET, avoid=tuple(recent), mode=mode, space=space)
        if len(got) < 2:
            recent.clear()
            continue
        members = [index[p["hash"]] for p in got]
        for p in got:
            shows += 1
            if p["hash"] in shown_ever:
                repeats += 1
            shown_ever.add(p["hash"])
            wear_of_shown.append(p["comparisons"])
            recent.append(p["hash"])
        if len(recent) > recent_window:
            del recent[: len(recent) - recent_window]
        # The clicker.
        utility = hidden[members] + RNG.gumbel(size=len(members))
        winner = int(np.argmax(utility))
        rank.record(conn, got[winner]["id"], [p["id"] for i, p in enumerate(got) if i != winner])
        if (round_number + 1) % refit_every == 0:
            fitted = rank.ranking(conn)
            conn.executemany("UPDATE images SET elo = ? WHERE content_hash = ?",
                             [(v, h) for h, v in fitted.items()])
            conn.commit()

    fitted = rank.ranking(conn)
    judged = [h for h in fitted if h in index]
    from scipy.stats import spearmanr
    rho = spearmanr([fitted[h] for h in judged], [hidden[index[h]] for h in judged]).statistic if len(judged) > 4 else 0.0
    wear = np.asarray(wear_of_shown)
    counts = rank.seen(conn)
    per_photo = np.asarray(list(counts.values()))
    conn.close()
    return {
        "mode": mode,
        "repeat%": 100.0 * repeats / max(shows, 1),
        "unique": len(shown_ever),
        "judged": len(judged),
        "rho": rho,
        "mean_wear_at_show": float(wear.mean()),
        "max_per_photo": int(per_photo.max()),
        "p90_per_photo": float(np.percentile(per_photo, 90)),
    }


if __name__ == "__main__":
    for mode in ("close", "random", "diverse", "tournament"):
        got = session(mode)
        print(" ".join(f"{k}={got[k]:.3f}" if isinstance(got[k], float) else f"{k}={got[k]}"
                       for k in got))
