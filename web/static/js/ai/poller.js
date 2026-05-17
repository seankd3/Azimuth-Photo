import {
    aiStatusPollDelay,
    renderAIStatusWidgets,
} from './status.js';

const DEFAULT_ACTIVE_POLL_MS = 5000;
const DEFAULT_IDLE_POLL_MS = 30000;

export function createAIStatusPoller({
    documentImpl = globalThis.document,
    fetchImpl = (url) => globalThis.fetch(url),
    setTimeoutImpl = (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
    clearTimeoutImpl = (timer) => globalThis.clearTimeout(timer),
    initVisibilityRefresh = null,
    pollDelay = aiStatusPollDelay,
    renderStatus = renderAIStatusWidgets,
    statusUrl = '/api/ai/status',
    activePollMs = DEFAULT_ACTIVE_POLL_MS,
    idlePollMs = DEFAULT_IDLE_POLL_MS,
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

    async function poll() {
        pollTimer = null;
        if (documentImpl?.hidden || pollInFlight) return;
        pollInFlight = true;
        let nextDelay = idlePollMs;
        try {
            const res = await fetchImpl(statusUrl);
            const data = await res.json();
            nextDelay = pollDelay(data, { activePollMs, idlePollMs });
            renderStatus(data, { documentImpl });
        } catch {
            const aiSection = documentImpl?.getElementById?.('bar-ai');
            if (aiSection) aiSection.style.display = 'none';
        } finally {
            pollInFlight = false;
            schedule(nextDelay);
        }
    }

    function start(initialDelayMs = 0) {
        initVisibilityRefresh?.();
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
