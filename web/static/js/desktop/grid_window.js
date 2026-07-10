import { viewState } from './state.js';

const chunks = [];

let chunkObserver = null;
let renderCell = null;
let observeImages = null;
let unobserveImages = null;

function canvas() {
    return document.getElementById('canvas');
}

function flow() {
    return document.getElementById('grid-flow');
}

function ensureObserver() {
    if (chunkObserver) return chunkObserver;
    chunkObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            const chunk = chunks.find((item) => item.el === entry.target);
            if (!chunk) continue;
            if (entry.isIntersecting) materialize(chunk);
            else despawn(chunk);
        }
    }, { root: canvas(), rootMargin: '200% 0px 200% 0px' });
    return chunkObserver;
}

function chunkHtml(chunk) {
    return viewState.images
        .slice(chunk.start, chunk.start + chunk.count)
        .map((img, i) => (img ? renderCell(img, chunk.start + i) : ''))
        .join('');
}

function materialize(chunk) {
    if (chunk.live || !renderCell) return;
    chunk.el.classList.remove('ghost');
    chunk.el.inert = false;
    chunk.el.style.height = '';
    chunk.el.innerHTML = chunkHtml(chunk);
    chunk.live = true;
    if (observeImages) observeImages(chunk.el);
    chunk.height = chunk.el.offsetHeight || chunk.height;
}

function despawn(chunk) {
    if (!chunk.live) return;
    chunk.height = chunk.el.offsetHeight || chunk.height || 1;
    if (unobserveImages) unobserveImages(chunk.el);
    chunk.el.style.height = `${chunk.height}px`;
    chunk.el.replaceChildren();
    chunk.el.classList.add('ghost');
    chunk.el.inert = true;
    chunk.live = false;
}

function createChunk(startIndex, images) {
    const chunk = {
        el: document.createElement('div'),
        start: Number(startIndex) || 0,
        count: (images || []).length,
        height: 0,
        live: true,
    };
    chunk.el.className = 'grid-chunk';
    chunk.el.inert = false;
    chunk.el.dataset.start = String(chunk.start);
    chunk.el.dataset.count = String(chunk.count);
    chunk.el.innerHTML = (images || []).map((img, i) => renderCell(img, chunk.start + i)).join('');
    return chunk;
}

function insertChunk(startIndex, images, { preserveScroll = false } = {}) {
    const count = (images || []).length;
    if (!count || !renderCell) return null;
    const existing = chunks.find((item) => item.start === Number(startIndex));
    if (existing) return existing.el;
    const host = flow();
    const scroller = canvas();
    const oldHeight = scroller.scrollHeight;
    const oldTop = scroller.scrollTop;
    const chunk = createChunk(startIndex, images);
    const next = chunks.find((item) => item.start > chunk.start);
    host.insertBefore(chunk.el, next?.el || null);
    chunks.push(chunk);
    chunks.sort((a, b) => a.start - b.start);
    ensureObserver().observe(chunk.el);
    if (observeImages) observeImages(chunk.el);
    if (preserveScroll) scroller.scrollTop = oldTop + (scroller.scrollHeight - oldHeight);
    requestAnimationFrame(() => {
        if (chunk.live) chunk.height = chunk.el.offsetHeight || chunk.height;
    });
    return chunk.el;
}

function measuredHeight(chunk) {
    const probe = document.createElement('div');
    probe.className = 'grid-chunk grid-measure';
    probe.innerHTML = chunkHtml(chunk);
    flow().appendChild(probe);
    const height = probe.offsetHeight || chunk.height || 1;
    probe.remove();
    return height;
}

export function configureGridWindow(options = {}) {
    renderCell = options.renderCell;
    observeImages = options.observeImages;
    unobserveImages = options.unobserveImages;
}

export function appendChunk(startIndex, images) {
    return insertChunk(startIndex, images);
}

export function prependChunk(startIndex, images) {
    return insertChunk(startIndex, images, { preserveScroll: true });
}

export function ensureChunkLive(globalIndex) {
    const index = Number(globalIndex) || 0;
    const chunk = chunks.find((item) => index >= item.start && index < item.start + item.count);
    if (!chunk) return null;
    materialize(chunk);
    return chunk.el;
}

export function firstLiveChunk() {
    return chunks.find((chunk) => chunk.live && chunk.el.querySelector('.cell[data-id]'))?.el || null;
}

export function invalidateHeights({ remeasureGhosts = false } = {}) {
    const scroller = canvas();
    const canvasTop = scroller.getBoundingClientRect().top;
    const anchor = chunks.find((chunk) => chunk.el.getBoundingClientRect().bottom > canvasTop)?.el || null;
    const oldAnchorTop = anchor?.getBoundingClientRect().top || 0;
    for (const chunk of chunks) {
        if (chunk.live) {
            chunk.height = chunk.el.offsetHeight || chunk.height;
        } else if (chunk.height) {
            if (remeasureGhosts) chunk.height = measuredHeight(chunk);
            chunk.el.style.height = `${chunk.height}px`;
        }
    }
    if (anchor) scroller.scrollTop += anchor.getBoundingClientRect().top - oldAnchorTop;
}

export function reset() {
    if (chunkObserver) {
        chunkObserver.disconnect();
        chunkObserver = null;
    }
    chunks.splice(0, chunks.length);
    const host = flow();
    if (host) host.replaceChildren();
}
