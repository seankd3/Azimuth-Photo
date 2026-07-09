"""Shared embedding similarity scans."""

from __future__ import annotations


def scan_duplicate_pairs(
    matrix,
    image_ids,
    cached_sm_ids=None,
    *,
    threshold: float,
    batch_size: int = 500,
    limit: int | None = None,
    n: int | None = None,
):
    pairs = []
    total_pairs = 0
    hidden_pairs = 0
    cached_ids = set(cached_sm_ids) if cached_sm_ids is not None else None
    image_count = int(n if n is not None else len(image_ids))
    max_pairs = int(limit) if limit is not None else None
    for start in range(0, image_count, batch_size):
        end = min(start + batch_size, image_count)
        chunk_sims = matrix[start:end] @ matrix.T
        for i_local in range(end - start):
            i = start + i_local
            j_start = max(i + 1, 0)
            row = chunk_sims[i_local, j_start:]
            above = (row >= threshold).nonzero()[0]
            for offset in above:
                j = j_start + int(offset)
                id_a = int(image_ids[i])
                id_b = int(image_ids[j])
                total_pairs += 1
                if cached_ids is None or (id_a in cached_ids and id_b in cached_ids):
                    pairs.append((id_a, id_b, float(row[int(offset)])))
                else:
                    hidden_pairs += 1
                if max_pairs is not None and len(pairs) >= max_pairs:
                    break
            if max_pairs is not None and len(pairs) >= max_pairs:
                break
        if max_pairs is not None and len(pairs) >= max_pairs:
            break
    return pairs, total_pairs, hidden_pairs
