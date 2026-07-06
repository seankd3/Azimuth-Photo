export function createImagePreloader({
    limit = 240,
    concurrency = 8,
} = {}) {
    const preloaded = new Map();
    const queue = [];
    let active = 0;

    function pump() {
        while (active < concurrency && queue.length > 0) {
            const item = queue.shift();
            active++;
            const img = new Image();
            img.decoding = 'async';
            if ('fetchPriority' in img) img.fetchPriority = item.priority;
            const finish = () => {
                active = Math.max(0, active - 1);
                item.resolve();
                pump();
            };
            img.onload = finish;
            img.onerror = finish;
            img.src = item.url;
        }
    }

    return function preloadImage(url, priority = 'auto') {
        if (preloaded.has(url)) return preloaded.get(url);
        if (preloaded.size >= limit) {
            const first = preloaded.keys().next().value;
            preloaded.delete(first);
        }
        const normalizedPriority = priority === 'high' || priority === 'low' ? priority : 'auto';
        const promise = new Promise((resolve) => {
            const item = { url, priority: normalizedPriority, resolve };
            if (normalizedPriority === 'high') {
                queue.unshift(item);
            } else {
                queue.push(item);
            }
            pump();
        });
        preloaded.set(url, promise);
        return promise;
    };
}


export function loadImageProbe(url, { priority = 'auto', timeoutMs = 0 } = {}) {
    if (!url) return Promise.resolve({ ok: false });
    return new Promise((resolve) => {
        const img = new Image();
        let settled = false;
        let timer = null;
        const finish = (ok, timedOut = false) => {
            if (settled) return;
            settled = true;
            if (timer) clearTimeout(timer);
            resolve({
                ok,
                timedOut,
                url: img.src,
                width: img.naturalWidth || 0,
                height: img.naturalHeight || 0,
            });
        };
        img.decoding = 'async';
        if ('fetchPriority' in img) img.fetchPriority = priority;
        img.onload = () => finish(Boolean(img.naturalWidth && img.naturalHeight));
        img.onerror = () => finish(false);
        if (timeoutMs > 0) timer = setTimeout(() => finish(false, true), timeoutMs);
        img.src = url;
    });
}


export function withTimeout(promise, timeoutMs, fallback) {
    let timer = null;
    return Promise.race([
        Promise.resolve(promise),
        new Promise((resolve) => {
            timer = setTimeout(() => resolve(fallback), timeoutMs);
        }),
    ]).finally(() => {
        if (timer) clearTimeout(timer);
    });
}


export function createWarmCacheStore({
    prefix,
    maxAgeMs = 30000,
} = {}) {
    function cacheKey(key) {
        return `${prefix}${key}`;
    }

    function save(key, data) {
        if (typeof sessionStorage === 'undefined' || !data) return;
        try {
            sessionStorage.setItem(
                cacheKey(key),
                JSON.stringify({ savedAt: Date.now(), data }),
            );
        } catch {}
    }

    function take(key) {
        if (typeof sessionStorage === 'undefined') return null;
        try {
            const storageKey = cacheKey(key);
            const raw = sessionStorage.getItem(storageKey);
            if (!raw) return null;
            sessionStorage.removeItem(storageKey);
            const parsed = JSON.parse(raw);
            if (!parsed?.data) return null;
            if (Date.now() - Number(parsed.savedAt || 0) > maxAgeMs) return null;
            return parsed.data;
        } catch {
            return null;
        }
    }

    return { save, take };
}


export function createWarmupManager({
    fetchJsonImpl = null,
    preloadImageWithTimeout,
    fetchImpl = (...args) => fetch(...args),
    queueConcurrency = 2,
    backgroundWarmDelayMs = 250,
    warmCacheStore = createWarmCacheStore({
        prefix: 'photoarchive:warm:',
        maxAgeMs: 30000,
    }),
    warmTierDedupeMs = 8000,
    warmTierBatchDelayMs = 120,
    onWarmTiersApplied = () => {},
} = {}) {
    const backgroundWarmTimers = new Map();
    const backgroundWarmTokens = new Map();
    const warmupQueue = [];
    const recentWarmTierIds = new Map();
    const pendingWarmTiers = new Map();
    let warmupActive = 0;
    let warmupGeneration = 0;
    let warmTierFlushTimer = null;

    function currentGeneration() {
        return warmupGeneration;
    }

    function clearWarmups() {
        warmupGeneration++;
        for (const timer of backgroundWarmTimers.values()) clearTimeout(timer);
        backgroundWarmTimers.clear();
        for (const item of warmupQueue.splice(0)) {
            if (item.onDrop) item.onDrop();
        }
    }

    function enqueueWarmup(task, { generation = warmupGeneration, onDrop = null, priority = 'normal' } = {}) {
        const item = { task, generation, onDrop };
        if (priority === 'high') {
            warmupQueue.unshift(item);
        } else {
            warmupQueue.push(item);
        }
        pumpWarmupQueue();
    }

    async function pumpWarmupQueue() {
        if (warmupActive >= queueConcurrency) return;
        const item = warmupQueue.shift();
        if (!item) return;
        if (item.generation !== warmupGeneration) {
            if (item.onDrop) item.onDrop();
            pumpWarmupQueue();
            return;
        }

        warmupActive++;
        try {
            await item.task();
        } catch {
            // Warmups are opportunistic.
        } finally {
            warmupActive = Math.max(0, warmupActive - 1);
            pumpWarmupQueue();
        }
    }

    function scheduleBackgroundWarm(key, task, delay = backgroundWarmDelayMs) {
        const token = (backgroundWarmTokens.get(key) || 0) + 1;
        backgroundWarmTokens.set(key, token);
        const generation = warmupGeneration;

        const existingTimer = backgroundWarmTimers.get(key);
        if (existingTimer) clearTimeout(existingTimer);

        const timer = setTimeout(() => {
            backgroundWarmTimers.delete(key);
            enqueueWarmup(
                () => task(token, generation),
                { generation },
            );
        }, delay);

        backgroundWarmTimers.set(key, timer);
    }

    function isWarmTokenCurrent(key, token, generation = warmupGeneration) {
        return backgroundWarmTokens.get(key) === token && generation === warmupGeneration;
    }

    async function fetchWarmJson(url) {
        if (fetchJsonImpl) return fetchJsonImpl(url, { defaultValue: null });
        try {
            const res = await fetchImpl(url);
            if (!res.ok) return null;
            return await res.json();
        } catch {
            return null;
        }
    }

    function saveWarmCache(key, data) {
        warmCacheStore.save(key, data);
    }

    function takeWarmCache(key) {
        return warmCacheStore.take(key);
    }

    async function warmImageUrls(urls, generation = warmupGeneration) {
        for (const url of urls || []) {
            if (generation !== warmupGeneration) return;
            if (preloadImageWithTimeout) await preloadImageWithTimeout(url, 'low', 1200);
        }
    }

    function warmImageTiers(tiers) {
        const now = Date.now();
        let queued = false;
        for (const [tier, ids] of Object.entries(tiers || {})) {
            const unique = [...new Set((ids || []).map((id) => Number(id)).filter((id) => id > 0))];
            for (const id of unique) {
                const key = `${tier}:${id}`;
                const lastWarm = recentWarmTierIds.get(key) || 0;
                if (now - lastWarm < warmTierDedupeMs) continue;
                recentWarmTierIds.set(key, now);
                if (!pendingWarmTiers.has(tier)) pendingWarmTiers.set(tier, new Set());
                pendingWarmTiers.get(tier).add(id);
                queued = true;
            }
        }
        if (!queued || warmTierFlushTimer) return;
        warmTierFlushTimer = setTimeout(flushWarmImageTiers, warmTierBatchDelayMs);
    }

    function flushWarmImageTiers() {
        warmTierFlushTimer = null;
        const payload = {};
        for (const [tier, ids] of pendingWarmTiers.entries()) {
            if (ids.size) payload[tier] = Array.from(ids).slice(0, 96);
        }
        pendingWarmTiers.clear();
        if (!Object.keys(payload).length) return;
        const cutoff = Date.now() - (warmTierDedupeMs * 3);
        for (const [key, warmedAt] of recentWarmTierIds.entries()) {
            if (warmedAt < cutoff) recentWarmTierIds.delete(key);
        }
        fetchImpl('/api/images/warm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tiers: payload }),
            keepalive: true,
        }).then((response) => {
            if (!response.ok) throw new Error('warm request failed');
            onWarmTiersApplied(payload);
        }).catch(() => {
            // Unmark ids from a failed warm so the dedupe window does not
            // suppress retries for tiers that were never actually warmed.
            for (const [tier, ids] of Object.entries(payload)) {
                for (const id of ids) recentWarmTierIds.delete(`${tier}:${id}`);
            }
        });
    }

    async function warmRequests(key, token, generation, requests) {
        for (const request of requests) {
            if (!isWarmTokenCurrent(key, token, generation)) return;
            const data = await fetchWarmJson(request.url);
            if (!data || !isWarmTokenCurrent(key, token, generation)) return;
            if (request.cacheKey) saveWarmCache(request.cacheKey, data);
            await warmImageUrls(request.extract(data), generation);
        }
    }

    return {
        clearWarmups,
        currentGeneration,
        enqueueWarmup,
        fetchWarmJson,
        scheduleBackgroundWarm,
        takeWarmCache,
        warmImageTiers,
        warmRequests,
    };
}


export function imageThumbUrls(data) {
    return (data.images || []).map((img) => img.thumb_url).filter(Boolean);
}


export function compareThumbUrls(data) {
    const urls = [];
    for (const pair of data.pairs || []) {
        if (pair.left?.thumb_url) urls.push(pair.left.thumb_url);
        if (pair.right?.thumb_url) urls.push(pair.right.thumb_url);
    }
    return urls;
}
