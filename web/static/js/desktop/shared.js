import { getCollectionShareFavorites, listSharedSurfaces } from './api.js';
import { setActiveLens } from './state.js';
import { openLeftDrawer, openPublishOverlay, openShareOverlay, viewClientPicks } from './panel.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

let root = null;
let loading = false;
let loadError = false;
let items = [];
let loadGeneration = 0;
let query = '';
let statusFilter = 'all';

const SHARED_CHANGED_EVENT = 'shares/publishes-changed';
const EXPIRING_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

const FILTERS = [
    { id: 'all', label: 'All' },
    { id: 'attention', label: 'Needs attention' },
    { id: 'picks', label: 'Picks waiting' },
    { id: 'unprotected', label: 'Unprotected' },
    { id: 'website', label: 'Website' },
    { id: 'private', label: 'Private' },
];

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

function expiryState(expiresAt) {
    const raw = Number(expiresAt || 0);
    if (!raw) return null;
    const ms = raw * 1000 - Date.now();
    if (Number.isNaN(ms)) return null;
    if (ms <= 0) return { kind: 'expired', label: 'Expired', tone: 'bad' };
    if (ms <= EXPIRING_WINDOW_MS) {
        const days = Math.max(1, Math.ceil(ms / 86400000));
        return {
            kind: 'expiring',
            label: days === 1 ? 'Expires in 1 day' : `Expires in ${days} days`,
            tone: 'warn',
        };
    }
    const days = Math.ceil(ms / 86400000);
    return {
        kind: 'ok',
        label: days === 1 ? 'Expires in 1 day' : `Expires in ${days} days`,
        tone: '',
    };
}

function hookFailed(item) {
    const hook = item.website?.hook_status || {};
    return Boolean(item.website && hook.configured && !hook.ok);
}

function isUnprotected(item) {
    return Boolean(item.private_link && !item.private_link.protected);
}

function hasPicks(item) {
    return Number(item.private_link?.pick_count || 0) > 0;
}

function needsAttention(item) {
    const expiry = expiryState(item.private_link?.expires_at);
    return hookFailed(item) || expiry?.kind === 'expired' || expiry?.kind === 'expiring';
}

function itemFlags(item) {
    return {
        attention: needsAttention(item),
        picks: hasPicks(item),
        unprotected: isUnprotected(item),
        website: Boolean(item.website),
        private: Boolean(item.private_link),
    };
}

function matchesFilter(item, filter = statusFilter) {
    if (filter === 'all') return true;
    const flags = itemFlags(item);
    return Boolean(flags[filter]);
}

function matchesQuery(item, text = query) {
    const needle = String(text || '').trim().toLowerCase();
    if (!needle) return true;
    return String(item.name || '').toLowerCase().includes(needle);
}

function filteredItems() {
    return items.filter((item) => matchesQuery(item) && matchesFilter(item));
}

function summaryCounts() {
    const counts = {
        all: items.length,
        attention: 0,
        picks: 0,
        unprotected: 0,
        website: 0,
        private: 0,
    };
    for (const item of items) {
        const flags = itemFlags(item);
        if (flags.attention) counts.attention += 1;
        if (flags.picks) counts.picks += 1;
        if (flags.unprotected) counts.unprotected += 1;
        if (flags.website) counts.website += 1;
        if (flags.private) counts.private += 1;
    }
    return counts;
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
    const expiry = expiryState(share.expires_at);
    const protectedBadge = share.protected
        ? `<span class="shared-badge">${icon('lock')} Protected</span>`
        : '<span class="shared-risk">No password</span>';
    return '<section class="shared-surface">'
        + '<div class="shared-surface-head"><b>Private link</b>'
        + protectedBadge
        + '</div>'
        + `<p>${fmt(opened)} view${opened === 1 ? '' : 's'} · last opened ${esc(relLine(share.last_viewed_at))}</p>`
        + `<p>${fmt(share.pick_count || 0)} client pick${Number(share.pick_count || 0) === 1 ? '' : 's'}</p>`
        + (expiry ? `<p class="shared-expiry${expiry.tone ? ` ${expiry.tone}` : ''}">${esc(expiry.label)}</p>` : '')
        + `<code title="${esc(share.url || '')}">${esc(share.url || 'No link')}</code>`
        + '<div class="shared-actions">'
        + actionButton('share-copy', 'Copy', 'copy', !share.url)
        + actionButton('share-open', 'Open', 'external-link', !share.url)
        + actionButton('share-manage', 'Manage', 'sliders-horizontal')
        + actionButton('share-picks', 'View picks', 'heart', !Number(share.pick_count || 0))
        + '</div></section>';
}

function websiteBadge(website) {
    const hook = website.hook_status || {};
    if (hook.configured && !hook.ok) {
        return '<span class="shared-badge bad">Hook failed</span>';
    }
    if (!website.url) {
        return '<span class="shared-badge neutral">Published locally</span>';
    }
    return '<span class="shared-badge">Published</span>';
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
        + websiteBadge(website)
        + '</div>'
        + `<p>Published ${esc(dateLine(website.published_at, 'Unknown'))} · updated ${esc(relLine(website.updated_at, 'never'))}</p>`
        + (hook.configured && !hook.ok ? `<p class="shared-error">Published locally, hook failed${hook.output ? ` · ${esc(hook.output.split('\n').slice(-1)[0])}` : ''}</p>` : '')
        + (!website.url && !(hook.configured && !hook.ok) ? '<p class="shared-meta">Local bundle ready · set Site base URL for a live link</p>' : '')
        + `<code title="${esc(website.url || '')}">${esc(website.url || 'Base URL not set')}</code>`
        + '<div class="shared-actions">'
        + actionButton('publish-open', 'Open', 'external-link', !website.url)
        + actionButton('publish-copy', 'Copy', 'copy', !website.url)
        + actionButton('publish-manage', 'Manage', 'sliders-horizontal')
        + '</div></section>';
}

function rowHtml(item) {
    const flags = itemFlags(item);
    const attention = flags.attention ? ' attention' : '';
    return `<article class="shared-row${attention}" data-collection-id="${Number(item.collection_id) || 0}" data-name="${esc(item.name || 'Collection')}">`
        + coverHtml(item)
        + '<div class="shared-main">'
        + '<header class="shared-row-head">'
        + `<div><b title="${esc(item.name || 'Collection')}">${esc(item.name || 'Collection')}</b><span>${fmt(item.photo_count || 0)} photos</span></div>`
        + (item.private_link?.url ? actionButton('share-copy', 'Copy private link', 'copy') : '')
        + '</header>'
        + '<div class="shared-surfaces">'
        + privateBlock(item)
        + websiteBlock(item)
        + '</div></div></article>';
}

function triageHtml(counts) {
    const chips = FILTERS.map((filter) => {
        const count = counts[filter.id] ?? 0;
        const active = statusFilter === filter.id ? ' active' : '';
        return `<button type="button" class="shared-chip${active}" data-shared-filter="${esc(filter.id)}">${esc(filter.label)}<em>${fmt(count)}</em></button>`;
    }).join('');
    return '<div class="shared-triage">'
        + `<label class="shared-find"><span class="sr-only">Find by name</span><input id="shared-find" type="search" value="${esc(query)}" placeholder="Find by name" autocomplete="off" spellcheck="false"></label>`
        + `<div class="shared-chips" role="toolbar" aria-label="Shared status filters">${chips}</div>`
        + '</div>';
}

function summaryHtml(counts, visible) {
    const parts = [
        `${fmt(visible)} shown`,
        counts.attention ? `${fmt(counts.attention)} need attention` : null,
        counts.picks ? `${fmt(counts.picks)} with picks` : null,
        counts.unprotected ? `${fmt(counts.unprotected)} unprotected` : null,
    ].filter(Boolean);
    return `<div class="shared-summary" aria-live="polite">${esc(parts.join(' · '))}</div>`;
}

function shellHead(subhead) {
    return `<header class="canvas-head"><div><b>Shared</b><span>${esc(subhead)}</span></div><button class="icon-btn" id="shared-close" aria-label="Return to Grid">${icon('x')}</button></header>`;
}

function emptyBoardHtml() {
    return '<div class="shared-empty">'
        + '<h3>No shared collections yet</h3>'
        + '<p>Private links make a gallery for a client or friend. Website publishing writes a static gallery to your site folder.</p>'
        + '<div class="shared-empty-actions">'
        + '<button class="btn primary" id="shared-open-library" type="button">Open a collection</button>'
        + '</div></div>';
}

function emptyFilterHtml() {
    return '<div class="shared-empty compact">'
        + '<h3>No matches</h3>'
        + '<p>Try another name or clear the status filter.</p>'
        + '<div class="shared-empty-actions">'
        + '<button class="btn" id="shared-clear-filters" type="button">Clear filters</button>'
        + '</div></div>';
}

function render() {
    if (!root) return;
    if (loading) {
        root.innerHTML = `<div class="shared-shell">${shellHead('Loading private links and website galleries')}<div class="shared-list"><div class="shared-skel"></div><div class="shared-skel"></div></div></div>`;
    } else if (loadError) {
        root.innerHTML = `<div class="shared-shell">${shellHead('Private links and website galleries')}<div class="load-error"><h4>Couldn't load Shared</h4><p>The archive did not respond. Try again.</p><button class="btn" id="shared-retry">Try again</button></div></div>`;
    } else if (!items.length) {
        root.innerHTML = `<div class="shared-shell">${shellHead('Private links and website galleries')}${emptyBoardHtml()}</div>`;
    } else {
        const counts = summaryCounts();
        const visible = filteredItems();
        root.innerHTML = `<div class="shared-shell">${shellHead('Private links and website galleries')}`
            + '<div class="shared-board">'
            + triageHtml(counts)
            + summaryHtml(counts, visible.length)
            + '<div class="shared-list">'
            + (visible.length ? visible.map(rowHtml).join('') : emptyFilterHtml())
            + '</div></div></div>';
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
        let data = null;
        try {
            data = await getCollectionShareFavorites(collectionId);
        } catch {
            showToast("Couldn't load client picks");
            return;
        }
        if (!Number(data?.count || 0)) {
            showToast('No client picks yet');
            return;
        }
        await viewClientPicks(collectionId);
    }
}

function restoreFindFocus(hadFocus, selectionStart, selectionEnd) {
    const input = root?.querySelector('#shared-find');
    if (!input || !hadFocus) return;
    input.focus();
    if (typeof selectionStart === 'number' && typeof selectionEnd === 'number') {
        try {
            input.setSelectionRange(selectionStart, selectionEnd);
        } catch {
            /* ignore unsupported selection ranges */
        }
    }
}

function bind() {
    root.querySelector('#shared-close')?.addEventListener('click', () => setActiveLens('grid'));
    root.querySelector('#shared-retry')?.addEventListener('click', load);
    root.querySelector('#shared-open-library')?.addEventListener('click', () => {
        setActiveLens('grid');
        openLeftDrawer();
        showToast('Open a collection, then Share or Publish');
    });
    root.querySelector('#shared-clear-filters')?.addEventListener('click', () => {
        query = '';
        statusFilter = 'all';
        render();
    });
    const find = root.querySelector('#shared-find');
    find?.addEventListener('input', () => {
        query = find.value;
        const start = find.selectionStart;
        const end = find.selectionEnd;
        render();
        restoreFindFocus(true, start, end);
    });
    for (const chip of root.querySelectorAll('[data-shared-filter]')) {
        chip.addEventListener('click', () => {
            statusFilter = chip.dataset.sharedFilter || 'all';
            render();
        });
    }
    for (const button of root.querySelectorAll('[data-shared-action]')) {
        button.addEventListener('click', () => handleAction(button));
    }
}

function handleSharedChanged() {
    if (!root) return;
    load({ showLoading: !items.length });
}

async function load({ showLoading = true } = {}) {
    const generation = ++loadGeneration;
    loading = Boolean(showLoading);
    loadError = false;
    render();
    try {
        const data = await listSharedSurfaces();
        if (generation !== loadGeneration || !root) return;
        items = Array.isArray(data?.items) ? data.items : [];
    } catch {
        if (generation !== loadGeneration || !root) return;
        loadError = true;
    } finally {
        if (generation !== loadGeneration || !root) return;
        loading = false;
        render();
    }
}

export function mountShared() {
    root = document.getElementById('view-shared');
    root.classList.add('active');
    window.addEventListener(SHARED_CHANGED_EVENT, handleSharedChanged);
    load();
}

export function unmountShared() {
    if (!root) return;
    loadGeneration += 1;
    window.removeEventListener(SHARED_CHANGED_EVENT, handleSharedChanged);
    root.classList.remove('active');
    root.innerHTML = '';
    root = null;
    query = '';
    statusFilter = 'all';
}
