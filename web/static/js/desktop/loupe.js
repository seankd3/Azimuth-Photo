import { byId, emit, on, rememberImages, viewState } from './state.js';
import { thumbUrl, writeFlag } from './api.js';
import { applyFlags } from './selection.js';
import { openCollectionPicker } from './panel.js';
import { requestMorePhotos } from './grid.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';

const ZOOM_STEP = 1.15;
const MAX_SCALE = 4;
const PREFETCH_AHEAD = 10;
const LOAD_WAIT_MS = 5000;

let index = 0;
let open = false;
let returnCell = null;
let sessionImages = null;
let naturalWidth = 0;
let naturalHeight = 0;
let fitScale = 1;
let scale = 1;
let panX = 0;
let panY = 0;
let zoomMode = 'fit';
let dragState = null;
let renderToken = 0;
let fullImageLoadingId = null;
let imageWaiters = [];

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&#34;', "'": '&#39;',
}[c]));

function images() {
    return sessionImages || viewState.images;
}

function current() {
    return images()[index] || null;
}

function isGridScope() {
    return !sessionImages;
}

function scopeTotal() {
    if (!isGridScope()) return images().length;
    return viewState.visibleImages || images().length;
}

function flagGlyph(flag) {
    if (flag === 'picked') return '★';
    if (flag === 'rejected') return '×';
    return '';
}

function caption(img) {
    const name = img.filename || img.id;
    const elo = Math.round(Number(img.elo) || 0);
    const flag = flagGlyph(img.flag || 'unflagged');
    return [name, `${index + 1} / ${scopeTotal() || images().length}`, `Elo ${elo}`, flag]
        .filter(Boolean)
        .map(esc)
        .join(' · ');
}

function imageSizeFromMetadata(img, imageEl) {
    const width = Number(img?.width) || Number(imageEl?.naturalWidth) || 1;
    const height = Number(img?.height) || Number(imageEl?.naturalHeight) || 1;
    return { width: Math.max(1, width), height: Math.max(1, height) };
}

function stageRect() {
    return document.getElementById('loupe-stage').getBoundingClientRect();
}

function computeFitScale() {
    const rect = stageRect();
    if (!naturalWidth || !naturalHeight || !rect.width || !rect.height) return 1;
    return Math.min(1, rect.width / naturalWidth, rect.height / naturalHeight);
}

function clampPan() {
    const rect = stageRect();
    const scaledWidth = naturalWidth * scale;
    const scaledHeight = naturalHeight * scale;

    if (!rect.width || !rect.height || !scaledWidth || !scaledHeight) return;

    if (scaledWidth <= rect.width) panX = (rect.width - scaledWidth) / 2;
    else panX = Math.min(0, Math.max(rect.width - scaledWidth, panX));

    if (scaledHeight <= rect.height) panY = (rect.height - scaledHeight) / 2;
    else panY = Math.min(0, Math.max(rect.height - scaledHeight, panY));
}

function zoomLabel() {
    if (zoomMode === 'fit' || Math.abs(scale - fitScale) < 0.001) return 'Fit';
    return `${Math.round(scale * 100)}%`;
}

function updateZoomChip() {
    const chip = document.getElementById('loupe-zoom');
    if (chip) chip.textContent = zoomLabel();
}

function updateCursor() {
    const root = document.getElementById('loupe');
    root.classList.toggle('zoomed', zoomMode !== 'fit' && scale > fitScale + 0.001);
    root.classList.toggle('dragging', Boolean(dragState?.dragging));
}

function applyTransform({ animate = false } = {}) {
    const image = document.getElementById('loupe-img');
    image.classList.toggle('animate', animate);
    image.style.width = `${naturalWidth}px`;
    image.style.height = `${naturalHeight}px`;
    image.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
    updateZoomChip();
    updateCursor();
}

function centerFit({ animate = true } = {}) {
    fitScale = computeFitScale();
    scale = fitScale;
    zoomMode = 'fit';
    clampPan();
    useMediumTier();
    applyTransform({ animate });
}

function setImageMetrics({ animate = false } = {}) {
    const img = current();
    const image = document.getElementById('loupe-img');
    const size = imageSizeFromMetadata(img, image);
    naturalWidth = size.width;
    naturalHeight = size.height;
    fitScale = computeFitScale();
    if (zoomMode === 'fit') {
        centerFit({ animate });
        return;
    }
    scale = Math.max(fitScale, Math.min(MAX_SCALE, scale));
    clampPan();
    applyTransform({ animate });
}

function clientPointInStage(clientX, clientY) {
    const rect = stageRect();
    return {
        x: clientX - rect.left,
        y: clientY - rect.top,
    };
}

function zoomTo(nextScale, clientX, clientY, mode = 'custom') {
    if (!current()) return;
    fitScale = computeFitScale();
    const clamped = Math.max(fitScale, Math.min(MAX_SCALE, nextScale));
    if (clamped <= fitScale + 0.001) {
        centerFit();
        return;
    }

    const point = clientPointInStage(clientX, clientY);
    const imageX = (point.x - panX) / scale;
    const imageY = (point.y - panY) / scale;

    scale = clamped;
    zoomMode = mode;
    panX = point.x - imageX * scale;
    panY = point.y - imageY * scale;
    clampPan();
    requestFullImage();
    applyTransform({ animate: true });
}

function useMediumTier() {
    const img = current();
    const image = document.getElementById('loupe-img');
    if (!img || !image) return;
    const md = thumbUrl('md', img.id);
    if (image.dataset.tier !== 'md') {
        image.dataset.tier = 'md';
        image.src = md;
    }
}

function requestFullImage() {
    const img = current();
    const image = document.getElementById('loupe-img');
    if (!img || image.dataset.tier === 'lg' || fullImageLoadingId === img.id) return;
    fullImageLoadingId = img.id;
    const token = renderToken;
    const large = new Image();
    large.onload = async () => {
        if (large.decode) await large.decode().catch(() => {});
        if (!open || token !== renderToken || !current() || Number(current().id) !== Number(img.id)) return;
        image.dataset.tier = 'lg';
        image.src = large.src;
        fullImageLoadingId = null;
    };
    large.onerror = () => {
        if (Number(fullImageLoadingId) === Number(img.id)) fullImageLoadingId = null;
    };
    large.src = thumbUrl('lg', img.id);
}

function updateFlagControls() {
    const flag = current()?.flag || 'unflagged';
    for (const [id, value] of [['lp-pick', 'picked'], ['lp-reject', 'rejected'], ['lp-unflag', 'unflagged']]) {
        const button = document.getElementById(id);
        if (button) button.classList.toggle('active', flag === value);
    }
}

function renderStrip() {
    const host = document.getElementById('loupe-strip');
    const loaded = images();
    host.innerHTML = loaded.map((img, i) => {
        if (!img) return '';
        return `<button class="loupe-thumb ${i === index ? 'cur' : ''}" data-index="${i}" aria-label="Photo ${i + 1}">`
            + `<img loading="lazy" decoding="async" src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" alt="">`
            + '</button>';
    }).join('');
    for (const item of host.querySelectorAll('.loupe-thumb[data-index]')) {
        item.addEventListener('click', () => {
            index = Number(item.dataset.index);
            render();
        });
    }
    const currentThumb = host.querySelector('.cur');
    if (currentThumb) currentThumb.scrollIntoView({ block: 'nearest', inline: 'center' });
}

function preloadNeighbors() {
    for (const neighbor of [images()[index + 1], images()[index - 1]]) {
        if (!neighbor) continue;
        const preload = new Image();
        preload.src = thumbUrl('md', neighbor.id);
    }
}

function updateChrome() {
    const img = current();
    if (!img) return;
    document.getElementById('loupe-cap').textContent = caption(img);
    updateFlagControls();
    updateZoomChip();
}

function render() {
    const img = current();
    if (!img) {
        closeLoupe({ force: true });
        return;
    }
    renderToken += 1;
    fullImageLoadingId = null;
    const token = renderToken;
    const image = document.getElementById('loupe-img');
    image.dataset.tier = 'md';
    image.onload = () => {
        if (token !== renderToken) return;
        setImageMetrics();
    };
    image.src = thumbUrl('md', img.id);
    const size = imageSizeFromMetadata(img, image);
    naturalWidth = size.width;
    naturalHeight = size.height;
    zoomMode = 'fit';
    centerFit({ animate: false });
    updateChrome();
    renderStrip();
    preloadNeighbors();
    maybeRequestMore();
}

function resolveImageWaiters() {
    const remaining = [];
    for (const waiter of imageWaiters) {
        if (images().length > waiter.targetIndex && images()[waiter.targetIndex]) {
            waiter.resolve(true);
        } else {
            remaining.push(waiter);
        }
    }
    imageWaiters = remaining;
}

function waitForImage(targetIndex) {
    if (images().length > targetIndex && images()[targetIndex]) return Promise.resolve(true);
    return new Promise((resolve) => {
        const waiter = { targetIndex, resolve };
        imageWaiters.push(waiter);
        window.setTimeout(() => {
            imageWaiters = imageWaiters.filter((item) => item !== waiter);
            resolve(Boolean(images()[targetIndex]));
        }, LOAD_WAIT_MS);
    });
}

async function ensureImageAvailable(targetIndex) {
    if (!isGridScope()) return Boolean(images()[targetIndex]);
    if (images()[targetIndex]) return true;
    await requestMorePhotos();
    return waitForImage(targetIndex);
}

function maybeRequestMore() {
    if (!isGridScope()) return;
    if (scopeTotal() && index >= scopeTotal() - 1) return;
    if (images().length - index <= PREFETCH_AHEAD) requestMorePhotos();
}

export function openLoupe(target = 0) {
    sessionImages = Array.isArray(target.images) && target.images.length ? target.images : null;
    if (sessionImages) rememberImages(sessionImages);
    const list = images();
    const startIndex = typeof target === 'object' ? Number(target.index || 0) : Number(target);
    const id = typeof target === 'object' ? Number(target.id) : null;
    const resolvedIndex = id ? list.findIndex((img) => Number(img?.id) === id) : -1;
    returnCell = id
        ? document.querySelector(`.cell[data-id="${id}"]`)
        : document.querySelector(`.cell[data-idx="${startIndex}"]`);
    index = Math.max(0, Math.min(list.length - 1, resolvedIndex >= 0 ? resolvedIndex : startIndex));
    open = true;
    const root = document.getElementById('loupe');
    root.hidden = false;
    trapFocus(root, root);
    render();
}

export function closeLoupe(options = {}) {
    if (!open) return;
    if (!options.force && zoomMode !== 'fit') {
        centerFit();
        return;
    }
    open = false;
    sessionImages = null;
    imageWaiters = [];
    const root = document.getElementById('loupe');
    root.hidden = true;
    releaseFocus(root);
    if (returnCell) returnCell.focus({ preventScroll: true });
}

export function loupeOpen() {
    return open;
}

export async function navLoupe(delta) {
    if (!open) return;
    const targetIndex = index + delta;
    if (targetIndex < 0) return;
    if (scopeTotal() && targetIndex >= scopeTotal()) return;
    const available = await ensureImageAvailable(targetIndex);
    if (!available) return;
    index = targetIndex;
    render();
}

function toggleFitOneToOne(event) {
    const rect = stageRect();
    const clientX = event?.clientX ?? rect.left + rect.width / 2;
    const clientY = event?.clientY ?? rect.top + rect.height / 2;
    if (zoomMode === 'fit') zoomTo(1, clientX, clientY, 'one-to-one');
    else centerFit();
}

async function flagCurrent(flag) {
    const img = current();
    if (!img) return;
    const old = img.flag || 'unflagged';
    img.flag = flag;
    updateChrome();
    emit('flags', { imageIds: [img.id], flag });
    const result = await writeFlag(img.id, flag);
    if (!result || !result.ok) {
        img.flag = old;
        updateChrome();
        emit('flags', { imageIds: [img.id] });
        showToast("Flag change didn't save");
        return;
    }
    showToast(flag === 'picked' ? 'Picked' : flag === 'rejected' ? 'Rejected' : 'Flag cleared', {
        undo: async () => {
            img.flag = old;
            await writeFlag(img.id, old);
            updateChrome();
            emit('flags', { imageIds: [img.id] });
        },
    });
}

export function flagLoupeOrFocused(flag) {
    if (open) flagCurrent(flag);
    else {
        const img = viewState.images[viewState.focusIndex];
        if (img) applyFlags([img.id], flag);
    }
}

function ensureLoupeChrome() {
    const root = document.getElementById('loupe');
    const bar = document.getElementById('loupe-bar');
    const stage = document.getElementById('loupe-stage');
    const actions = document.getElementById('loupe-actions');
    const captionEl = document.getElementById('loupe-cap');
    const close = document.getElementById('loupe-close');
    const strip = document.getElementById('loupe-strip');

    if (captionEl.parentElement !== bar) bar.prepend(captionEl);
    if (!document.getElementById('loupe-zoom')) {
        const zoom = document.createElement('div');
        zoom.id = 'loupe-zoom';
        zoom.className = 'num';
        zoom.textContent = 'Fit';
        bar.insertBefore(zoom, actions);
    }
    if (close.parentElement !== actions) actions.append(close);
    if (strip.parentElement !== root) root.append(strip);
    if (stage.parentElement !== root) root.append(stage);
}

function bindPointer() {
    const stage = document.getElementById('loupe-stage');
    stage.addEventListener('click', (event) => {
        if (dragState?.moved) {
            dragState = null;
            updateCursor();
            return;
        }
        if (!event.target.closest('#loupe-img')) return;
        toggleFitOneToOne(event);
    });
    stage.addEventListener('dblclick', (event) => {
        if (!event.target.closest('#loupe-img')) return;
        event.preventDefault();
        centerFit();
    });
    stage.addEventListener('wheel', (event) => {
        if (!event.target.closest('#loupe-img') && zoomMode === 'fit') return;
        event.preventDefault();
        const steps = Math.max(-4, Math.min(4, -event.deltaY / 100));
        const factor = ZOOM_STEP ** steps;
        zoomTo(scale * factor, event.clientX, event.clientY, 'custom');
    }, { passive: false });
    stage.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || zoomMode === 'fit') return;
        dragState = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            panX,
            panY,
            moved: false,
            dragging: true,
        };
        stage.setPointerCapture(event.pointerId);
        updateCursor();
        event.preventDefault();
    });
    stage.addEventListener('pointermove', (event) => {
        if (!dragState?.dragging || dragState.pointerId !== event.pointerId) return;
        const dx = event.clientX - dragState.startX;
        const dy = event.clientY - dragState.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragState.moved = true;
        panX = dragState.panX + dx;
        panY = dragState.panY + dy;
        clampPan();
        applyTransform();
    });
    stage.addEventListener('pointerup', (event) => {
        if (!dragState || dragState.pointerId !== event.pointerId) return;
        dragState.dragging = false;
        updateCursor();
    });
    stage.addEventListener('pointercancel', () => {
        dragState = null;
        updateCursor();
    });
}

function bindKeyboard() {
    window.addEventListener('keydown', (event) => {
        if (!open || event.ctrlKey || event.metaKey || event.altKey) return;
        const key = event.key.toLowerCase();
        if (event.key === ' ' || key === 'z') {
            event.preventDefault();
            event.stopImmediatePropagation();
            toggleFitOneToOne();
        }
    }, true);
}

export function initLoupe() {
    ensureLoupeChrome();
    bindPointer();
    bindKeyboard();
    on('loupe:open', ({ id, index: startIndex, images: sourceImages }) => openLoupe({ id, index: startIndex, images: sourceImages }));
    document.getElementById('loupe-prev').addEventListener('click', (event) => {
        event.stopPropagation();
        navLoupe(-1);
    });
    document.getElementById('loupe-next').addEventListener('click', (event) => {
        event.stopPropagation();
        navLoupe(1);
    });
    document.getElementById('loupe-close').addEventListener('click', () => closeLoupe({ force: true }));
    document.getElementById('lp-pick').addEventListener('click', () => flagCurrent('picked'));
    document.getElementById('lp-reject').addEventListener('click', () => flagCurrent('rejected'));
    document.getElementById('lp-unflag').addEventListener('click', () => flagCurrent('unflagged'));
    document.getElementById('lp-coll').addEventListener('click', () => {
        const img = current();
        if (img) openCollectionPicker([img.id]);
    });
    document.getElementById('lp-similar').addEventListener('click', () => {
        const img = current();
        if (img) emit('similar:find', { imageId: img.id });
    });
    on('flags', ({ imageIds } = {}) => {
        for (const id of imageIds || []) {
            const img = byId.get(Number(id));
            if (img && current() && Number(img.id) === Number(current().id)) updateChrome();
        }
    });
    on('images', () => {
        resolveImageWaiters();
        if (!open || !isGridScope()) return;
        if (index >= images().length) index = Math.max(0, images().length - 1);
        updateChrome();
        renderStrip();
    });
    window.addEventListener('resize', () => {
        if (!open || !current()) return;
        if (zoomMode === 'fit') centerFit({ animate: false });
        else setImageMetrics({ animate: false });
    });
}
