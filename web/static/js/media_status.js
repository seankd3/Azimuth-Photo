import { fetchJson } from './api.js';


export function createMediaStatusClient({ maxAgeMs = 15000 } = {}) {
    const cache = new Map();
    const inflight = new Map();

    function cacheStatus(status, now = Date.now()) {
        if (status?.id) {
            cache.set(Number(status.id), { time: now, data: status });
        }
    }

    async function getStatus(imageId, { force = false } = {}) {
        const id = Number(imageId);
        if (!id) return null;
        const cached = cache.get(id);
        if (!force && cached && Date.now() - cached.time < maxAgeMs) {
            return cached.data;
        }
        if (!force && inflight.has(id)) {
            return inflight.get(id);
        }
        let request;
        request = fetchJson(`/api/image/${id}/media-status`, { defaultValue: null })
            .then((data) => {
                if (data) cacheStatus(data);
                return data;
            })
            .finally(() => {
                if (inflight.get(id) === request) inflight.delete(id);
            });
        inflight.set(id, request);
        return request;
    }

    function primeStatuses(imageIds) {
        const ids = [...new Set((imageIds || [])
            .map((id) => Number(id))
            .filter((id) => id > 0 && !cache.has(id) && !inflight.has(id)))]
            .slice(0, 96);
        if (!ids.length) return;
        const request = fetchJson('/api/images/media-status', {
            defaultValue: null,
            fetchOptions: {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids }),
            },
        }).then((data) => {
            const now = Date.now();
            for (const status of data?.statuses || []) cacheStatus(status, now);
            return data;
        });
        for (const id of ids) {
            const perImageRequest = request
                .then(() => cache.get(id)?.data || null)
                .finally(() => {
                    if (inflight.get(id) === perImageRequest) inflight.delete(id);
                });
            inflight.set(id, perImageRequest);
        }
    }

    function invalidateForPayload(payload) {
        for (const ids of Object.values(payload || {})) {
            for (const id of ids || []) cache.delete(Number(id));
        }
    }

    return {
        getStatus,
        invalidateForPayload,
        primeStatuses,
    };
}


export function createMediaStatusController({
    client = createMediaStatusClient(),
    getCurrentLoupeImage = () => null,
    getLoupeImageToken = () => 0,
    refreshLoupeMediaStatus = () => {},
} = {}) {
    async function getMediaStatus(imageId, { force = false } = {}) {
        return client.getStatus(imageId, { force });
    }

    function primeMediaStatuses(imageIds) {
        client.primeStatuses(imageIds);
    }

    function invalidateMediaStatusesForPayload(payload) {
        client.invalidateForPayload(payload);
    }

    function handleWarmTiersApplied(payload) {
        invalidateMediaStatusesForPayload(payload);
        const currentImage = getCurrentLoupeImage();
        const currentId = Number(currentImage?.id || 0);
        if (!currentId) return false;
        const includesCurrent = Object.values(payload || {}).some((ids) => (ids || []).includes(currentId));
        if (!includesCurrent) return false;
        refreshLoupeMediaStatus(currentImage, getLoupeImageToken(), { force: true });
        return true;
    }

    return {
        getMediaStatus,
        handleWarmTiersApplied,
        invalidateMediaStatusesForPayload,
        primeMediaStatuses,
    };
}
