import {
    byId, clearFacet, clearSelection, emit, nonSearchFacetCount, on, scope, scopeActive, selection, setBestOfTotal, setImages, setRankingsMeta, setScope, viewState,
} from './state.js';
import { createStack, getCatalog, getScanStatus, getStack, thumbUrl, unstack } from './api.js';
import { loadScopePage } from './scope_data.js';
import {
    enterSelection, isSelectionMode, toggleSelection,
} from './selection.js';
import { openGridContextMenu } from './context_menu.js';
import { icon } from '../icons.js';
import {
    appendChunk, configureGridWindow, ensureChunkLive, firstLiveChunk, invalidateHeights, prependChunk, reset as resetGridWindow,
} from './grid_window.js';
import { afterMotion } from './motion.js';
import { showToast } from './toast.js';
import { keepCoverRejectRest } from './stack_cull.js';
import { emptyStateHtml } from './empty_state.js';
import { gridLoadingHtml } from './loading_state.js';
import { escapeHtml as esc } from './dom.js';

let offset = 0;
let loading = false;
let done = false;
let generation = 0;
let imageObserver = null;
let sentinelObserver = null;
let prependObserver = null;
let mounted = false;
let initialized = false;
let resizeHandler = null;
let resizeTimer = null;
let lastThumbSize = viewState.thumbSize;
let previousFocusedCell = null;
let savedScrollTop = 0;
let expandedStack = null;
let stackExpansionRequest = 0;
let creatingStack = false;
let emptyStateRequest = 0;
let emptyScanTimer = 0;
let windowStart = 0;
let windowEnd = 0;
let beforeDone = true;
let loadToken = 0;
let loadController = null;
let activeJumpToken = 0;
let reloadPending = false;
let thumbRetryFocusBound = false;
const stackCache = new Map();
const stackKindCache = new Map();

function cancelPendingLoad() {
    if (!loadController) return;
    loadController.abort();
    loadController = null;
}

function aspect(img) {
    const ar = Number(img.aspect_ratio) || (Number(img.width) && Number(img.height) ? Number(img.width) / Number(img.height) : 1.5);
    return Math.max(.45, Math.min(3.8, ar));
}

function flagGlyph(flag) {
    if (flag === 'picked') return icon('star');
    if (flag === 'rejected') return icon('x');
    return '';
}

export function cellHtml(img, index) {
    const flag = img.flag || 'unflagged';
    const stackId = Number(img.stack_id) || 0;
    const stackCount = Number(img.stack_count) || 0;
    const versionStack = img.stack_kind === 'version';
    const stackLabel = versionStack ? `RAW+${Math.max(1, stackCount - 1)}` : String(stackCount);
    const stackTip = versionStack ? `Version stack · RAW plus ${Math.max(1, stackCount - 1)} edit${stackCount === 2 ? '' : 's'} · S` : 'Expand stack · S';
    const stackBadge = stackId && stackCount > 1
        ? `<button class="c-stack ${versionStack ? 'version-stack' : ''}" data-stack-id="${stackId}" data-tip="${stackTip}" aria-label="Expand ${versionStack ? 'version' : 'stack'} with ${stackCount} photos" aria-expanded="false" tabindex="-1">${icon('layers')}<span>${stackLabel}</span></button>`
        : '';
    const selected = selection.has(Number(img.id));
    return `<figure class="cell ${img.thumb_url ? '' : 'skel'} ${selected ? 'sel' : ''}" data-id="${img.id}" data-idx="${index}" draggable="true" tabindex="-1" aria-selected="${selected ? 'true' : 'false'}" style="--ar:${aspect(img)}">`
        + `<img data-src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" loading="lazy" decoding="async" fetchpriority="low" alt="${esc(img.filename || '')}">`
        + `<span class="c-thumb-offline" aria-live="polite">${icon('image')}<span>${esc(img.filename || 'Original offline')}</span></span>`
        + stackBadge
        + `<button class="c-check" aria-label="Select photo" tabindex="-1">${icon('check')}</button>`
        + `<span class="c-idx">${index + 1}</span>`
        + `<span class="c-flag ${flag}">${flagGlyph(flag)}</span>`
        + `<span class="c-elo"><span class="elo-chip">${Math.round(Number(img.elo) || 0)}</span></span>`
        + `<button class="c-menu" data-tip="Photo actions" aria-label="Photo actions" tabindex="-1">${icon('ellipsis')}</button></figure>`;
}

function applyStackBadgeKind(imageId, stack) {
    const cell = document.querySelector(`.cell[data-id="${Number(imageId)}"]`);
    const badge = cell?.querySelector('.c-stack');
    if (!badge || stack?.stack_kind !== 'version') return;
    const count = Number(stack.stack_count) || 0;
    const edits = Math.max(1, count - 1);
    badge.classList.add('version-stack');
    badge.querySelector('span').textContent = `RAW+${edits}`;
    badge.dataset.tip = `Version stack · RAW plus ${edits} edit${edits === 1 ? '' : 's'} · S`;
    badge.setAttribute('aria-label', `Expand version stack with ${count} photos`);
}

async function hydrateStackBadgeKinds(images) {
    const candidates = (images || []).filter((image) => Number(image?.stack_id) && Number(image?.stack_count) > 1);
    const unknownIds = candidates
        .map((image) => Number(image.id))
        .filter((imageId) => !stackKindCache.has(imageId));
    if (unknownIds.length) {
        try {
            const response = await fetch(`/api/stacks/representatives?image_ids=${encodeURIComponent(unknownIds.join(','))}`);
            const payload = response.ok ? await response.json() : {};
            const representatives = payload?.representatives || {};
            for (const imageId of unknownIds) stackKindCache.set(imageId, representatives[String(imageId)] || null);
        } catch {
            for (const imageId of unknownIds) stackKindCache.set(imageId, null);
        }
    }
    for (const image of candidates) {
        const stack = stackKindCache.get(Number(image.id));
        if (!stack) continue;
        image.stack_kind = stack.stack_kind;
        image.stack_count = Number(stack.stack_count) || image.stack_count;
        applyStackBadgeKind(image.id, stack);
    }
}

function memberCellHtml(img, index) {
    return cellHtml({ ...img, stack_id: null, stack_count: null }, index)
        .replace('class="cell ', 'class="cell stack-member ')
        .replace('<figure ', '<figure data-stack-member="1" ');
}

function ensureImageObserver() {
    if (imageObserver) return imageObserver;
    imageObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            const img = entry.target;
            if (entry.isIntersecting) {
                if (!img.src) img.src = img.dataset.src;
            }
        }
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px' });
    return imageObserver;
}

function retryOfflineThumb(img) {
    if (!img?.isConnected || img.dataset.thumbRetried === '1') return;
    img.dataset.thumbRetried = '1';
    img.closest('.cell')?.classList.add('thumb-retrying');
    const joiner = img.dataset.src.includes('?') ? '&' : '?';
    img.src = `${img.dataset.src}${joiner}retry=${Date.now()}`;
}

function markThumbOffline(img) {
    const cell = img.closest('.cell');
    if (!cell) return;
    img.classList.remove('ld');
    cell.classList.remove('skel', 'thumb-retrying');
    cell.classList.add('thumb-offline');
    cell.setAttribute('aria-label', `${img.alt || 'Photo'} — original offline`);
    if (img.dataset.thumbRetryScheduled !== '1' && img.dataset.thumbRetried !== '1') {
        img.dataset.thumbRetryScheduled = '1';
        window.setTimeout(() => retryOfflineThumb(img), 30_000);
    }
}

function ensureThumbRetryFocusHandler() {
    if (thumbRetryFocusBound) return;
    thumbRetryFocusBound = true;
    window.addEventListener('focus', () => {
        document.querySelectorAll('.cell.thumb-offline img[data-src]').forEach(retryOfflineThumb);
    });
}

function observeImages(rootEl) {
    ensureThumbRetryFocusHandler();
    const observer = ensureImageObserver();
    for (const img of (rootEl || document).querySelectorAll('img[data-src]')) {
        if (img.dataset.thumbObserved === '1') {
            observer.observe(img);
            continue;
        }
        img.dataset.thumbObserved = '1';
        img.addEventListener('load', () => {
            img.classList.add('ld');
            img.closest('.cell')?.classList.remove('skel', 'thumb-offline', 'thumb-retrying');
        });
        img.addEventListener('error', () => markThumbOffline(img));
        observer.observe(img);
    }
}

function unobserveImages(rootEl) {
    if (!imageObserver || !rootEl) return;
    for (const img of rootEl.querySelectorAll('img[data-src]')) {
        imageObserver.unobserve(img);
    }
}

function warmMediumThumb(cell) {
    const id = Number(cell?.dataset.id);
    if (!id) return;
    const queue = window.requestIdleCallback || ((callback) => window.setTimeout(callback, 0));
    queue(() => {
        const preload = new Image();
        preload.fetchPriority = 'low';
        preload.src = thumbUrl('md', id);
    });
}

function resetImageObserver() {
    if (imageObserver) imageObserver.disconnect();
    imageObserver = null;
}

function closeExpandedStack() {
    if (!expandedStack) return false;
    const { stackId, trayEl } = expandedStack;
    if (trayEl?.isConnected) {
        unobserveImages(trayEl);
        trayEl.classList.add('closing');
        afterMotion('base', () => {
            if (trayEl.isConnected) trayEl.remove();
            invalidateHeights({ remeasureGhosts: true });
        });
    }
    const badge = document.querySelector(`.c-stack[data-stack-id="${stackId}"]`);
    if (badge) {
        badge.classList.remove('expanded');
        badge.setAttribute('aria-expanded', 'false');
        badge.setAttribute('aria-label', badge.getAttribute('aria-label')?.replace(/^Collapse/, 'Expand') || 'Expand stack');
    }
    expandedStack = null;
    invalidateHeights({ remeasureGhosts: true });
    return true;
}

async function expandStack(stackId, cell) {
    const id = Number(stackId) || 0;
    if (!id || !cell) return false;
    if (expandedStack?.stackId === id) {
        closeExpandedStack();
        return true;
    }
    closeExpandedStack();
    const request = ++stackExpansionRequest;
    const seq = generation;
    const cellId = Number(cell.dataset.id) || 0;
    const badge = cell.querySelector(`.c-stack[data-stack-id="${id}"]`);
    if (badge) {
        badge.classList.add('loading');
        badge.disabled = true;
    }
    let data = stackCache.get(id) || null;
    if (!data) {
        try {
            data = await getStack(id);
        } catch {
            if (badge?.isConnected) {
                badge.classList.remove('loading');
                badge.disabled = false;
            }
            showToast("Stack couldn't load");
            return false;
        }
    }
    if (data) stackCache.set(id, data);
    const current = mounted && request === stackExpansionRequest && seq === generation && cell.isConnected && Number(cell.dataset.id) === cellId;
    if (badge?.isConnected) {
        badge.classList.remove('loading');
        badge.disabled = false;
    }
    if (!current) return false;
    const members = (data && data.members || []).filter((img) => Number(img?.id) !== Number(cell.dataset.id));
    if (!members.length) {
        showToast('Couldn’t expand this stack');
        return false;
    }
    for (const member of members) {
        const image = { ...member, stack_id: id };
        byId.set(Number(image.id), image);
    }
    const index = Number(cell.dataset.idx) || 0;
    const tray = document.createElement('div');
    tray.className = 'stack-tray';
    tray.dataset.stackId = String(id);
    tray.innerHTML = '<div class="stack-tray-rail"></div><div class="stack-tray-main">'
        + `<div class="stack-tray-head"><span>${members.length.toLocaleString('en-US')} more in this stack</span><div class="stack-tray-actions"><button class="btn btn-danger" data-stack-reject-rest="${id}">Keep cover, reject rest</button><button class="icon-btn stack-tray-collapse" data-tip="Collapse stack" aria-label="Collapse stack">${icon('x')}</button></div></div>`
        + '<div class="stack-tray-cells">'
        + members.map((member) => memberCellHtml(member, index)).join('')
        + '</div></div>';
    cell.insertAdjacentElement('afterend', tray);
    observeImages(tray);
    if (badge) {
        badge.classList.add('expanded');
        badge.setAttribute('aria-expanded', 'true');
        badge.setAttribute('aria-label', `Collapse stack with ${Number(data.member_count || members.length + 1)} photos`);
        badge.setAttribute('data-tip', 'Collapse stack · S');
    }
    expandedStack = { stackId: id, trayEl: tray };
    invalidateHeights({ remeasureGhosts: true });
    return true;
}

function render({ append = false, start = 0, images = [] } = {}) {
    const flow = document.getElementById('grid-flow');
    if (!append) {
        stackExpansionRequest += 1;
        closeExpandedStack();
        resetImageObserver();
        resetGridWindow();
    }
    appendChunk(start, images);
    hydrateStackBadgeKinds(images);
    flow.classList.toggle('selmode', isSelectionMode());
    if (!append) setFocus(viewState.focusIndex);
}

function renderSkeletons() {
    stackExpansionRequest += 1;
    closeExpandedStack();
    resetImageObserver();
    resetGridWindow();
    document.getElementById('grid-flow').innerHTML = gridLoadingHtml();
}

function bindScopeEmptyActions(flow) {
    flow.querySelector('[data-empty-action="clear-query"]')?.addEventListener('click', () => clearFacet('q'));
    flow.querySelector('[data-empty-action="deep-search"]')?.addEventListener('click', () => {
        setScope({ q: scope.q, deep: true }, { merge: true });
    });
    flow.querySelector('[data-empty-action="clear-filters"]')?.addEventListener('click', () => {
        setScope({ q: scope.q, sort: scope.sort || 'elo' });
    });
    flow.querySelector('[data-empty-action="clear-view"]')?.addEventListener('click', () => setScope({}));
}

async function hydrateFirstRunEmpty(request) {
    if (scopeActive() || viewState.bestOf) return;
    const [catalog, scan] = await Promise.all([
        getCatalog().catch(() => null),
        getScanStatus().catch(() => null),
    ]);
    if (request !== emptyStateRequest || !mounted || viewState.images.length) return;
    const flow = document.getElementById('grid-flow');
    const sources = (catalog && catalog.sources) || [];
    const scanning = Boolean(scan && scan.scanning);
    const found = Number(scan && (scan.total_found || scan.total_inserted)) || 0;
    window.clearTimeout(emptyScanTimer);
    if (!sources.length) {
        flow.innerHTML = emptyStateHtml({
            title: 'Welcome to Azimuth Photo',
            detail: 'No source folders are connected yet. Add a folder or import photos to start your private library.',
            actions: [
                { label: 'Add a source', action: 'add-source', primary: true },
                { label: 'Import photos', action: 'import' },
            ],
            iconName: 'folder-plus',
        });
    } else if (scanning) {
        flow.innerHTML = emptyStateHtml({
            title: 'Scanning this source',
            detail: `This source is being indexed${found ? ` · ${found.toLocaleString('en-US')} photos found so far` : ''}. Photos will appear here as they are ready.`,
            actions: [{ label: 'View scan progress', action: 'add-source', primary: true }],
            iconName: 'loader',
        });
        emptyScanTimer = window.setTimeout(async () => {
            if (request !== emptyStateRequest || !mounted || viewState.images.length) return;
            const page = await loadScopePage({ limit: 1, offset: 0 });
            if (page && (page.images || []).length) loadFirstPage();
            else hydrateFirstRunEmpty(request);
        }, 1500);
    } else {
        flow.innerHTML = emptyStateHtml({
            title: 'No photos found',
            detail: 'Your source is connected, but it has no available photos. Check the source or start a rescan in System.',
            actions: [
                { label: 'Open Sources', action: 'add-source', primary: true },
                { label: 'Import photos', action: 'import' },
            ],
            iconName: 'folder',
        });
    }
    flow.querySelector('[data-empty-action="add-source"]')?.addEventListener('click', () => document.getElementById('system-btn')?.click());
    flow.querySelector('[data-empty-action="import"]')?.addEventListener('click', () => emit('import:open'));
}

function renderEmptyState() {
    stackExpansionRequest += 1;
    closeExpandedStack();
    resetImageObserver();
    resetGridWindow();
    const flow = document.getElementById('grid-flow');
    const showClearFilters = nonSearchFacetCount() > 0;
    const showClearScope = scopeActive() || viewState.bestOf;
    const request = ++emptyStateRequest;
    if (scope.q) {
        flow.innerHTML = emptyStateHtml({
            title: `No matches for “${scope.q}”`,
            detail: scope.deep ? 'Nothing in this library matches that search.' : 'Try another phrase, or use Deep search for a broader visual match.',
            actions: [
                ...(!scope.deep ? [{ label: 'Try Deep search', action: 'deep-search', primary: true }] : []),
                { label: 'Clear search', action: 'clear-query', primary: scope.deep },
            ],
            iconName: 'search',
        });
        bindScopeEmptyActions(flow);
        return;
    }
    const scopeDetail = scope.collectionId
        ? `“${scope.collectionName || 'This collection'}” has no photos yet. Add photos to this collection to see them here.`
        : scope.people
            ? `No photos are currently linked to ${scope.personLabel || 'this person'}. Clear the person view or keep naming faces.`
            : scope.date_taken
                ? `No photos were taken in ${scope.date_taken}. Choose another date or clear this date filter.`
                : scope.folder?.length
                    ? 'This folder has no matching photos. Choose another folder or clear the folder filter.'
                    : showClearFilters
                        ? 'Your filters exclude every photo. Clear them to widen this view.'
                        : 'There are no photos in this view yet. Widen the view to continue.';
    flow.innerHTML = emptyStateHtml({
        title: scope.collectionId ? 'Collection is empty' : 'No photos in this view',
        detail: scopeDetail,
        actions: [
            ...(showClearFilters ? [{ label: 'Clear filters', action: 'clear-filters' }] : []),
            ...(showClearScope ? [{ label: 'Clear view', action: 'clear-view', primary: true }] : []),
        ],
        iconName: scope.collectionId ? 'folder' : 'image',
    });
    bindScopeEmptyActions(flow);
    hydrateFirstRunEmpty(request);
}

function renderError(message, retry = loadFirstPage) {
    document.getElementById('grid-error').innerHTML = '<div class="load-error"><h4>Couldn\'t load this view</h4>'
        + `<p>${esc(message || 'The archive did not respond.')}</p><button class="btn" id="grid-retry">Retry</button></div>`;
    document.getElementById('grid-retry').addEventListener('click', retry);
}

function watchWindowStart(chunkEl) {
    if (!prependObserver || beforeDone || !chunkEl) return;
    prependObserver.disconnect();
    prependObserver.observe(chunkEl);
}

async function loadPage({ direction = 'after', start = null, jump = false } = {}) {
    if (loading || (direction === 'after' && done)) return false;
    loading = true;
    const token = ++loadToken;
    if (jump) activeJumpToken = token;
    const seq = generation;
    const pageSize = 100;
    const requestStart = start == null ? offset : Math.max(0, Number(start) || 0);
    let limit = direction === 'before' ? Math.min(pageSize, windowStart - requestStart) : pageSize;
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        const remaining = viewState.bestOfLimit - requestStart;
        if (remaining <= 0) {
            done = true;
            loading = false;
            if (activeJumpToken === token) {
                activeJumpToken = 0;
                emit('grid:jump-loading', { loading: false });
            }
            document.getElementById('grid-end').hidden = viewState.images.length === 0;
            return false;
        }
        limit = Math.min(pageSize, remaining);
    }
    const controller = new AbortController();
    loadController = controller;
    let data = null;
    try {
        data = await loadScopePage({ limit, offset: requestStart, signal: controller.signal });
    } catch {
        if (controller.signal.aborted || seq !== generation || token !== loadToken) return false;
        renderError('Retry when the local service is ready.', jump ? () => jumpToOffset(requestStart) : loadFirstPage);
        return false;
    } finally {
        if (loadController === controller) loadController = null;
        if (token === loadToken) loading = false;
        if (activeJumpToken === token) {
            activeJumpToken = 0;
            emit('grid:jump-loading', { loading: false });
        }
    }
    if (seq !== generation || token !== loadToken) {
        return false;
    }
    if (!data) {
        renderError('Retry when the local service is ready.', jump ? () => jumpToOffset(requestStart) : loadFirstPage);
        return false;
    }
    const rawIncoming = data.images || [];
    if (offset === 0 && viewState.bestOf) setBestOfTotal(data.visible_images);
    const cap = viewState.bestOf ? viewState.bestOfLimit : null;
    const remaining = cap == null ? rawIncoming.length : Math.max(0, cap - requestStart);
    const incoming = cap == null ? rawIncoming : rawIncoming.slice(0, remaining);
    const wasEmpty = viewState.images.length === 0;
    const next = viewState.images.slice();
    while (next.length < requestStart) next.push(null);
    incoming.forEach((img, i) => {
        next[requestStart + i] = img;
    });
    if (direction === 'before') {
        windowStart = requestStart;
        beforeDone = requestStart === 0 || rawIncoming.length < limit;
    } else {
        offset = requestStart + incoming.length;
        windowEnd = offset;
        done = data.source === 'similar' || rawIncoming.length < limit || (cap != null && offset >= cap);
    }
    setImages(next);
    if (wasEmpty) viewState.focusIndex = requestStart;
    if (wasEmpty) {
        setRankingsMeta({
            visibleImages: data.visible_images,
            sortQuality: data.sort_quality,
            searchMode: data.search_mode,
            searchSources: data.search_sources,
        });
    }
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = !done || next.length === 0;
    if (next.length === 0 && done) renderEmptyState();
    else {
        const chunkEl = direction === 'before'
            ? prependChunk(requestStart, incoming)
            : (render({ append: !wasEmpty, start: requestStart, images: incoming }), ensureChunkLive(requestStart));
        if (direction === 'before') watchWindowStart(chunkEl);
        if (jump && chunkEl) {
            viewState.focusIndex = requestStart;
            chunkEl.scrollIntoView({ block: 'start', behavior: 'auto' });
            setFocus(requestStart);
            watchWindowStart(chunkEl);
        }
    }
    return incoming.length > 0;
}

async function loadPreviousPage() {
    if (loading || beforeDone || windowStart <= 0) return false;
    const requestStart = Math.max(0, windowStart - 100);
    const existing = ensureChunkLive(requestStart);
    if (existing) {
        windowStart = Number(existing.dataset.start) || requestStart;
        beforeDone = windowStart === 0;
        watchWindowStart(existing);
        return true;
    }
    return loadPage({ direction: 'before', start: requestStart });
}

export async function requestMorePhotos() {
    if (loading || done) return false;
    return loadPage();
}

export function loadFirstPage() {
    if (!mounted) return;
    cancelPendingLoad();
    window.clearTimeout(emptyScanTimer);
    prependObserver?.disconnect();
    generation += 1;
    viewState.generation = generation;
    offset = 0;
    windowStart = 0;
    windowEnd = 0;
    beforeDone = true;
    done = false;
    loading = false;
    setImages([]);
    setRankingsMeta({ visibleImages: 0, sortQuality: null });
    viewState.focusIndex = 0;
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = true;
    renderSkeletons();
    loadPage();
}

export function jumpToOffset(nextOffset = 0) {
    if (!mounted) return;
    const target = Math.max(0, Number(nextOffset) || 0);
    const existing = ensureChunkLive(target);
    if (existing && viewState.images[target]) {
        windowStart = Number(existing.dataset.start) || target;
        windowEnd = windowStart + (Number(existing.dataset.count) || 0);
        offset = windowEnd;
        beforeDone = windowStart === 0;
        done = false;
        viewState.focusIndex = target;
        setFocus(target);
        watchWindowStart(existing);
        return;
    }
    cancelPendingLoad();
    generation += 1;
    viewState.generation = generation;
    loadToken += 1;
    offset = target;
    windowStart = target;
    windowEnd = target;
    beforeDone = target === 0;
    done = false;
    loading = false;
    viewState.focusIndex = target;
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = true;
    emit('grid:jump-loading', { loading: true });
    loadPage({ start: target, jump: true });
}

export function setFocus(index) {
    if (!viewState.images.length) return;
    const maxIndex = viewState.images.length - 1;
    let nextIndex = Math.max(0, Math.min(maxIndex, Number(index) || 0));
    while (nextIndex <= maxIndex && !viewState.images[nextIndex]) nextIndex += 1;
    if (nextIndex > maxIndex) {
        nextIndex = Math.max(0, Math.min(maxIndex, Number(index) || 0));
        while (nextIndex >= 0 && !viewState.images[nextIndex]) nextIndex -= 1;
    }
    if (nextIndex < 0 || !viewState.images[nextIndex]) return;
    viewState.focusIndex = nextIndex;
    if (previousFocusedCell?.isConnected) {
        previousFocusedCell.classList.remove('kb-focus');
        previousFocusedCell.tabIndex = -1;
    }
    ensureChunkLive(viewState.focusIndex);
    const current = document.querySelector(`.cell[data-idx="${viewState.focusIndex}"]`);
    if (!current) return;
    current.classList.add('kb-focus');
    current.tabIndex = 0;
    current.focus({ preventScroll: true });
    current.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' });
    previousFocusedCell = current;
    emit('focus', { image: viewState.images[viewState.focusIndex] || null, index: viewState.focusIndex });
}

export function moveFocus(delta) {
    setFocus(viewState.focusIndex + delta);
}

export function currentFocusedImage() {
    return viewState.images[viewState.focusIndex] || null;
}

function columns() {
    const liveChunk = firstLiveChunk();
    const cells = liveChunk ? [...liveChunk.querySelectorAll('.cell[data-id]')].slice(0, 20) : [];
    if (cells.length < 2) return 1;
    const top = cells[0].offsetTop;
    return Math.max(1, cells.filter((cell) => Math.abs(cell.offsetTop - top) < 4).length);
}

function handleClick(event) {
    const rejectRestButton = event.target.closest('[data-stack-reject-rest]');
    if (rejectRestButton) {
        event.preventDefault();
        event.stopPropagation();
        const stack = stackCache.get(Number(rejectRestButton.dataset.stackRejectRest));
        const members = stack?.members || [];
        const coverId = Number(stack?.representative?.id || stack?.representative_id);
        keepCoverRejectRest(members, coverId);
        return;
    }
    if (event.target.closest('.stack-tray-collapse')) {
        event.preventDefault();
        event.stopPropagation();
        closeExpandedStack();
        return;
    }
    const cell = event.target.closest('.cell[data-id]');
    if (!cell) return;
    const id = Number(cell.dataset.id);
    const index = Number(cell.dataset.idx);
    const stackButton = event.target.closest('.c-stack[data-stack-id]');
    if (stackButton) {
        event.preventDefault();
        event.stopPropagation();
        expandStack(stackButton.dataset.stackId, cell);
        return;
    }
    if (event.target.closest('.c-check')) {
        toggleSelection(id, index, { range: event.shiftKey });
        return;
    }
    if (event.target.closest('.c-menu')) {
        const rect = event.target.closest('.c-menu').getBoundingClientRect();
        openGridContextMenu({ id, index, x: rect.right, y: rect.bottom + 4, returnTo: event.target.closest('.c-menu') });
        return;
    }
    if (isSelectionMode()) {
        toggleSelection(id, index, { range: event.shiftKey });
        return;
    }
    const tray = cell.closest('.stack-tray');
    if (tray) {
        const stack = stackCache.get(Number(tray.dataset.stackId));
        const images = stack && stack.members ? stack.members : [...tray.querySelectorAll('.cell[data-id]')]
            .map((item) => byId.get(Number(item.dataset.id))).filter(Boolean);
        emit('loupe:open', { id, index: images.findIndex((img) => Number(img.id) === id), images });
        return;
    }
    emit('loupe:open', { id, index });
}

function patchCell(cell) {
    if (!cell) return;
    const id = Number(cell.dataset.id);
    const img = byId.get(id);
    const flag = (img && img.flag) || 'unflagged';
    const flagEl = cell.querySelector('.c-flag');
    cell.classList.toggle('sel', selection.has(id));
    if (flagEl) {
        flagEl.className = `c-flag ${flag}`;
        flagEl.textContent = flagGlyph(flag);
    }
}

function patchCells(imageIds = null) {
    const ids = Array.isArray(imageIds) ? imageIds.map(Number).filter((id) => id > 0) : [];
    const cells = ids.length
        ? ids.map((id) => document.querySelector(`.cell[data-id="${id}"]`)).filter(Boolean)
        : [...document.querySelectorAll('.cell[data-id]')];
    document.getElementById('grid-flow').classList.toggle('selmode', isSelectionMode());
    for (const cell of cells) patchCell(cell);
}

export function initGrid() {
    if (initialized) return;
    initialized = true;
    configureGridWindow({ renderCell: cellHtml, observeImages, unobserveImages });
    prependObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadPreviousPage();
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px 900px 0px' });
    const flow = document.getElementById('grid-flow');
    flow.addEventListener('click', handleClick);
    flow.addEventListener('pointerover', (event) => warmMediumThumb(event.target.closest('.cell[data-id]')));
    flow.addEventListener('focusin', (event) => warmMediumThumb(event.target.closest('.cell[data-id]')));
    document.addEventListener('pointerdown', (event) => {
        if (!expandedStack || !mounted) return;
        if (event.target.closest('.stack-tray, .c-stack')) return;
        closeExpandedStack();
    });
    flow.addEventListener('contextmenu', (event) => {
        const cell = event.target.closest('.cell[data-id]');
        if (!cell) return;
        event.preventDefault();
        openGridContextMenu({
            id: Number(cell.dataset.id),
            index: Number(cell.dataset.idx),
            x: event.clientX,
            y: event.clientY,
            returnTo: cell,
        });
    });
    flow.addEventListener('dragstart', (event) => {
        const cell = event.target.closest('.cell[data-id]');
        if (!cell) return;
        if (!selection.size) enterSelection(Number(cell.dataset.id), Number(cell.dataset.idx));
        event.dataTransfer.setData('text/plain', [...selection].join(','));
        event.dataTransfer.effectAllowed = 'copy';
    });
    on('scope', () => {
        if (mounted) loadFirstPage();
    });
    on('flags', ({ imageIds } = {}) => patchCells(imageIds));
    document.addEventListener('photoarchive:cull-applied', () => {
        if (mounted) loadFirstPage();
    });
    on('selection', ({ imageIds } = {}) => patchCells(imageIds));
    on('trash:changed', ({ imageIds } = {}) => {
        if (!imageIds?.length) return;
        reloadPending = !mounted;
        if (mounted) loadFirstPage();
    });
    on('scan', ({ scanning } = {}) => {
        if (!mounted || viewState.images.length) return;
        if (scanning) renderEmptyState();
        else loadFirstPage();
    });
    resizeHandler = () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (!mounted) return;
            invalidateHeights({ remeasureGhosts: true });
            setFocus(viewState.focusIndex);
        }, 120);
    };
    window.addEventListener('resize', resizeHandler);
    on('thumbsize', (size) => {
        const next = Number(size) || lastThumbSize;
        requestAnimationFrame(() => invalidateHeights({ remeasureGhosts: true }));
        lastThumbSize = next;
    });
}

export async function toggleFocusedStack() {
    const img = currentFocusedImage();
    const stackId = Number(img?.stack_id) || 0;
    if (!stackId) return false;
    ensureChunkLive(viewState.focusIndex);
    const cell = document.querySelector(`.cell[data-id="${Number(img.id)}"]`);
    if (!cell) return false;
    return expandStack(stackId, cell);
}

export async function createStackFromSelection() {
    const imageIds = [...selection].map(Number).filter((id) => id > 0);
    if (imageIds.length < 2 || creatingStack) return false;
    creatingStack = true;
    try {
        const focused = currentFocusedImage();
        const representativeId = focused && imageIds.includes(Number(focused.id)) ? Number(focused.id) : imageIds[0];
        const result = await createStack(imageIds, representativeId);
        const stackId = Number(result?.stack?.id || result?.id || result?.stack_id);
        if (!result || !(result.ok || result.stack || stackId)) {
            showToast("Stack couldn't be created");
            return true;
        }
        clearSelection();
        showToast(`Stacked ${imageIds.length.toLocaleString('en-US')} photos`, {
            undo: stackId ? async () => {
                const undone = await unstack(stackId);
                showToast(undone ? 'Stack undone' : 'Couldn’t undo');
                loadFirstPage();
            } : null,
        });
        loadFirstPage();
        return true;
    } finally {
        creatingStack = false;
    }
}

export function focusColumns() {
    return columns();
}

export function focusPageStep() {
    const canvas = document.getElementById('canvas');
    const cell = ensureChunkLive(viewState.focusIndex)?.querySelector('.cell[data-id]');
    const gap = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--cell-gap')) || 0;
    const rowHeight = cell?.getBoundingClientRect().height || viewState.thumbSize || 1;
    const rows = Math.max(1, Math.floor((canvas?.clientHeight || rowHeight) / Math.max(1, rowHeight + gap)));
    return rows * columns();
}

export function mountGrid() {
    mounted = true;
    document.getElementById('view-grid').classList.add('active');
    sentinelObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadPage();
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px' });
    sentinelObserver.observe(document.getElementById('grid-sentinel'));
    if (reloadPending) {
        reloadPending = false;
        loadFirstPage();
    } else if (viewState.images.length || offset > 0) {
        observeImages(document.getElementById('grid-flow'));
        watchWindowStart(ensureChunkLive(windowStart));
        requestAnimationFrame(() => {
            document.getElementById('canvas').scrollTo({ top: savedScrollTop, behavior: 'auto' });
            setFocus(viewState.focusIndex);
        });
    } else {
        loadFirstPage();
    }
}

export function unmountGrid() {
    mounted = false;
    savedScrollTop = document.getElementById('canvas').scrollTop;
    generation += 1;
    cancelPendingLoad();
    stackExpansionRequest += 1;
    loading = false;
    closeExpandedStack();
    resetImageObserver();
    if (sentinelObserver) sentinelObserver.disconnect();
    if (prependObserver) prependObserver.disconnect();
    sentinelObserver = null;
    document.getElementById('view-grid').classList.remove('active');
}
