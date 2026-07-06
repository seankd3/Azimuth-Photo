export function buildComparisonPayload(pair = {}, side, mode) {
    const winner = side === 'left' ? pair.left || {} : pair.right || {};
    const loser = side === 'left' ? pair.right || {} : pair.left || {};
    return {
        winner_id: winner.id,
        loser_id: loser.id,
        mode,
    };
}


async function responseJsonOrEmpty(res) {
    try {
        return await res.json();
    } catch {
        return {};
    }
}


export async function parseComparisonSaveResult(res) {
    const result = await responseJsonOrEmpty(res);
    if (!res.ok) throw new Error(result.error || 'Failed to save comparison');
    return result;
}


export async function postComparison(payload, { fetchImpl = globalThis.fetch } = {}) {
    const res = await fetchImpl('/api/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    return parseComparisonSaveResult(res);
}


export function applyComparisonElos(pair = {}, side, result = {}) {
    if (side === 'left') {
        if (pair.left) pair.left.elo = result.winner_elo;
        if (pair.right) pair.right.elo = result.loser_elo;
    } else {
        if (pair.right) pair.right.elo = result.winner_elo;
        if (pair.left) pair.left.elo = result.loser_elo;
    }
    return pair;
}


export async function saveComparison(pair, side, mode, options = {}) {
    const result = await postComparison(buildComparisonPayload(pair, side, mode), options);
    applyComparisonElos(pair, side, result);
    return result;
}


export function undoComparisonCount(result = {}) {
    return Number(result.comparisons_undone || 1);
}


export async function parseUndoComparisonResult(res) {
    if (!res.ok) {
        const body = await responseJsonOrEmpty(res);
        return { ok: false, result: null, comparisonsUndone: 0, error: body.error || '' };
    }
    const result = await res.json();
    return {
        ok: true,
        result,
        comparisonsUndone: undoComparisonCount(result),
    };
}


export async function postUndoComparison({ fetchImpl = globalThis.fetch } = {}) {
    const res = await fetchImpl('/api/compare/undo', { method: 'POST' });
    return parseUndoComparisonResult(res);
}


export function undoComparisonToastText(comparisonsUndone) {
    const count = Number(comparisonsUndone || 1);
    return `Undid ${count} comparison${count === 1 ? '' : 's'}`;
}
