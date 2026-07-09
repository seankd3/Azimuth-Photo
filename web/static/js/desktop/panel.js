import {
    addToCollection, createCollection, getCatalog, getCollection,
    getCounts, getTrash,
    createCollectionShare, deleteCollection, getCollectionShare, getCollectionShareFavorites, listCollections,
    getCollectionPublish, publishCollection, removeFromCollection, renameCollection,
    revokeCollectionPublish, revokeCollectionShare, thumbUrl, updateCollection,
} from './api.js';
import { loadCollectionImageIds } from './scope_data.js';
import {
    byId, emit, on, scope, scopeActive, scopeParams, scopePatchFromSmartQuery, selection, selectionChanged, setActiveLens,
    setLeftCollapsed, setScope, smartQueryActive, smartQueryFromScope, smartQueryName, smartQuerySummary, viewState,
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
let collectionsLoading = true;
let sourcesLoading = true;
let drawerOpen = false;
let collectionMenu = null;
let collectionMenuReturn = null;
let shareOverlay = null;
let shareOverlayToken = 0;
let publishOverlay = null;
let publishOverlayToken = 0;
let publishPollTimer = 0;
let chromeRefreshTimer = 0;
let editingSmartCollection = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
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
        + `<span class="coll-cover">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="">` : icon(smart ? 'sparkles' : 'folder')}${smart && c.cover_image_id ? `<span class="coll-smart-badge">${icon('sparkles')}</span>` : ''}</span>`
        + `<span class="nr-label" title="${esc(c.name)}">${esc(c.name)}</span><span class="nr-count">${fmt(c.image_count)}</span>`
        + `${c.published ? `<span class="coll-published" data-tip="Published to website">${icon('globe')}</span>` : ''}</button>`
        + `<button class="coll-menu-btn" type="button" data-tip="Collection actions" aria-label="Collection actions">${icon('ellipsis')}</button></div>`
    }).join('');
    for (const row of host.querySelectorAll('.coll-row')) {
        const mainButton = row.querySelector('.coll-main');
        mainButton?.addEventListener('click', () => {
            setScope({
                collectionId: row.dataset.collId,
                collectionName: row.dataset.collName || 'Collection',
                collectionSmart: row.dataset.collSmart === '1',
            });
            closeLeftDrawer();
        });
        row.addEventListener('dragover', (event) => {
            if (row.dataset.collSmart === '1' || !selectedIds().length) return;
            event.preventDefault();
            row.classList.add('drag-over');
        });
        row.addEventListener('dragleave', () => row.classList.remove('drag-over'));
        row.addEventListener('drop', async (event) => {
            event.preventDefault();
            row.classList.remove('drag-over');
            if (row.dataset.collSmart === '1') return;
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
    const coll = collectionById(id);
    const smart = Boolean(coll?.smart);
    collectionMenu.innerHTML = '<div class="pm-group">'
        + (smart ? `<button data-act="edit-query">${icon('sparkles')} Edit query</button>`
            + `<button data-act="materialize">${icon('archive')} Convert to static</button>` : '')
        + `<button data-act="share">${icon('share-2')} Share…</button>`
        + `<button data-act="publish">${icon('globe')} Publish to website…</button>`
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
            if (action === 'share') openShareOverlay(id, name);
            if (action === 'publish') openPublishOverlay(id, name);
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

function ensurePublishOverlay() {
    if (publishOverlay) return publishOverlay;
    publishOverlay = document.createElement('div');
    publishOverlay.id = 'publish-overlay';
    publishOverlay.className = 'modal-scrim';
    publishOverlay.hidden = true;
    document.body.appendChild(publishOverlay);
    publishOverlay.addEventListener('click', (event) => {
        if (event.target === publishOverlay) closePublishOverlay();
    });
    publishOverlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closePublishOverlay();
        }
    });
    return publishOverlay;
}

function publishOverlayIsCurrent(token) {
    return Boolean(publishOverlay && !publishOverlay.hidden && token === publishOverlayToken);
}

function closePublishOverlay() {
    window.clearTimeout(publishPollTimer);
    publishPollTimer = 0;
    if (!publishOverlay || publishOverlay.hidden) return;
    publishOverlayToken += 1;
    releaseFocus(publishOverlay);
    publishOverlay.hidden = true;
}

function slugifyName(value) {
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96) || 'gallery';
}

function publishCopyText(count, slug) {
    return `Publish ${fmt(count)} photos publicly at seankennethdoherty.com/g/${slug}/.`;
}

function publishPhaseCopy(job) {
    if (!job) return '';
    if (job.state === 'error') return 'Publishing needs attention.';
    if (job.phase === 'deploying') return 'Deploying to the website…';
    if (job.phase === 'building') return 'Building static gallery…';
    if (job.state === 'live') return 'Gallery is live.';
    if (job.state === 'revoked') return 'Gallery is unpublished.';
    return job.state === 'revoking' ? 'Unpublishing gallery…' : 'Publishing queued…';
}

function publishErrorBlock(job) {
    if (!job || job.state !== 'error') return '';
    const paths = Array.isArray(job.paths) && job.paths.length
        ? `<div class="publish-error-list">${job.paths.map((path) => `<code>${esc(path)}</code>`).join('')}</div>`
        : '';
    const tail = job.tail ? `<pre>${esc(job.tail)}</pre>` : '';
    return '<div class="publish-error">'
        + `<b>${esc(job.status_code ? `${job.status_code} error` : 'Publish error')}</b>`
        + `<p>${esc(job.error || "Couldn't publish this gallery.")}</p>`
        + paths
        + tail
        + '</div>';
}

function publishStatusBlock(data) {
    const job = data?.job || null;
    const publish = data?.publish || job?.publish || null;
    const url = data?.url || job?.url || publish?.url || '';
    const status = publishPhaseCopy(job);
    return '<div class="publish-status">'
        + (status ? `<span>${esc(status)}</span>` : '<span>Ready to publish.</span>')
        + (url ? `<div class="share-link-row"><input id="publish-url" readonly value="${esc(url)}"><button id="publish-copy" type="button">${icon('copy')} Copy</button></div>` : '')
        + publishErrorBlock(job)
        + '</div>';
}

async function copyPublishUrl(url) {
    try {
        await navigator.clipboard.writeText(url);
        showToast('Public URL copied');
    } catch {
        showToast("Couldn't copy URL");
    }
}

async function renderPublishOverlay(collectionId, name, data = null, token = publishOverlayToken) {
    ensurePublishOverlay();
    if (!publishOverlayIsCurrent(token)) return;
    const coll = collectionById(collectionId) || {};
    const publish = data?.publish || null;
    const job = data?.job || null;
    const busy = Boolean(data?.in_progress || (job && ['publishing', 'revoking'].includes(job.state)));
    const count = Number(coll.image_count || publish?.image_count || 0);
    const slug = (job?.slug || publish?.slug || slugifyName(name));
    const title = (job?.title || publish?.title || name || 'Gallery');
    const liveUrl = data?.url || publish?.url || job?.url || '';
    const actionLabel = publish ? 'Republish to website' : 'Publish to website';
    publishOverlay.innerHTML = '<div class="modal-card publish-card" role="dialog" aria-modal="true" aria-labelledby="publish-title">'
        + `<div class="mo-head"><h2 id="publish-title">Publish ${esc(name)}</h2><button type="button" id="publish-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
        + '<div class="mo-body">'
        + `<p class="publish-confirm-copy">${esc(publishCopyText(count, slug))}</p>`
        + '<div class="publish-fields">'
        + `<label>Title <input id="publish-title-input" value="${esc(title)}" maxlength="160" ${busy ? 'disabled' : ''}></label>`
        + `<label>Slug <input id="publish-slug-input" value="${esc(slug)}" maxlength="96" ${busy || publish ? 'disabled' : ''}></label>`
        + '</div>'
        + publishStatusBlock(data)
        + '<div class="publish-actions">'
        + (publish ? '<button id="publish-revoke" type="button" class="danger" ' + (busy ? 'disabled' : '') + '>Unpublish</button>' : '')
        + `<button id="publish-submit" type="button" ${busy ? 'disabled' : ''}>${esc(actionLabel)}</button>`
        + '</div>'
        + '</div></div>';
    publishOverlay.hidden = false;
    publishOverlay.querySelector('#publish-close')?.addEventListener('click', closePublishOverlay);
    publishOverlay.querySelector('#publish-copy')?.addEventListener('click', () => copyPublishUrl(liveUrl));
    const slugInput = publishOverlay.querySelector('#publish-slug-input');
    const copy = publishOverlay.querySelector('.publish-confirm-copy');
    slugInput?.addEventListener('input', () => {
        const nextSlug = slugifyName(slugInput.value);
        copy.textContent = publishCopyText(count, nextSlug);
    });
    const startPublish = async () => {
        const nextSlug = slugifyName(slugInput?.value || slug);
        const nextTitle = publishOverlay.querySelector('#publish-title-input')?.value.trim() || title;
        const result = await publishCollection(collectionId, { slug: nextSlug, title: nextTitle });
        if (!publishOverlayIsCurrent(token)) return;
        if (result.ok) {
            showToast(publish ? 'Republishing gallery' : 'Publishing gallery');
            await pollPublishStatus(collectionId, name, token, true);
        } else {
            await renderPublishOverlay(collectionId, name, {
                ...data,
                job: {
                    state: 'error',
                    status_code: result.status,
                    error: result.data?.error || "Couldn't start publishing.",
                },
            }, token);
        }
    };
    if (publish) {
        bindPublishConfirmButton('#publish-submit', 'Confirm republish', startPublish);
    } else {
        publishOverlay.querySelector('#publish-submit')?.addEventListener('click', startPublish);
    }
    bindPublishConfirmButton('#publish-revoke', 'Confirm unpublish', async () => {
        const result = await revokeCollectionPublish(collectionId);
        if (!publishOverlayIsCurrent(token)) return;
        if (result.ok) {
            showToast('Unpublishing gallery');
            await pollPublishStatus(collectionId, name, token, true);
        } else {
            await renderPublishOverlay(collectionId, name, {
                ...data,
                job: {
                    state: 'error',
                    status_code: result.status,
                    error: result.data?.error || "Couldn't start unpublishing.",
                },
            }, token);
        }
    });
    if (busy) schedulePublishPoll(collectionId, name, token);
    trapFocus(publishOverlay, publishOverlay.querySelector('input, button'));
}

function bindPublishConfirmButton(selector, label, action) {
    const button = publishOverlay?.querySelector(selector);
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

function schedulePublishPoll(collectionId, name, token) {
    window.clearTimeout(publishPollTimer);
    publishPollTimer = window.setTimeout(() => pollPublishStatus(collectionId, name, token), 2500);
}

async function pollPublishStatus(collectionId, name, token, immediate = false) {
    window.clearTimeout(publishPollTimer);
    if (!publishOverlayIsCurrent(token)) return;
    const data = await getCollectionPublish(collectionId);
    if (!publishOverlayIsCurrent(token)) return;
    await renderPublishOverlay(collectionId, name, data, token);
    if (data?.in_progress) {
        schedulePublishPoll(collectionId, name, token);
    } else if (!immediate) {
        await loadCollections();
    }
}

async function openPublishOverlay(collectionId, name = 'Collection') {
    ensurePublishOverlay();
    const token = ++publishOverlayToken;
    publishOverlay.innerHTML = `<div class="modal-card publish-card" role="dialog" aria-modal="true"><div class="mo-head"><h2>Publish ${esc(name)}</h2><button type="button" id="publish-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div><div class="mo-body"><div class="muted">Loading…</div></div></div>`;
    publishOverlay.hidden = false;
    publishOverlay.querySelector('#publish-close')?.addEventListener('click', closePublishOverlay);
    trapFocus(publishOverlay, publishOverlay.querySelector('button'));
    const data = await getCollectionPublish(collectionId);
    if (!publishOverlayIsCurrent(token)) return;
    await renderPublishOverlay(collectionId, name, data, token);
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

function startSmartQueryEdit(collectionId) {
    const coll = collectionById(collectionId);
    if (!coll?.smart) return;
    editingSmartCollection = { id: collectionId, name: coll.name || 'Smart collection' };
    setScope(scopePatchFromSmartQuery(coll.query || {}));
    requestNewCollection({ preferSmart: true });
    closeLeftDrawer();
    showToast('Smart query loaded. Tweak filters, then update it.');
}

function startSmartMaterialize(collectionId, name = 'Collection') {
    const row = document.querySelector(`.coll-row[data-coll-id="${collectionId}"]`);
    if (!row) return;
    row.outerHTML = `<div class="coll-confirm" data-materialize-coll="${collectionId}">Convert to static? `
        + '<button data-yes="1">Yes</button> / <button data-no="1">No</button></div>';
    const confirm = document.querySelector(`.coll-confirm[data-materialize-coll="${collectionId}"]`);
    confirm.querySelector('[data-no]')?.addEventListener('click', renderCollections);
    confirm.querySelector('[data-yes]')?.addEventListener('click', async () => {
        const result = await updateCollection(collectionId, { materialize: true });
        if (result && result.ok) {
            showToast(`Converted “${name}” to static`);
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
    if (sourcesLoading) {
        host.innerHTML = skeletonRows(3);
        return;
    }
    host.innerHTML = sources.length ? sources.map((s) => {
        const online = Number(s.online) === 1;
        const count = s.active_image_count != null ? s.active_image_count : s.image_count;
        const label = s.display_name || s.path;
        return `<button class="nav-row" data-source="${esc(s.path)}" title="${esc(label)}">`
            + `<span class="nr-dot ${online ? 'on' : 'off'}"></span><span class="nr-label" title="${esc(label)}">${esc(label)}</span>`
            + `<span class="nr-count">${fmt(count)}</span>${online ? '' : '<span class="nr-tag">offline</span>'}</button>`;
    }).join('') : emptyState('hard-drive', 'No sources yet.');
    for (const row of host.querySelectorAll('[data-source]')) {
        row.addEventListener('click', () => {
            setScope({ folder: row.dataset.source });
            closeLeftDrawer();
        });
    }
}

async function loadCollections() {
    collectionsLoading = true;
    renderCollections();
    const data = await listCollections();
    collections = (data && data.collections) || [];
    collectionsLoading = false;
    renderCollections();
    emit('collections:changed', { collections });
}

async function loadCatalogChrome() {
    sourcesLoading = true;
    renderSources();
    catalog = await getCatalog();
    sourcesLoading = false;
    renderSources();
}

function scheduleChromeRefresh() {
    window.clearTimeout(chromeRefreshTimer);
    chromeRefreshTimer = window.setTimeout(() => {
        loadLibraryCounts();
        loadCatalogChrome();
        loadCollections();
    }, 300);
}

async function addImagesToCollection(collectionId, imageIds) {
    const coll = collections.find((c) => Number(c.id) === Number(collectionId));
    if (coll?.smart) {
        showToast('Smart collections update from their query');
        return;
    }
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
    const regularCollections = collections.filter((c) => !c.smart);
    list.innerHTML = regularCollections.length ? regularCollections.map((c) => (
        `<button data-coll-id="${c.id}" title="${esc(c.name)}"><span title="${esc(c.name)}">${esc(c.name)}</span><span class="num">${fmt(c.image_count)}</span></button>`
    )).join('') : '<div class="muted">No static collections yet.</div>';
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
    const options = arguments[0] && arguments[0].preferSmart ? arguments[0] : {};
    if (narrowPanel()) openLeftDrawer();
    const form = document.getElementById('new-coll-form');
    form.hidden = false;
    renderNewCollectionForm({ resetName: true, preferSmart: Boolean(options.preferSmart) });
    const input = document.getElementById('new-coll-name');
    input.focus();
    input.select();
}

export function requestSaveSmartCollection() {
    if (!smartQueryActive()) {
        showToast('Add a filter or search first');
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
    const query = smartQueryFromScope();
    const canSaveSmart = smartQueryActive(query);
    if (smartButton) {
        smartButton.hidden = !canSaveSmart;
        smartButton.disabled = !canSaveSmart;
        smartButton.textContent = editingSmartCollection ? 'Update Smart Collection' : 'Save as Smart Collection';
        smartButton.title = canSaveSmart ? smartQuerySummary(query) : '';
    }
    if (createButton) createButton.textContent = canSaveSmart ? 'Create Static' : 'Create';
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
        showToast('Add a filter or search first');
        return;
    }
    const name = input.value.trim() || smartQueryName(query);
    if (button) button.disabled = true;
    let result = null;
    if (editingSmartCollection) {
        if (name !== editingSmartCollection.name) await renameCollection(editingSmartCollection.id, name);
        result = await updateCollection(editingSmartCollection.id, { query });
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
    document.getElementById('new-coll-smart')?.addEventListener('click', saveSmartCollectionFromForm);
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
    on('flags', scheduleChromeRefresh);
    on('trash:changed', scheduleChromeRefresh);
    on('import:changed', scheduleChromeRefresh);
    on('scope', () => {
        if (!smartQueryActive()) editingSmartCollection = null;
        renderNewCollectionForm();
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
    renderLibrary();
    renderCollections();
    renderSources();
    loadLibraryCounts();
    await loadCollections();
    await loadCatalogChrome();
    await initFoldersPanel({ closeDrawer: closeLeftDrawer });
    setTimeout(loadSuggestionsOnce, 0);
}
