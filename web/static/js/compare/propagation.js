export function propagationCountsFromPayload(payload = {}) {
    const counts = {};
    for (const [id, count] of Object.entries(payload.counts || {})) {
        const imageId = Number.parseInt(id, 10);
        if (Number.isFinite(imageId)) counts[imageId] = Number(count || 0);
    }
    return counts;
}


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export function precomputePropagationCounts(images = [], {
    fetchImpl = globalThis.fetch,
    onCounts = () => {},
} = {}) {
    const gridIds = images.map(img => img.id);
    if (!gridIds.length || !fetchImpl) return false;
    fetchImpl('/api/propagation/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grid_ids: gridIds }),
    }).then(r => responseDataOrError(r, 'Propagation prediction failed')).then(data => {
        if (!data.counts || typeof data.counts !== 'object') return;
        onCounts(propagationCountsFromPayload(data));
    }).catch(() => {});
    return true;
}


export function fetchPropagationCount(directCount = 0, {
    fetchImpl = globalThis.fetch,
    onApply = () => {},
    onBadge = () => {},
} = {}) {
    if (!fetchImpl) return false;
    fetchImpl('/api/propagation/last').then(r => responseDataOrError(r, 'Propagation count failed')).then(data => {
        const propagated = Number(data.count || 0);
        const total = Number(directCount || 0) + propagated;
        if (total > 0) {
            onApply(total, Number(directCount || 0));
            if (propagated > 0) onBadge(propagated);
        }
    }).catch(() => {
        const direct = Number(directCount || 0);
        if (direct > 0) onApply(direct, direct);
    });
    return true;
}
