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

export function configureGridWindow(options = {}) {
    renderCell = options.renderCell;
    observeImages = options.observeImages;
    unobserveImages = options.unobserveImages;
}

export function appendChunk(startIndex, images) {
    const count = (images || []).length;
    if (!count || !renderCell) return null;
    const chunk = {
        el: document.createElement('div'),
        start: Number(startIndex) || 0,
        count,
        height: 0,
        live: true,
    };
    chunk.el.className = 'grid-chunk';
    chunk.el.inert = false;
    chunk.el.dataset.start = String(chunk.start);
    chunk.el.innerHTML = (images || []).map((img, i) => renderCell(img, chunk.start + i)).join('');
    flow().appendChild(chunk.el);
    chunks.push(chunk);
    ensureObserver().observe(chunk.el);
    if (observeImages) observeImages(chunk.el);
    requestAnimationFrame(() => {
        if (chunk.live) chunk.height = chunk.el.offsetHeight || chunk.height;
    });
    return chunk.el;
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

export function invalidateHeights(scale = 1) {
    const ratio = Number(scale) > 0 ? Number(scale) : 1;
    for (const chunk of chunks) {
        if (chunk.live) {
            chunk.height = chunk.el.offsetHeight || chunk.height;
        } else if (chunk.height) {
            if (ratio === 1 && renderCell) {
                chunk.el.inert = true;
                chunk.el.classList.remove('ghost');
                chunk.el.style.height = '';
                chunk.el.innerHTML = chunkHtml(chunk);
                chunk.height = chunk.el.offsetHeight || chunk.height;
                chunk.el.replaceChildren();
                chunk.el.classList.add('ghost');
            } else {
                chunk.height *= ratio;
            }
            chunk.el.style.height = `${chunk.height}px`;
        }
    }
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
