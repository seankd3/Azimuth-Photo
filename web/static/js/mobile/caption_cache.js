export function cacheCaptionRequest(cache, id, load) {
    if (cache.has(id)) return cache.get(id);
    const request = Promise.resolve()
        .then(load)
        .then((data) => {
            if (!data || data.error || data.not_found) cache.delete(id);
            return data;
        })
        .catch(() => {
            cache.delete(id);
            return null;
        });
    cache.set(id, request);
    return request;
}
