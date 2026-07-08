import {
    byId, emit, on, scopeParams, selection, setBestOfTotal, setImages, setRankingsMeta, viewState,
} from './state.js';
import { thumbUrl } from './api.js';
import { loadScopePage } from './scope_data.js';
import {
    enterSelection, isSelectionMode, toggleSelection,
} from './selection.js';
import { openGridContextMenu } from './context_menu.js';

let offset = 0;
let loading = false;
let done = false;
let generation = 0;
let imageObserver = null;
let sentinelObserver = null;
let mounted = false;
let initialized = false;
let resizeHandler = null;

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

function cellHtml(img, index) {
    const flag = img.flag || 'unflagged';
    return `<figure class="cell ${selection.has(Number(img.id)) ? 'sel' : ''}" data-id="${img.id}" data-idx="${index}" draggable="true" tabindex="-1" style="--ar:${aspect(img)}">`
        + `<img data-src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" loading="lazy" decoding="async" alt="${esc(img.filename || '')}">`
        + '<button class="c-check" aria-label="Select photo">✓</button>'
        + `<span class="c-idx">${index + 1}</span>`
        + `<span class="c-flag ${flag}">${flagGlyph(flag)}</span>`
        + `<span class="c-elo"><span class="elo-chip">${Math.round(Number(img.elo) || 0)}</span></span>`
        + '<button class="c-menu" aria-label="Photo actions">⋯</button></figure>';
}

function observeImages() {
    if (imageObserver) imageObserver.disconnect();
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
    for (const img of document.querySelectorAll('#grid-flow img[data-src]')) {
        img.addEventListener('load', () => img.classList.add('ld'), { once: true });
        imageObserver.observe(img);
    }
}

function render({ append = false } = {}) {
    const flow = document.getElementById('grid-flow');
    const html = viewState.images.map((img, index) => cellHtml(img, index)).join('');
    if (append) {
        const start = flow.children.length;
        flow.insertAdjacentHTML('beforeend', viewState.images.slice(start).map((img, i) => cellHtml(img, start + i)).join(''));
    } else {
        flow.innerHTML = html;
    }
    flow.classList.toggle('selmode', isSelectionMode());
    observeImages();
    setFocus(viewState.focusIndex);
}

function renderSkeletons() {
    document.getElementById('grid-flow').innerHTML = Array.from({ length: 18 }, (_, i) => (
        `<div class="cell skel-cell" style="--ar:${[1.5, .75, 1.2, 1.8][i % 4]}"></div>`
    )).join('');
}

function renderError(message) {
    document.getElementById('grid-error').innerHTML = '<div class="load-error"><h4>Couldn\'t load this scope</h4>'
        + `<p>${esc(message || 'The archive did not respond.')}</p><button class="btn" id="grid-retry">Retry</button></div>`;
    document.getElementById('grid-retry').addEventListener('click', () => loadFirstPage());
}

async function loadPage() {
    if (!mounted || loading || done) return;
    loading = true;
    const seq = generation;
    const pageSize = 100;
    let limit = pageSize;
    if (viewState.bestOf && viewState.bestOfLimit != null) {
        const remaining = viewState.bestOfLimit - offset;
        if (remaining <= 0) {
            done = true;
            loading = false;
            document.getElementById('grid-end').hidden = viewState.images.length === 0;
            return;
        }
        limit = Math.min(pageSize, remaining);
    }
    const params = scopeParams({ limit, offset });
    const data = await loadScopePage({ limit, offset });
    if (seq !== generation) return;
    loading = false;
    if (!data) {
        renderError('Retry when the local service is ready.');
        return;
    }
    const rawIncoming = data.images || [];
    if (offset === 0 && viewState.bestOf) setBestOfTotal(data.visible_images);
    const cap = viewState.bestOf ? viewState.bestOfLimit : null;
    const remaining = cap == null ? rawIncoming.length : Math.max(0, cap - offset);
    const incoming = cap == null ? rawIncoming : rawIncoming.slice(0, remaining);
    const wasEmpty = viewState.images.length === 0;
    const next = wasEmpty ? incoming : [...viewState.images, ...incoming];
    offset += incoming.length;
    done = data.source === 'similar' || rawIncoming.length < limit || (cap != null && offset >= cap);
    setImages(next);
    if (wasEmpty) {
        setRankingsMeta({ visibleImages: data.visible_images, sortQuality: data.sort_quality });
    }
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = !done || next.length === 0;
    render({ append: !wasEmpty });
}

export function loadFirstPage() {
    if (!mounted) return;
    generation += 1;
    viewState.generation = generation;
    offset = 0;
    done = false;
    loading = false;
    setImages([]);
    setRankingsMeta({ visibleImages: 0, sortQuality: null });
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
    document.getElementById('grid-error').innerHTML = '';
    document.getElementById('grid-end').hidden = true;
    renderSkeletons();
    document.getElementById('canvas').scrollTo({ top: 0, behavior: 'auto' });
    loadPage();
}

export function setFocus(index) {
    const cells = [...document.querySelectorAll('.cell[data-id]')];
    if (!cells.length) return;
    viewState.focusIndex = Math.max(0, Math.min(cells.length - 1, index));
    for (const cell of cells) {
        cell.classList.remove('kb-focus');
        cell.tabIndex = -1;
    }
    const current = cells[viewState.focusIndex];
    current.classList.add('kb-focus');
    current.tabIndex = 0;
    current.focus({ preventScroll: true });
    emit('focus', { image: viewState.images[viewState.focusIndex] || null, index: viewState.focusIndex });
}

export function moveFocus(delta) {
    setFocus(viewState.focusIndex + delta);
}

export function currentFocusedImage() {
    return viewState.images[viewState.focusIndex] || null;
}

function columns() {
    const cells = [...document.querySelectorAll('.cell[data-id]')].slice(0, 20);
    if (cells.length < 2) return 1;
    const top = cells[0].offsetTop;
    return Math.max(1, cells.filter((cell) => Math.abs(cell.offsetTop - top) < 4).length);
}

function handleClick(event) {
    const cell = event.target.closest('.cell[data-id]');
    if (!cell) return;
    const id = Number(cell.dataset.id);
    const index = Number(cell.dataset.idx);
    if (event.target.closest('.c-check')) {
        enterSelection(id, index);
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
    const flow = document.getElementById('grid-flow');
    flow.addEventListener('click', handleClick);
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
    resizeHandler = () => {
        if (mounted) setFocus(viewState.focusIndex);
    };
    window.addEventListener('resize', resizeHandler);
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
    loadFirstPage();
}

export function unmountGrid() {
    mounted = false;
    generation += 1;
    loading = false;
    if (imageObserver) imageObserver.disconnect();
    if (sentinelObserver) sentinelObserver.disconnect();
    imageObserver = null;
    sentinelObserver = null;
    document.getElementById('view-grid').classList.remove('active');
}
