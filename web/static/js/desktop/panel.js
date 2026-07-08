import {
    addToCollection, createCollection, getCatalog, getCollectionSuggestions,
    listCollections, removeFromCollection, thumbUrl,
} from './api.js';
import { loadCollectionImageIds } from './scope_data.js';
import {
    on, scope, scopeParams, setLeftCollapsed, setScope, viewState,
} from './state.js';
import { selectedIds, setCollectionPicker } from './selection.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';

const DISMISSED_KEY = 'pa_d_dismissed_suggestions';
let collections = [];
let catalog = null;
let suggestions = null;
let suggestionsLoading = false;
let drawerOpen = false;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const fingerprint = (s) => `${s.kind || ''}|${s.cover_image_id || ''}|${s.count || 0}`;
const narrowPanel = () => window.matchMedia('(max-width: 880px)').matches;

function dismissed() {
    try {
        const values = JSON.parse(localStorage.getItem(DISMISSED_KEY) || '[]');
        return Array.isArray(values) ? values.filter((v) => typeof v === 'string') : [];
    } catch {
        return [];
    }
}

function setDismissed(values) {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(values.slice(-100)));
}

function dismiss(fp) {
    setDismissed([...dismissed().filter((v) => v !== fp), fp]);
}

function restore(fp) {
    setDismissed(dismissed().filter((v) => v !== fp));
}

function renderCollections() {
    const host = document.getElementById('collection-list');
    if (!collections.length) {
        host.innerHTML = '<div class="muted">No collections yet. Create one, then drag photos onto it.</div>';
        return;
    }
    host.innerHTML = collections.map((c) => (
        `<button class="nav-row coll-row ${String(scope.collectionId || '') === String(c.id) ? 'active' : ''}" data-coll-id="${c.id}" data-coll-name="${esc(c.name)}">`
        + `<span class="coll-cover">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="">` : '⊞'}</span>`
        + `<span class="nr-label">${esc(c.name)}</span><span class="nr-count">${fmt(c.image_count)}</span></button>`
    )).join('');
    for (const row of host.querySelectorAll('.coll-row')) {
        row.addEventListener('click', () => {
            setScope({ collectionId: row.dataset.collId, collectionName: row.dataset.collName || 'Collection' });
            closeLeftDrawer();
        });
        row.addEventListener('dragover', (event) => {
            if (!selectedIds().length) return;
            event.preventDefault();
            row.classList.add('drag-over');
        });
        row.addEventListener('dragleave', () => row.classList.remove('drag-over'));
        row.addEventListener('drop', async (event) => {
            event.preventDefault();
            row.classList.remove('drag-over');
            const ids = selectedIds();
            if (ids.length) await addImagesToCollection(Number(row.dataset.collId), ids);
        });
    }
}

function renderSuggestions() {
    const host = document.getElementById('suggestion-list');
    if (suggestionsLoading && suggestions == null) {
        host.innerHTML = '<div class="suggest-card skel" style="height:132px"></div><div class="suggest-card skel" style="height:132px"></div>';
        return;
    }
    const gone = new Set(dismissed());
    const visible = (suggestions || []).filter((s) => !gone.has(fingerprint(s)));
    host.innerHTML = visible.map((s, index) => (
        `<article class="suggest-card" data-index="${index}">`
        + `<div class="cover">${s.cover_image_id ? `<img src="${esc(thumbUrl('md', s.cover_image_id))}" alt="">` : '◇'}</div>`
        + `<div class="body"><b>${esc(s.title)}</b><span>${esc(s.subtitle || `${fmt(s.count)} photos`)}</span></div>`
        + '<div class="actions"><button data-act="create">Create</button><button data-act="dismiss">×</button></div></article>'
    )).join('');
    for (const card of host.querySelectorAll('.suggest-card[data-index]')) {
        const suggestion = visible[Number(card.dataset.index)];
        card.querySelector('[data-act="create"]').addEventListener('click', () => createSuggestion(suggestion));
        card.querySelector('[data-act="dismiss"]').addEventListener('click', () => dismissSuggestion(suggestion));
    }
}

async function createSuggestion(suggestion) {
    const fp = fingerprint(suggestion);
    dismiss(fp);
    renderSuggestions();
    const result = await createCollection(suggestion.title, suggestion.image_ids || [], suggestion.subtitle || '');
    if (!(result && result.ok)) {
        restore(fp);
        renderSuggestions();
        showToast("Couldn't create collection");
        return;
    }
    await loadCollections();
    showToast('Collection created', { undo: null });
}

function dismissSuggestion(suggestion) {
    const fp = fingerprint(suggestion);
    dismiss(fp);
    renderSuggestions();
    showToast('Dismissed', {
        undo: () => {
            restore(fp);
            renderSuggestions();
        },
    });
}

async function loadSuggestionsOnce() {
    if (suggestions || suggestionsLoading) return;
    suggestionsLoading = true;
    renderSuggestions();
    const data = await getCollectionSuggestions();
    suggestions = (data && data.suggestions) || [];
    suggestionsLoading = false;
    renderSuggestions();
}

function renderLibrary() {
    const rows = [
        ['all', '⌂', 'All Photos', ''],
        ['picked', '★', 'Picked', 'picked'],
        ['rejected', '×', 'Rejected', 'rejected'],
        ['recent', '◷', 'Recent', ''],
    ];
    document.getElementById('library-list').innerHTML = rows.map(([id, glyph, label]) => (
        `<button class="nav-row" data-lib="${id}"><span class="nr-glyph">${glyph}</span><span class="nr-label">${label}</span></button>`
    )).join('');
    for (const row of document.querySelectorAll('[data-lib]')) {
        row.addEventListener('click', () => {
            const key = row.dataset.lib;
            if (key === 'all') setScope({});
            if (key === 'picked') setScope({ flag: 'picked' });
            if (key === 'rejected') setScope({ flag: 'rejected' });
            if (key === 'recent') setScope({ sort: 'date_taken' });
            closeLeftDrawer();
        });
    }
}

function renderSources() {
    const host = document.getElementById('source-list');
    const sources = (catalog && catalog.sources) || [];
    host.innerHTML = sources.length ? sources.map((s) => {
        const online = Number(s.online) === 1;
        const count = s.active_image_count != null ? s.active_image_count : s.image_count;
        return `<button class="nav-row" data-source="${esc(s.path)}">`
            + `<span class="nr-dot ${online ? 'on' : 'off'}"></span><span class="nr-label">${esc(s.display_name || s.path)}</span>`
            + `<span class="nr-count">${fmt(count)}</span>${online ? '' : '<span class="nr-tag">offline</span>'}</button>`;
    }).join('') : '<div class="muted">No sources yet.</div>';
    for (const row of host.querySelectorAll('[data-source]')) {
        row.addEventListener('click', () => {
            setScope({ folder: row.dataset.source });
            closeLeftDrawer();
        });
    }
}

async function loadCollections() {
    const data = await listCollections();
    collections = (data && data.collections) || [];
    renderCollections();
}

async function addImagesToCollection(collectionId, imageIds) {
    const coll = collections.find((c) => Number(c.id) === Number(collectionId));
    const result = await addToCollection(collectionId, imageIds);
    if (result && result.ok) {
        showToast(`Added ${imageIds.length} to “${coll ? coll.name : 'collection'}”`, {
            undo: async () => {
                await removeFromCollection(collectionId, imageIds);
                await loadCollections();
                showToast('Removed from collection');
            },
        });
        await loadCollections();
    } else {
        showToast("Couldn't add to collection");
    }
}

export async function openCollectionPicker(imageIds, { onDone = null } = {}) {
    const ids = [...new Set(imageIds.map(Number))].filter((id) => id > 0);
    if (!ids.length) return;
    let picker = document.getElementById('collection-picker');
    if (!picker) {
        picker = document.createElement('div');
        picker.id = 'collection-picker';
        document.body.appendChild(picker);
    } else {
        releaseFocus(picker);
    }
    picker.innerHTML = '<div class="picker-card"><div class="picker-head"><b>Add to collection</b><button aria-label="Close">×</button></div>'
        + '<form><input type="text" placeholder="New collection name" autocomplete="off"><button>Create & add</button></form>'
        + '<div class="picker-list"></div></div>';
    const close = () => {
        releaseFocus(picker);
        picker.remove();
    };
    picker.querySelector('.picker-head button').addEventListener('click', close);
    picker.addEventListener('click', (event) => {
        if (event.target === picker) close();
    });
    picker.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
        }
    });
    picker.querySelector('form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const name = picker.querySelector('input').value.trim();
        if (!name) return;
        close();
        if (onDone) onDone();
        const result = await createCollection(name, ids);
        if (result && result.ok) {
            const coll = result.collection || {};
            await loadCollections();
            showToast(`Created “${name}”`, {
                undo: async () => coll.id && removeFromCollection(coll.id, ids),
            });
        } else showToast("Couldn't create collection");
    });
    const list = picker.querySelector('.picker-list');
    list.innerHTML = collections.length ? collections.map((c) => (
        `<button data-coll-id="${c.id}"><span>${esc(c.name)}</span><span class="num">${fmt(c.image_count)}</span></button>`
    )).join('') : '<div class="muted">No collections yet.</div>';
    for (const row of list.querySelectorAll('[data-coll-id]')) {
        row.addEventListener('click', async () => {
            close();
            if (onDone) onDone();
            await addImagesToCollection(Number(row.dataset.collId), ids);
        });
    }
    picker.querySelector('input').focus();
    trapFocus(picker, picker.querySelector('input'));
}

export async function exportCurrentScope(format = 'csv') {
    const params = scopeParams({ format });
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        params.set('sort', 'elo');
        params.set('limit', String(viewState.bestOfLimit));
    }
    if (scope.collectionId) {
        const ids = await loadCollectionImageIds(scope.collectionId);
        if (!ids.length) {
            showToast('Collection is empty');
            return;
        }
        params.set('ids', ids.join(','));
    }
    if (scope.similarIds.length) {
        params.set('ids', scope.similarIds.map(Number).filter((id) => id > 0).join(','));
    }
    const link = document.getElementById('download-link');
    link.href = `/api/export?${params.toString()}`;
    link.download = `photoarchive-export.${format}`;
    link.click();
    showToast(`Exporting current view as ${format.toUpperCase()}`);
}

export function openLeftDrawer() {
    if (!narrowPanel() || drawerOpen) return;
    const shell = document.getElementById('shell');
    const panel = document.getElementById('panel-left');
    const scrim = document.getElementById('panel-scrim');
    drawerOpen = true;
    scrim.hidden = false;
    shell.classList.add('drawer-open');
    trapFocus(panel, panel.querySelector('button, input'));
}

export function closeLeftDrawer() {
    if (!drawerOpen) return;
    const shell = document.getElementById('shell');
    const panel = document.getElementById('panel-left');
    const scrim = document.getElementById('panel-scrim');
    drawerOpen = false;
    shell.classList.remove('drawer-open');
    scrim.hidden = true;
    releaseFocus(panel);
}

export function leftDrawerOpen() {
    return drawerOpen;
}

export function toggleLeftPanel() {
    if (narrowPanel()) {
        if (drawerOpen) closeLeftDrawer();
        else openLeftDrawer();
    } else {
        setLeftCollapsed(!viewState.leftCollapsed);
    }
}

export function requestNewCollection() {
    if (narrowPanel()) openLeftDrawer();
    const form = document.getElementById('new-coll-form');
    form.hidden = false;
    document.getElementById('new-coll-name').focus();
}

export async function initPanel() {
    setCollectionPicker(openCollectionPicker);
    document.getElementById('shell').classList.toggle('left-collapsed', viewState.leftCollapsed);
    document.getElementById('collapse-left').addEventListener('click', toggleLeftPanel);
    document.getElementById('panel-scrim').addEventListener('click', closeLeftDrawer);
    window.matchMedia('(max-width: 880px)').addEventListener('change', (event) => {
        if (!event.matches) closeLeftDrawer();
    });
    document.getElementById('export-view').addEventListener('click', () => exportCurrentScope('csv'));
    document.getElementById('new-coll-btn').addEventListener('click', requestNewCollection);
    document.getElementById('new-coll-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = document.getElementById('new-coll-name');
        const name = input.value.trim();
        if (!name) return;
        input.value = '';
        event.currentTarget.hidden = true;
        const result = await createCollection(name, []);
        if (result && result.ok) {
            showToast(`Created “${name}”`);
            await loadCollections();
        } else showToast("Couldn't create collection");
    });
    on('panel', (collapsed) => {
        document.getElementById('shell').classList.toggle('left-collapsed', collapsed);
        closeLeftDrawer();
    });
    on('panel:toggle', toggleLeftPanel);
    on('collection:new', requestNewCollection);
    on('scope', () => {
        for (const row of document.querySelectorAll('[data-source]')) row.classList.toggle('active', row.dataset.source === scope.folder);
        for (const row of document.querySelectorAll('[data-coll-id]')) row.classList.toggle('active', row.dataset.collId === String(scope.collectionId || ''));
        for (const row of document.querySelectorAll('[data-lib]')) {
            const key = row.dataset.lib;
            const active = (!scope.collectionId && !scope.import_batch && !scope.similarIds.length && key === 'all' && !scope.flag && !scope.q && !scope.people && !scope.folder && !scope.date_taken && !scope.file_type && !scope.camera && !scope.lens && !scope.orientation && !scope.compared && !scope.min_stars)
                || (key === 'picked' && scope.flag === 'picked')
                || (key === 'rejected' && scope.flag === 'rejected');
            row.classList.toggle('active', active);
        }
    });
    renderLibrary();
    await loadCollections();
    catalog = await getCatalog();
    renderSources();
    setTimeout(loadSuggestionsOnce, 0);
}
