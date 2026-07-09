import {
    addToCollection, createCollection, getCatalog, getCollection,
    getCounts, getTrash,
    createCollectionShare, deleteCollection, getCollectionShare, getCollectionShareFavorites, listCollections,
    removeFromCollection, renameCollection, revokeCollectionShare, thumbUrl,
} from './api.js';
import { loadCollectionImageIds } from './scope_data.js';
import {
    byId, on, scope, scopeActive, scopeParams, selection, selectionChanged, setActiveLens, setLeftCollapsed, setScope, viewState,
} from './state.js';
import { applyFlags, selectedIds, setCollectionPicker } from './selection.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { downloadExport, openExportMenu } from './export_menu.js';
import { initFoldersPanel } from './folders.js';
import { icon } from '../icons.js';
import {
    initSuggestions, loadSuggestionsOnce, openSuggestionsReview, suggestionsAreLoading, visibleSuggestions,
} from './suggestions.js';

let collections = [];
let catalog = null;
let libraryCounts = null;
let trashTotal = null;
let drawerOpen = false;
let collectionMenu = null;
let collectionMenuReturn = null;
let shareOverlay = null;
let shareOverlayToken = 0;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const narrowPanel = () => window.matchMedia('(max-width: 880px)').matches;

function renderCollections() {
    const host = document.getElementById('collection-list');
    if (!collections.length) {
        host.innerHTML = '<div class="muted">No collections yet. Create one, then drag photos onto it.</div>';
        return;
    }
    host.innerHTML = collections.map((c) => (
        `<div class="nav-row coll-row ${String(scope.collectionId || '') === String(c.id) ? 'active' : ''}" data-coll-id="${c.id}" data-coll-name="${esc(c.name)}" role="button" tabindex="0">`
        + `<span class="coll-cover">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="">` : icon('folder')}</span>`
        + `<span class="nr-label">${esc(c.name)}</span><span class="nr-count">${fmt(c.image_count)}</span>`
        + `<button class="coll-menu-btn" type="button" data-tip="Collection actions" aria-label="Collection actions">${icon('ellipsis')}</button></div>`
    )).join('');
    for (const row of host.querySelectorAll('.coll-row')) {
        row.addEventListener('click', () => {
            setScope({ collectionId: row.dataset.collId, collectionName: row.dataset.collName || 'Collection' });
            closeLeftDrawer();
        });
        row.addEventListener('keydown', (event) => {
            if (event.target.closest('.coll-menu-btn') || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
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
    collectionMenu.innerHTML = '<div class="pm-group">'
        + `<button data-act="share">${icon('share-2')} Share…</button>`
        + `<button data-act="rename">${icon('pencil')} Rename</button>`
        + `<button data-act="delete">${icon('trash-2')} Delete</button></div>`;
    collectionMenu.hidden = false;
    positionCollectionMenu(anchor);
    trapFocus(collectionMenu, collectionMenu.querySelector('button'));
    for (const button of collectionMenu.querySelectorAll('[data-act]')) {
        button.addEventListener('click', () => {
            const action = button.dataset.act;
            closeCollectionMenu();
            if (action === 'share') openShareOverlay(id, name);
            if (action === 'rename') startCollectionRename(id);
            if (action === 'delete') startCollectionDelete(id, name);
        });
    }
}

function formatShareDate(value) {
    if (value == null) return 'Never';
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString([], {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
    });
}

function formatRelativeShareDate(value) {
    if (value == null) return 'never';
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) return 'unknown';
    const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000);
    const ranges = [
        ['year', 31536000],
        ['month', 2592000],
        ['week', 604800],
        ['day', 86400],
        ['hour', 3600],
        ['minute', 60],
    ];
    const formatter = new Intl.RelativeTimeFormat([], { numeric: 'auto' });
    for (const [unit, seconds] of ranges) {
        if (Math.abs(diffSeconds) >= seconds) {
            return formatter.format(Math.round(diffSeconds / seconds), unit);
        }
    }
    return formatter.format(diffSeconds, 'second');
}

function shareStatsLine(share) {
    const count = Number(share?.view_count || 0);
    if (!count) return 'Never opened';
    return `Opened ${fmt(count)} ${count === 1 ? 'time' : 'times'} · last ${formatRelativeShareDate(share.last_viewed_at)}`;
}

function clientPickIds(pickData) {
    return ((pickData && pickData.favorites) || [])
        .map((row) => Number(row && row.image_id))
        .filter((id) => id > 0);
}

function sharePicksRow(pickData) {
    const ids = clientPickIds(pickData);
    const count = Number(pickData?.count ?? ids.length);
    return '<div class="share-picks">'
        + `<span>Client picks: <b>${fmt(count)}</b></span>`
        + '<div>'
        + `<button id="share-view-picks" type="button" ${ids.length ? '' : 'disabled'}>View picks</button>`
        + `<button id="share-apply-picks" type="button" ${ids.length ? '' : 'disabled'}>Apply as picks</button>`
        + '</div></div>';
}

async function rememberCollectionImages(collectionId) {
    const data = await getCollection(collectionId, { limit: 1000 });
    const images = (data && data.collection && data.collection.images) || [];
    for (const image of images) {
        if (image && image.id != null) byId.set(Number(image.id), image);
    }
}

function selectClientPicks(ids) {
    if (!ids.length) {
        showToast('No client picks yet');
        return;
    }
    const before = new Set(selection);
    selection.clear();
    ids.forEach((id) => selection.add(id));
    const changed = [...new Set([...before, ...selection])];
    selectionChanged(changed);
    closeShareOverlay();
    showToast(`${fmt(ids.length)} client picks selected`);
}

async function applyClientPicks(collectionId, ids) {
    if (!ids.length) {
        showToast('No client picks yet');
        return;
    }
    await rememberCollectionImages(collectionId);
    await applyFlags(ids, 'picked');
}

function sharePasswordControls(share) {
    const canSave = Boolean(share);
    const isProtected = Boolean(share?.protected);
    return '<div class="share-password-row">'
        + '<div class="share-password-head"><span>Password</span>'
        + (isProtected ? '<b class="share-badge">Protected</b>' : '')
        + '</div>'
        + '<div class="share-link-row">'
        + `<input id="share-password" type="password" autocomplete="new-password" placeholder="${isProtected ? 'Protected' : 'No password'}">`
        + (canSave ? `<button id="share-password-save" type="button">${isProtected ? 'Change' : 'Set'}</button>` : '')
        + '</div>'
        + (isProtected ? '<button class="share-remove-password" id="share-password-clear" type="button">Remove password</button>' : '')
        + '</div>';
}

function shareExpiryOptions() {
    return '<label class="share-expiry">Expires <select id="share-expiry">'
        + '<option value="">Never</option>'
        + '<option value="7">7 days</option>'
        + '<option value="30">30 days</option>'
        + '</select></label>';
}

function ensureShareOverlay() {
    if (shareOverlay) return shareOverlay;
    shareOverlay = document.createElement('div');
    shareOverlay.id = 'share-overlay';
    shareOverlay.className = 'modal-scrim';
    shareOverlay.hidden = true;
    document.body.appendChild(shareOverlay);
    shareOverlay.addEventListener('click', (event) => {
        if (event.target === shareOverlay) closeShareOverlay();
    });
    shareOverlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeShareOverlay();
        }
    });
    return shareOverlay;
}

function shareOverlayIsCurrent(token) {
    return Boolean(shareOverlay && !shareOverlay.hidden && token === shareOverlayToken);
}

function closeShareOverlay() {
    if (!shareOverlay || shareOverlay.hidden) return;
    shareOverlayToken += 1;
    releaseFocus(shareOverlay);
    shareOverlay.hidden = true;
}

async function copyShareUrl(url) {
    try {
        await navigator.clipboard.writeText(url);
        showToast('Link copied');
    } catch {
        showToast("Couldn't copy link");
    }
}

async function renderShareOverlay(collectionId, name, share = null, token = shareOverlayToken) {
    ensureShareOverlay();
    if (!shareOverlayIsCurrent(token)) return;
    const pickData = share ? await getCollectionShareFavorites(collectionId) : null;
    if (!shareOverlayIsCurrent(token)) return;
    const body = share
        ? '<div class="share-link-row"><input id="share-url" readonly value="' + esc(share.url || '') + '"><button id="share-copy" type="button">Copy</button></div>'
            + '<div class="share-meta">'
            + `<div><span>Created</span><b>${esc(formatShareDate(share.created_at))}</b></div>`
            + `<div><span>Expires</span><b>${esc(formatShareDate(share.expires_at))}</b></div></div>`
            + `<div class="share-stats">${esc(shareStatsLine(share))}</div>`
            + sharePicksRow(pickData)
            + sharePasswordControls(share)
            + '<div class="share-actions"><button id="share-rotate" type="button">Rotate link</button><button id="share-revoke" type="button">Revoke</button></div>'
        : '<p class="share-empty">Create a private gallery link for this collection.</p>'
            + shareExpiryOptions()
            + sharePasswordControls(null)
            + '<div class="share-actions"><button id="share-create" type="button">Create share link</button></div>';
    shareOverlay.innerHTML = '<div class="modal-card share-card" role="dialog" aria-modal="true" aria-labelledby="share-title">'
        + `<div class="mo-head"><h2 id="share-title">Share ${esc(name)}</h2><button type="button" id="share-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
        + '<div class="mo-body">' + body + '</div></div>';
    shareOverlay.hidden = false;
    const pickIds = clientPickIds(pickData);
    shareOverlay.querySelector('#share-close')?.addEventListener('click', closeShareOverlay);
    shareOverlay.querySelector('#share-copy')?.addEventListener('click', () => copyShareUrl(share.url));
    shareOverlay.querySelector('#share-view-picks')?.addEventListener('click', () => selectClientPicks(pickIds));
    shareOverlay.querySelector('#share-apply-picks')?.addEventListener('click', () => applyClientPicks(collectionId, pickIds));
    shareOverlay.querySelector('#share-create')?.addEventListener('click', async () => {
        const actionToken = shareOverlayToken;
        const value = shareOverlay.querySelector('#share-expiry')?.value || '';
        const password = shareOverlay.querySelector('#share-password')?.value || '';
        const result = await createCollectionShare(collectionId, {
            expiresInDays: value ? Number(value) : null,
            ...(password ? { password } : {}),
        });
        if (!shareOverlayIsCurrent(actionToken)) return;
        if (result && result.ok) {
            showToast('Share link created');
            await renderShareOverlay(collectionId, name, result.share, actionToken);
        } else {
            showToast("Couldn't create share link");
        }
    });
    shareOverlay.querySelector('#share-password-save')?.addEventListener('click', async () => {
        if (!share) return;
        const actionToken = shareOverlayToken;
        const password = shareOverlay.querySelector('#share-password')?.value || '';
        if (!password) {
            showToast('Enter a password');
            return;
        }
        const result = await createCollectionShare(collectionId, { password });
        if (!shareOverlayIsCurrent(actionToken)) return;
        if (result && result.ok) {
            showToast(share.protected ? 'Password changed' : 'Password set');
            await renderShareOverlay(collectionId, name, result.share, actionToken);
        } else {
            showToast("Couldn't save password");
        }
    });
    shareOverlay.querySelector('#share-password-clear')?.addEventListener('click', async () => {
        const actionToken = shareOverlayToken;
        const result = await createCollectionShare(collectionId, { clearPassword: true });
        if (!shareOverlayIsCurrent(actionToken)) return;
        if (result && result.ok) {
            showToast('Password removed');
            await renderShareOverlay(collectionId, name, result.share, actionToken);
        } else {
            showToast("Couldn't remove password");
        }
    });
    bindShareConfirmButton('#share-rotate', 'Confirm rotate', async () => {
        const actionToken = shareOverlayToken;
        const result = await createCollectionShare(collectionId, { rotate: true });
        if (!shareOverlayIsCurrent(actionToken)) return;
        if (result && result.ok) {
            showToast('Share link rotated');
            await renderShareOverlay(collectionId, name, result.share, actionToken);
        } else {
            showToast("Couldn't rotate link");
        }
    });
    bindShareConfirmButton('#share-revoke', 'Confirm revoke', async () => {
        const actionToken = shareOverlayToken;
        const result = await revokeCollectionShare(collectionId);
        if (!shareOverlayIsCurrent(actionToken)) return;
        if (result && result.ok) {
            showToast('Share link revoked');
            await renderShareOverlay(collectionId, name, null, actionToken);
        } else {
            showToast("Couldn't revoke link");
        }
    });
    trapFocus(shareOverlay, shareOverlay.querySelector('input, select, button'));
}

function bindShareConfirmButton(selector, label, action) {
    const button = shareOverlay?.querySelector(selector);
    if (!button) return;
    let armed = false;
    const original = button.textContent;
    button.addEventListener('click', async () => {
        if (!armed) {
            armed = true;
            button.textContent = label;
            return;
        }
        button.disabled = true;
        await action();
        button.disabled = false;
        button.textContent = original;
    });
}

async function openShareOverlay(collectionId, name = 'Collection') {
    ensureShareOverlay();
    const token = ++shareOverlayToken;
    shareOverlay.innerHTML = `<div class="modal-card share-card" role="dialog" aria-modal="true"><div class="mo-head"><h2>Share ${esc(name)}</h2><button type="button" id="share-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div><div class="mo-body"><div class="muted">Loading…</div></div></div>`;
    shareOverlay.hidden = false;
    shareOverlay.querySelector('#share-close')?.addEventListener('click', closeShareOverlay);
    trapFocus(shareOverlay, shareOverlay.querySelector('button'));
    const data = await getCollectionShare(collectionId);
    if (!shareOverlayIsCurrent(token)) return;
    await renderShareOverlay(collectionId, name, data && data.share, token);
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
            cancel();
        }
    });
    input.focus();
    input.select();
}

function startCollectionDelete(collectionId, name = 'Collection') {
    const row = document.querySelector(`.coll-row[data-coll-id="${collectionId}"]`);
    if (!row) return;
    row.outerHTML = `<div class="coll-confirm" data-delete-coll="${collectionId}">Delete? `
        + '<button data-yes="1">Yes</button> / <button data-no="1">No</button></div>';
    const confirm = document.querySelector(`.coll-confirm[data-delete-coll="${collectionId}"]`);
    confirm.querySelector('[data-no]')?.addEventListener('click', renderCollections);
    confirm.querySelector('[data-yes]')?.addEventListener('click', async () => {
        const result = await deleteCollection(collectionId);
        if (result && result.ok) {
            showToast(`Deleted “${name}”`);
            if (String(scope.collectionId || '') === String(collectionId)) setScope({});
            await loadCollections();
        } else {
            showToast("Couldn't delete collection");
            renderCollections();
        }
    });
    confirm.querySelector('[data-yes]')?.focus();
}

function renderSuggestions() {
    const host = document.getElementById('suggestions-wrap');
    if (!host) return;
    if (suggestionsAreLoading()) {
        host.innerHTML = '<div class="suggest-row skel" style="height:30px"></div>';
        return;
    }
    const count = visibleSuggestions().length;
    if (!count) {
        host.innerHTML = '';
        return;
    }
    host.innerHTML = '<button class="nav-row suggest-row" id="review-suggestions" type="button">'
        + `<span class="nr-glyph">${icon('sparkles')}</span>`
        + '<span class="nr-label">Suggested collections</span>'
        + `<span class="nr-count">${fmt(count)}</span></button>`;
    host.querySelector('#review-suggestions')?.addEventListener('click', () => {
        openSuggestionsReview();
        closeLeftDrawer();
    });
}

function renderLibrary() {
    const rows = [
        ['all', 'house', 'All Photos', '', libraryCounts?.total, ''],
        ['picked', 'star', 'Picked', 'picked', libraryCounts?.picked, ''],
        ['rejected', 'x', 'Rejected', 'rejected', libraryCounts?.rejected, ''],
        ['trash', 'trash-2', 'Trash', '', trashTotal, 'Deleted photos'],
        ['recent', 'clock-3', 'Recent', '', null, 'Newest first'],
    ];
    document.getElementById('library-list').innerHTML = rows.map(([id, glyph, label, , count, tip]) => {
        const hasCount = count != null || id !== 'recent';
        return `<button class="nav-row" data-lib="${id}"${tip ? ` data-tip="${esc(tip)}"` : ''}>`
            + `<span class="nr-glyph">${icon(glyph)}</span><span class="nr-label">${label}</span>`
            + (hasCount ? `<span class="nr-count">${count == null ? '…' : fmt(count)}</span>` : '')
            + '</button>';
    }).join('');
    for (const row of document.querySelectorAll('[data-lib]')) {
        row.addEventListener('click', () => {
            const key = row.dataset.lib;
            if (key === 'all') setScope({});
            if (key === 'picked') setScope({ flag: 'picked' });
            if (key === 'rejected') setScope({ flag: 'rejected' });
            if (key === 'trash') setActiveLens('trash');
            if (key === 'recent') setScope({ sort: 'date_taken' });
            closeLeftDrawer();
        });
    }
}

async function loadLibraryCounts() {
    const [data, trash] = await Promise.all([getCounts(new URLSearchParams()), getTrash({ limit: 1, offset: 0 })]);
    libraryCounts = data || {};
    trashTotal = trash && trash.total != null ? Number(trash.total) || 0 : null;
    renderLibrary();
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

export async function exportCurrentScope(format = 'csv', size = '') {
    const params = scopeParams({ format });
    if (size) params.set('size', size);
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        params.set('sort', 'elo');
        params.set('limit', String(viewState.bestOfLimit));
    }
    let count = viewState.bestOf && viewState.bestOfLimit != null ? viewState.bestOfLimit : viewState.visibleImages;
    if (scope.collectionId) {
        const ids = await loadCollectionImageIds(scope.collectionId);
        if (!ids.length) {
            showToast('Collection is empty');
            return;
        }
        count = ids.length;
        params.set('ids', ids.join(','));
    }
    if (scope.similarIds.length) {
        const ids = scope.similarIds.map(Number).filter((id) => id > 0);
        count = ids.length;
        params.set('ids', ids.join(','));
    }
    downloadExport(params, {
        count: format === 'zip' ? count : 0,
        message: format === 'zip' ? 'Preparing current view zip' : `Exporting current view as ${format.toUpperCase()}`,
    });
}

export function openScopeExportMenu(anchor) {
    openExportMenu(anchor, ({ format, size }) => exportCurrentScope(format, size));
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

export function requestShareCurrentCollection() {
    if (!scope.collectionId) {
        showToast('Open a collection first');
        return;
    }
    openShareOverlay(scope.collectionId, scope.collectionName || 'Collection');
}

export async function initPanel() {
    initSuggestions({
        refreshCollections: loadCollections,
        notifyChange: renderSuggestions,
    });
    setCollectionPicker(openCollectionPicker);
    document.getElementById('shell').classList.toggle('left-collapsed', viewState.leftCollapsed);
    document.getElementById('collapse-left').addEventListener('click', toggleLeftPanel);
    document.getElementById('panel-scrim').addEventListener('click', closeLeftDrawer);
    window.matchMedia('(max-width: 880px)').addEventListener('change', (event) => {
        if (!event.matches) closeLeftDrawer();
    });
    ensureCollectionMenu();
    document.addEventListener('pointerdown', (event) => {
        if (!collectionMenu || collectionMenu.hidden || collectionMenu.contains(event.target) || event.target.closest('.coll-menu-btn')) return;
        closeCollectionMenu();
    });
    window.addEventListener('resize', closeCollectionMenu);
    document.getElementById('export-view').addEventListener('click', (event) => openScopeExportMenu(event.currentTarget));
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
            const recentActive = key === 'recent' && scope.sort === 'date_taken' && !scopeActive();
            const active = (key === 'all' && !scopeActive() && scope.sort !== 'date_taken')
                || (key === 'picked' && scope.flag === 'picked')
                || (key === 'rejected' && scope.flag === 'rejected')
                || recentActive;
            row.classList.toggle('active', active);
        }
    });
    on('lens', (lens) => {
        document.querySelector('[data-lib="trash"]')?.classList.toggle('active', lens === 'trash');
    });
    on('trash:changed', loadLibraryCounts);
    renderLibrary();
    loadLibraryCounts();
    await loadCollections();
    catalog = await getCatalog();
    await initFoldersPanel({ closeDrawer: closeLeftDrawer });
    renderSources();
    setTimeout(loadSuggestionsOnce, 0);
}
