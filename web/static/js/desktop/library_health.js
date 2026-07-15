import {
    createCatalogBackup, discardCatalogRestore, getCatalogBackups, getCatalogRestoreStatus,
    getIntegrityStatus, prepareCatalogRestore, startIntegrityScan,
} from './api.js';
import { showToast } from './toast.js';
import { escapeHtml as esc, formatCount } from './dom.js';

const CACHE_MS = 30000;
let backups = null;
let integrity = null;
let restore = null;
let lastLoaded = 0;
let loading = false;
let busy = '';
let confirmName = '';
let pollTimer = null;
let backupsOpen = false;

function dateValue(value) {
    if (!value) return null;
    if (value instanceof Date) return value;
    const numeric = Number(value);
    const date = Number.isFinite(numeric) && numeric > 0
        ? new Date(numeric * 1000)
        : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
}

function shortDate(value) {
    const date = dateValue(value);
    if (!date) return 'Unknown date';
    return date.toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
    });
}

function bytes(value) {
    let size = Number(value) || 0;
    const units = ['B', 'KB', 'MB', 'GB'];
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function backupState() {
    const items = (backups && backups.backups) || [];
    if (!items.length) return { tone: 'bad', label: 'Needs attention', detail: 'No catalog snapshots yet' };
    const newest = dateValue(items[0].created_at);
    if (!newest) return { tone: 'warn', label: 'Backup due', detail: 'Newest snapshot date is unavailable' };
    const ageHours = (Date.now() - newest.getTime()) / 3600000;
    if (ageHours <= 36) return { tone: 'ok', label: 'Protected', detail: `Snapshot ${shortDate(newest)}` };
    if (ageHours <= 168) return { tone: 'warn', label: 'Backup due', detail: `Last snapshot ${shortDate(newest)}` };
    return { tone: 'bad', label: 'Needs attention', detail: `Last snapshot ${shortDate(newest)}` };
}

function integrityState() {
    const scan = (integrity && integrity.scan) || {};
    if (integrity && (integrity.bit_rot || Number(integrity.mismatch_count) > 0)) {
        return { tone: 'bad', label: 'Possible file changes detected', detail: `${Number(integrity.mismatch_count) || 0} original${Number(integrity.mismatch_count) === 1 ? '' : 's'} changed` };
    }
    if (integrity && integrity.db_error) return { tone: 'bad', label: 'Couldn’t check catalog', detail: integrity.db_error };
    if (scan.state === 'error') return { tone: 'bad', label: 'Couldn’t complete check', detail: scan.last_error || 'Try again' };
    if (scan.state === 'running') {
        return { tone: 'active', label: 'Checking originals', detail: `${Number(scan.checked) || 0} of ${Number(scan.limit) || 50}` };
    }
    if (!scan.finished_at && !Number(integrity && integrity.checksummed)) {
        return { tone: 'quiet', label: 'Not checked yet', detail: 'Start with 50 originals' };
    }
    const skipped = Number(scan.skipped_offline) || 0;
    return {
        tone: Number(scan.errors) ? 'warn' : 'ok',
        label: 'Originals checked',
        detail: `${Number(integrity && integrity.checksummed) || Number(scan.checked) || 0} recorded${skipped ? ` · ${skipped} offline skipped` : ''}`,
    };
}

function badge(state) {
    return `<span class="health-badge ${state.tone}">${esc(state.label)}</span>`;
}

function renderRestore() {
    if (!restore || !restore.artifacts_present) return '';
    if (!restore.prepared) {
        return '<div class="health-alert warn"><b>Incomplete prepared restore</b><span>Discard it before preparing another.</span>'
            + '<button class="mini-btn" type="button" data-health-discard>Discard</button></div>';
    }
    return '<div class="health-alert ok"><b>Restore prepared safely</b>'
        + `<span>${restore.name ? `${esc(shortDate(restore.created_at))} · ` : ''}${esc(bytes(restore.bytes))}. Your current catalog is untouched.</span>`
        + '<div class="health-actions">'
        + '<button class="mini-btn" type="button" data-health-copy>Copy path</button>'
        + '<button class="mini-btn" type="button" data-health-discard>Discard</button></div></div>';
}

function renderBackups() {
    const state = backupState();
    const items = (backups && backups.backups) || [];
    const rows = items.slice(0, 3).map((item) => {
        const confirming = confirmName === item.name;
        return '<li class="health-backup-row">'
            + `<span><b>${esc(shortDate(item.created_at))}</b><small>${esc(bytes(item.bytes))}</small></span>`
            + `<button class="mini-btn" type="button" data-health-prepare="${esc(item.name)}" ${busy ? 'disabled' : ''}>Prepare restore</button>`
            + (confirming ? '<div class="health-confirm"><p>Prepare this snapshot? Your current catalog will not be changed.</p>'
                + '<div class="health-actions"><button class="mini-btn" type="button" data-health-confirm-restore>Prepare safely</button>'
                + '<button class="mini-btn" type="button" data-health-cancel-restore>Cancel</button></div></div>' : '')
            + '</li>';
    }).join('');
    return '<div class="health-block"><div class="health-line"><span><b>Catalog snapshots</b>'
        + `<small>${esc(state.detail)} · ${items.length} retained</small></span>${badge(state)}</div>`
        + '<button class="btn" type="button" data-health-backup ' + (busy ? 'disabled' : '') + '>Back up catalog now</button>'
        + (rows ? `<details class="health-details"${backupsOpen ? ' open' : ''}><summary>Latest snapshots</summary><ul>${rows}</ul></details>` : '')
        + '</div>';
}

function renderIntegrity() {
    const state = integrityState();
    const running = ((integrity && integrity.scan) || {}).state === 'running';
    return '<div class="health-block"><div class="health-line"><span><b>Original files</b>'
        + `<small>${esc(state.detail)}</small></span>${badge(state)}</div>`
        + `<button class="btn" type="button" data-health-integrity ${running || busy ? 'disabled' : ''}>${running ? 'Checking…' : 'Check 50 originals'}</button></div>`;
}

function renderOffline(catalog) {
    const sources = ((catalog && catalog.sources) || []).filter((source) => !(Number(source.online) === 1 || source.online === true));
    if (!sources.length) return '';
    const rows = sources.map((source) => {
        const name = source.display_name || source.path || `Source ${source.id}`;
        const count = Number(source.active_image_count ?? source.image_count) || 0;
        return `<li><b>${esc(name)}</b><span>Disconnected · ${formatCount(count)} photos remain indexed</span></li>`;
    }).join('');
    return '<div class="health-block offline"><div class="health-line"><span><b>Disconnected sources</b>'
        + '<small>Cached previews remain available. Reconnect the drive when convenient.</small></span>'
        + `<span class="health-badge warn">${sources.length}</span></div><ul class="health-offline-list">${rows}</ul>`
        + '<button class="mini-btn" type="button" data-health-check-sources>Check again</button></div>';
}

export function renderLibraryHealth(catalog) {
    if (!backups && !integrity && loading) {
        return '<section class="dr-sec library-health"><h3>Library Health</h3><div class="muted">Checking library health…</div></section>';
    }
    if (!backups && !integrity) {
        return '<section class="dr-sec library-health"><h3>Library Health</h3><div class="health-alert warn"><b>Health status unavailable</b><span>Check the connection and try again.</span><button class="mini-btn" type="button" data-health-retry>Retry</button></div></section>';
    }
    const overall = integrityState().tone === 'bad' ? integrityState() : backupState();
    return '<section class="dr-sec library-health"><div class="health-title"><h3>Library Health</h3>'
        + badge(overall) + '</div>'
        + '<p class="health-promise">Catalog snapshots protect organization, edits, and rankings. Your original photo files still need their own backup.</p>'
        + renderRestore() + renderBackups() + renderIntegrity() + renderOffline(catalog) + '</section>';
}

export async function refreshLibraryHealth({ force = false } = {}) {
    if (loading || (!force && lastLoaded && Date.now() - lastLoaded < CACHE_MS)) return;
    loading = true;
    const [nextBackups, nextIntegrity, nextRestore] = await Promise.all([
        getCatalogBackups().catch(() => null),
        getIntegrityStatus().catch(() => null),
        getCatalogRestoreStatus().catch(() => null),
    ]);
    if (nextBackups) backups = nextBackups;
    if (nextIntegrity) integrity = nextIntegrity;
    if (nextRestore) restore = nextRestore;
    lastLoaded = Date.now();
    loading = false;
}

function errorText(result, fallback) {
    return String(result && result.data && result.data.error || fallback);
}

async function runAction(key, action, rerender) {
    if (busy) return null;
    busy = key;
    rerender();
    try {
        return await action();
    } finally {
        busy = '';
        await refreshLibraryHealth({ force: true });
        rerender();
    }
}

function syncIntegrityPolling(rerender) {
    const running = ((integrity && integrity.scan) || {}).state === 'running';
    if (!running) {
        window.clearInterval(pollTimer);
        pollTimer = null;
        return;
    }
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
        await refreshLibraryHealth({ force: true });
        rerender();
        syncIntegrityPolling(rerender);
    }, 2000);
}

export function bindLibraryHealth(root, { rerender, refreshCatalog }) {
    root.querySelector('.health-details')?.addEventListener('toggle', (event) => {
        backupsOpen = event.currentTarget.open;
    });
    root.querySelector('[data-health-retry]')?.addEventListener('click', async () => {
        await refreshLibraryHealth({ force: true });
        rerender();
    });
    root.querySelector('[data-health-backup]')?.addEventListener('click', async () => {
        const result = await runAction('backup', createCatalogBackup, rerender);
        showToast(result && result.ok ? 'Catalog backup created' : errorText(result, 'Couldn’t back up the catalog'));
    });
    root.querySelector('[data-health-integrity]')?.addEventListener('click', async () => {
        const result = await runAction('integrity', () => startIntegrityScan(50), rerender);
        showToast(result && result.ok ? 'Checking 50 originals' : errorText(result, 'Couldn’t start the check'));
        syncIntegrityPolling(rerender);
    });
    root.querySelectorAll('[data-health-prepare]').forEach((button) => button.addEventListener('click', () => {
        confirmName = button.dataset.healthPrepare || '';
        rerender();
    }));
    root.querySelector('[data-health-cancel-restore]')?.addEventListener('click', () => {
        confirmName = '';
        rerender();
    });
    root.querySelector('[data-health-confirm-restore]')?.addEventListener('click', async () => {
        const name = confirmName;
        const result = await runAction('restore', () => prepareCatalogRestore(name), rerender);
        if (result && result.ok) {
            confirmName = '';
            rerender();
        }
        showToast(result && result.ok ? 'Restore prepared · current catalog untouched' : errorText(result, 'Couldn’t prepare the restore'));
    });
    root.querySelector('[data-health-discard]')?.addEventListener('click', async () => {
        const result = await runAction('discard', discardCatalogRestore, rerender);
        showToast(result && result.ok ? 'Prepared restore discarded' : errorText(result, 'Couldn’t discard the prepared restore'));
    });
    root.querySelector('[data-health-copy]')?.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(restore && restore.staging_path || '');
            showToast('Restore path copied');
        } catch {
            showToast('Couldn’t copy the path');
        }
    });
    root.querySelector('[data-health-check-sources]')?.addEventListener('click', refreshCatalog);
    syncIntegrityPolling(rerender);
}

export function stopLibraryHealthPolling() {
    window.clearInterval(pollTimer);
    pollTimer = null;
}
