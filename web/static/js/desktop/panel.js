import {
    addToCollection, createCollection, getCatalog, getCollection,
    getCounts, getTrash,
    createCollectionShare, deleteCollection, getCollectionShare, getCollectionShareFavorites, listCollections,
    getCollectionPublish, publishCollection, removeFromCollection, renameCollection,
    revokeCollectionPublish, revokeCollectionShare, thumbUrl, updateCollection,
    createSavedView, deleteSavedView, listSavedViews,
} from './api.js';
import { loadCollectionImageIds } from './scope_data.js';
import {
    byId, emit, folderActive, navigateToScope, on, patchPrefs, scope, scopeActive, scopeParams, scopePatchFromSmartQuery, selection, selectionChanged, setActiveLens,
    setLeftCollapsed, setScope, smartQueryActive, smartQueryFromScope, smartQueryName, smartQuerySummary, sortBase, viewState,
} from './state.js';
import { applyFlags, selectedIds, setCollectionPicker } from './selection.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { confirmAction, confirmTypedCount } from './trash.js';
import { downloadExport, openExportMenu } from './export_menu.js';
import { initFoldersPanel } from './folders.js';
import { openSourceRevealMenu } from './source_reveal_menu.js';
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
let collectionsLoadError = false;
let sourcesLoadError = false;
let drawerOpen = false;
let collectionMenu = null;
let collectionMenuReturn = null;
let deliverOverlay = null;
let deliverOverlayToken = 0;
let deliverOverlayReturn = null;
let deliverPollTimer = 0;
let deliverSession = null;
let chromeRefreshTimer = 0;
let editingSmartCollection = null;
let savedViews = [];

const SHARED_CHANGED_EVENT = 'shares/publishes-changed';
const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const narrowPanel = () => window.matchMedia('(max-width: 880px)').matches;
const emptyState = (glyph, copy, action = '') => (
    `<div class="chrome-empty"><span class="chrome-empty-glyph">${icon(glyph)}</span><span>${esc(copy)}</span>${action}</div>`
);
const skeletonRows = (count = 3) => Array.from({ length: count }, () => '<div class="chrome-skel nav-row skel"></div>').join('');

function emitSharedSurfacesChanged(collectionId) {
    window.dispatchEvent(new CustomEvent(SHARED_CHANGED_EVENT, { detail: { collectionId } }));
}

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
        + `<button data-act="deliver">${icon('send')} Deliver…</button>`
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
            if (action === 'deliver') openDeliverOverlay(id, name, anchor);
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

const DELIVER_TABS = [
    ['private', 'Private link'],
    ['gallery', 'Client gallery'],
    ['website', 'Website'],
];

function ensureDeliverOverlay() {
    if (deliverOverlay) return deliverOverlay;
    deliverOverlay = document.createElement('div');
    deliverOverlay.id = 'deliver-overlay';
    deliverOverlay.className = 'modal-scrim';
    deliverOverlay.hidden = true;
    document.body.appendChild(deliverOverlay);
    deliverOverlay.addEventListener('click', (event) => {
        if (event.target === deliverOverlay) closeDeliverOverlay();
    });
    deliverOverlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeDeliverOverlay();
        }
    });
    return deliverOverlay;
}

function deliverOverlayIsCurrent(token) {
    return Boolean(deliverOverlay && !deliverOverlay.hidden && token === deliverOverlayToken);
}

function closeDeliverOverlay() {
    window.clearTimeout(deliverPollTimer);
    deliverPollTimer = 0;
    if (!deliverOverlay || deliverOverlay.hidden) return;
    deliverOverlayToken += 1;
    releaseFocus(deliverOverlay);
    deliverOverlay.hidden = true;
    if (deliverOverlayReturn && document.contains(deliverOverlayReturn) && deliverOverlayReturn.focus) {
        deliverOverlayReturn.focus({ preventScroll: true });
    }
}

function deliverTabMarkup(activeTab) {
    return '<div class="deliver-tabs" role="tablist" aria-label="Delivery type">'
        + DELIVER_TABS.map(([id, label]) => `<button type="button" role="tab" data-deliver-tab="${id}" aria-selected="${id === activeTab}">${label}</button>`).join('')
        + '</div>';
}

function renderDeliverShell(name, activeTab, body = '<div class="muted">Loading…</div>') {
    deliverOverlay.innerHTML = '<div class="modal-card deliver-card" role="dialog" aria-modal="true" aria-labelledby="deliver-title">'
        + `<div class="mo-head"><h2 id="deliver-title">Deliver ${esc(name)}</h2><button type="button" id="deliver-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
        + deliverTabMarkup(activeTab)
        + `<div class="mo-body deliver-body" data-deliver-body>${body}</div>`
        + '</div>';
    deliverOverlay.hidden = false;
    deliverOverlay.querySelector('#deliver-close')?.addEventListener('click', closeDeliverOverlay);
}

function bindDeliverTabs(onSelect) {
    for (const button of deliverOverlay.querySelectorAll('[data-deliver-tab]')) {
        button.addEventListener('click', () => onSelect(button.dataset.deliverTab));
    }
}

export async function openDeliverOverlay(collectionId, name = 'Collection', opener = null, activeTab = 'private') {
    ensureDeliverOverlay();
    const token = ++deliverOverlayToken;
    deliverOverlayReturn = opener || document.activeElement;
    deliverSession = {
        collectionId,
        name,
        activeTab,
        share: null,
        publish: null,
        draft: { title: name, password: '', expiry: '', slug: slugifyName(name) },
    };
    renderDeliverShell(name, activeTab);
    bindDeliverTabs((tab) => switchDeliverTab(deliverSession, tab));
    trapFocus(deliverOverlay, deliverOverlay.querySelector('button'));
    const [shareData, publishData] = await Promise.all([getCollectionShare(collectionId), getCollectionPublish(collectionId)]);
    if (!deliverOverlayIsCurrent(token)) return token;
    deliverSession.share = shareData?.share || null;
    deliverSession.publish = publishData;
    if (deliverSession.publish?.publish?.title) deliverSession.draft.title = deliverSession.publish.publish.title;
    if (deliverSession.publish?.publish?.slug) deliverSession.draft.slug = deliverSession.publish.publish.slug;
    renderDeliver(deliverSession, token);
    return token;
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
        + `<button data-deliver-view-picks type="button" ${ids.length ? '' : 'disabled'}>View picks</button>`
        + `<button data-deliver-apply-picks type="button" ${ids.length ? '' : 'disabled'}>Apply as picks</button>`
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
    closeDeliverOverlay();
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

function deliverTitleRow(session, value, disabled = false) {
    return `<label class="deliver-title-row">Title<input data-deliver-title value="${esc(value || session.name)}" maxlength="160" ${disabled ? 'disabled' : ''}></label>`;
}

function deliverPasswordRow({ protected: isProtected = false, value = '', action = '', clear = false } = {}) {
    return '<div class="share-password-row deliver-password-row">'
        + '<div class="share-password-head"><span>Password</span>'
        + (isProtected ? `<span class="shared-badge">${icon('lock')} Protected</span>` : '')
        + '</div><div class="share-link-row">'
        + `<input data-deliver-password type="password" autocomplete="new-password" value="${esc(value)}" placeholder="${isProtected ? 'Protected' : 'Optional'}">`
        + (action ? `<button data-deliver-password-save type="button">${action}</button>` : '')
        + '</div>'
        + (clear ? '<button class="share-remove-password" data-deliver-password-clear type="button">Remove password</button>' : '')
        + '</div>';
}

function deliverLinkRow(url) {
    return url ? `<div class="share-link-row deliver-link-row"><input readonly value="${esc(url)}"><button data-deliver-copy type="button">${icon('copy')} Copy</button><button data-deliver-open type="button">${icon('external-link')} Open</button></div>` : '';
}

function shareExpiryOptions() {
    return '<label class="share-expiry">Expires <select data-deliver-expiry>'
        + '<option value="">Never</option><option value="7">7 days</option><option value="30">30 days</option></select></label>';
}

async function copyDeliverUrl(url) {
    try {
        await navigator.clipboard.writeText(url);
        showToast('Link copied');
    } catch {
        showToast("Couldn't copy link");
    }
}

async function withBusyButton(button, action) {
    if (!button || button.disabled) return;
    button.disabled = true;
    try {
        await action();
    } finally {
        if (document.contains(button)) button.disabled = false;
    }
}

function bindDeliverConfirmButton(selector, label, action) {
    const button = deliverOverlay?.querySelector(selector);
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

function slugifyName(value) {
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96) || 'gallery';
}

function configuredGalleryUrl(slug, publishing = {}) {
    const base = String(publishing.site_base_url || '').replace(/\/+$/, '');
    return base && slug ? `${base}/g/${slug}/` : '';
}

function publishCopyText(count, slug, publishing = {}) {
    const url = configuredGalleryUrl(slug, publishing);
    if (url) return `Publish ${fmt(count)} photos publicly at ${url}.`;
    return `Publish ${fmt(count)} photos into the configured gallery folder.`;
}

function publishLeadText(count, slug, publish, publishing = {}) {
    const url = configuredGalleryUrl(slug, publishing);
    if (publish) return url ? `Update the live gallery at ${url}.` : 'Update the published gallery.';
    return publishCopyText(count, slug, publishing);
}

function publishPhaseCopy(job) {
    if (!job) return '';
    if (job.state === 'error') return 'Publishing didn’t finish.';
    if (job.state === 'hook_failed') return 'Gallery files are ready, but the website update didn’t finish.';
    if (job.state === 'revoked_hook_failed') return 'Gallery files were removed, but the website update didn’t finish.';
    if (job.phase === 'hook') return 'Updating website…';
    if (job.phase === 'deploying') return 'Updating website…';
    if (job.phase === 'building') return 'Building gallery…';
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
    const details = paths || tail ? `<details><summary>Details</summary>${paths}${tail}</details>` : '';
    return '<div class="publish-error">'
        + '<b>Couldn\'t publish this gallery.</b>'
        + `<p>${esc(job.error || 'The website did not finish the publish. Try again when ready.')}</p>`
        + '<button class="btn primary" id="publish-retry" type="button">Try again</button>'
        + details
        + '</div>';
}

function deliverPublishStatus(data) {
    const job = data?.job || null;
    const publish = data?.publish || job?.publish || null;
    const status = data?.publishing?.enabled === false ? 'Publishing is disabled until a folder is set.' : publishPhaseCopy(job);
    const hook = publish?.hook_status || job?.hook || null;
    return '<div class="publish-status">'
        + `<span>${esc(status || 'Ready to publish.')}</span>`
        + (hook?.configured && !hook.ok ? `<div class="publish-error"><b>Gallery files are ready, but the website update didn’t finish.</b><p>${esc(hook.output || 'The website update did not finish successfully.')}</p></div>` : '')
        + publishErrorBlock(job).replace('id="publish-retry"', 'data-deliver-publish-retry')
        + '</div>';
}

function saveDeliverDraft(session) {
    const root = deliverOverlay;
    session.draft.title = root.querySelector('[data-deliver-title]')?.value || session.draft.title;
    session.draft.password = root.querySelector('[data-deliver-password]')?.value || session.draft.password;
    session.draft.expiry = root.querySelector('[data-deliver-expiry]')?.value || session.draft.expiry;
    session.draft.slug = root.querySelector('[data-deliver-slug]')?.value || session.draft.slug;
}

function bindDeliverCommon(session, url = '') {
    deliverOverlay.querySelector('[data-deliver-title]')?.addEventListener('input', () => saveDeliverDraft(session));
    deliverOverlay.querySelector('[data-deliver-password]')?.addEventListener('input', () => saveDeliverDraft(session));
    deliverOverlay.querySelector('[data-deliver-expiry]')?.addEventListener('change', () => saveDeliverDraft(session));
    deliverOverlay.querySelector('[data-deliver-copy]')?.addEventListener('click', () => copyDeliverUrl(url));
    deliverOverlay.querySelector('[data-deliver-open]')?.addEventListener('click', () => window.open(url, '_blank', 'noopener,noreferrer'));
}

async function renderPrivateDeliver(session, token) {
    const share = session.share;
    const picks = share ? await getCollectionShareFavorites(session.collectionId) : null;
    if (!deliverOverlayIsCurrent(token) || deliverSession !== session) return;
    const body = deliverTitleRow(session, session.draft.title)
        + (share
            ? (share.expired ? '<div class="share-expired">Expired - rotate to renew</div>' : '')
                + deliverLinkRow(share.url || '')
                + `<div class="share-meta"><div><span>Created</span><b>${esc(formatShareDate(share.created_at))}</b></div><div><span>Expires</span><b>${esc(formatShareDate(share.expires_at))}</b></div></div>`
                + `<div class="share-stats">${esc(shareStatsLine(share))}</div>`
                + sharePicksRow(picks)
                + deliverPasswordRow({ protected: share.protected, value: session.draft.password, action: share.protected ? 'Change' : 'Set', clear: share.protected })
                + '<div class="share-actions"><button data-deliver-rotate type="button">Rotate link</button><button data-deliver-revoke type="button">Revoke</button></div>'
            : '<p class="share-empty">Create a private gallery link for this collection.</p>'
                + shareExpiryOptions()
                + deliverPasswordRow({ value: session.draft.password })
                + '<div class="share-actions"><button data-deliver-create type="button">Create private link</button></div>');
    renderDeliverShell(session.name, 'private', body);
    bindDeliverTabs((tab) => switchDeliverTab(session, tab));
    bindDeliverCommon(session, share?.url || '');
    const pickIds = clientPickIds(picks);
    deliverOverlay.querySelector('[data-deliver-view-picks]')?.addEventListener('click', () => selectClientPicks(pickIds));
    deliverOverlay.querySelector('[data-deliver-apply-picks]')?.addEventListener('click', () => applyClientPicks(session.collectionId, pickIds));
    deliverOverlay.querySelector('[data-deliver-create]')?.addEventListener('click', (event) => withBusyButton(event.currentTarget, async () => {
        saveDeliverDraft(session);
        const result = await createCollectionShare(session.collectionId, { expiresInDays: session.draft.expiry ? Number(session.draft.expiry) : null, ...(session.draft.password ? { password: session.draft.password } : {}) });
        if (deliverOverlayIsCurrent(token) && result?.ok) {
            session.share = result.share;
            showToast('Private link created');
            emitSharedSurfacesChanged(session.collectionId);
            renderDeliver(session, token);
        } else if (deliverOverlayIsCurrent(token)) showToast("Couldn't create private link");
    }));
    deliverOverlay.querySelector('[data-deliver-password-save]')?.addEventListener('click', (event) => withBusyButton(event.currentTarget, async () => {
        saveDeliverDraft(session);
        if (!session.draft.password) return showToast('Enter a password');
        const result = await createCollectionShare(session.collectionId, { password: session.draft.password });
        if (deliverOverlayIsCurrent(token) && result?.ok) {
            session.share = result.share;
            session.draft.password = '';
            showToast(share.protected ? 'Password changed' : 'Password set');
            emitSharedSurfacesChanged(session.collectionId);
            renderDeliver(session, token);
        }
    }));
    deliverOverlay.querySelector('[data-deliver-password-clear]')?.addEventListener('click', async () => {
        const result = await createCollectionShare(session.collectionId, { clearPassword: true });
        if (deliverOverlayIsCurrent(token) && result?.ok) {
            session.share = result.share;
            showToast('Password removed');
            emitSharedSurfacesChanged(session.collectionId);
            renderDeliver(session, token);
        }
    });
    bindDeliverConfirmButton('[data-deliver-rotate]', 'Confirm rotate', async () => {
        const result = await createCollectionShare(session.collectionId, { rotate: true });
        if (deliverOverlayIsCurrent(token) && result?.ok) {
            session.share = result.share;
            showToast('New private link created');
            emitSharedSurfacesChanged(session.collectionId);
            renderDeliver(session, token);
        }
    });
    bindDeliverConfirmButton('[data-deliver-revoke]', 'Confirm revoke', async () => {
        const result = await revokeCollectionShare(session.collectionId);
        if (deliverOverlayIsCurrent(token) && result?.ok) {
            session.share = null;
            showToast('Private link revoked');
            emitSharedSurfacesChanged(session.collectionId);
            renderDeliver(session, token);
        }
    });
    trapFocus(deliverOverlay, deliverOverlay.querySelector('input, select, button'));
}

function renderWebsiteDeliver(session, token) {
    const data = session.publish;
    const publish = data?.publish || data?.job?.publish || null;
    const job = data?.job || null;
    const publishing = data?.publishing || {};
    const setupNeeded = publishing.enabled === false;
    const busy = Boolean(data?.in_progress || (job && ['publishing', 'revoking'].includes(job.state)));
    const count = Number(collectionById(session.collectionId)?.image_count || publish?.image_count || 0);
    const slug = session.draft.slug || job?.slug || publish?.slug || slugifyName(session.name);
    const title = session.draft.title || job?.title || publish?.title || session.name;
    const url = data?.url || publish?.url || job?.url || '';
    const body = (setupNeeded ? '<div class="publish-setup"><b>Choose a publishing folder first.</b><p>Choose a folder for public galleries in System, then come back here to publish.</p><button class="btn primary" data-deliver-open-settings type="button">Open Publishing settings</button></div>' : '')
        + deliverTitleRow(session, title, busy || setupNeeded)
        + `<label class="deliver-slug-row">URL name<input data-deliver-slug value="${esc(slug)}" maxlength="96" ${busy || publish || setupNeeded ? 'disabled' : ''}>${publish ? '<small>URL is fixed after first publish.</small>' : ''}</label>`
        + `<p class="publish-confirm-copy"${setupNeeded ? ' hidden' : ''}>${esc(publishLeadText(count, slug, publish, publishing))}</p>`
        + deliverLinkRow(url)
        + deliverPublishStatus(data)
        + '<div class="publish-actions">'
        + (publish ? `<button data-deliver-unpublish type="button" class="btn-danger" ${busy ? 'disabled' : ''}>Unpublish</button>` : '')
        + `<button data-deliver-publish type="button" ${busy || setupNeeded ? 'disabled' : ''}>${esc(publish ? 'Republish to website' : 'Publish to website')}</button></div>`;
    renderDeliverShell(session.name, 'website', body);
    bindDeliverTabs((tab) => switchDeliverTab(session, tab));
    bindDeliverCommon(session, url);
    const slugInput = deliverOverlay.querySelector('[data-deliver-slug]');
    slugInput?.addEventListener('input', () => {
        saveDeliverDraft(session);
        deliverOverlay.querySelector('.publish-confirm-copy').textContent = publishLeadText(count, slugifyName(slugInput.value), publish, publishing);
    });
    deliverOverlay.querySelector('[data-deliver-open-settings]')?.addEventListener('click', () => {
        closeDeliverOverlay();
        document.getElementById('system-btn')?.click();
    });
    const startPublish = async () => {
        saveDeliverDraft(session);
        const result = await publishCollection(session.collectionId, { slug: slugifyName(session.draft.slug || slug), title: session.draft.title.trim() || title });
        if (!deliverOverlayIsCurrent(token)) return;
        if (result.ok) {
            showToast(publish ? 'Republishing gallery' : 'Publishing gallery');
            emitSharedSurfacesChanged(session.collectionId);
            pollDeliverPublish(session, token, true);
        } else {
            session.publish = { ...data, job: { state: 'error', error: result.data?.error || "Couldn't start publishing." } };
            renderDeliver(session, token);
        }
    };
    if (publish) bindDeliverConfirmButton('[data-deliver-publish]', 'Confirm republish', startPublish);
    else deliverOverlay.querySelector('[data-deliver-publish]')?.addEventListener('click', startPublish);
    deliverOverlay.querySelector('[data-deliver-publish-retry]')?.addEventListener('click', startPublish);
    deliverOverlay.querySelector('[data-deliver-unpublish]')?.addEventListener('click', async () => {
        const ok = await confirmTypedCount({ title: 'Unpublish gallery', message: `Remove this public gallery from the website? Type ${fmt(count).replace(/,/g, '')} to confirm.`, count, confirmLabel: 'Unpublish' });
        if (!ok) return;
        const result = await revokeCollectionPublish(session.collectionId);
        if (deliverOverlayIsCurrent(token) && result.ok) {
            showToast('Unpublishing gallery');
            emitSharedSurfacesChanged(session.collectionId);
            pollDeliverPublish(session, token, true);
        }
    });
    if (busy) scheduleDeliverPoll(session, token);
    trapFocus(deliverOverlay, deliverOverlay.querySelector('input, button'));
}

async function renderGalleryDeliver(session, token) {
    renderDeliverShell(session.name, 'gallery', '<div class="muted">Client gallery settings are loading…</div>');
    bindDeliverTabs((tab) => switchDeliverTab(session, tab));
    trapFocus(deliverOverlay, deliverOverlay.querySelector('button'));
    try {
        const galleryEditor = await import('./gallery_editor.js');
        const galleryData = await galleryEditor.loadGalleryDelivery(session.collectionId, getCollection);
        if (!deliverOverlayIsCurrent(token) || deliverSession !== session || session.activeTab !== 'gallery') return;
        session.galleryEditor = galleryEditor;
        session.galleryData = galleryData;
        const gallery = galleryData.gallery;
        const body = deliverTitleRow(session, session.draft.title || gallery?.title || session.name)
            + galleryEditor.galleryDeliveryFields(gallery, galleryData.images)
            + deliverPasswordRow({ protected: Boolean(gallery?.protected), value: session.draft.password, action: gallery ? (gallery.protected ? 'Change' : 'Set') : '', clear: Boolean(gallery?.protected) })
            + deliverLinkRow(gallery?.url || '')
            + '<div class="publish-actions">'
            + (gallery ? '<button data-deliver-gallery-revoke type="button" class="btn-danger">Revoke</button>' : '')
            + `<button data-deliver-gallery-save type="button">${gallery ? 'Update client gallery' : 'Create client gallery'}</button></div>`;
        renderDeliverShell(session.name, 'gallery', body);
        bindDeliverTabs((tab) => switchDeliverTab(session, tab));
        bindDeliverCommon(session, gallery?.url || '');
        deliverOverlay.querySelector('[data-deliver-gallery-save]')?.addEventListener('click', (event) => withBusyButton(event.currentTarget, async () => {
            saveDeliverDraft(session);
            try {
                const result = await galleryEditor.saveGalleryDelivery(session.collectionId, gallery, galleryEditor.galleryDeliveryPayload(deliverOverlay, {
                    title: session.draft.title || session.name,
                    password: session.draft.password,
                }));
                if (!deliverOverlayIsCurrent(token)) return;
                session.galleryData.gallery = result.gallery;
                session.draft.password = '';
                showToast(gallery ? 'Client gallery updated' : 'Client gallery created');
                emitSharedSurfacesChanged(session.collectionId);
                renderGalleryDeliver(session, token);
            } catch (error) {
                showToast(error.message || 'Could not save client gallery');
            }
        }));
        deliverOverlay.querySelector('[data-deliver-password-save]')?.addEventListener('click', () => deliverOverlay.querySelector('[data-deliver-gallery-save]')?.click());
        deliverOverlay.querySelector('[data-deliver-password-clear]')?.addEventListener('click', (event) => withBusyButton(event.currentTarget, async () => {
            try {
                const result = await galleryEditor.saveGalleryDelivery(session.collectionId, gallery, galleryEditor.galleryDeliveryPayload(deliverOverlay, {
                    title: session.draft.title || session.name,
                    clearPassword: true,
                }));
                if (!deliverOverlayIsCurrent(token)) return;
                session.galleryData.gallery = result.gallery;
                showToast('Password removed');
                renderGalleryDeliver(session, token);
            } catch (error) {
                showToast(error.message || 'Could not remove password');
            }
        }));
        bindDeliverConfirmButton('[data-deliver-gallery-revoke]', 'Confirm revoke', async () => {
            try {
                await galleryEditor.revokeGalleryDelivery(session.collectionId, gallery.id);
                if (!deliverOverlayIsCurrent(token)) return;
                session.galleryData.gallery = null;
                showToast('Client gallery revoked');
                emitSharedSurfacesChanged(session.collectionId);
                renderGalleryDeliver(session, token);
            } catch (error) {
                showToast(error.message || 'Could not revoke client gallery');
            }
        });
        trapFocus(deliverOverlay, deliverOverlay.querySelector('input, select, button'));
    } catch (error) {
        if (!deliverOverlayIsCurrent(token) || deliverSession !== session || session.activeTab !== 'gallery') return;
        renderDeliverShell(session.name, 'gallery', `<div class="publish-error"><b>Couldn’t load client gallery settings.</b><p>${esc(error.message || 'Try again when ready.')}</p><button data-deliver-gallery-retry type="button">Try again</button></div>`);
        bindDeliverTabs((tab) => switchDeliverTab(session, tab));
        deliverOverlay.querySelector('[data-deliver-gallery-retry]')?.addEventListener('click', () => renderGalleryDeliver(session, token));
        trapFocus(deliverOverlay, deliverOverlay.querySelector('button'));
    }
}

async function renderDeliver(session, token = deliverOverlayToken) {
    if (!deliverOverlayIsCurrent(token) || deliverSession !== session) return;
    if (session.activeTab === 'private') return renderPrivateDeliver(session, token);
    if (session.activeTab === 'website') return renderWebsiteDeliver(session, token);
    return renderGalleryDeliver(session, token);
}

function switchDeliverTab(session, tab) {
    if (!DELIVER_TABS.some(([id]) => id === tab)) return;
    saveDeliverDraft(session);
    session.activeTab = tab;
    renderDeliver(session);
}

function scheduleDeliverPoll(session, token) {
    window.clearTimeout(deliverPollTimer);
    deliverPollTimer = window.setTimeout(() => pollDeliverPublish(session, token), 2500);
}

async function pollDeliverPublish(session, token, immediate = false) {
    window.clearTimeout(deliverPollTimer);
    if (!deliverOverlayIsCurrent(token) || deliverSession !== session) return;
    session.publish = await getCollectionPublish(session.collectionId);
    if (!deliverOverlayIsCurrent(token) || deliverSession !== session) return;
    if (session.publish?.in_progress) scheduleDeliverPoll(session, token);
    else {
        if (!immediate && session.publish?.job?.state === 'revoked') showToast('Gallery unpublished');
        emitSharedSurfacesChanged(session.collectionId);
        loadCollections();
    }
    if (session.activeTab === 'website') renderDeliver(session, token);
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
    if (sourcesLoadError) {
        host.innerHTML = emptyState('hard-drive', "Couldn't load sources.", '<button type="button" data-retry-sources>Retry</button>');
        host.querySelector('[data-retry-sources]')?.addEventListener('click', loadCatalogChrome);
        return;
    }
    host.innerHTML = sources.length ? sources.map((s) => {
        const online = Number(s.online) === 1;
        const count = s.active_image_count != null ? s.active_image_count : s.image_count;
        const label = s.display_name || s.path;
        return `<button class="nav-row" data-source="${esc(s.path)}" data-source-id="${Number(s.id) || 0}" title="${esc(label)}">`
            + `<span class="nr-dot ${online ? 'on' : 'off'}"></span><span class="nr-label" title="${esc(label)}">${esc(label)}</span>`
            + `<span class="nr-count">${fmt(count)}</span>${online ? '' : '<span class="nr-tag">offline</span>'}</button>`;
    }).join('') : emptyState('hard-drive', 'No sources yet.', '<button type="button" data-add-source>Add a source</button>');
    host.querySelector('[data-add-source]')?.addEventListener('click', () => document.getElementById('system-btn')?.click());
    for (const row of host.querySelectorAll('[data-source]')) {
        row.addEventListener('click', () => {
            navigateToScope({ folder: [row.dataset.source] });
            closeLeftDrawer();
        });
        row.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            const source = sources.find((item) => item.path === row.dataset.source);
            const count = source?.active_image_count != null ? source.active_image_count : source?.image_count;
            openSourceRevealMenu(row.dataset.source, row, count, {
                sourceId: source?.id,
                revealAvailable: source ? !String(source.path || '').toLowerCase().startsWith('hub:') : true,
            });
        });
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
    sourcesLoading = true;
    sourcesLoadError = false;
    renderSources();
    try {
        catalog = await getCatalog();
    } catch {
        catalog = null;
        sourcesLoadError = true;
    } finally {
        sourcesLoading = false;
        renderSources();
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
    if (size) params.set('size', size);
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        params.set('sort', 'elo');
        params.set('limit', String(viewState.bestOfLimit));
    }
    let count = viewState.bestOf && viewState.bestOfLimit != null ? viewState.bestOfLimit : viewState.visibleImages;
    if (scope.collectionId) {
        const ids = await loadCollectionImageIds(scope.collectionId);
        if (!ids.length) {
            showToast('This collection has no photos');
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

export function requestShareCurrentCollection() {
    if (!scope.collectionId) {
        showToast('Open a collection first');
        return;
    }
    openDeliverOverlay(scope.collectionId, scope.collectionName || 'Collection');
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
    document.getElementById('shared-view')?.addEventListener('click', () => setActiveLens('shared'));
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
    on('scope', () => {
        renderNewCollectionForm();
        for (const row of document.querySelectorAll('[data-source]')) row.classList.toggle('active', folderActive(row.dataset.source));
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
    renderSources();
    loadLibraryCounts();
    await loadCollections();
    await loadSavedViews();
    await loadCatalogChrome();
    await initFoldersPanel({ closeDrawer: closeLeftDrawer });
    setTimeout(loadSuggestionsOnce, 0);
}
