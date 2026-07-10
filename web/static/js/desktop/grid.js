import {
    byId, clearSelection, emit, nonSearchFacetCount, on, scope, scopeActive, scopeParams, selection, setBestOfTotal, setImages, setRankingsMeta, setScope, viewState,
} from './state.js';
import { createStack, getCatalog, getScanStatus, getStack, thumbUrl, unstack } from './api.js';
import { loadScopePage } from './scope_data.js';
import {
    enterSelection, isSelectionMode, toggleSelection,
} from './selection.js';
import { openGridContextMenu } from './context_menu.js';
import { icon } from '../icons.js';
import {
    appendChunk, configureGridWindow, ensureChunkLive, firstLiveChunk, invalidateHeights, reset as resetGridWindow,
} from './grid_window.js';
import { afterMotion } from './motion.js';
import { showToast } from './toast.js';
import { keepCoverRejectRest } from './stack_cull.js';

let offset = 0;
let loading = false;
let done = false;
let generation = 0;
let imageObserver = null;
let sentinelObserver = null;
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
const stackCache = new Map();

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

function aspect(img) {
    const ar = Number(img.aspect_ratio) || (Number(img.width) && Number(img.height) ? Number(img.width) / Number(img.height) : 1.5);
    return Math.max(.45, Math.min(3.8, ar));
}

function flagGlyph(flag) {
    if (flag === 'picked') return '★';
    if (flag === 'rejected') return '×';
    return '';
}

export function cellHtml(img, index) {
    const flag = img.flag || 'unflagged';
    const stackId = Number(img.stack_id) || 0;
    const stackCount = Number(img.stack_count) || 0;
    const stackBadge = stackId && stackCount > 1
        ? `<button class="c-stack" data-stack-id="${stackId}" data-tip="Expand stack · S" aria-label="Expand stack with ${stackCount} photos" aria-expanded="false" tabindex="-1">${icon('layers')}<span>${stackCount}</span></button>`
        : '';
    const selected = selection.has(Number(img.id));
    return `<figure class="cell ${selected ? 'sel' : ''}" data-id="${img.id}" data-idx="${index}" draggable="true" tabindex="-1" aria-selected="${selected ? 'true' : 'false'}" style="--ar:${aspect(img)}">`
        + `<img data-src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" loading="lazy" decoding="async" alt="${esc(img.filename || '')}">`
        + stackBadge
        + `<button class="c-check" aria-label="Select photo" tabindex="-1">${icon('check')}</button>`
        + `<span class="c-idx">${index + 1}</span>`
        + `<span class="c-flag ${flag}">${flagGlyph(flag)}</span>`
        + `<span class="c-elo"><span class="elo-chip">${Math.round(Number(img.elo) || 0)}</span></span>`
        + `<button class="c-menu" data-tip="Photo actions" aria-label="Photo actions" tabindex="-1">${icon('ellipsis')}</button></figure>`;
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
            } else if (Math.abs(entry.boundingClientRect.top) > window.innerHeight * 3) {
                img.removeAttribute('src');
                img.classList.remove('ld');
            }
        }
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px' });
    return imageObserver;
}

function observeImages(rootEl) {
    const observer = ensureImageObserver();
    for (const img of (rootEl || document).querySelectorAll('img[data-src]')) {
        img.addEventListener('load', () => img.classList.add('ld'), { once: true });
        observer.observe(img);
    }
}

function unobserveImages(rootEl) {
    if (!imageObserver || !rootEl) return;
    for (const img of rootEl.querySelectorAll('img[data-src]')) {
        imageObserver.unobserve(img);
    }
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
            invalidateHeights(1);
        });
    }
    const badge = document.querySelector(`.c-stack[data-stack-id="${stackId}"]`);
    if (badge) {
        badge.classList.remove('expanded');
        badge.setAttribute('aria-expanded', 'false');
        badge.setAttribute('aria-label', badge.getAttribute('aria-label')?.replace(/^Collapse/, 'Expand') || 'Expand stack');
    }
    expandedStack = null;
    invalidateHeights(1);
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
    invalidateHeights(1);
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
    flow.classList.toggle('selmode', isSelectionMode());
    if (!append) setFocus(viewState.focusIndex);
}

function renderSkeletons() {
    stackExpansionRequest += 1;
    closeExpandedStack();
    resetImageObserver();
    resetGridWindow();
    document.getElementById('grid-flow').innerHTML = '<div class="grid-chunk">'
        + Array.from({ length: 18 }, (_, i) => (
            `<div class="cell skel-cell" style="--ar:${[1.5, .75, 1.2, 1.8][i % 4]}"></div>`
        )).join('')
        + '</div>';
}

function bindEmptyActions() {
    document.getElementById('grid-add-source')?.addEventListener('click', () => document.getElementById('system-btn')?.click());
    document.getElementById('grid-import')?.addEventListener('click', () => emit('import:open'));
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
        flow.innerHTML = '<div class="grid-empty">'
            + '<h3>Welcome to photoArchive</h3>'
            + '<p>Add a folder of photos to start your private, local library.</p>'
            + '<div class="grid-empty-actions"><button class="btn primary" id="grid-add-source">Add a source</button>'
            + '<button class="btn" id="grid-import">Import photos</button></div></div>';
    } else if (scanning) {
        flow.innerHTML = '<div class="grid-empty">'
            + '<h3>Source added</h3>'
            + `<p>Scanning${found ? ` · ${found.toLocaleString('en-US')} photos found` : ' for photos'}.</p>`
            + '<p>Your first thumbnails will appear here when they’re ready.</p>'
            + '<div class="grid-empty-actions"><button class="btn primary" id="grid-add-source">View scan progress</button></div></div>';
        emptyScanTimer = window.setTimeout(async () => {
            if (request !== emptyStateRequest || !mounted || viewState.images.length) return;
            const page = await loadScopePage({ limit: 1, offset: 0 });
            if (page && (page.images || []).length) loadFirstPage();
            else hydrateFirstRunEmpty(request);
        }, 1500);
    } else {
        flow.innerHTML = '<div class="grid-empty">'
            + '<h3>No photos yet</h3>'
            + '<p>Your source is ready, but no photos have appeared. Check the source or start a rescan in System.</p>'
            + '<div class="grid-empty-actions"><button class="btn primary" id="grid-add-source">Open Sources</button>'
            + '<button class="btn" id="grid-import">Import photos</button></div></div>';
    }
    bindEmptyActions();
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
    flow.innerHTML = '<div class="grid-empty">'
        + '<h3>No photos in this view.</h3>'
        + '<p>Try widening this view or clearing filters.</p>'
        + '<div class="grid-empty-actions">'
        + (showClearFilters ? '<button class="btn" id="grid-clear-filters">Clear filters</button>' : '')
        + (showClearScope ? '<button class="btn primary" id="grid-clear-scope">Clear view</button>' : '')
        + '</div></div>';
    document.getElementById('grid-clear-filters')?.addEventListener('click', () => {
        setScope({ q: scope.q, sort: scope.sort || 'elo' });
    });
    document.getElementById('grid-clear-scope')?.addEventListener('click', () => setScope({}));
    hydrateFirstRunEmpty(request);
}

function renderError(message) {
    document.getElementById('grid-error').innerHTML = '<div class="load-error"><h4>Couldn\'t load this view</h4>'
        + `<p>${esc(message || 'The archive did not respond.')}</p><button class="btn" id="grid-retry">Retry</button></div>`;
    document.getElementById('grid-retry').addEventListener('click', () => loadFirstPage());
}

async function loadPage() {
    if (loading || done) return false;
    loading = true;
    const seq = generation;
    const pageSize = 100;
    const requestStart = offset;
    let limit = pageSize;
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        const remaining = viewState.bestOfLimit - offset;
        if (remaining <= 0) {
            done = true;
            loading = false;
            document.getElementById('grid-end').hidden = viewState.images.length === 0;
            return false;
        }
        limit = Math.min(pageSize, remaining);
    }
    const params = scopeParams({ limit, offset });
    const data = await loadScopePage({ limit, offset });
    if (seq !== generation) {
        loading = false;
        return false;
    }
    loading = false;
    if (!data) {
        renderError('Retry when the local service is ready.');
        return false;
    }
    const rawIncoming = data.images || [];
    if (offset === 0 && viewState.bestOf) setBestOfTotal(data.visible_images);
    const cap = viewState.bestOf ? viewState.bestOfLimit : null;
    const remaining = cap == null ? rawIncoming.length : Math.max(0, cap - offset);
    const incoming = cap == null ? rawIncoming : rawIncoming.slice(0, remaining);
    const wasEmpty = viewState.images.length === 0;
    const next = viewState.images.slice();
    next.length = Math.max(next.length, requestStart);
    incoming.forEach((img, i) => {
        next[requestStart + i] = img;
    });
    offset += incoming.length;
    done = data.source === 'similar' || rawIncoming.length < limit || (cap != null && offset >= cap);
    setImages(next);
    if (wasEmpty) viewState.focusIndex = requestStart;
    if (wasEmpty) {
        setRankingsMeta({ visibleImages: data.visible_images, sortQuality: data.sort_quality });
    }
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = !done || next.length === 0;
    if (next.length === 0 && done) renderEmptyState();
    else render({ append: !wasEmpty, start: requestStart, images: incoming });
    return incoming.length > 0;
}

export async function requestMorePhotos() {
    if (loading || done) return false;
    return loadPage();
}

export function loadFirstPage() {
    if (!mounted) return;
    window.clearTimeout(emptyScanTimer);
    generation += 1;
    viewState.generation = generation;
    offset = 0;
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
    generation += 1;
    viewState.generation = generation;
    offset = Math.max(0, Number(nextOffset) || 0);
    done = false;
    loading = false;
    setImages([]);
    viewState.focusIndex = offset;
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = true;
    renderSkeletons();
    document.getElementById('canvas').scrollTo({ top: 0, behavior: 'auto' });
    loadPage();
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
    const flow = document.getElementById('grid-flow');
    flow.addEventListener('click', handleClick);
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
    on('selection', ({ imageIds } = {}) => patchCells(imageIds));
    on('trash:changed', () => {
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
            invalidateHeights();
            setFocus(viewState.focusIndex);
        }, 120);
    };
    window.addEventListener('resize', resizeHandler);
    on('thumbsize', (size) => {
        const next = Number(size) || lastThumbSize;
        invalidateHeights(next / lastThumbSize);
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

export function mountGrid() {
    mounted = true;
    document.getElementById('view-grid').classList.add('active');
    sentinelObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadPage();
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px' });
    sentinelObserver.observe(document.getElementById('grid-sentinel'));
    if (viewState.images.length || offset > 0) {
        observeImages(document.getElementById('grid-flow'));
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
    stackExpansionRequest += 1;
    loading = false;
    closeExpandedStack();
    resetImageObserver();
    if (sentinelObserver) sentinelObserver.disconnect();
    sentinelObserver = null;
    document.getElementById('view-grid').classList.remove('active');
}
