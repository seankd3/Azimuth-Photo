import {
    addToCollection, createCollection, getCatalog, getCollection,
    getCounts, getTrash,
    deleteCollection, listCollections,
    removeFromCollection, renameCollection,
    thumbUrl, updateCollection,
    createSavedView, deleteSavedView, listSavedViews,
} from './api.js';
import {
    byId, emit, folderActive, navigateToScope, on, patchPrefs, scope, scopeActive, scopeParams, scopePatchFromSmartQuery, selection, selectionChanged, setActiveLens,
    setLeftCollapsed, setScope, smartQueryActive, smartQueryFromScope, smartQueryName, smartQuerySummary, sortBase, viewState,
} from './state.js';
import { applyFlags, selectedIds, setCollectionPicker } from './selection.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { confirmAction } from './trash.js';
import { exportScope, openExportMenu, savedOriginalsExportSize } from './export_menu.js';
import { pollJob } from './jobs.js';
import { initFoldersPanel } from './folders.js';
import {
    rememberSources, applyExcludeSources,
} from './quiet_sources.js';
import { icon } from '../icons.js';
import { esc, formatCount as fmt } from '../lib.js';
import {
    initSuggestions, loadSuggestionsOnce, openSuggestionsReview, suggestionsAreLoading, visibleSuggestions,
} from './suggestions.js';

let collections = [];
let catalog = null;
let libraryCounts = null;
let trashTotal = null;
let collectionsLoading = true;
let collectionsLoadError = false;
let drawerOpen = false;
let rightDrawerVisible = false;
let collectionMenu = null;
let collectionMenuReturn = null;
let chromeRefreshTimer = 0;
let libraryCountsRetryTimer = 0;
let editingSmartCollection = null;
let savedViews = [];

const narrowPanel = () => window.matchMedia('(max-width: 880px)').matches;
const emptyState = (glyph, copy, action = '') => (
    `<div class="chrome-empty"><span class="chrome-empty-glyph">${icon(glyph)}</span><span>${esc(copy)}</span>${action}</div>`
);
const skeletonRows = (count = 3) => Array.from({ length: count }, () => '<div class="chrome-skel nav-row skel"></div>').join('');

function renderCollections() {
    const host = document.getElementById('collection-list');
    if (collectionsLoading) {
        host.innerHTML = skeletonRows(3);
        return;
    }
    if (collectionsLoadError) {
        host.innerHTML = emptyState('folder-plus', "Couldn't load collections.", '<button type="button" data-retry-collections>Retry</button>');
        host.querySelector('[data-retry-collections]')?.addEventListener('click', loadCollections);
        return;
    }
    if (!collections.length) {
        host.innerHTML = emptyState('folder-plus', 'No collections yet.', '<button type="button" data-new-collection>Create one</button>');
        host.querySelector('[data-new-collection]')?.addEventListener('click', requestNewCollection);
        return;
    }
    host.innerHTML = collections.map((c) => {
        const smart = Boolean(c.smart);
        const queryTitle = smart ? smartQuerySummary(c.query || {}, { fallback: 'Smart collection' }) : c.name;
        const title = smart ? `${c.name} · ${queryTitle}` : c.name;
        return `<div class="nav-row coll-row ${smart ? 'smart' : ''} ${String(scope.collectionId || '') === String(c.id) ? 'active' : ''}" data-coll-id="${c.id}" data-coll-name="${esc(c.name)}" data-coll-smart="${smart ? '1' : ''}">`
        + `<button class="coll-main" type="button" title="${esc(title)}">`
        + `<span class="coll-cover">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" loading="lazy" decoding="async" alt="">` : icon(smart ? 'sparkles' : 'folder')}${smart && c.cover_image_id ? `<span class="coll-smart-badge">${icon('sparkles')}</span>` : ''}</span>`
        + `<span class="nr-label" title="${esc(c.name)}">${esc(c.name)}</span><span class="nr-count">${fmt(c.image_count)}</span>`
        + `${smart ? `<span class="coll-live" data-tip="Updates automatically" aria-label="Live">${icon('sparkles')} Live</span>` : ''}`
        + `${c.published ? `<span class="coll-published" data-tip="Published to website" aria-label="Published">${icon('globe')} Published</span>` : ''}</button>`
        + `<button class="coll-menu-btn" type="button" data-tip="Collection actions" aria-label="Collection actions">${icon('ellipsis')}</button></div>`
    }).join('');
    for (const row of host.querySelectorAll('.coll-row')) {
        const mainButton = row.querySelector('.coll-main');
        mainButton?.addEventListener('click', () => {
            navigateToScope({
                collectionId: row.dataset.collId,
                collectionName: row.dataset.collName || 'Collection',
                collectionSmart: row.dataset.collSmart === '1',
            });
            closeLeftDrawer();
        });
        row.addEventListener('dragover', (event) => {
            if (row.dataset.collSmart === '1') {
                event.preventDefault();
                event.dataTransfer.dropEffect = 'none';
                row.classList.add('not-allowed');
                return;
            }
            if (!selectedIds().length) return;
            event.preventDefault();
            row.classList.add('drag-over');
        });
        row.addEventListener('dragleave', () => row.classList.remove('drag-over', 'not-allowed'));
        row.addEventListener('drop', async (event) => {
            event.preventDefault();
            row.classList.remove('drag-over', 'not-allowed');
            if (row.dataset.collSmart === '1') {
                await addImagesToCollection(Number(row.dataset.collId), selectedIds());
                return;
            }
            const ids = selectedIds();
            if (ids.length) await addImagesToCollection(Number(row.dataset.collId), ids);
        });
        const menuButton = row.querySelector('.coll-menu-btn');
        menuButton?.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            openCollectionMenu(row, menuButton);
        });
        menuButton?.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            event.stopPropagation();
            openCollectionMenu(row, menuButton);
        });
    }
}

function ensureCollectionMenu() {
    if (collectionMenu) return collectionMenu;
    collectionMenu = document.createElement('div');
    collectionMenu.id = 'collection-pop-menu';
    collectionMenu.className = 'pop-menu grid-pop-menu';
    collectionMenu.setAttribute('role', 'menu');
    collectionMenu.hidden = true;
    document.body.appendChild(collectionMenu);
    collectionMenu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeCollectionMenu();
        }
    });
    return collectionMenu;
}

function positionCollectionMenu(anchor) {
    const rect = anchor.getBoundingClientRect();
    const menuRect = collectionMenu.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left));
    const top = Math.max(8, Math.min(window.innerHeight - menuRect.height - 8, rect.bottom + 6));
    collectionMenu.style.left = `${left}px`;
    collectionMenu.style.top = `${top}px`;
}

function closeCollectionMenu() {
    if (!collectionMenu || collectionMenu.hidden) return;
    collectionMenu.hidden = true;
    releaseFocus(collectionMenu);
    if (collectionMenuReturn && document.contains(collectionMenuReturn) && collectionMenuReturn.focus) {
        collectionMenuReturn.focus({ preventScroll: true });
    }
}

function collectionById(collectionId) {
    return collections.find((item) => Number(item.id) === Number(collectionId)) || null;
}

function openCollectionMenu(row, anchor) {
    ensureCollectionMenu();
    releaseFocus(collectionMenu);
    collectionMenuReturn = anchor;
    const id = Number(row.dataset.collId);
    const name = row.dataset.collName || 'Collection';
    const coll = collectionById(id);
    const smart = Boolean(coll?.smart);
    collectionMenu.innerHTML = '<div class="pm-group">'
        + (smart ? `<button data-act="edit-query">${icon('sparkles')} Edit query</button>`
            + `<button data-act="materialize">${icon('archive')} Convert to static</button>` : '')
        + `<button data-act="rename">${icon('pencil')} Rename</button>`
        + `<button data-act="delete">${icon('trash-2')} Delete</button></div>`;
    collectionMenu.hidden = false;
    positionCollectionMenu(anchor);
    trapFocus(collectionMenu, collectionMenu.querySelector('button'));
    for (const button of collectionMenu.querySelectorAll('[data-act]')) {
        button.addEventListener('click', () => {
            const action = button.dataset.act;
            closeCollectionMenu();
            if (action === 'edit-query') startSmartQueryEdit(id);
            if (action === 'materialize') startSmartMaterialize(id, name);
            if (action === 'rename') {
                setLeftCollapsed(false);
                requestAnimationFrame(() => startCollectionRename(id));
            }
            if (action === 'delete') startCollectionDelete(id, name);
        });
    }
}

export function openCollectionActions(collection, anchor) {
    if (!collection?.id || !anchor) return;
    const row = {
        dataset: {
            collId: String(collection.id),
            collName: collection.name || 'Collection',
        },
    };
    openCollectionMenu(row, anchor);
}

function startCollectionRename(collectionId) {
    const row = document.querySelector(`.coll-row[data-coll-id="${collectionId}"]`);
    const coll = collectionById(collectionId);
    if (!row || !coll) return;
    row.outerHTML = `<form class="coll-rename-form" data-rename-coll="${collectionId}">`
        + `<input value="${esc(coll.name)}" maxlength="160" autocomplete="off" aria-label="Collection name">`
        + '<button type="submit">Save</button></form>';
    const form = document.querySelector(`.coll-rename-form[data-rename-coll="${collectionId}"]`);
    const input = form.querySelector('input');
    const cancel = () => renderCollections();
    const submit = async () => {
        const name = input.value.trim();
        if (!name || name === coll.name) {
            cancel();
            return;
        }
        const result = await renameCollection(collectionId, name);
        if (result && result.ok) {
            showToast(`Renamed to “${name}”`);
            if (String(scope.collectionId || '') === String(collectionId)) {
                setScope({ collectionId, collectionName: name }, { merge: true });
            }
            await loadCollections();
        } else {
            showToast("Couldn't rename collection");
            renderCollections();
        }
    };
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        submit();
    });
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            cancel();
        }
    });
    input.focus();
    input.select();
}

async function startCollectionDelete(collectionId, name = 'Collection') {
    const row = document.querySelector(`.coll-row[data-coll-id="${collectionId}"]`);
    if (!row) return;
    const ok = await confirmAction({
        title: 'Delete collection',
        message: `Delete “${name}”? Photos stay in the archive, but this collection is removed.`,
        confirmLabel: 'Delete',
    });
    if (!ok) return;
    let offset = 0;
    let snapshot = null;
    const imageIds = [];
    do {
        const detail = await getCollection(collectionId, { limit: 1000, offset });
        snapshot = detail?.collection || snapshot;
        const images = detail?.collection?.images || [];
        imageIds.push(...images.map((image) => Number(image.id)).filter((id) => id > 0));
        offset += images.length;
        if (!snapshot || snapshot.smart || images.length < 1000 || offset >= Number(snapshot.image_count || 0)) break;
    } while (true);
    if (!snapshot) {
        showToast("Couldn't prepare collection deletion");
        return;
    }
    const result = await deleteCollection(collectionId);
    if (result && result.ok) {
        showToast(`Deleted “${name}”`, {
            undo: async () => {
                const restored = await createCollection(snapshot.name || name, imageIds, '', snapshot.smart ? snapshot.query : null);
                if (restored?.ok && restored.collection?.id) {
                    await loadCollections();
                    showToast(`Restored “${snapshot.name || name}”`);
                } else {
                    showToast("Couldn't restore collection");
                }
            },
        });
        if (String(scope.collectionId || '') === String(collectionId)) setScope({});
        await loadCollections();
    } else {
        showToast("Couldn't delete collection");
        renderCollections();
    }
}

function startSmartQueryEdit(collectionId) {
    const coll = collectionById(collectionId);
    if (!coll?.smart) return;
    editingSmartCollection = { id: collectionId, name: coll.name || 'Smart collection' };
    setScope(scopePatchFromSmartQuery(coll.query || {}));
    requestNewCollection({ preferSmart: true });
    closeLeftDrawer();
    showToast('Smart collection loaded. Adjust filters, then update.');
}

function startSmartMaterialize(collectionId, name = 'Collection') {
    const row = document.querySelector(`.coll-row[data-coll-id="${collectionId}"]`);
    if (!row) return;
    row.outerHTML = `<div class="coll-confirm" data-materialize-coll="${collectionId}">`
        + `<b>Convert “${esc(name)}” to static?</b><span>The live query is removed; the current photos stay in a normal collection.</span>`
        + '<div><button class="btn primary" data-yes="1">Convert</button><button class="btn" data-no="1">Cancel</button></div></div>';
    const confirm = document.querySelector(`.coll-confirm[data-materialize-coll="${collectionId}"]`);
    confirm.querySelector('[data-no]')?.addEventListener('click', renderCollections);
    confirm.querySelector('[data-yes]')?.addEventListener('click', async () => {
        const result = await updateCollection(collectionId, { materialize: true });
        if (result && result.ok) {
            showToast(`Converted “${name}” to a regular collection`);
            if (String(scope.collectionId || '') === String(collectionId)) {
                setScope({ collectionId, collectionName: name, collectionSmart: false }, { merge: true });
            }
            await loadCollections();
        } else {
            showToast("Couldn't convert collection");
            renderCollections();
        }
    });
    confirm.querySelector('[data-yes]')?.focus();
}

function renderSuggestions() {
    const host = document.getElementById('suggestions-wrap');
    if (!host) return;
    if (suggestionsAreLoading()) {
        host.innerHTML = '<div class="chrome-skel nav-row skel"></div>';
        return;
    }
    const count = visibleSuggestions().length;
    if (!count) {
        host.innerHTML = emptyState('sparkles', 'No suggestions to review.');
        return;
    }
    host.innerHTML = '<button class="nav-row suggest-row" id="review-suggestions" type="button">'
        + `<span class="nr-glyph">${icon('sparkles')}</span>`
        + '<span class="nr-label" title="Suggested collections">Suggested collections</span>'
        + `<span class="nr-count">${fmt(count)}</span></button>`;
    host.querySelector('#review-suggestions')?.addEventListener('click', () => {
        openSuggestionsReview();
        closeLeftDrawer();
    });
}

function renderLibrary() {
    const rows = [
        ['all', 'house', 'All photos', '', libraryCounts?.total, ''],
        ['picked', 'star', 'Picked', 'picked', libraryCounts?.picked, ''],
        ['rejected', 'x', 'Rejected', 'rejected', libraryCounts?.rejected, ''],
        ['trash', 'trash-2', 'Trash', '', trashTotal, 'Deleted photos'],
        ['recent', 'clock-3', 'Recent', '', null, 'Newest first'],
    ];
    document.getElementById('library-list').innerHTML = rows.map(([id, glyph, label, , count, tip]) => {
        const hasCount = count != null || id !== 'recent';
        return `<button class="nav-row" data-lib="${id}"${tip ? ` data-tip="${esc(tip)}"` : ''}>`
            + `<span class="nr-glyph">${icon(glyph)}</span><span class="nr-label" title="${esc(label)}">${esc(label)}</span>`
            + (hasCount ? `<span class="nr-count">${count == null ? '…' : fmt(count)}</span>` : '')
            + '</button>';
    }).join('');
    for (const row of document.querySelectorAll('[data-lib]')) {
        row.addEventListener('click', () => {
            const key = row.dataset.lib;
            if (key === 'all') navigateToScope({});
            if (key === 'picked') navigateToScope({ flag: 'picked' });
            if (key === 'rejected') navigateToScope({ flag: 'rejected' });
            if (key === 'trash') setActiveLens('trash');
            if (key === 'recent') navigateToScope({ sort: 'date_taken' });
            closeLeftDrawer();
        });
    }
}

async function loadLibraryCounts() {
    const params = new URLSearchParams();
    applyExcludeSources(params);
    window.clearTimeout(libraryCountsRetryTimer);
    try {
        const [data, trash] = await Promise.all([getCounts(params), getTrash({ limit: 1, offset: 0 })]);
        libraryCounts = data || {};
        trashTotal = trash && trash.total != null ? Number(trash.total) || 0 : null;
        renderLibrary();
    } catch {
        // Keep any loaded counts; retry while the nav still shows placeholder ellipses.
        if (libraryCounts == null) libraryCountsRetryTimer = window.setTimeout(loadLibraryCounts, 15000);
    }
}

async function loadCollections() {
    collectionsLoading = true;
    collectionsLoadError = false;
    renderCollections();
    try {
        const data = await listCollections();
        collections = (data && data.collections) || [];
        emit('collections:changed', { collections });
    } catch {
        collections = [];
        collectionsLoadError = true;
    } finally {
        collectionsLoading = false;
        renderCollections();
    }
}

function savedViewSnapshot() {
    return JSON.stringify({
        scope: { ...scope, folder: [...scope.folder], similarIds: [...scope.similarIds] },
        layout: { density: viewState.prefs.density, collapseStacks: viewState.prefs.collapseStacks },
    });
}

function restoreSavedView(view) {
    try {
        const saved = JSON.parse(view.query);
        navigateToScope(saved.scope && typeof saved.scope === 'object' ? saved.scope : saved);
        if (saved.layout) patchPrefs(saved.layout);
        closeLeftDrawer();
        showToast(`Opened “${view.name}”`);
    } catch {
        showToast(`“${view.name}” is no longer a valid view`);
    }
}

function renderSavedViews() {
    const host = document.getElementById('saved-view-list');
    host.innerHTML = savedViews.length ? savedViews.map((view) =>
        `<div class="nav-row saved-view-row" data-saved-view="${view.id}">`
        + `<button type="button"><span class="nr-glyph">${icon('bookmark')}</span><span class="nr-label">${esc(view.name)}</span></button>`
        + '<button class="saved-view-delete" type="button" aria-label="Delete saved view">×</button></div>'
    ).join('') : emptyState('bookmark', 'No saved views yet.');
    for (const row of host.querySelectorAll('[data-saved-view]')) {
        const view = savedViews.find((item) => Number(item.id) === Number(row.dataset.savedView));
        row.querySelector('button')?.addEventListener('click', () => restoreSavedView(view));
        row.querySelector('.saved-view-delete')?.addEventListener('click', async () => {
            const confirmed = await confirmAction({
                title: 'Delete saved view?',
                message: `“${view.name}” will no longer appear in your Library.`,
                confirmLabel: 'Delete view',
            });
            if (!confirmed) return;
            if (await deleteSavedView(view.id)) {
                savedViews = savedViews.filter((item) => item.id !== view.id);
                renderSavedViews();
                showToast('Saved view deleted');
            } else {
                showToast('Couldn’t delete saved view');
            }
        });
    }
}

async function loadSavedViews() {
    const data = await listSavedViews();
    savedViews = data?.views || [];
    renderSavedViews();
}

export function requestSaveCurrentView() {
    const form = document.getElementById('saved-view-form');
    form.hidden = false;
    const input = document.getElementById('saved-view-name');
    input.value = '';
    input.focus();
}

async function loadCatalogChrome() {
    try {
        catalog = await getCatalog();
        rememberSources((catalog && catalog.sources) || []);
    } catch {
        catalog = null;
    }
}

function scheduleChromeRefresh() {
    window.clearTimeout(chromeRefreshTimer);
    chromeRefreshTimer = window.setTimeout(() => {
        loadLibraryCounts();
        loadCatalogChrome();
        loadCollections();
    }, 300);
}

function patchCollectionCount(collectionId, delta) {
    const collection = collections.find((item) => Number(item.id) === Number(collectionId));
    if (!collection) return () => {};
    const previous = Number(collection.image_count) || 0;
    collection.image_count = Math.max(0, previous + delta);
    renderCollections();
    return () => {
        if (!collections.includes(collection)) return;
        collection.image_count = previous;
        renderCollections();
    };
}

async function undoCollectionAdd(collectionId, imageIds, successMessage) {
    const result = await removeFromCollection(collectionId, imageIds);
    if (!result?.ok) {
        showToast("Couldn't undo collection change");
        await loadCollections();
        return false;
    }
    await loadCollections();
    showToast(successMessage);
    return true;
}

async function addImagesToCollection(collectionId, imageIds) {
    const coll = collections.find((c) => Number(c.id) === Number(collectionId));
    if (coll?.smart) {
        showToast('Smart collections update from their filters');
        return false;
    }
    const restoreCount = patchCollectionCount(collectionId, imageIds.length);
    const result = await addToCollection(collectionId, imageIds);
    if (result && result.ok) {
        showToast(`Added ${imageIds.length} to “${coll ? coll.name : 'collection'}”`, {
            undo: () => undoCollectionAdd(collectionId, imageIds, 'Removed from collection'),
        });
        await loadCollections();
        return true;
    } else {
        restoreCount();
        showToast("Couldn't add to collection");
        return false;
    }
}

export async function removeImagesFromCollection(collectionId, imageIds, name = '') {
    const ids = [...new Set(imageIds.map(Number))].filter((id) => id > 0);
    if (!collectionId || !ids.length) return false;
    const restoreCount = patchCollectionCount(collectionId, -ids.length);
    const result = await removeFromCollection(collectionId, ids);
    if (!result?.ok) {
        restoreCount();
        showToast("Couldn't remove photos from this collection");
        return false;
    }
    const label = name || collections.find((collection) => Number(collection.id) === Number(collectionId))?.name || 'collection';
    await loadCollections();
    emit('scope', scope);
    showToast(`Removed ${ids.length} photo${ids.length === 1 ? '' : 's'} from “${label}”`, {
        undo: async () => {
            const restored = await addToCollection(collectionId, ids);
            if (restored?.ok) {
                await loadCollections();
                emit('scope', scope);
                showToast(`Restored to “${label}”`);
            } else showToast("Couldn't restore photos to this collection");
        },
    });
    return true;
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
    picker.innerHTML = `<div class="picker-card"><div class="picker-head"><b>Add to collection</b><button data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
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
            event.stopPropagation();
            close();
        }
    });
    const setBusy = (busy) => {
        for (const control of picker.querySelectorAll('input, button')) control.disabled = busy;
    };
    let creating = false;
    picker.querySelector('form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const name = picker.querySelector('input').value.trim();
        if (!name || creating) return;
        creating = true;
        setBusy(true);
        try {
            const result = await createCollection(name, ids);
            if (result && result.ok) {
                const coll = result.collection || {};
                await loadCollections();
                close();
                if (onDone) onDone();
                showToast(`Created “${name}”`, {
                    undo: coll.id ? () => undoCollectionAdd(coll.id, ids, 'Collection removed') : null,
                });
            } else showToast("Couldn't create collection");
        } finally {
            creating = false;
            if (picker.isConnected) setBusy(false);
        }
    });
    const list = picker.querySelector('.picker-list');
    const regularCollections = collections.filter((c) => !c.smart);
    list.innerHTML = regularCollections.length ? regularCollections.map((c) => (
        `<button data-coll-id="${c.id}" title="${esc(c.name)}"><span title="${esc(c.name)}">${esc(c.name)}</span><span class="num">${fmt(c.image_count)}</span></button>`
    )).join('') : '<div class="muted">No regular collections yet.</div>';
    for (const row of list.querySelectorAll('[data-coll-id]')) {
        row.addEventListener('click', async () => {
            setBusy(true);
            const added = await addImagesToCollection(Number(row.dataset.collId), ids);
            if (added) {
                close();
                if (onDone) onDone();
            } else if (picker.isConnected) setBusy(false);
        });
    }
    picker.querySelector('input').focus();
    trapFocus(picker, picker.querySelector('input'));
}

export async function exportCurrentScope(format = 'csv', size = '') {
    const params = scopeParams({ format });
    const exportSize = format === 'zip' ? (size || savedOriginalsExportSize()) : size;
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        params.set('sort', 'elo');
        params.set('limit', String(viewState.bestOfLimit));
    }
    let count = viewState.bestOf && viewState.bestOfLimit != null ? viewState.bestOfLimit : viewState.visibleImages;
    if (scope.similarIds.length) {
        const ids = scope.similarIds.map(Number).filter((id) => id > 0);
        count = ids.length;
        params.set('ids', ids.join(','));
    }
    exportScope({ format, size: exportSize, count, query: params });
}

export function openScopeExportMenu(anchor) {
    openExportMenu(anchor, ({ format, size }) => exportCurrentScope(format, size));
}

export function openLeftDrawer() {
    if (!narrowPanel() || drawerOpen) return;
    closeRightDrawer();
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
    if (!rightDrawerVisible) scrim.hidden = true;
    releaseFocus(panel);
}

export function leftDrawerOpen() {
    return drawerOpen;
}

// ≤880px slide-over mirror of the left drawer for #panel-right, so the
// Info/Metadata/Keywords/Ranking panes stay reachable at narrow widths.
export function openRightDrawer() {
    const shell = document.getElementById('shell');
    if (!narrowPanel() || rightDrawerVisible || shell.classList.contains('right-hidden')) return;
    closeLeftDrawer();
    const panel = document.getElementById('panel-right');
    const scrim = document.getElementById('panel-scrim');
    rightDrawerVisible = true;
    scrim.hidden = false;
    shell.classList.add('right-drawer-open');
    trapFocus(panel, panel.querySelector('button, input'));
}

export function closeRightDrawer() {
    if (!rightDrawerVisible) return;
    const shell = document.getElementById('shell');
    const panel = document.getElementById('panel-right');
    const scrim = document.getElementById('panel-scrim');
    rightDrawerVisible = false;
    shell.classList.remove('right-drawer-open');
    if (!drawerOpen) scrim.hidden = true;
    releaseFocus(panel);
}

export function rightDrawerOpen() {
    return rightDrawerVisible;
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
    const options = arguments[0] && arguments[0].preferSmart ? arguments[0] : {};
    if (narrowPanel()) openLeftDrawer();
    const form = document.getElementById('new-coll-form');
    form.hidden = false;
    renderNewCollectionForm({ resetName: true, preferSmart: Boolean(options.preferSmart) });
    const input = document.getElementById('new-coll-name');
    input.focus();
    input.select();
}

function cancelSmartCollectionEdit() {
    if (!editingSmartCollection) return;
    const name = editingSmartCollection.name;
    editingSmartCollection = null;
    const form = document.getElementById('new-coll-form');
    const input = document.getElementById('new-coll-name');
    if (input) input.value = '';
    if (form) form.hidden = true;
    renderNewCollectionForm();
    showToast(`Stopped updating “${name}”`);
}

export function requestSaveSmartCollection() {
    if (!smartQueryActive()) {
        showToast('Add a search or filter first');
        return;
    }
    requestNewCollection({ preferSmart: true });
}

function renderNewCollectionForm({ resetName = false, preferSmart = false } = {}) {
    const form = document.getElementById('new-coll-form');
    if (!form || form.hidden) return;
    const input = document.getElementById('new-coll-name');
    const smartButton = document.getElementById('new-coll-smart');
    const createButton = document.getElementById('new-coll-create');
    const cancelButton = document.getElementById('new-coll-cancel-edit');
    const editBanner = document.getElementById('new-coll-edit-banner');
    const query = smartQueryFromScope();
    const canSaveSmart = smartQueryActive(query);
    if (editBanner) {
        editBanner.hidden = !editingSmartCollection;
        editBanner.querySelector('span').textContent = editingSmartCollection
            ? `Updating ${editingSmartCollection.name} · ${smartQuerySummary(query)}`
            : '';
    }
    if (cancelButton) cancelButton.hidden = !editingSmartCollection;
    if (smartButton) {
        smartButton.hidden = !canSaveSmart && !editingSmartCollection;
        smartButton.disabled = !canSaveSmart;
        smartButton.textContent = editingSmartCollection ? 'Update smart collection' : 'Save as smart collection';
        smartButton.title = canSaveSmart ? smartQuerySummary(query) : '';
    }
    if (createButton) {
        createButton.hidden = Boolean(editingSmartCollection);
        createButton.textContent = canSaveSmart ? 'Create collection' : 'Create';
    }
    if (resetName && canSaveSmart && (preferSmart || !input.value.trim())) {
        input.value = editingSmartCollection?.name || smartQueryName(query);
    } else if (resetName && !canSaveSmart && preferSmart) {
        input.value = '';
    }
}

async function saveSmartCollectionFromForm() {
    const input = document.getElementById('new-coll-name');
    const button = document.getElementById('new-coll-smart');
    const query = smartQueryFromScope();
    if (!smartQueryActive(query)) {
        showToast('Add a search or filter first');
        return;
    }
    const name = input.value.trim() || smartQueryName(query);
    if (button) button.disabled = true;
    let result = null;
    if (editingSmartCollection) {
        result = await updateCollection(editingSmartCollection.id, { name, query });
    } else {
        result = await createCollection(name, [], '', query);
    }
    if (button) button.disabled = false;
    if (result && result.ok) {
        const collection = result.collection || {};
        const collectionId = collection.id || editingSmartCollection?.id;
        showToast(editingSmartCollection ? `Updated “${name}”` : `Saved smart collection “${name}”`);
        editingSmartCollection = null;
        input.value = '';
        document.getElementById('new-coll-form').hidden = true;
        await loadCollections();
        if (collectionId) setScope({ collectionId, collectionName: name, collectionSmart: true });
    } else {
        showToast(editingSmartCollection ? "Couldn't update smart collection" : "Couldn't save smart collection");
    }
}

export function requestRenameCurrentCollection() {
    if (!scope.collectionId) {
        showToast('Open a collection first');
        return;
    }
    if (narrowPanel()) openLeftDrawer();
    startCollectionRename(scope.collectionId);
}

export function requestDeleteCurrentCollection() {
    if (!scope.collectionId) {
        showToast('Open a collection first');
        return;
    }
    if (narrowPanel()) openLeftDrawer();
    startCollectionDelete(scope.collectionId, scope.collectionName || 'Collection');
}

export async function initPanel() {
    initSuggestions({
        refreshCollections: loadCollections,
        notifyChange: renderSuggestions,
    });
    setCollectionPicker(openCollectionPicker);
    document.getElementById('shell').classList.toggle('left-collapsed', viewState.leftCollapsed);
    document.getElementById('collapse-left').addEventListener('click', toggleLeftPanel);
    document.getElementById('panel-scrim').addEventListener('click', () => {
        closeLeftDrawer();
        closeRightDrawer();
    });
    window.matchMedia('(max-width: 880px)').addEventListener('change', (event) => {
        if (!event.matches) {
            closeLeftDrawer();
            closeRightDrawer();
        }
    });
    ensureCollectionMenu();
    document.addEventListener('pointerdown', (event) => {
        if (!collectionMenu || collectionMenu.hidden || collectionMenu.contains(event.target) || event.target.closest('.coll-menu-btn')) return;
        closeCollectionMenu();
    });
    window.addEventListener('resize', closeCollectionMenu);
    document.getElementById('export-view').addEventListener('click', (event) => openScopeExportMenu(event.currentTarget));
    document.getElementById('new-coll-btn').addEventListener('click', requestNewCollection);
    document.getElementById('save-view-btn').addEventListener('click', requestSaveCurrentView);
    document.getElementById('saved-view-cancel').addEventListener('click', () => { document.getElementById('saved-view-form').hidden = true; });
    document.getElementById('saved-view-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const input = document.getElementById('saved-view-name');
        const result = await createSavedView(input.value.trim() || 'Current view', savedViewSnapshot());
        if (!result?.view) return showToast("Couldn't save this view");
        savedViews.unshift(result.view);
        renderSavedViews();
        form.hidden = true;
        showToast(`Saved “${result.view.name}”`);
    });
    document.getElementById('new-coll-smart')?.addEventListener('click', saveSmartCollectionFromForm);
    document.getElementById('new-coll-cancel-edit')?.addEventListener('click', cancelSmartCollectionEdit);
    document.getElementById('new-coll-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        if (editingSmartCollection) {
            await saveSmartCollectionFromForm();
            return;
        }
        const input = document.getElementById('new-coll-name');
        const name = input.value.trim();
        if (!name) return;
        const submit = event.submitter || document.getElementById('new-coll-create');
        if (submit) submit.disabled = true;
        const result = await createCollection(name, []);
        if (result && result.ok) {
            input.value = '';
            event.currentTarget.hidden = true;
            showToast(`Created “${name}”`);
            await loadCollections();
        } else {
            showToast("Couldn't create collection");
            if (submit) submit.disabled = false;
        }
    });
    on('panel', (collapsed) => {
        document.getElementById('shell').classList.toggle('left-collapsed', collapsed);
        closeLeftDrawer();
    });
    on('panel:toggle', toggleLeftPanel);
    on('collection:new', requestNewCollection);
    on('flags', scheduleChromeRefresh);
    on('trash:changed', scheduleChromeRefresh);
    on('import:changed', scheduleChromeRefresh);
    on('collections:refresh', scheduleChromeRefresh);
    on('quiet:changed', loadLibraryCounts);
    on('scope', () => {
        renderNewCollectionForm();
        for (const row of document.querySelectorAll('[data-folder-source-path]')) {
            row.classList.toggle('active', folderActive(row.dataset.folderSourcePath));
        }
        for (const row of document.querySelectorAll('[data-coll-id]')) row.classList.toggle('active', row.dataset.collId === String(scope.collectionId || ''));
        for (const row of document.querySelectorAll('[data-lib]')) {
            const key = row.dataset.lib;
            const recentActive = key === 'recent' && sortBase() === 'date_taken' && !scopeActive();
            const active = (key === 'all' && !scopeActive() && sortBase() !== 'date_taken')
                || (key === 'picked' && scope.flag === 'picked')
                || (key === 'rejected' && scope.flag === 'rejected')
                || recentActive;
            row.classList.toggle('active', active);
        }
    });
    on('lens', (lens) => {
        document.querySelector('[data-lib="trash"]')?.classList.toggle('active', lens === 'trash');
    });
    renderLibrary();
    renderCollections();
    loadLibraryCounts();
    await loadCollections();
    await loadSavedViews();
    await loadCatalogChrome();
    await initFoldersPanel({ closeDrawer: closeLeftDrawer });
    setTimeout(loadSuggestionsOnce, 0);
}
