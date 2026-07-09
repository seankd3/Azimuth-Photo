import { getCollectionShareFavorites, listSharedSurfaces } from './api.js';
import { setActiveLens } from './state.js';
import { openPublishOverlay, openShareOverlay, viewClientPicks } from './panel.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

let root = null;
let loading = false;
let loadError = false;
let items = [];

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function dateLine(value, fallback = 'Not opened') {
    const raw = Number(value || 0);
    if (!raw) return fallback;
    const date = new Date(raw * 1000);
    if (Number.isNaN(date.getTime())) return fallback;
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function relLine(value, fallback = 'never') {
    const raw = Number(value || 0);
    if (!raw) return fallback;
    const date = new Date(raw * 1000);
    if (Number.isNaN(date.getTime())) return fallback;
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
        if (Math.abs(diffSeconds) >= seconds) return formatter.format(Math.round(diffSeconds / seconds), unit);
    }
    return formatter.format(diffSeconds, 'second');
}

function actionButton(action, label, iconName, disabled = false) {
    return `<button class="mini-btn" data-shared-action="${esc(action)}" ${disabled ? 'disabled aria-disabled="true"' : ''}>${icon(iconName)}<span>${esc(label)}</span></button>`;
}

function coverHtml(item) {
    if (item.cover_thumb_url) {
        return `<figure class="shared-cover"><img src="${esc(item.cover_thumb_url)}" alt="" loading="lazy" decoding="async"></figure>`;
    }
    return `<figure class="shared-cover empty">${icon('images')}</figure>`;
}

function privateBlock(item) {
    const share = item.private_link;
    if (!share) {
        return '<section class="shared-surface quiet">'
            + '<div class="shared-surface-head"><b>Private link</b></div>'
            + '<p>Not shared privately.</p>'
            + '<div class="shared-actions">'
            + actionButton('share-manage', 'Share privately', 'share-2')
            + '</div></section>';
    }
    const opened = Number(share.view_count || 0);
    return '<section class="shared-surface">'
        + '<div class="shared-surface-head"><b>Private link</b>'
        + (share.protected ? `<span class="shared-badge">${icon('lock')} Protected</span>` : '<span class="shared-badge neutral">Link</span>')
        + '</div>'
        + `<p>${fmt(opened)} view${opened === 1 ? '' : 's'} · last opened ${esc(relLine(share.last_viewed_at))}</p>`
        + `<p>${fmt(share.pick_count || 0)} client pick${Number(share.pick_count || 0) === 1 ? '' : 's'}</p>`
        + `<code title="${esc(share.url || '')}">${esc(share.url || 'No link')}</code>`
        + '<div class="shared-actions">'
        + actionButton('share-copy', 'Copy', 'copy', !share.url)
        + actionButton('share-open', 'Open', 'external-link', !share.url)
        + actionButton('share-manage', 'Manage', 'sliders-horizontal')
        + actionButton('share-picks', 'View picks', 'heart', !Number(share.pick_count || 0))
        + '</div></section>';
}

function websiteBlock(item) {
    const website = item.website;
    if (!website) {
        return '<section class="shared-surface quiet">'
            + '<div class="shared-surface-head"><b>Website</b></div>'
            + '<p>Not published to the website.</p>'
            + '<div class="shared-actions">'
            + actionButton('publish-manage', 'Publish to website', 'globe')
            + '</div></section>';
    }
    const hook = website.hook_status || {};
    return '<section class="shared-surface">'
        + '<div class="shared-surface-head"><b>Website</b>'
        + `<span class="shared-badge ${hook.configured && !hook.ok ? 'bad' : ''}">${hook.configured && !hook.ok ? 'Hook failed' : 'Published'}</span>`
        + '</div>'
        + `<p>Published ${esc(dateLine(website.published_at, 'Unknown'))} · updated ${esc(relLine(website.updated_at, 'never'))}</p>`
        + (hook.configured && !hook.ok ? `<p class="shared-error">Published locally, hook failed${hook.output ? ` · ${esc(hook.output.split('\n').slice(-1)[0])}` : ''}</p>` : '')
        + `<code title="${esc(website.url || '')}">${esc(website.url || 'Base URL not set')}</code>`
        + '<div class="shared-actions">'
        + actionButton('publish-open', 'Open', 'external-link', !website.url)
        + actionButton('publish-copy', 'Copy', 'copy', !website.url)
        + actionButton('publish-manage', 'Manage', 'sliders-horizontal')
        + '</div></section>';
}

function rowHtml(item) {
    return `<article class="shared-row" data-collection-id="${Number(item.collection_id) || 0}" data-name="${esc(item.name || 'Collection')}">`
        + coverHtml(item)
        + '<div class="shared-main">'
        + '<header class="shared-row-head">'
        + `<div><b title="${esc(item.name || 'Collection')}">${esc(item.name || 'Collection')}</b><span>${fmt(item.photo_count || 0)} photos</span></div>`
        + '</header>'
        + '<div class="shared-surfaces">'
        + privateBlock(item)
        + websiteBlock(item)
        + '</div></div></article>';
}

function render() {
    if (!root) return;
    if (loading) {
        root.innerHTML = '<div class="shared-shell"><header class="canvas-head"><div><b>Shared</b><span>Loading outbound collections</span></div><button class="icon-btn" id="shared-close" aria-label="Return to Grid">' + icon('x') + '</button></header><div class="shared-list"><div class="shared-skel"></div><div class="shared-skel"></div></div></div>';
    } else if (loadError) {
        root.innerHTML = '<div class="shared-shell"><header class="canvas-head"><div><b>Shared</b><span>Outbound collection surfaces</span></div><button class="icon-btn" id="shared-close" aria-label="Return to Grid">' + icon('x') + '</button></header><div class="load-error"><h4>Couldn\'t load Shared</h4><p>The archive did not respond. Try again.</p><button class="btn" id="shared-retry">Try again</button></div></div>';
    } else if (!items.length) {
        root.innerHTML = '<div class="shared-shell"><header class="canvas-head"><div><b>Shared</b><span>Outbound collection surfaces</span></div><button class="icon-btn" id="shared-close" aria-label="Return to Grid">' + icon('x') + '</button></header><div class="shared-empty"><h3>No shared collections yet</h3><p>Private links make a protected gallery for a client or friend. Website publishing writes a static gallery bundle to your site folder and can run your hook.</p></div></div>';
    } else {
        root.innerHTML = '<div class="shared-shell"><header class="canvas-head"><div><b>Shared</b><span>Private links and website galleries</span></div><button class="icon-btn" id="shared-close" aria-label="Return to Grid">' + icon('x') + '</button></header><div class="shared-list">'
            + items.map(rowHtml).join('')
            + '</div></div>';
    }
    bind();
}

async function copyText(value, label) {
    try {
        await navigator.clipboard.writeText(value || '');
        showToast(`${label} copied`);
    } catch {
        showToast('Copy failed');
    }
}

async function handleAction(button) {
    if (button.disabled || button.getAttribute('aria-disabled') === 'true') return;
    const row = button.closest('.shared-row');
    if (!row) return;
    const collectionId = Number(row.dataset.collectionId || 0);
    const name = row.dataset.name || 'Collection';
    const item = items.find((candidate) => Number(candidate.collection_id) === collectionId) || {};
    const action = button.dataset.sharedAction;
    if (action === 'share-manage') openShareOverlay(collectionId, name);
    if (action === 'publish-manage') openPublishOverlay(collectionId, name);
    if (action === 'share-open') window.open(item.private_link?.url || '', '_blank', 'noopener');
    if (action === 'publish-open') window.open(item.website?.url || '', '_blank', 'noopener');
    if (action === 'share-copy') copyText(item.private_link?.url || '', 'Private link');
    if (action === 'publish-copy') copyText(item.website?.url || '', 'Website URL');
    if (action === 'share-picks') {
        const data = await getCollectionShareFavorites(collectionId);
        if (!Number(data?.count || 0)) {
            showToast('No client picks yet');
            return;
        }
        await viewClientPicks(collectionId);
    }
}

function bind() {
    root.querySelector('#shared-close')?.addEventListener('click', () => setActiveLens('grid'));
    root.querySelector('#shared-retry')?.addEventListener('click', load);
    for (const button of root.querySelectorAll('[data-shared-action]')) {
        button.addEventListener('click', () => handleAction(button));
    }
}

async function load() {
    loading = true;
    loadError = false;
    render();
    try {
        const data = await listSharedSurfaces();
        items = Array.isArray(data?.items) ? data.items : [];
    } catch {
        loadError = true;
    } finally {
        loading = false;
        render();
    }
}

export function mountShared() {
    root = document.getElementById('view-shared');
    root.classList.add('active');
    load();
}

export function unmountShared() {
    if (!root) return;
    root.classList.remove('active');
    root.innerHTML = '';
    root = null;
}
