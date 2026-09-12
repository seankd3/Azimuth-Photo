"""Which selection strategy reaches the best ranking fastest?

Candidates, all fed the same softmax-consistent clicker over the same
hidden taste, measured at round budgets on two answers: Spearman rho over
everything judged (the whole order) and top-decile recall (did we find the
best tenth — what the stars actually read).

Strategies:
  close / random / diverse / tournament — the shipped modes, as baselines.
  window   — the most uncertain photographs that are closest together:
             slide an n-window over the mu-sorted pool, take the window
             with the greatest total uncertainty (sigma = 1/sqrt(1+seen)).
  greedy   — information-greedy: seat the highest-sigma photograph, then
             each next seat maximizes sigma_j - |mu_j - mean(mu_set)|/T,
             T = the fit's own spread scale.
  top      — uncertainty window restricted to the current top third, after
             one coverage epoch (a "find the best" specialist).
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
import model
import rank

PHOTOS = 2000
CLUSTERS = 20
DIM = 64
SET = 9
RECENT = 48
CHECKPOINTS = (100, 300, 600)
REFIT = 25
T = 400.0     # rank.SPREAD — mu is spoken on this scale


def build(seed):
    rng = np.random.default_rng(seed)
    path = os.path.join(tempfile.mkdtemp(), "sim.db")
    conn = model.connect(path)
    centers = rng.normal(size=(CLUSTERS, DIM))
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    which = rng.integers(0, CLUSTERS, PHOTOS)
    vectors = centers[which] + 0.35 * rng.normal(size=(PHOTOS, DIM))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    hidden = 0.8 * rng.normal(size=CLUSTERS)[which] + rng.normal(size=PHOTOS)
    hashes = [f"h{i:05d}" for i in range(PHOTOS)]
    conn.executemany(
        "INSERT INTO images (id, filename, tail, content_hash, width, height, status)"
        " VALUES (?, ?, ?, ?, 6000, 4000, 'unflagged')",
        [(i + 1, f"p{i}.jpg", f"Raws/p{i}.jpg", hashes[i]) for i in range(PHOTOS)])
    conn.commit()
    return conn, hashes, hidden, (hashes, vectors.astype(np.float32)), rng


def pool_state(conn, hashes):
    seen = rank.seen(conn)
    mu = np.zeros(len(hashes))
    appearances = np.zeros(len(hashes))
    rows = {r["content_hash"]: r["elo"] for r in conn.execute(
        "SELECT content_hash, elo FROM images")}
    for i, h in enumerate(hashes):
        mu[i] = rows.get(h, rank.BASE)
        appearances[i] = seen.get(h, 0)
    sigma = 1.0 / np.sqrt(1.0 + appearances)
    return mu, sigma, appearances


def choose_window(mu, sigma, avoid, n, top_only=False):
    live = np.asarray([i for i in range(len(mu)) if i not in avoid])
    if top_only:
        cut = np.quantile(mu[live], 2 / 3)
        upper = live[mu[live] >= cut]
        if len(upper) >= n:
            live = upper
    order = live[np.argsort(mu[live], kind="stable")]
    if len(order) <= n:
        return list(order)
    weight = sigma[order]
    sums = np.convolve(weight, np.ones(n), mode="valid")
    start = int(np.argmax(sums))
    return list(order[start:start + n])


def choose_band(mu, sigma, avoid, n, share=0.15):
    """The least-worn of the current top band, spread across it — the
    finding-the-best half of a mixed strategy."""

    live = np.asarray([i for i in range(len(mu)) if i not in avoid])
    cut = np.quantile(mu[live], 1 - share)
    band = live[mu[live] >= cut]
    if len(band) < n:
        band = live[np.argsort(-mu[live])[: max(n * 2, 16)]]
    order = band[np.lexsort((mu[band], -sigma[band]))]   # least-worn first
    fresh = order[: max(n * 3, 12)]
    fresh = fresh[np.argsort(mu[fresh], kind="stable")]
    if len(fresh) <= n:
        return list(fresh)
    step = (len(fresh) - 1) / (n - 1)
    return [int(fresh[round(i * step)]) for i in range(n)]


def choose_greedy(mu, sigma, avoid, n):
    live = [i for i in range(len(mu)) if i not in avoid]
    live = np.asarray(live)
    chosen = [live[int(np.argmax(sigma[live]))]]
    rest = set(live.tolist()) - set(chosen)
    while len(chosen) < n and rest:
        held = np.asarray(sorted(rest))
        middle = np.mean(mu[chosen])
        score = sigma[held] - np.abs(mu[held] - middle) / T
        pick = int(held[int(np.argmax(score))])
        chosen.append(pick)
        rest.discard(pick)
    return chosen


def session(strategy, seed):
    import random

    random.seed(seed)   # the shipped draws use the global generator
    conn, hashes, hidden, space, rng = build(seed)
    index = {h: i for i, h in enumerate(hashes)}
    recent = []
    results = {}
    for round_number in range(max(CHECKPOINTS)):
        if strategy in ("close", "random", "diverse", "tournament", "learn"):
            got = rank.candidates(conn, SET, avoid=tuple(recent), mode=strategy, space=space)
            members = [index[p["hash"]] for p in got]
        else:
            mu, sigma, appearances = pool_state(conn, hashes)
            avoid = {index[h] for h in recent}
            if strategy == "fisher":
                # E4: the shipped mixture (teach by the widest window, find
                # at the band's cut, a coin once there are leaders) with the
                # fit's own uncertainty instead of 1/sqrt(1+rounds).
                unsure = rank.fitted(rank.rounds(conn))[1]
                sigma = np.asarray([unsure.get(h, 1.0 / np.sqrt(rank.LAM_B)) for h in hashes])
                judged_n = int((appearances > 0).sum())
                if judged_n >= 2 * SET and rng.random() < 0.5:
                    members = choose_band(mu, sigma, avoid, SET)
                else:
                    members = choose_window(mu, sigma, avoid, SET)
            elif strategy == "window":
                members = choose_window(mu, sigma, avoid, SET)
            elif strategy == "top":
                covered = (appearances > 0).mean() > 0.95
                members = choose_window(mu, sigma, avoid, SET, top_only=covered)
            elif strategy == "mix":
                # Two learning rounds, then one finding round, once the
                # first coverage epoch is done.
                covered = (appearances > 0).mean() > 0.95
                if covered and round_number % 3 == 2:
                    members = choose_band(mu, sigma, avoid, SET)
                else:
                    members = choose_window(mu, sigma, avoid, SET)
            elif strategy == "mixcoin":
                covered = (appearances > 0).mean() > 0.95
                if covered and rng.random() < 1 / 3:
                    members = choose_band(mu, sigma, avoid, SET)
                else:
                    members = choose_window(mu, sigma, avoid, SET)
            elif strategy == "mix1":
                covered = (appearances > 0).mean() > 0.95
                if covered and round_number % 2 == 1:
                    members = choose_band(mu, sigma, avoid, SET)
                else:
                    members = choose_window(mu, sigma, avoid, SET)
            else:
                members = choose_greedy(mu, sigma, avoid, SET)
        if len(members) < 2:
            recent.clear()
            continue
        for i in members:
            recent.append(hashes[i])
        if len(recent) > RECENT:
            del recent[: len(recent) - RECENT]
        utility = hidden[members] + rng.gumbel(size=len(members))
        winner = int(np.argmax(utility))
        rank.record(conn, members[winner] + 1,
                    [members[i] + 1 for i in range(len(members)) if i != winner])
        done = round_number + 1
        if done % REFIT == 0 or done in CHECKPOINTS:
            fitted = rank.ranking(conn)
            conn.executemany("UPDATE images SET elo = ? WHERE content_hash = ?",
                             [(v, h) for h, v in fitted.items()])
            conn.commit()
        if done in CHECKPOINTS:
            fitted = rank.ranking(conn)
            judged = [h for h in fitted if h in index]
            from scipy.stats import spearmanr
            rho = spearmanr([fitted[h] for h in judged],
                            [hidden[index[h]] for h in judged]).statistic if len(judged) > 9 else 0.0
            # Top-decile recall over the WHOLE pool: unfound photographs
            # count against, which is what "find the best" means.
            decile = max(1, PHOTOS // 10)
            true_top = set(np.argsort(-hidden)[:decile].tolist())
            scored = np.full(PHOTOS, -1e18)
            for h, v in fitted.items():
                scored[index[h]] = v
            found = set(np.argsort(-scored)[:decile].tolist())
            results[done] = (rho, len(true_top & found) / decile, len(judged))
    conn.close()
    return results


if __name__ == "__main__":
    strategies = sys.argv[1].split(",") if len(sys.argv) > 1 else [
        "close", "random", "diverse", "tournament", "window", "greedy", "top"]
    seeds = (7, 11, 23)
    for strategy in strategies:
        rows = [session(strategy, seed) for seed in seeds]
        for at in CHECKPOINTS:
            rho = np.mean([r[at][0] for r in rows])
            recall = np.mean([r[at][1] for r in rows])
            judged = np.mean([r[at][2] for r in rows])
            print(f"{strategy:10s} @{at:3d}  rho={rho:.3f}  top10%={recall:.3f}  judged={judged:.0f}",
                  flush=True)
