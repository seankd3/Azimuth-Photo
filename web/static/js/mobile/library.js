// Library tab: real collections (create/add/browse), Picked/Rejected
// rows with real counts from /api/counts, and source status from
// /api/catalog. Collection rows show the real sorted % computed from
// each member's ranking signals.

import {
    createCollection, fetchJson, getCatalog, getCollection, getCounts,
    listCollections, thumbUrl,
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

    root.innerHTML = html;

    bindSuggestions();
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
        `<div class="ml-head"><h3>${esc(coll.name)}</h3><button class="ml-back" id="ml-back">‹ Library</button></div>`
        + `<div class="ml-coll-grid">${'<div class="skel-cell"></div>'.repeat(9)}</div>`;
    root.querySelector('#ml-back').addEventListener('click', render);

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
        `<div class="ml-head"><h3>${esc(coll.name)}</h3><button class="ml-back" id="ml-back">‹ Library</button></div>`
        + `<div class="ms-empty">${fmtInt(images.length)} photos${pct == null ? '' : ` · ${pct}% sorted`}</div>`
        + `<div class="ml-coll-grid">${grid || '<div class="ms-empty" style="grid-column:span 3">Empty collection.</div>'}</div>`;
    root.querySelector('#ml-back').addEventListener('click', () => {
        render();
        loadAll();
    });
    root.querySelector('.ml-coll-grid').addEventListener('click', (e) => {
        const cell = e.target.closest('.mcell[data-i]');
        if (cell) openViewer(images, Number(cell.dataset.i));
    });
}

export function initLibrary() {
    root = document.getElementById('m-library');
    on('flags', () => {
        counts = null;   // flag writes change picked/rejected counts
    });
}

export function showLibrary() {
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
