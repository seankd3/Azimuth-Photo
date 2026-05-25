import {
    aiStatusPollDelay,
    bindBackgroundWorkPanel,
    ingestBackgroundWorkPoll,
    renderAIStatusWidgets,
} from './status.js';

const DEFAULT_ACTIVE_POLL_MS = 5000;
const DEFAULT_IDLE_POLL_MS = 30000;
const DEFAULT_STATUS_TIMEOUT_MS = 5000;

function stalePeopleStatus(latencyMs = DEFAULT_STATUS_TIMEOUT_MS) {
    return {
        active: false,
        worker: { state: 'stale' },
        counts: { pending_cached_images: 0, scan: {} },
        counts_stale: true,
        status_stale: true,
        latency_ms: latencyMs,
    };
}

function staleCacheStatus(latencyMs = DEFAULT_STATUS_TIMEOUT_MS) {
    return {
        pregen: {},
        disk: { tiers: {} },
        memory: { tiers: {} },
        counts_stale: true,
        status_stale: true,
        latency_ms: latencyMs,
    };
}

function staleAIStatus(latencyMs = DEFAULT_STATUS_TIMEOUT_MS) {
    return {
        worker_state: 'stale',
        status_stale: true,
        worker_error: '',
        worker_message: latencyMs > 0 ? `Status delayed ${latencyMs.toFixed(0)}ms` : 'Status unavailable',
        model_installed: true,
        embedded: 0,
        total_images: 0,
        progress_pct: 0,
        embedding_manual_pause: true,
        latency_ms: latencyMs,
    };
}

export async function fetchStatusJson(url, {
    fetchImpl = (target, options) => globalThis.fetch(target, options),
    timeoutMs = DEFAULT_STATUS_TIMEOUT_MS,
    fallback = null,
} = {}) {
    const started = Date.now();
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    let timer = null;
    try {
        const timeout = new Promise((_, reject) => {
            timer = globalThis.setTimeout(() => {
                controller?.abort?.();
                reject(new Error('status timeout'));
            }, Math.max(50, Number(timeoutMs) || DEFAULT_STATUS_TIMEOUT_MS));
        });
        const request = fetchImpl(url, controller ? { signal: controller.signal } : undefined)
            .then((response) => {
                if (!response?.ok) throw new Error('status request failed');
                return response.json();
            });
        return await Promise.race([request, timeout]);
    } catch {
        const latencyMs = Math.max(0, Date.now() - started);
        return typeof fallback === 'function' ? fallback(latencyMs) : fallback;
    } finally {
        if (timer) globalThis.clearTimeout(timer);
    }
}

export function createAIStatusPoller({
    documentImpl = globalThis.document,
    fetchImpl = (url, options) => globalThis.fetch(url, options),
    setTimeoutImpl = (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
    clearTimeoutImpl = (timer) => globalThis.clearTimeout(timer),
    initVisibilityRefresh = null,
    pollDelay = aiStatusPollDelay,
    renderStatus = renderAIStatusWidgets,
    statusUrl = '/api/ai/status',
    cacheStatusUrl = '/api/cache/status',
    peopleStatusUrl = '/api/people/status',
    activePollMs = DEFAULT_ACTIVE_POLL_MS,
    idlePollMs = DEFAULT_IDLE_POLL_MS,
    statusTimeoutMs = DEFAULT_STATUS_TIMEOUT_MS,
} = {}) {
    let pollTimer = null;
    let pollingStarted = false;
    let pollInFlight = false;

    function started() {
        return pollingStarted;
    }

    function schedule(delayMs) {
        if (!pollingStarted || documentImpl?.hidden) return;
        if (pollTimer) clearTimeoutImpl(pollTimer);
        pollTimer = setTimeoutImpl(poll, Math.max(0, Number(delayMs) || 0));
    }

    function renderMergedStatus(aiStatus, cacheStatus, peopleStatus) {
        const merged = ingestBackgroundWorkPoll(aiStatus, cacheStatus, peopleStatus);
        renderStatus(merged.aiStatus, {
            documentImpl,
            cacheStatus: merged.cacheStatus,
            peopleStatus: merged.peopleStatus,
        });
    }

    async function poll() {
        pollTimer = null;
        if (documentImpl?.hidden || pollInFlight) return;
        pollInFlight = true;
        let nextDelay = idlePollMs;
        try {
            const [data, cacheStatus, peopleStatus] = await Promise.all([
                fetchStatusJson(statusUrl, {
                    fetchImpl,
                    timeoutMs: statusTimeoutMs,
                    fallback: staleAIStatus,
                }),
                fetchStatusJson(cacheStatusUrl, {
                    fetchImpl,
                    timeoutMs: statusTimeoutMs,
                    fallback: staleCacheStatus,
                }),
                fetchStatusJson(peopleStatusUrl, {
                    fetchImpl,
                    timeoutMs: statusTimeoutMs,
                    fallback: stalePeopleStatus,
                }),
            ]);
            const aiStatus = data || staleAIStatus();
            const cache = cacheStatus || staleCacheStatus();
            const people = peopleStatus || stalePeopleStatus();
            const merged = ingestBackgroundWorkPoll(aiStatus, cache, people);
            nextDelay = pollDelay(merged.aiStatus, {
                activePollMs,
                idlePollMs,
                cacheStatus: merged.cacheStatus,
                peopleStatus: merged.peopleStatus,
            });
            renderStatus(merged.aiStatus, {
                documentImpl,
                cacheStatus: merged.cacheStatus,
                peopleStatus: merged.peopleStatus,
            });
        } catch {
            renderMergedStatus(staleAIStatus(), staleCacheStatus(), stalePeopleStatus());
        } finally {
            pollInFlight = false;
            schedule(nextDelay);
        }
    }

    function start(initialDelayMs = 0) {
        initVisibilityRefresh?.();
        bindBackgroundWorkPanel({ documentImpl });
        if (pollingStarted) return;
        pollingStarted = true;
        schedule(initialDelayMs);
    }

    function handleVisible() {
        if (pollingStarted) schedule(0);
    }

    return {
        start,
        schedule,
        poll,
        handleVisible,
        started,
    };
}
