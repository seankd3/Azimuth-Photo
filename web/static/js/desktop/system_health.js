/** System Health settings panel — live aggregation from /api/health/details. */
import { getHealthDetails } from './api.js';
import { escapeHtml as esc } from './dom.js';

const POLL_MS = 30000;
let payload = null;
let lastLoaded = 0;
let lastRefreshFailed = false;
let loading = false;
let pollTimer = null;

function ageLabel(ts) {
    const minutes = Math.floor((Date.now() - ts) / 60000);
    if (minutes < 1) return 'moments ago';
    if (minutes < 60) return `${minutes}m ago`;
    return `${Math.floor(minutes / 60)}h ago`;
}

function badge(status) {
    const tone = status === 'bad' || status === 'warn' || status === 'ok' ? status : 'ok';
    const label = tone === 'ok' ? 'OK' : tone === 'warn' ? 'Warn' : 'Bad';
    return `<span class="health-badge ${tone}">${label}</span>`;
}

function overallBanner(overall) {
    if (overall !== 'bad') return '';
    return '<div class="health-alert warn" role="status"><b>Health degraded</b>'
        + '<span>One or more checks need attention. Review the rows below.</span></div>';
}

function checkRow(check) {
    return '<div class="health-block" data-health-check="' + esc(check.id || '') + '">'
        + '<div class="health-line"><span><b>' + esc(check.label || check.id || 'Check') + '</b>'
        + '<small>' + esc(check.detail || '') + '</small></span>'
        + badge(check.status) + '</div></div>';
}

export function renderSystemHealth() {
    if (!payload && loading) {
        return '<section class="dr-sec system-health" id="system-health-panel"><h3>System Health</h3>'
            + '<div class="muted">Checking system health…</div></section>';
    }
    if (!payload) {
        return '<section class="dr-sec system-health" id="system-health-panel"><h3>System Health</h3>'
            + '<div class="health-alert warn"><b>Health status unavailable</b>'
            + '<span>Check the connection and try again.</span>'
            + '<button class="mini-btn" type="button" data-system-health-retry>Retry</button></div></section>';
    }
    const checks = payload.checks || [];
    const overall = payload.overall || 'ok';
    // A stale snapshot must never wear the "Live read" promise or an all-clear badge.
    const stale = lastRefreshFailed;
    const staleAlert = '<div class="health-alert warn" role="status"><b>Couldn’t refresh health</b>'
        + `<span>Showing the last read from ${esc(ageLabel(lastLoaded))}. Retries automatically.</span>`
        + '<button class="mini-btn" type="button" data-system-health-retry>Retry now</button></div>';
    return '<section class="dr-sec system-health" id="system-health-panel">'
        + '<div class="health-title"><h3>System Health</h3>' + badge(stale && overall === 'ok' ? 'warn' : overall) + '</div>'
        + (stale
            ? staleAlert
            : '<p class="health-promise">Live read of catalog, backups, vault, disk, memory, and workers — the same story a host watchdog would surface, inside the app.</p>')
        + overallBanner(overall)
        + checks.map(checkRow).join('')
        + '</section>';
}

export async function refreshSystemHealth({ force = false } = {}) {
    if (loading || (!force && lastLoaded && Date.now() - lastLoaded < 5000)) return;
    loading = true;
    try {
        const next = await getHealthDetails().catch(() => null);
        if (next) {
            payload = next;
            lastLoaded = Date.now();
        }
        lastRefreshFailed = !next;
    } finally {
        loading = false;
    }
}

function syncPolling(rerender) {
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
        await refreshSystemHealth({ force: true });
        rerender();
    }, POLL_MS);
}

export function bindSystemHealth(root, { rerender }) {
    root.querySelector('[data-system-health-retry]')?.addEventListener('click', async () => {
        await refreshSystemHealth({ force: true });
        rerender();
    });
    syncPolling(rerender);
}

export function stopSystemHealthPolling() {
    window.clearInterval(pollTimer);
    pollTimer = null;
}
