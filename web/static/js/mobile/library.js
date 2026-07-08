// Library tab: real collections (create/add/browse), Picked/Rejected
// rows with real counts from /api/counts, and source status from
// /api/catalog. Collection rows show the real sorted % computed from
// each member's ranking signals.

import {
    createCollection, fetchJson, getAiStatus, getCacheStatus, getCatalog, getCollection, getCounts,
    deleteCollection, getPeopleStatus, listCollections, renameCollection, setBackgroundWork, thumbUrl,
} from './api.js';
import { nav, on, rememberImages, setScope, clearScope } from './state.js';
import { openSheet, closeSheet } from './selection.js';
import { showToast } from './toast.js';
import { openViewer } from './viewer.js';

// Matches RANK_QUALITY_MIN_SIGNALS in data/repositories/rankings.py.
const SORT_QUALITY_MIN_SIGNALS = 3;
const DISMISSED_SUGGESTIONS_KEY = 'pa_m_dismissed_suggestions';
const TOAST_ACTION_RESET_MS = 6200;

let root = null;
let built = false;
let counts = null;
let collections = null;
let catalog = null;
let suggestions = null;
let suggestionsLoading = false;
let suggestionsLoaded = false;
let showingCollection = false;
let workStatus = null;
let workPollTimer = null;
let workLoading = false;
const sortedPctCache = new Map();

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmtInt = (n) => (n == null ? '…' : Number(n).toLocaleString('en-US'));
const suggestionFingerprint = (s) => `${s.kind || ''}|${s.cover_image_id || ''}|${s.count || 0}`;
const suggestionGlyph = (kind) => (kind === 'event' ? '◷' : '◇');

/* ---------- main render ---------- */
function render() {
    showingCollection = false;
    const colls = collections || [];
    let html = renderSuggestions();
    html += '<div class="ml-head"><h3>Collections</h3></div><div class="m-lib-grid">';
    colls.forEach((c, i) => {
        const pct = sortedPctCache.get(c.id);
        const pctLabel = pct == null ? '' : ` · ${pct}% sorted`;
        html += `<button class="m-lib-card" data-ci="${i}">`
            + `<div class="m-lib-cover">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="">` : '⊞'}</div>`
            + `<div class="m-lib-cap"><b>${esc(c.name)}</b>`
            + `<span class="num">${fmtInt(c.image_count)} photos${pctLabel}</span></div></button>`;
    });
    html += '<button class="m-lib-card m-lib-new" id="ml-new"><span class="g">+</span>New collection</button></div>';

    html += '<div class="ms-sec" style="padding-left:0;padding-right:0"><h3>Quick access</h3>'
        + `<button class="m-lib-row" data-q="picked"><span class="g">★</span><span class="body">Picked</span><span class="n num">${fmtInt(counts && counts.picked)}</span></button>`
        + `<button class="m-lib-row" data-q="rejected"><span class="g">✕</span><span class="body">Rejected</span><span class="n num">${fmtInt(counts && counts.rejected)}</span></button>`
        + `<button class="m-lib-row" data-q="all"><span class="g">◷</span><span class="body">All photos</span><span class="n num">${fmtInt(counts && counts.total)}</span></button></div>`;

    html += '<div class="ms-sec" style="padding-left:0;padding-right:0"><h3>Sources</h3>';
    const sources = (catalog && catalog.sources) || null;
    if (sources && sources.length) {
        for (const s of sources) {
            const online = Number(s.online) === 1;
            const photoCount = s.active_image_count != null ? s.active_image_count : s.image_count;
            html += '<div class="m-lib-row">'
                + '<span class="g">▤</span>'
                + `<span class="body">${esc(s.display_name || s.path)}`
                + `<span class="sub num">${fmtInt(photoCount)} photos${online ? '' : ' · offline'}</span></span>`
                + `<span class="nr-dot ${online ? 'on' : 'off'}"></span></div>`;
        }
    } else if (sources) {
        html += '<div class="ms-empty">No sources yet — add one on the desktop app.</div>';
    } else {
        html += '<div class="skel-row"></div>';
    }
    html += '</div>';

    html += renderWorkRows();

    root.innerHTML = html;

    bindSuggestions();
    bindWorkRows();
    for (const el of root.querySelectorAll('.m-lib-card[data-ci]')) {
        el.addEventListener('click', () => {
            const coll = colls[Number(el.dataset.ci)];
            if (coll) openCollectionView(coll);
        });
    }
    root.querySelector('#ml-new').addEventListener('click', newCollectionSheet);
    for (const el of root.querySelectorAll('.m-lib-row[data-q]')) {
        el.addEventListener('click', () => {
            const q = el.dataset.q;
            if (q === 'all') clearScope();
            else setScope({ flag: q, label: q === 'picked' ? 'Picked' : 'Rejected' });
            nav.setTab('photos');
        });
    }
}

/* ---------- background work glass box ---------- */
const pct = (value) => (value == null ? null : Math.max(0, Math.min(100, Number(value) || 0)));

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
            glyph: '⌕',
            title: 'AI embeddings',
            progress: pct(ai && ai.progress_pct),
            detail: ai
                ? `${fmtInt(ai.embedded)} / ${fmtInt(ai.total_images)} indexed`
                : 'Checking status…',
            paused: Boolean(ai && ai.embedding_manual_pause),
            running: Boolean(ai && ['embedding', 'loading_model'].includes(ai.worker_state)),
        },
        {
            key: 'cache',
            glyph: '▧',
            title: 'Cache pregen',
            progress: pct(preview.progress_pct),
            detail: cache
                ? `${fmtInt(preview.count)} / ${fmtInt(preview.total)} previews`
                : 'Checking status…',
            paused: Boolean(cachePregen.manual_pause),
            running: cachePregen.state === 'running',
        },
        {
            key: 'people',
            glyph: '◉',
            title: 'People scan',
            progress: null,
            detail: peopleStatus
                ? `${fmtInt((peopleStatus.counts || {}).people)} people · ${fmtInt((peopleStatus.counts || {}).pending_cached_images)} pending`
                : 'Checking status…',
            paused: Boolean(peopleStatus && !peopleStatus.active),
            running: Boolean(peopleStatus && peopleStatus.active && peopleWorker.state !== 'idle'),
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
            title: 'AI embeddings',
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
            title: 'Cache pregen',
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
        + `<button class="sheet-btn" id="ml-work-action">${action === 'resume' ? 'Resume' : 'Pause'}</button>`
    );
    sheet.querySelector('#ml-work-action').addEventListener('click', async () => {
        closeSheet();
        const result = await setBackgroundWork(kind, action);
        showToast(result && result.ok ? `${details.title} ${action === 'resume' ? 'resumed' : 'paused'}` : "Couldn't update background work");
        await loadWorkStatus();
    });
}

async function loadWorkStatus() {
    if (workLoading) return;
    workLoading = true;
    const [ai, cache, peopleStatus] = await Promise.all([
        getAiStatus(),
        getCacheStatus(),
        getPeopleStatus(),
    ]);
    workStatus = { ai, cache, people: peopleStatus };
    workLoading = false;
    if (!showingCollection) render();
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
        return '<div class="ml-suggest-row ml-suggest-loading">'
            + '<div class="ml-suggest-card skel"></div>'
            + '<div class="ml-suggest-card skel"></div></div>';
    }

    const visible = visibleSuggestions();
    if (!visible.length) return '';

    let html = '<div class="ml-head ml-suggest-head"><h3>Suggested</h3></div><div class="ml-suggest-row">';
    visible.forEach((s, i) => {
        const count = Number(s.count) || 0;
        html += `<article class="ml-suggest-card" data-si="${i}">`
            + `<div class="ml-suggest-cover">${s.cover_image_id ? `<img loading="lazy" decoding="async" src="${esc(thumbUrl('md', s.cover_image_id))}" alt="">` : suggestionGlyph(s.kind)}</div>`
            + '<div class="ml-suggest-body">'
            + `<b><span class="g">${suggestionGlyph(s.kind)}</span>${esc(s.title)}</b>`
            + `<span>${esc(s.subtitle || `${fmtInt(count)} photos`)}</span></div>`
            + '<div class="ml-suggest-actions">'
            + '<button class="ml-suggest-create" type="button">Create</button>'
            + '<button class="ml-suggest-dismiss" type="button" aria-label="Dismiss suggestion">×</button></div>'
            + '</article>';
    });
    html += '</div>';
    return html;
}

async function loadSuggestionsOnce() {
    if (suggestionsLoaded || suggestionsLoading) return;
    suggestionsLoading = true;
    if (!showingCollection) render();
    const data = await fetchJson('/api/collections/suggestions', { defaultValue: null });
    if (data == null) {
        console.error('collection suggestions failed');
        suggestions = [];
    } else {
        suggestions = data.suggestions || [];
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
    const visible = visibleSuggestions();
    for (const card of root.querySelectorAll('.ml-suggest-card[data-si]')) {
        const suggestion = visible[Number(card.dataset.si)];
        if (!suggestion) continue;
        card.querySelector('.ml-suggest-create').addEventListener('click', () => createSuggestion(suggestion));
        card.querySelector('.ml-suggest-dismiss').addEventListener('click', () => dismissSuggestion(suggestion));
    }
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
        showToast("Couldn't create collection");
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
    const [countsData, collData, catalogData] = await Promise.all([
        getCounts(new URLSearchParams()),   // archive-wide quick-access counts
        listCollections(),
        getCatalog(),
    ]);
    counts = countsData;
    collections = (collData && collData.collections) || [];
    catalog = catalogData;
    render();
    computeSortedPcts();
}

async function computeSortedPcts() {
    // Real sorted %: fetch each collection's members once and count
    // how many have enough ranking signal (comparisons + propagated).
    for (const coll of collections || []) {
        if (sortedPctCache.has(coll.id) || !coll.image_count) continue;
        const data = await getCollection(coll.id, 500);
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
        + '<button class="sheet-btn" id="ml-new-create">Create</button>'
    );
    sheet.querySelector('#ml-new-create').addEventListener('click', async () => {
        const name = sheet.querySelector('#ml-new-name').value.trim();
        if (!name) return;
        closeSheet();
        const result = await createCollection(name, []);
        if (result && result.ok) {
            showToast(`Created “${name}”`);
            collections = null;
            loadAll();
        } else {
            showToast("Couldn't create collection");
        }
    });
    sheet.querySelector('#ml-new-name').focus();
}

/* ---------- collection drill-in ---------- */
async function openCollectionView(coll) {
    showingCollection = true;
    root.innerHTML =
        `<div class="ml-head"><button class="ml-back" id="ml-back">‹ Library</button><h3>${esc(coll.name)}</h3><button class="ml-more" id="ml-more" aria-label="Collection actions">⋯</button></div>`
        + `<div class="ml-coll-grid">${'<div class="skel-cell"></div>'.repeat(9)}</div>`;
    root.querySelector('#ml-back').addEventListener('click', render);
    root.querySelector('#ml-more').addEventListener('click', () => openCollectionActionsSheet(coll));

    const data = await getCollection(coll.id, 1000);
    const images = (data && data.collection && data.collection.images) || [];
    rememberImages(images);
    const pct = sortedPctCache.get(coll.id);
    const grid = images.map((img, i) =>
        `<figure class="mcell" data-i="${i}" role="button" aria-label="${esc(img.filename || img.id)}">`
        + `<img loading="lazy" decoding="async" src="${esc(thumbUrl('sm', img.id))}" onload="this.classList.add('ld')" alt="">`
        + '</figure>'
    ).join('');
    root.innerHTML =
        `<div class="ml-head"><button class="ml-back" id="ml-back">‹ Library</button><h3>${esc(coll.name)}</h3><button class="ml-more" id="ml-more" aria-label="Collection actions">⋯</button></div>`
        + `<div class="ms-empty">${fmtInt(images.length)} photos${pct == null ? '' : ` · ${pct}% sorted`}</div>`
        + `<div class="ml-coll-grid">${grid || '<div class="ms-empty" style="grid-column:span 3">Empty collection.</div>'}</div>`;
    root.querySelector('#ml-back').addEventListener('click', () => {
        render();
        loadAll();
    });
    root.querySelector('#ml-more').addEventListener('click', () => openCollectionActionsSheet(coll));
    root.querySelector('.ml-coll-grid').addEventListener('click', (e) => {
        const cell = e.target.closest('.mcell[data-i]');
        if (cell) openViewer(images, Number(cell.dataset.i));
    });
}

function openCollectionActionsSheet(coll) {
    const sheet = openSheet(
        `<h3>${esc(coll.name)}</h3>`
        + '<input class="sheet-input" id="ml-rename-name" type="text" autocomplete="off">'
        + '<button class="sheet-btn" id="ml-rename-save">Save name</button>'
        + '<button class="sheet-row" id="ml-delete"><span class="g">✕</span>Delete collection</button>'
        + '<div class="sheet-confirm" id="ml-delete-confirm" hidden>Delete? <button data-yes="1">Yes</button><button data-no="1">No</button></div>'
    );
    const input = sheet.querySelector('#ml-rename-name');
    input.value = coll.name || '';
    sheet.querySelector('#ml-rename-save').addEventListener('click', async () => {
        const next = input.value.trim();
        if (!next || next === coll.name) return;
        closeSheet();
        const result = await renameCollection(coll.id, next);
        if (result && result.ok) {
            showToast(`Renamed to “${next}”`);
            collections = null;
            coll.name = next;
            await loadAll();
            openCollectionView(coll);
        } else {
            showToast("Couldn't rename collection");
        }
    });
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
    confirm.querySelector('[data-yes]')?.addEventListener('click', async () => {
        closeSheet();
        const result = await deleteCollection(coll.id);
        if (result && result.ok) {
            showToast(`Deleted “${coll.name}”`);
            collections = null;
            counts = null;
            showingCollection = false;
            await loadAll();
        } else {
            showToast("Couldn't delete collection");
        }
    });
    input.focus();
    input.select();
}

export function initLibrary() {
    root = document.getElementById('m-library');
    on('flags', () => {
        counts = null;   // flag writes change picked/rejected counts
    });
    on('tab', (tab) => {
        if (tab === 'library') startWorkPolling();
        else stopWorkPolling();
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
