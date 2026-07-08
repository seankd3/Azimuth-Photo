import {
    addCatalogSource, clearCache, getAiStatus, getCacheStatus, getCatalog, getPeopleStatus,
    getRemoteAccess, getScanStatus, pauseAiEmbeddings, pausePeopleScan, removeCatalogSource,
    rescanCatalogSource, resumeAiEmbeddings, resumePeopleScan, startCachePregen, stopCachePregen,
} from './api.js';
import {
    on, patchPrefs, setThumbSize, viewState,
} from './state.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';

let open = false;
let drawerTimer = null;
let activityTimer = null;
let scanTimer = null;
let scanSourceId = null;
let catalog = null;
let aiStatus = null;
let cacheStatus = null;
let peopleStatus = null;
let remoteAccess = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function bytes(value) {
    const n = Number(value) || 0;
    if (!n) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = n;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function pct(value) {
    return Math.max(0, Math.min(100, Number(value) || 0));
}

function progress(done, total) {
    const d = Number(done) || 0;
    const t = Number(total) || 0;
    return t > 0 ? pct((d / t) * 100) : 0;
}

function activeProgress() {
    const ai = pct(aiStatus && aiStatus.progress_pct);
    const pregen = cacheStatus && cacheStatus.pregen ? cacheStatus.pregen : {};
    const cache = pct((pregen.preview && pregen.preview.progress_pct) || pregen.progress_pct);
    const worker = (peopleStatus && peopleStatus.worker) || {};
    const counts = (peopleStatus && peopleStatus.counts) || {};
    const people = worker.progress_pct != null
        ? pct(worker.progress_pct)
        : progress(counts.detected_faces || counts.people || 0, (counts.detected_faces || 0) + (counts.pending_cached_images || 0));
    return { ai, cache, people };
}

function statusText(name, data) {
    if (name === 'AI') {
        return `${fmt(data.embedded)} / ${fmt(data.total_images)} · ${data.worker_state || 'idle'}`;
    }
    if (name === 'Cache') {
        const pregen = (data && data.pregen) || {};
        const preview = pregen.preview || {};
        return `${fmt(preview.count)} / ${fmt(preview.total)} · ${pregen.state || 'idle'}`;
    }
    const worker = (data && data.worker) || {};
    const counts = (data && data.counts) || {};
    return `${fmt(counts.detected_faces || counts.people || 0)} faces · ${worker.state || 'idle'}`;
}

function renderActivity() {
    const widget = document.getElementById('activity-widget');
    const pop = document.getElementById('activity-popover');
    if (!widget || !pop) return;
    const values = activeProgress();
    for (const [key, value] of Object.entries(values)) {
        const bar = widget.querySelector(`[data-worker="${key}"]`);
        if (bar) bar.style.height = `${Math.max(3, Math.round((value / 100) * 14))}px`;
    }
    const aiPaused = aiStatus && aiStatus.embedding_manual_pause;
    const cachePaused = cacheStatus && cacheStatus.pregen && cacheStatus.pregen.manual_pause;
    const peoplePaused = peopleStatus && peopleStatus.worker && peopleStatus.worker.manual_pause;
    widget.classList.toggle('paused', Boolean(aiPaused || cachePaused || peoplePaused));
    widget.classList.toggle('active', Object.values(values).some((value) => value > 0 && value < 100));
    pop.innerHTML = [
        ['AI', values.ai, aiStatus || {}],
        ['Cache', values.cache, cacheStatus || {}],
        ['People', values.people, peopleStatus || {}],
    ].map(([name, value, data]) => (
        `<div class="ap-row"><span>${name}</span><span class="ap-track"><i style="width:${value}%"></i></span><span class="ap-val">${esc(statusText(name, data))}</span></div>`
    )).join('');
}

async function refreshActivity() {
    if (document.hidden) return;
    const [ai, cache, people] = await Promise.all([getAiStatus(), getCacheStatus(), getPeopleStatus()]);
    aiStatus = ai || aiStatus;
    cacheStatus = cache || cacheStatus;
    peopleStatus = people || peopleStatus;
    renderActivity();
    if (open) renderDrawer();
}

function sourceName(source) {
    return source.display_name || source.name || source.path || `Source ${source.id}`;
}

function sourceCount(source) {
    return source.active_image_count != null ? source.active_image_count : source.image_count;
}

function lastScan(source) {
    const raw = source.last_scan_at || source.scanned_at || source.updated_at;
    if (!raw) return 'not scanned';
    const stamp = Number(raw);
    const date = Number.isFinite(stamp) ? new Date(stamp * 1000) : new Date(raw);
    if (Number.isNaN(date.getTime())) return 'not scanned';
    return `scanned ${date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;
}

function renderSources() {
    const sources = (catalog && catalog.sources) || [];
    const rows = sources.length ? sources.map((source) => {
        const online = Number(source.online) === 1 || source.online === true;
        const id = Number(source.id);
        const scanning = scanSourceId === id;
        return `<article class="src-card" data-source-id="${id}">`
            + `<span class="sc-dot ${online ? 'on' : ''}"></span><div>`
            + `<div class="sc-name">${esc(sourceName(source))}</div>`
            + `<div class="sc-sub">${fmt(sourceCount(source))} photos · ${esc(lastScan(source))}${online ? '' : ' · offline'}</div>`
            + '<div class="src-actions">'
            + `<button class="mini-btn" data-act="rescan" ${online ? '' : 'aria-disabled="true"'}>Rescan</button>`
            + '<button class="mini-btn danger" data-act="remove">Remove</button></div>'
            + `<div class="remove-choice" hidden><button class="mini-btn" data-mode="keep">Keep photos</button><button class="mini-btn danger" data-mode="delete">Delete catalog data</button></div>`
            + `<div class="scan-progress" ${scanning ? '' : 'hidden'}>Scanning…</div>`
            + '</div></article>';
    }).join('') : '<div class="muted">No sources yet.</div>';
    return '<section class="dr-sec"><h3>Sources</h3>'
        + `<div id="drawer-sources">${rows}</div>`
        + '<form class="add-source" id="add-source-form"><input class="drawer-input" name="path" placeholder="/path/to/photos" autocomplete="off" aria-label="Source path"><button class="btn">Add & scan</button></form>'
        + '</section>';
}

function workerRow(key, label, value, detail, paused, actionLabel) {
    return '<div class="work-row" data-worker-row="' + key + '">'
        + '<div class="wr-body"><div class="wr-top">'
        + `<span>${esc(label)}</span><span class="v">${esc(detail)}</span></div>`
        + `<div class="wr-track"><i style="width:${pct(value)}%"></i></div></div>`
        + `<button class="mini-btn" data-worker-action="${key}">${esc(actionLabel || (paused ? 'Resume' : 'Pause'))}</button></div>`;
}

function renderWork() {
    const pregen = (cacheStatus && cacheStatus.pregen) || {};
    const preview = pregen.preview || {};
    const worker = (peopleStatus && peopleStatus.worker) || {};
    const counts = (peopleStatus && peopleStatus.counts) || {};
    const peoplePct = worker.progress_pct != null
        ? pct(worker.progress_pct)
        : progress(counts.detected_faces || counts.people || 0, (counts.detected_faces || 0) + (counts.pending_cached_images || 0));
    return '<section class="dr-sec"><h3>Background Work</h3>'
        + workerRow('ai', 'AI embeddings', aiStatus ? aiStatus.progress_pct : 0, aiStatus ? statusText('AI', aiStatus) : 'unavailable', aiStatus && aiStatus.embedding_manual_pause)
        + workerRow('cache', 'Cache pregeneration', (preview.progress_pct || pregen.progress_pct || 0), cacheStatus ? statusText('Cache', cacheStatus) : 'unavailable', pregen.manual_pause || pregen.state === 'paused', pregen.manual_pause || pregen.state === 'paused' ? 'Resume' : 'Pause')
        + workerRow('people', 'People scan', peoplePct, peopleStatus ? statusText('People', peopleStatus) : 'unavailable', worker.manual_pause)
        + '</section>';
}

function renderStorage() {
    const tiers = (cacheStatus && cacheStatus.disk && cacheStatus.disk.tiers) || {};
    const chips = ['sm', 'md', 'lg'].map((size) => {
        const tier = tiers[size] || {};
        return `<span class="tier-chip"><b>${size.toUpperCase()}</b><span>${fmt(tier.count)} files</span><span>${bytes(tier.bytes)}</span></span>`;
    }).join('');
    return '<section class="dr-sec"><h3>Storage</h3>'
        + `<div class="tier-chips">${chips}</div>`
        + '<button class="btn" id="clear-cache-btn">Clear cache</button>'
        + '</section>';
}

function renderRemote() {
    const url = (remoteAccess && remoteAccess.tailscale && remoteAccess.tailscale.url)
        || (remoteAccess && remoteAccess.current_url)
        || '';
    return '<section class="dr-sec"><h3>Remote Access</h3>'
        + '<div class="remote-row">'
        + `<span class="remote-url" title="${esc(url)}">${esc(url || 'Unavailable')}</span>`
        + `<button class="mini-btn" id="copy-remote" ${url ? '' : 'aria-disabled="true"'}>Copy</button>`
        + '</div></section>';
}

function checkbox(key, label) {
    return `<div class="pref-row"><label for="pref-${key}">${esc(label)}</label><input id="pref-${key}" type="checkbox" data-pref="${key}" ${viewState.prefs[key] ? 'checked' : ''}></div>`;
}

function renderPrefs() {
    return '<section class="dr-sec"><h3>Preferences</h3>'
        + '<div class="pref-row"><label for="drawer-thumb-size">Thumbnail size</label><input class="ctl-range" id="drawer-thumb-size" type="range" min="120" max="320" step="10" value="' + viewState.thumbSize + '"></div>'
        + '<div class="pref-row"><label for="pref-density">Density</label><select id="pref-density" data-pref-sel="density">'
        + `<option value="comfortable"${viewState.prefs.density !== 'compact' ? ' selected' : ''}>Comfortable</option>`
        + `<option value="compact"${viewState.prefs.density === 'compact' ? ' selected' : ''}>Compact</option></select></div>`
        + checkbox('badgeCheck', 'Cell badge · check')
        + checkbox('badgeFlag', 'Cell badge · flag')
        + checkbox('badgeElo', 'Cell badge · Elo')
        + checkbox('badgeIndex', 'Cell badge · index')
        + checkbox('reduceMotion', 'Reduce motion')
        + '</section>';
}

function renderDrawer() {
    const body = document.getElementById('drawer-body');
    if (!body) return;
    body.innerHTML = renderSources() + renderWork() + renderStorage() + renderRemote() + renderPrefs();
    bindDrawerActions();
}

async function refreshDrawer() {
    const [nextCatalog, ai, cache, people, remote] = await Promise.all([
        getCatalog(), getAiStatus(), getCacheStatus(), getPeopleStatus(), getRemoteAccess(),
    ]);
    catalog = nextCatalog || catalog;
    aiStatus = ai || aiStatus;
    cacheStatus = cache || cacheStatus;
    peopleStatus = people || peopleStatus;
    remoteAccess = remote || remoteAccess;
    renderActivity();
    renderDrawer();
}

async function pollScanUntilDone(sourceId) {
    scanSourceId = Number(sourceId) || null;
    clearInterval(scanTimer);
    const tick = async () => {
        const status = await getScanStatus();
        if (!status || !status.scanning) {
            clearInterval(scanTimer);
            scanTimer = null;
            scanSourceId = null;
            catalog = await getCatalog();
            renderDrawer();
            showToast('Source scan finished');
            return;
        }
        const progressEl = document.querySelector(`.src-card[data-source-id="${scanSourceId}"] .scan-progress`);
        if (progressEl) progressEl.textContent = `Scanning ${fmt(status.processed || status.count || 0)} photos`;
    };
    await tick();
    scanTimer = setInterval(tick, 1000);
    renderDrawer();
}

async function handleSourceAction(card, action) {
    const sourceId = Number(card.dataset.sourceId);
    if (!sourceId) return;
    if (action === 'rescan') {
        const result = await rescanCatalogSource(sourceId);
        if (result && result.ok) {
            showToast('Rescan started');
            pollScanUntilDone(sourceId);
        } else showToast('Rescan could not start');
    } else if (action === 'remove') {
        card.querySelector('.remove-choice').hidden = false;
    }
}

async function handleRemove(card, mode) {
    const sourceId = Number(card.dataset.sourceId);
    const result = await removeCatalogSource(sourceId, mode);
    if (result && result.ok) {
        catalog = result.catalog || await getCatalog();
        renderDrawer();
        showToast(mode === 'keep' ? 'Source removed; photos kept in catalog' : 'Source and catalog data removed. Undo is unavailable.');
    } else showToast('Source could not be removed');
}

function bindDrawerActions() {
    const body = document.getElementById('drawer-body');
    body.querySelector('#add-source-form')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = event.currentTarget.querySelector('input[name="path"]');
        const path = input ? input.value.trim() : '';
        if (!path) return;
        const result = await addCatalogSource(path, true);
        if (result && result.ok) {
            catalog = result.catalog || catalog;
            event.currentTarget.reset();
            renderDrawer();
            showToast('Source added; scan started');
            pollScanUntilDone(result.source && result.source.id);
        } else showToast('Source could not be added');
    });
    for (const btn of body.querySelectorAll('[data-act]')) {
        btn.addEventListener('click', () => {
            if (btn.getAttribute('aria-disabled') === 'true') return;
            handleSourceAction(btn.closest('.src-card'), btn.dataset.act);
        });
    }
    for (const btn of body.querySelectorAll('[data-mode]')) {
        btn.addEventListener('click', () => handleRemove(btn.closest('.src-card'), btn.dataset.mode));
    }
    for (const btn of body.querySelectorAll('[data-worker-action]')) {
        btn.addEventListener('click', async () => {
            const key = btn.dataset.workerAction;
            let result = null;
            if (key === 'ai') result = aiStatus && aiStatus.embedding_manual_pause ? await resumeAiEmbeddings() : await pauseAiEmbeddings();
            if (key === 'cache') {
                const pregen = (cacheStatus && cacheStatus.pregen) || {};
                result = pregen.manual_pause || pregen.state === 'paused' ? await startCachePregen() : await stopCachePregen();
            }
            if (key === 'people') {
                const worker = (peopleStatus && peopleStatus.worker) || {};
                result = worker.manual_pause ? await resumePeopleScan() : await pausePeopleScan();
            }
            if (!result) showToast('Worker command did not save');
            await refreshDrawer();
        });
    }
    body.querySelector('#clear-cache-btn')?.addEventListener('click', async () => {
        const result = await clearCache();
        if (result && result.ok) {
            cacheStatus = result.cache_stats || await getCacheStatus();
            renderDrawer();
            showToast('Cache cleared. Undo is unavailable.');
        } else showToast('Cache could not be cleared');
    });
    body.querySelector('#copy-remote')?.addEventListener('click', async (event) => {
        if (event.currentTarget.getAttribute('aria-disabled') === 'true') return;
        const url = (remoteAccess && remoteAccess.tailscale && remoteAccess.tailscale.url) || remoteAccess.current_url || '';
        try {
            await navigator.clipboard.writeText(url);
            showToast('Remote URL copied');
        } catch {
            showToast('Copy failed');
        }
    });
    body.querySelector('#drawer-thumb-size')?.addEventListener('input', (event) => setThumbSize(event.target.value));
    body.querySelector('[data-pref-sel="density"]')?.addEventListener('change', (event) => patchPrefs({ density: event.target.value }));
    for (const input of body.querySelectorAll('[data-pref]')) {
        input.addEventListener('change', () => patchPrefs({ [input.dataset.pref]: input.checked }));
    }
}

function startDrawerPolling() {
    clearInterval(drawerTimer);
    refreshDrawer();
    drawerTimer = setInterval(refreshDrawer, 5000);
}

function stopDrawerPolling() {
    clearInterval(drawerTimer);
    drawerTimer = null;
}

export function openSystemDrawer() {
    if (open) return;
    const drawer = document.getElementById('drawer');
    const scrim = document.getElementById('drawer-scrim');
    open = true;
    scrim.hidden = false;
    drawer.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
        scrim.classList.add('on');
        drawer.classList.add('on');
    });
    trapFocus(drawer, document.getElementById('drawer-close'));
    startDrawerPolling();
}

export function closeSystemDrawer() {
    if (!open) return;
    const drawer = document.getElementById('drawer');
    const scrim = document.getElementById('drawer-scrim');
    open = false;
    scrim.classList.remove('on');
    drawer.classList.remove('on');
    drawer.setAttribute('aria-hidden', 'true');
    setTimeout(() => {
        if (!open) scrim.hidden = true;
    }, 240);
    releaseFocus(drawer);
    stopDrawerPolling();
}

export function systemDrawerOpen() {
    return open;
}

export function initDrawer() {
    document.getElementById('system-btn').addEventListener('click', openSystemDrawer);
    document.getElementById('activity-widget').addEventListener('click', openSystemDrawer);
    document.getElementById('drawer-close').addEventListener('click', closeSystemDrawer);
    document.getElementById('drawer-scrim').addEventListener('click', closeSystemDrawer);
    document.getElementById('drawer').addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeSystemDrawer();
        }
    });
    on('thumbsize', () => {
        const input = document.getElementById('drawer-thumb-size');
        if (input) input.value = String(viewState.thumbSize);
    });
    on('prefs', () => {
        if (open) renderDrawer();
    });
    refreshActivity();
    activityTimer = setInterval(refreshActivity, 10000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refreshActivity();
    });
}
