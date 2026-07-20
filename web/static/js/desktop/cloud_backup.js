/** Cloud Backup settings panel — rclone vault for original library trees. */
import {
    getCloudBackupStatus, saveCloudBackupConfig, startCloudBackup, stopCloudBackup,
} from './api.js';
import { showToast } from './toast.js';
import { escapeHtml as esc } from './dom.js';

const CACHE_MS = 15000;
let status = null;
let lastLoaded = 0;
let loading = false;
let busy = '';
let pollTimer = null;
let draft = null;

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
    if (!date) return 'Never';
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

function config() {
    return draft || (status && status.config) || {
        remote: '',
        dest_prefix: '',
        trees: [],
        bwlimit: '07:00,3M 23:00,off',
        exclude_from_catalog: true,
        nightly_enabled: false,
    };
}

function ensureDraft() {
    if (!draft) draft = { ...config(), trees: [...(config().trees || [])] };
    return draft;
}

function syncState() {
    if (!status) return { tone: 'quiet', label: 'Unknown', detail: 'Status unavailable' };
    if (!status.available) {
        return {
            tone: 'warn',
            label: 'Unavailable',
            detail: status.unavailable_reason || 'rclone is not installed',
        };
    }
    if (status.state === 'running' || status.state === 'stopping') {
        const tree = status.current_tree ? status.current_tree.split(/[\\/]/).pop() : 'library';
        return {
            tone: 'active',
            label: status.state === 'stopping' ? 'Pausing' : 'Syncing',
            detail: `${tree} · ${Math.round(Number(status.pct) || 0)}%`
                + (status.speed ? ` · ${status.speed}` : '')
                + (status.eta ? ` · ETA ${status.eta}` : ''),
        };
    }
    if (status.state === 'waiting') {
        return {
            tone: 'quiet',
            label: 'Waiting',
            detail: status.message || 'yielding disk to preview build',
        };
    }
    if (status.state === 'error' || status.last_error) {
        return {
            tone: 'bad',
            label: 'Needs attention',
            detail: String(status.last_error || status.message || 'Last sync failed'),
        };
    }
    if (status.last_ok_at) {
        return {
            tone: 'ok',
            label: 'Protected',
            detail: `Last sync ${shortDate(status.last_ok_at)} · ${bytes(status.last_bytes)}`,
        };
    }
    return { tone: 'quiet', label: 'Not synced yet', detail: 'Configure a remote, then start a backup' };
}

function badge(state) {
    return `<span class="health-badge ${state.tone}">${esc(state.label)}</span>`;
}

function treeOptions(catalog) {
    const sources = ((catalog && catalog.sources) || []).filter((source) => source && source.path);
    const selected = new Set((config().trees || []).map(String));
    if (!sources.length) {
        return '<div class="setting-hint">Add a library source first — Cloud Backup copies those folders.</div>';
    }
    return sources.map((source) => {
        const path = String(source.path);
        const name = source.display_name || path;
        const checked = selected.has(path) ? ' checked' : '';
        return `<label class="setting-toggle cloud-backup-tree">`
            + `<span><b>${esc(name)}</b><small>${esc(path)}</small></span>`
            + `<input type="checkbox" data-cloud-tree="${esc(path)}"${checked}${busy ? ' disabled' : ''}>`
            + '<i></i></label>';
    }).join('');
}

function remoteOptions() {
    const remotes = (status && status.remotes) || [];
    const current = String(config().remote || '');
    const options = ['<option value="">Choose a remote…</option>']
        .concat(remotes.map((name) => (
            `<option value="${esc(name)}"${name === current ? ' selected' : ''}>${esc(name)}</option>`
        )));
    if (current && !remotes.includes(current)) {
        options.push(`<option value="${esc(current)}" selected>${esc(current)} (missing)</option>`);
    }
    return options.join('');
}

function progressRow() {
    if (!status || (status.state !== 'running' && status.state !== 'stopping')) return '';
    const pct = Math.max(0, Math.min(100, Number(status.pct) || 0));
    return '<div class="work-row cloud-backup-progress">'
        + '<div class="wr-body"><div class="wr-top"><span>Vault sync</span>'
        + `<span class="v">${esc(syncState().detail)}</span></div>`
        + `<div class="wr-track"><i style="width:${pct}%"></i></div></div></div>`;
}

function freshnessRows() {
    const trees = (status && status.trees) || [];
    if (!trees.length) return '';
    const rows = trees.map((tree) => (
        `<li><b>${esc(tree.name || tree.path)}</b>`
        + `<span>${esc(shortDate(tree.last_ok_at))} · ${esc(bytes(tree.bytes))}</span></li>`
    )).join('');
    return `<ul class="health-offline-list cloud-backup-freshness">${rows}</ul>`;
}

export function renderCloudBackup(catalog) {
    if (!status && loading) {
        return '<section class="dr-sec cloud-backup" id="cloud-backup-panel"><h3>Cloud Backup</h3>'
            + '<div class="muted">Checking Cloud Backup…</div></section>';
    }
    if (!status) {
        return '<section class="dr-sec cloud-backup" id="cloud-backup-panel"><h3>Cloud Backup</h3>'
            + '<div class="health-alert warn"><b>Cloud Backup status unavailable</b>'
            + '<span>Check the connection and try again.</span>'
            + '<button class="mini-btn" type="button" data-cloud-retry>Retry</button></div></section>';
    }
    const state = syncState();
    const cfg = config();
    const running = status.state === 'running' || status.state === 'stopping';
    const unavailable = !status.available;
    return '<section class="dr-sec cloud-backup" id="cloud-backup-panel">'
        + '<div class="health-title"><h3>Cloud Backup</h3>' + badge(state) + '</div>'
        + '<p class="health-promise">Copy original photo files to an rclone remote. '
        + 'Catalog snapshots stay local — this backs up the files themselves.</p>'
        + (unavailable
            ? `<div class="health-alert warn"><b>Unavailable</b><span>${esc(state.detail)}</span></div>`
            : '')
        + `<div class="setting-status">${esc(state.detail)}</div>`
        + progressRow()
        + freshnessRows()
        + '<label class="setting-row" for="cloud-backup-remote"><span><b>rclone remote</b></span>'
        + `<select id="cloud-backup-remote" data-cloud-field="remote"${unavailable || busy ? ' disabled' : ''}>`
        + remoteOptions() + '</select></label>'
        + '<label class="setting-row" for="cloud-backup-prefix"><span><b>Destination prefix</b></span>'
        + `<input class="drawer-input" id="cloud-backup-prefix" data-cloud-field="dest_prefix" `
        + `spellcheck="false" autocomplete="off" value="${esc(cfg.dest_prefix || '')}"`
        + `${unavailable || busy ? ' disabled' : ''}></label>`
        + '<label class="setting-row" for="cloud-backup-bwlimit"><span><b>Bandwidth schedule</b></span>'
        + `<input class="drawer-input" id="cloud-backup-bwlimit" data-cloud-field="bwlimit" `
        + `spellcheck="false" autocomplete="off" value="${esc(cfg.bwlimit || '')}"`
        + `${unavailable || busy ? ' disabled' : ''}>`
        + '<small>rclone --bwlimit timetable, e.g. 07:00,3M 23:00,off</small></label>'
        + '<div class="health-block"><div class="health-line"><span><b>Library trees</b>'
        + '<small>Only selected sources are copied.</small></span></div>'
        + treeOptions(catalog) + '</div>'
        + `<label class="setting-toggle"><span>Skip trashed and rejected</span>`
        + `<input type="checkbox" data-cloud-field="exclude_from_catalog"`
        + `${cfg.exclude_from_catalog ? ' checked' : ''}${unavailable || busy ? ' disabled' : ''}>`
        + '<i></i></label>'
        + `<label class="setting-toggle"><span>Nightly sync</span>`
        + `<input type="checkbox" data-cloud-field="nightly_enabled"`
        + `${cfg.nightly_enabled ? ' checked' : ''}${unavailable || busy ? ' disabled' : ''}>`
        + '<i></i></label>'
        + '<div class="health-actions">'
        + `<button class="mini-btn" type="button" data-cloud-save ${unavailable || busy ? 'disabled' : ''}>Save</button>`
        + `<button class="btn" type="button" data-cloud-run ${unavailable || busy ? 'disabled' : ''}>`
        + `${running ? 'Pause' : 'Start backup'}</button>`
        + '</div></section>';
}

export async function refreshCloudBackup({ force = false } = {}) {
    if (loading || (!force && lastLoaded && Date.now() - lastLoaded < CACHE_MS)) return;
    loading = true;
    const next = await getCloudBackupStatus().catch(() => null);
    if (next) {
        status = next;
        if (!draft) draft = { ...next.config, trees: [...(next.config?.trees || [])] };
    }
    lastLoaded = Date.now();
    loading = false;
}

function errorText(result, fallback) {
    return String(result && result.data && result.data.error || fallback);
}

function collectDraft(root) {
    const next = ensureDraft();
    const remote = root.querySelector('[data-cloud-field="remote"]');
    const prefix = root.querySelector('[data-cloud-field="dest_prefix"]');
    const bwlimit = root.querySelector('[data-cloud-field="bwlimit"]');
    const exclude = root.querySelector('[data-cloud-field="exclude_from_catalog"]');
    const nightly = root.querySelector('[data-cloud-field="nightly_enabled"]');
    if (remote) next.remote = remote.value || '';
    if (prefix) next.dest_prefix = prefix.value || '';
    if (bwlimit) next.bwlimit = bwlimit.value || '';
    if (exclude) next.exclude_from_catalog = Boolean(exclude.checked);
    if (nightly) next.nightly_enabled = Boolean(nightly.checked);
    next.trees = [...root.querySelectorAll('[data-cloud-tree]:checked')].map((input) => input.dataset.cloudTree);
    draft = next;
    return next;
}

async function runAction(key, action, rerender) {
    if (busy) return null;
    busy = key;
    rerender();
    try {
        return await action();
    } finally {
        busy = '';
        await refreshCloudBackup({ force: true });
        rerender();
    }
}

function syncPolling(rerender) {
    const running = status && (status.state === 'running' || status.state === 'stopping');
    if (!running) {
        window.clearInterval(pollTimer);
        pollTimer = null;
        return;
    }
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
        await refreshCloudBackup({ force: true });
        rerender();
        syncPolling(rerender);
    }, 1500);
}

export function bindCloudBackup(root, { rerender }) {
    const panel = root.querySelector('#cloud-backup-panel');
    if (!panel) return;
    panel.querySelector('[data-cloud-retry]')?.addEventListener('click', async () => {
        await refreshCloudBackup({ force: true });
        rerender();
    });
    panel.querySelectorAll('[data-cloud-field], [data-cloud-tree]').forEach((input) => {
        input.addEventListener('change', () => collectDraft(panel));
        input.addEventListener('input', () => collectDraft(panel));
    });
    panel.querySelector('[data-cloud-save]')?.addEventListener('click', async () => {
        const payload = collectDraft(panel);
        const result = await runAction('save', () => saveCloudBackupConfig(payload), rerender);
        if (result && result.ok) {
            draft = { ...(result.data.config || payload), trees: [...((result.data.config || payload).trees || [])] };
            showToast('Cloud Backup settings saved');
        } else {
            showToast(errorText(result, 'Couldn’t save Cloud Backup settings'));
        }
    });
    panel.querySelector('[data-cloud-run]')?.addEventListener('click', async () => {
        const running = status && (status.state === 'running' || status.state === 'stopping');
        if (running) {
            const result = await runAction('stop', stopCloudBackup, rerender);
            showToast(result && result.ok ? 'Cloud Backup pausing' : errorText(result, 'Couldn’t pause'));
            syncPolling(rerender);
            return;
        }
        collectDraft(panel);
        const saved = await saveCloudBackupConfig(draft);
        if (!saved?.ok) {
            showToast(errorText(saved, 'Save settings before starting'));
            return;
        }
        draft = { ...(saved.data.config || draft), trees: [...((saved.data.config || draft).trees || [])] };
        const result = await runAction('start', startCloudBackup, rerender);
        showToast(result && result.ok ? 'Cloud Backup started' : errorText(result, 'Couldn’t start Cloud Backup'));
        syncPolling(rerender);
    });
    syncPolling(rerender);
}

export function stopCloudBackupPolling() {
    window.clearInterval(pollTimer);
    pollTimer = null;
}
