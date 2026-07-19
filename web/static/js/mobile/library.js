// Library tab: real collections (create/add/browse), Picked/Rejected
// rows with real counts from /api/counts, and source status from
// /api/catalog. Collection rows show the real sorted % computed from
// each member's ranking signals.

import {
    createCollection, deleteCollection, fetchJson, getAiStatus, getCacheStatus, getCatalog, getCollection, getCounts,
    getFoldersTree, getPeopleStatus, listCollections,
    renameCollection, setBackgroundWork, thumbUrl, writeFailureMessage,
} from './api.js';
import { applySmartScopeSort, nav, on, patchScope, setScope, clearScope, scopePatchFromSmartQuery } from './state.js';
import { canInstall, promptInstall } from './install.js';
import { dismissSheetThen, openSheet } from './selection.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { openCollectionShareSheet, renderSharedView } from './sharing.js';
import { offlineSummary, openOfflineStatusSheet } from './offline.js';
import { renderBackupView, stopBackupView } from './backup.js';
import { INACTIVE_WORKER_STATES, normalizeWorkerState } from '../worker_state.js';

// Matches RANK_QUALITY_MIN_SIGNALS in data/repositories/rankings.py.
const SORT_QUALITY_MIN_SIGNALS = 3;
const DISMISSED_SUGGESTIONS_KEY = 'pa_m_dismissed_suggestions';
const TOAST_ACTION_RESET_MS = 6200;
// Shared contract: const INACTIVE_WORKER_STATES = new Set(['idle', 'ready', 'paused', 'complete', 'caught_up', 'error', 'disabled', 'unavailable', 'stale']);

let root = null;
let built = false;
let counts = null;
let collections = null;
let catalog = null;
let folderTree = null;
let folderBrowser = null;
let suggestions = null;
let suggestionsLoading = false;
let suggestionsLoaded = false;
let showingCollection = false;
let loadError = false;
let workStatus = null;
let workPollTimer = null;
let workLoading = false;
let activeSuggestionIndex = 0;
const sortedPctCache = new Map();

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmtInt = (n) => (n == null ? '…' : Number(n).toLocaleString('en-US'));
const suggestionFingerprint = (s) => s.fingerprint || `${s.kind || ''}|${s.cover_image_id || ''}|${s.count || 0}`;
const folderName = (path) => String(path || '').split('/').filter(Boolean).pop() || path || 'Folder';

function folderLabel(node, source) {
    if (!node) return (source && (source.display_name || folderName(source.path))) || 'Source';
    return node.name || folderName(node.path);
}

function folderChildren(node, source) {
    return node ? (node.children || []) : (source && source.folders) || [];
}

function findFolderSource(source) {
    const sources = (folderTree && folderTree.sources) || [];
    return sources.find((item) => String(item.id) === String(source.id))
        || sources.find((item) => item.path === source.path)
        || null;
}

function openFolderBrowser(source) {
    const treeSource = findFolderSource(source);
    if (!treeSource) {
        showToast('Folders are still loading');
        return;
    }
    folderBrowser = { source: treeSource, trail: [] };
    renderFolderBrowser();
}

function scopeFolder(node, source) {
    const path = (node && node.path) || (source && source.path) || '';
    setScope({ folder: path, label: folderLabel(node, source) });
    nav.setTab('photos');
}

function renderFolderBrowser() {
    if (!folderBrowser) {
        render();
        return;
    }
    showingCollection = false;
    const { source, trail } = folderBrowser;
    const current = trail[trail.length - 1] || null;
    const children = folderChildren(current, source);
    const title = folderLabel(current, source);
    const count = (current || source).total_count;
    let html = '<div class="ml-head">'
        + '<button class="ml-back" id="ml-folder-back">Back</button>'
        + `<h3>${esc(title)}</h3><button class="ml-more" id="ml-folder-close" aria-label="Close">${icon('x')}</button></div>`
        + `<button class="m-lib-row" id="ml-folder-scope-current"><span class="g">${icon('folder-open')}</span>`
        + `<span class="body">Use ${esc(title)}<span class="sub num">${fmtInt(count)} photos</span></span></button>`
        + '<div class="ms-sec ml-folder-list" style="padding-left:0;padding-right:0"><h3>Folders</h3>';
    if (children.length) {
        children.forEach((child, i) => {
            const hasChildren = (child.children || []).length > 0;
            const childCount = child.total_count != null ? child.total_count : child.count;
            html += '<div class="ml-folder-row">'
                + `<button class="m-lib-row ml-folder-scope" data-folder-i="${i}"><span class="g">${icon(hasChildren ? 'folder-tree' : 'folder')}</span>`
                + `<span class="body">${esc(folderLabel(child, source))}<span class="sub num">${fmtInt(childCount)} photos</span></span></button>`
                + (hasChildren
                    ? `<button class="ml-folder-drill" data-folder-i="${i}" aria-label="Open ${esc(folderLabel(child, source))}">${icon('chevron-right')}</button>`
                    : '')
                + '</div>';
        });
    } else {
        html += '<div class="ms-empty">No child folders here.</div>';
    }
    html += '</div>';
    root.innerHTML = html;

    root.querySelector('#ml-folder-close')?.addEventListener('click', () => {
        folderBrowser = null;
        render();
    });
    root.querySelector('#ml-folder-back')?.addEventListener('click', () => {
        if (folderBrowser.trail.length) {
            folderBrowser.trail.pop();
            renderFolderBrowser();
        } else {
            folderBrowser = null;
            render();
        }
    });
    root.querySelector('#ml-folder-scope-current')?.addEventListener('click', () => scopeFolder(current, source));
    for (const row of root.querySelectorAll('.ml-folder-scope[data-folder-i]')) {
        row.addEventListener('click', () => {
            const child = children[Number(row.dataset.folderI)];
            if (child) scopeFolder(child, source);
        });
    }
    for (const row of root.querySelectorAll('.ml-folder-drill[data-folder-i]')) {
        row.addEventListener('click', () => {
            const child = children[Number(row.dataset.folderI)];
            if (!child) return;
            folderBrowser.trail.push(child);
            renderFolderBrowser();
        });
    }
}

/* ---------- main render ---------- */
function render() {
    if (folderBrowser) {
        renderFolderBrowser();
        return;
    }
    // Keep collection drill-in across tab re-entry / background refreshes.
    // Only closeCollectionView (Back) clears showingCollection.
    if (showingCollection) return;
    if (loadError) {
        root.innerHTML = '<div class="ms-empty">Couldn\'t load Library.</div>'
            + '<button class="sheet-btn" id="ml-retry-load" type="button">Try again</button>';
        root.querySelector('#ml-retry-load')?.addEventListener('click', () => {
            loadError = false;
            render();
            loadAll().then(loadSuggestionsOnce, loadSuggestionsOnce);
        });
        return;
    }
    const colls = collections || [];
    let html = renderSuggestions();
    html += '<div class="ml-head"><h3>Collections</h3></div><div class="m-lib-grid">';
    colls.forEach((c, i) => {
        const pct = sortedPctCache.get(c.id);
        const smart = Boolean(c.smart);
        const pctLabel = !smart && pct != null ? ` · ${pct}% sorted` : '';
        const countLabel = `${fmtInt(c.image_count)} photos`;
        const typeLabel = smart ? `Smart · ${countLabel}` : countLabel;
        const cover = c.cover_image_id
            ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="">`
            : icon(smart ? 'sparkles' : 'folder');
        html += `<button class="m-lib-card${smart ? ' smart' : ''}" data-ci="${i}">`
            + `<div class="m-lib-cover">${cover}${smart ? `<span class="smart-mark">${icon('sparkles')}</span>` : ''}</div>`
            + `<div class="m-lib-cap"><b>${smart ? icon('sparkles') : ''}${esc(c.name)}</b>`
            + `<span class="num">${typeLabel}${pctLabel}</span></div></button>`;
    });
    html += `<button class="m-lib-card m-lib-new" id="ml-new">${icon('plus', 'icon icon-lg')}New collection</button></div>`;

    if (canInstall()) {
        html += '<div class="ms-sec" style="padding-left:0;padding-right:0">'
            + `<button class="m-lib-row ml-install" id="ml-install"><span class="g">${icon('download')}</span>`
            + '<span class="body">Install Azimuth Photo<span class="sub">Add it to your home screen as an app</span></span></button></div>';
    }

    html += '<div class="ms-sec" style="padding-left:0;padding-right:0"><h3>Quick access</h3>'
        + `<button class="m-lib-row" data-q="picked"><span class="g">${icon('heart')}</span><span class="body">Favorites<span class="sub">Favorited photos</span></span><span class="n num">${fmtInt(counts && counts.picked)}</span></button>`
        + `<button class="m-lib-row" data-q="rejected"><span class="g">${icon('x')}</span><span class="body">Rejected</span><span class="n num">${fmtInt(counts && counts.rejected)}</span></button>`
        + `<button class="m-lib-row" id="ml-offline"><span class="g">${icon('download')}</span><span class="body">Available offline<span class="sub">Saved on this phone</span></span><span class="n num">${fmtInt(offlineSummary().count)}</span></button>`
        + `<button class="m-lib-row" id="ml-backup"><span class="g">${icon('upload')}</span><span class="body">Backup<span class="sub">Uploads and phone storage</span></span></button>`
        + `<button class="m-lib-row" id="ml-shared"><span class="g">${icon('share-2')}</span><span class="body">Shared with me</span></button>`
        + `<button class="m-lib-row" data-q="all"><span class="g">${icon('house')}</span><span class="body">All photos</span><span class="n num">${fmtInt(counts && counts.total)}</span></button></div>`;

    html += '<div class="ms-sec" style="padding-left:0;padding-right:0"><h3>Sources</h3>';
    const sources = (catalog && catalog.sources) || null;
    if (sources && sources.length) {
        sources.forEach((s, i) => {
            const online = Number(s.online) === 1;
            const photoCount = s.active_image_count != null ? s.active_image_count : s.image_count;
            html += `<button class="m-lib-row" data-source-i="${i}">`
                + `<span class="g">${icon('hard-drive')}</span>`
                + `<span class="body">${esc(s.display_name || s.path)}`
                + `<span class="sub num">${fmtInt(photoCount)} photos${online ? '' : ' · offline'}</span></span>`
                + `<span class="nr-dot ${online ? 'on' : 'off'}"></span></button>`;
        });
    } else if (sources) {
        html += '<div class="ms-empty">No sources yet — add one on the desktop app.</div>';
    } else {
        html += '<div class="skel-row"></div>';
    }
    html += '</div>';

    html += renderWorkRows();

    root.innerHTML = html;

    bindSuggestions();
    root.querySelector('#ml-install')?.addEventListener('click', async () => {
        try {
            const accepted = await promptInstall();
            showToast(accepted ? 'Installed — check your home screen' : 'Install cancelled');
        } catch {
            showToast("Couldn't start the install — try again");
        }
        render();
    });
    bindWorkRows();
    for (const el of root.querySelectorAll('.m-lib-card[data-ci]')) {
        el.addEventListener('click', () => {
            const coll = colls[Number(el.dataset.ci)];
            if (coll) openCollectionView(coll);
        });
    }
    root.querySelector('#ml-new').addEventListener('click', newCollectionSheet);
    root.querySelector('#ml-shared')?.addEventListener('click', () => {
        showingCollection = true;
        renderSharedView(root, () => {
            showingCollection = false;
            render();
        });
    });
    root.querySelector('#ml-offline')?.addEventListener('click', openOfflineStatusSheet);
    root.querySelector('#ml-backup')?.addEventListener('click', () => {
        showingCollection = true;
        stopWorkPolling();
        renderBackupView(root, () => {
            showingCollection = false;
            render();
            startWorkPolling();
        });
    });
    for (const el of root.querySelectorAll('.m-lib-row[data-q]')) {
        el.addEventListener('click', () => {
            const q = el.dataset.q;
            if (q === 'all') clearScope();
            else setScope({ flag: q, label: q === 'picked' ? 'Favorites' : 'Rejected' });
            nav.setTab('photos');
        });
    }
    for (const el of root.querySelectorAll('.m-lib-row[data-source-i]')) {
        el.addEventListener('click', () => {
            const source = sources[Number(el.dataset.sourceI)];
            if (source) openFolderBrowser(source);
        });
    }
}

/* ---------- background work glass box ---------- */
const pct = (value) => (value == null ? null : Math.max(0, Math.min(100, Number(value) || 0)));

function workerStateIsActive(value) {
    const state = normalizeWorkerState(value);
    return Boolean(state) && !INACTIVE_WORKER_STATES.has(state);
}

function workRows() {
    const ai = workStatus && workStatus.ai;
    const cache = workStatus && workStatus.cache;
    const peopleStatus = workStatus && workStatus.people;
    const cachePregen = (cache && cache.pregen) || {};
    const preview = cachePregen.preview || {};
    const peopleWorker = (peopleStatus && peopleStatus.worker) || {};
    return [
        {
            key: 'ai',
            glyph: icon('search'),
            title: 'Visual search index',
            progress: pct(ai && ai.progress_pct),
            detail: ai
                ? `${fmtInt(ai.embedded)} / ${fmtInt(ai.total_images)} indexed`
                : 'Checking status…',
            paused: Boolean(ai && ai.embedding_manual_pause),
            running: workerStateIsActive(ai && ai.worker_state),
        },
        {
            key: 'cache',
            glyph: icon('image'),
            title: 'Preview cache',
            progress: pct(preview.progress_pct),
            detail: cache
                ? `${fmtInt(preview.count)} / ${fmtInt(preview.total)} previews`
                : 'Checking status…',
            paused: Boolean(cachePregen.manual_pause),
            running: !cachePregen.manual_pause && workerStateIsActive(cachePregen.state),
        },
        {
            key: 'people',
            glyph: icon('users'),
            title: 'People scan',
            progress: null,
            detail: peopleStatus
                ? `${fmtInt((peopleStatus.counts || {}).people)} people · ${fmtInt((peopleStatus.counts || {}).pending_cached_images)} pending`
                : 'Checking status…',
            paused: Boolean(peopleStatus && !peopleStatus.active),
            running: workerStateIsActive(peopleWorker.state),
        },
    ];
}

function renderWorkRows() {
    const rows = workRows();
    return '<div class="ms-sec ml-work" style="padding-left:0;padding-right:0"><h3>Background work</h3>'
        + rows.map((row) => {
            const status = row.running ? 'Running' : row.paused ? 'Paused' : 'Idle';
            const meter = row.progress == null
                ? ''
                : `<span class="ml-work-meter"><span style="width:${row.progress}%"></span></span>`;
            return `<button class="m-lib-row ml-work-row" data-work="${row.key}">`
                + `<span class="g">${row.glyph}</span><span class="body">${row.title}`
                + `<span class="sub num">${row.detail}</span></span>${meter}`
                + `<span class="n">${status}</span></button>`;
        }).join('')
        + '</div>';
}

function bindWorkRows() {
    for (const row of root.querySelectorAll('.ml-work-row[data-work]')) {
        row.addEventListener('click', () => openWorkSheet(row.dataset.work));
    }
}

function workPayload(kind) {
    const rows = workRows();
    return rows.find((row) => row.key === kind) || null;
}

function detailsForWork(kind) {
    if (kind === 'ai') {
        const ai = workStatus && workStatus.ai;
        return {
            title: 'Visual search index',
            rows: [
                ['State', ai && ai.worker_state],
                ['Progress', ai ? `${fmtInt(ai.embedded)} / ${fmtInt(ai.total_images)} (${ai.progress_pct || 0}%)` : '—'],
                ['Remaining', ai && ai.remaining],
                ['Rate', ai && ai.recent_images_per_min ? `${Math.round(ai.recent_images_per_min)} / min` : '—'],
                ['Message', ai && ai.worker_message],
            ],
        };
    }
    if (kind === 'cache') {
        const pregen = (workStatus && workStatus.cache && workStatus.cache.pregen) || {};
        const preview = pregen.preview || {};
        return {
            title: 'Preview cache',
            rows: [
                ['State', pregen.state],
                ['Progress', `${fmtInt(preview.count)} / ${fmtInt(preview.total)} (${preview.progress_pct || 0}%)`],
                ['Remaining', preview.remaining],
                ['Message', pregen.message],
            ],
        };
    }
    const peopleStatus = workStatus && workStatus.people;
    const counts = (peopleStatus && peopleStatus.counts) || {};
    const worker = (peopleStatus && peopleStatus.worker) || {};
    return {
        title: 'People scan',
        rows: [
            ['State', worker.state || (peopleStatus && peopleStatus.active ? 'active' : 'paused')],
            ['People', counts.people],
            ['Faces', counts.detected_faces],
            ['Pending', counts.pending_cached_images],
            ['Model', peopleStatus && peopleStatus.model_id],
        ],
    };
}

function openWorkSheet(kind) {
    const row = workPayload(kind);
    const details = detailsForWork(kind);
    const action = row && row.paused ? 'resume' : 'pause';
    const sheet = openSheet(
        `<h3>${esc(details.title)}</h3>`
        + '<div class="sheet-meta">'
        + details.rows.map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v == null || v === '' ? '—' : v)}</b></div>`).join('')
        + '</div>'
        + `<button class="sheet-btn" id="ml-work-action" data-mutating>${action === 'resume' ? 'Resume' : 'Pause'}</button>`
    );
    sheet.querySelector('#ml-work-action').addEventListener('click', async () => {
        dismissSheetThen(async () => {
            const result = await setBackgroundWork(kind, action);
            showToast(result && result.ok ? `${details.title} ${action === 'resume' ? 'resumed' : 'paused'}` : writeFailureMessage());
            await loadWorkStatus();
        });
    });
}

async function loadWorkStatus() {
    if (workLoading) return;
    workLoading = true;
    try {
        const [ai, cache, peopleStatus] = await Promise.all([
            getAiStatus(),
            getCacheStatus(),
            getPeopleStatus(),
        ]);
        workStatus = { ai, cache, people: peopleStatus };
    } catch {
        workStatus = null;
    } finally {
        workLoading = false;
        if (!showingCollection) render();
    }
}

function startWorkPolling() {
    clearInterval(workPollTimer);
    loadWorkStatus();
    workPollTimer = setInterval(loadWorkStatus, 10000);
}

function stopWorkPolling() {
    clearInterval(workPollTimer);
    workPollTimer = null;
}

/* ---------- suggestions ---------- */
function getDismissedSuggestions() {
    try {
        const values = JSON.parse(localStorage.getItem(DISMISSED_SUGGESTIONS_KEY) || '[]');
        return Array.isArray(values) ? values.filter((v) => typeof v === 'string') : [];
    } catch {
        return [];
    }
}

function setDismissedSuggestions(values) {
    localStorage.setItem(DISMISSED_SUGGESTIONS_KEY, JSON.stringify(values.slice(-100)));
}

function dismissSuggestionFingerprint(fingerprint) {
    const values = getDismissedSuggestions().filter((value) => value !== fingerprint);
    values.push(fingerprint);
    setDismissedSuggestions(values);
}

function restoreSuggestionFingerprint(fingerprint) {
    setDismissedSuggestions(getDismissedSuggestions().filter((value) => value !== fingerprint));
}

function visibleSuggestions() {
    const dismissed = new Set(getDismissedSuggestions());
    return (suggestions || []).filter((s) => !dismissed.has(suggestionFingerprint(s)));
}

function renderSuggestions() {
    if (suggestionsLoading && suggestions == null) {
        return '<div class="ml-suggest-compact skel"></div>';
    }

    const visible = visibleSuggestions();
    if (!visible.length) return '';

    return `<button class="m-lib-row ml-suggest-compact" id="ml-suggest-review" type="button">`
        + `<span class="g">${icon('sparkles')}</span>`
        + `<span class="body">${fmtInt(visible.length)} suggested<span class="sub">Review</span></span>`
        + `<span class="n num">${fmtInt(visible.length)}</span></button>`;
}

async function loadSuggestionsOnce() {
    if (suggestionsLoaded || suggestionsLoading) return;
    suggestionsLoading = true;
    if (!showingCollection) render();
    try {
        const data = await fetchJson('/api/collections/suggestions', { defaultValue: null });
        suggestions = data.suggestions || [];
    } catch (error) {
        console.error('collection suggestions failed', error);
        suggestions = [];
    }
    suggestionsLoaded = true;
    suggestionsLoading = false;
    if (!showingCollection) render();
}

function setToastActionLabel(label) {
    const button = document.getElementById('m-toast-undo');
    if (button) button.textContent = label;
}

function showActionToast(message, label, action) {
    let resetTimer = null;
    setToastActionLabel(label);
    showToast(message, {
        undo: () => {
            clearTimeout(resetTimer);
            setToastActionLabel('Undo');
            action();
        },
    });
    resetTimer = setTimeout(() => setToastActionLabel('Undo'), TOAST_ACTION_RESET_MS);
}

function bindSuggestions() {
    root.querySelector('#ml-suggest-review')?.addEventListener('click', () => openSuggestionReviewSheet(0));
}

function suggestionPreviewStrip(suggestion) {
    const ids = (suggestion.image_ids || []).slice(0, 30);
    if (!ids.length) return '<div class="ml-suggest-strip empty"></div>';
    return '<div class="ml-suggest-strip">' + ids.map((id) => (
        `<img loading="lazy" decoding="async" src="${esc(thumbUrl('sm', id))}" alt="">`
    )).join('') + '</div>';
}

function openSuggestionReviewSheet(index = activeSuggestionIndex) {
    const visible = visibleSuggestions();
    if (!visible.length) {
        dismissSheetThen(render);
        return;
    }
    activeSuggestionIndex = Math.max(0, Math.min(visible.length - 1, Number(index) || 0));
    const suggestion = visible[activeSuggestionIndex];
    const count = Number(suggestion.count) || 0;
    const sheet = openSheet(
        `<h3>${esc(suggestion.title)}</h3>`
        + `<div class="ml-suggest-sheet-reason">${esc(suggestion.reason || 'Suggested')}<span>${esc(suggestion.subtitle || `${fmtInt(count)} photos`)}</span></div>`
        + suggestionPreviewStrip(suggestion)
        + '<button class="sheet-btn" id="ml-suggest-create" data-mutating>Create collection</button>'
        + `<button class="sheet-row" id="ml-suggest-dismiss"><span class="g">${icon('x')}</span>Dismiss</button>`
        + `<button class="sheet-row" id="ml-suggest-next"><span class="g">${icon('arrow-right')}</span>Next</button>`
    );
    sheet.querySelector('#ml-suggest-create').addEventListener('click', async () => {
        await createSuggestion(suggestion);
        openSuggestionReviewSheet(activeSuggestionIndex);
    });
    sheet.querySelector('#ml-suggest-dismiss').addEventListener('click', () => {
        dismissSuggestion(suggestion);
        openSuggestionReviewSheet(activeSuggestionIndex);
    });
    sheet.querySelector('#ml-suggest-next').addEventListener('click', () => {
        const nextVisible = visibleSuggestions();
        if (!nextVisible.length) return;
        openSuggestionReviewSheet((activeSuggestionIndex + 1) % nextVisible.length);
    });
}

async function createSuggestion(suggestion) {
    const fingerprint = suggestionFingerprint(suggestion);
    dismissSuggestionFingerprint(fingerprint);
    render();

    const result = await createCollection(
        suggestion.title,
        suggestion.image_ids || [],
        suggestion.subtitle || ''
    );
    if (!(result && result.ok)) {
        restoreSuggestionFingerprint(fingerprint);
        render();
        showToast(writeFailureMessage());
        return;
    }

    const created = result.collection || null;
    collections = null;
    counts = null;
    try {
        await loadAll();
    } catch (error) {
        console.error('collection refresh failed', error);
    }
    showActionToast('Collection created', 'View', () => {
        if (created) openCollectionView(created);
    });
}

function dismissSuggestion(suggestion) {
    const fingerprint = suggestionFingerprint(suggestion);
    dismissSuggestionFingerprint(fingerprint);
    render();
    showActionToast('Dismissed', 'Undo', () => {
        restoreSuggestionFingerprint(fingerprint);
        render();
    });
}

/* ---------- data ---------- */
async function loadAll() {
    try {
        const [countsData, collData, catalogData, folderData] = await Promise.all([
            getCounts(new URLSearchParams()),   // archive-wide quick-access counts
            listCollections(),
            getCatalog(),
            getFoldersTree(),
        ]);
        counts = countsData;
        collections = (collData && collData.collections) || [];
        catalog = catalogData;
        folderTree = folderData || { sources: [] };
        loadError = false;
        render();
        computeSortedPcts();
    } catch {
        counts = null;
        collections = [];
        catalog = null;
        folderTree = { sources: [] };
        loadError = true;
        render();
    }
}

async function computeSortedPcts() {
    // Real sorted %: fetch each collection's members once and count
    // how many have enough ranking signal (comparisons + propagated).
    for (const coll of collections || []) {
        if (sortedPctCache.has(coll.id) || !coll.image_count) continue;
        let data = null;
        try {
            data = await getCollection(coll.id, 500);
        } catch {
            continue;
        }
        const images = (data && data.collection && data.collection.images) || [];
        if (!images.length) continue;
        let done = 0;
        for (const img of images) {
            const signals = (Number(img.comparisons) || 0) + (Number(img.propagated_updates) || 0);
            if (signals >= SORT_QUALITY_MIN_SIGNALS) done += 1;
        }
        sortedPctCache.set(coll.id, Math.round((100 * done) / images.length));
    }
    render();
}

/* ---------- new collection ---------- */
function newCollectionSheet() {
    const sheet = openSheet(
        '<h3>New collection</h3>'
        + '<input class="sheet-input" id="ml-new-name" type="text" placeholder="Collection name" autocomplete="off">'
        + '<button class="sheet-btn" id="ml-new-create" data-mutating>Create</button>'
    );
    sheet.querySelector('#ml-new-create').addEventListener('click', async () => {
        const name = sheet.querySelector('#ml-new-name').value.trim();
        if (!name) return;
        dismissSheetThen(async () => {
            const result = await createCollection(name, []);
            if (result && result.ok) {
                showToast(`Created “${name}”`);
                collections = null;
                loadAll();
            } else {
                showToast(writeFailureMessage());
            }
        });
    });
    sheet.querySelector('#ml-new-name').focus();
}

/* ---------- collection drill-in ---------- */
function openCollectionView(coll) {
    if (coll.smart) {
        setScope({
            ...scopePatchFromSmartQuery(coll.query || {}),
            smartName: coll.name || 'Smart collection',
            smartCollectionId: String(coll.id),
            smartQuery: coll.query || {},
        });
        applySmartScopeSort(coll.query && coll.query.sort);
        nav.setTab('photos');
        return;
    }
    setScope({
        collectionId: String(coll.id),
        label: coll.name || 'Collection',
    });
    nav.setTab('photos');
}

export function popCollectionView() {
    return false;
}

/** Rename / share / delete for the collection currently open as a timeline scope. */
export function openCollectionActionsSheet(coll) {
    const sheet = openSheet(
        `<h3>${esc(coll.name)}</h3>`
        + '<input class="sheet-input" id="ml-rename-name" type="text" autocomplete="off">'
        + '<button class="sheet-btn" id="ml-rename-save" data-mutating>Save name</button>'
        + `<button class="sheet-row" id="ml-share"><span class="g">${icon('share-2')}</span>Share link</button>`
        + `<button class="sheet-row" id="ml-delete" data-mutating><span class="g">${icon('trash-2')}</span>Delete collection</button>`
        + '<div class="sheet-confirm" id="ml-delete-confirm" hidden>Delete? <button data-yes="1">Yes</button><button data-no="1">No</button></div>'
    );
    const input = sheet.querySelector('#ml-rename-name');
    input.value = coll.name || '';
    sheet.querySelector('#ml-rename-save').addEventListener('click', () => {
        const next = input.value.trim();
        if (!next || next === coll.name) return;
        dismissSheetThen(async () => {
            const result = await renameCollection(coll.id, next);
            if (result && result.ok) {
                showToast(`Renamed to \u201c${next}\u201d`);
                collections = null;
                if (coll.smart) patchScope({ smartName: next });
                else {
                    setScope({
                        collectionId: String(coll.id),
                        collectionSmart: false,
                        label: next,
                    });
                }
                document.dispatchEvent(new CustomEvent('collections-changed'));
            } else {
                showToast(writeFailureMessage());
            }
        });
    });
    sheet.querySelector('#ml-share')?.addEventListener('click', () => openCollectionShareSheet(coll));
    const deleteButton = sheet.querySelector('#ml-delete');
    const confirm = sheet.querySelector('#ml-delete-confirm');
    deleteButton.addEventListener('click', () => {
        deleteButton.hidden = true;
        confirm.hidden = false;
        confirm.querySelector('[data-yes]')?.focus();
    });
    confirm.querySelector('[data-no]')?.addEventListener('click', () => {
        confirm.hidden = true;
        deleteButton.hidden = false;
    });
    confirm.querySelector('[data-yes]')?.addEventListener('click', () => {
        dismissSheetThen(async () => {
            const result = await deleteCollection(coll.id);
            if (result && result.ok) {
                showToast('Collection deleted \u2014 photos stay in your library');
                collections = null;
                clearScope();
                nav.setTab('library');
                document.dispatchEvent(new CustomEvent('collections-changed'));
            } else {
                showToast(writeFailureMessage());
            }
        });
    });
}

export function initLibrary() {
    on('installable', () => {
        if (!showingCollection && built) render();
    });
    root = document.getElementById('m-library');
    on('flags', () => {
        counts = null;   // flag writes change picked/rejected counts
    });
    on('offline-availability', () => {
        if (built && !showingCollection) render();
    });
    document.addEventListener('collections-changed', () => {
        collections = null;
        if (built && !showingCollection) loadAll();
    });
    on('tab', (tab) => {
        if (tab === 'library') startWorkPolling();
        else {
            stopWorkPolling();
            stopBackupView();
        }
    });
}

export function showLibrary() {
    startWorkPolling();
    if (!built) {
        built = true;
        render();
        loadAll().then(loadSuggestionsOnce, loadSuggestionsOnce);
    } else if (counts == null) {
        loadAll().then(loadSuggestionsOnce, loadSuggestionsOnce);
    } else {
        render();
        loadSuggestionsOnce();
    }
}
