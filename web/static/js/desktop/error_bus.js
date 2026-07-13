import { resolveToast, showToast } from './toast.js';

const NETWORK_MESSAGE = "Can't reach Azimuth Photo. Retrying…";
const SERVER_MESSAGE = "Something went wrong on the server. Your photos are safe — try again.";
const WRITE_MESSAGE = "Couldn't save that change. It'll retry automatically.";
const RETRY_DELAYS = [1000, 2000, 4000, 8000, 15000];

let banner = null;
let retryTimer = null;
let retryAttempt = 0;
let connectionLost = false;

function connectionBanner() {
    if (banner) return banner;
    banner = document.createElement('div');
    banner.id = 'connection-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.textContent = NETWORK_MESSAGE;
    document.body.prepend(banner);
    return banner;
}

function scheduleProbe() {
    if (retryTimer) return;
    const delay = RETRY_DELAYS[Math.min(retryAttempt, RETRY_DELAYS.length - 1)];
    retryTimer = window.setTimeout(async () => {
        retryTimer = null;
        try {
            const response = await fetch('/api/dev/status', { cache: 'no-store' });
            if (!response.ok) throw new Error('status unavailable');
            recoverConnection();
        } catch {
            retryAttempt += 1;
            scheduleProbe();
        }
    }, delay);
}

function loseConnection() {
    connectionLost = true;
    connectionBanner().classList.add('on');
    scheduleProbe();
}

function recoverConnection() {
    connectionLost = false;
    retryAttempt = 0;
    if (retryTimer) window.clearTimeout(retryTimer);
    retryTimer = null;
    banner?.classList.remove('on');
    resolveToast('connection');
}

function messageFor(detail) {
    if (detail.kind === 'network') return NETWORK_MESSAGE;
    if (detail.kind === 'write') return WRITE_MESSAGE;
    return detail.error || SERVER_MESSAGE;
}

export function initErrorBus() {
    window.addEventListener('azimuth-api-failure', (event) => {
        const detail = event.detail || {};
        if (detail.kind === 'network') loseConnection();
        // Once connection loss is known, companion requests fail for the same
        // reason. Keep one actionable message instead of filling the stack.
        if (connectionLost && detail.kind !== 'network') return;
        showToast(messageFor(detail), {
            kind: 'error',
            duration: 0,
            key: detail.kind === 'network' ? 'connection' : '',
        });
    });
    window.addEventListener('azimuth-api-success', () => {
        if (connectionLost) recoverConnection();
    });
    window.addEventListener('online', () => {
        if (connectionLost) scheduleProbe();
    });
    window.addEventListener('offline', loseConnection);
}
