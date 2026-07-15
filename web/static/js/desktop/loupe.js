import {
    byId, emit, on, rememberImages, setActiveLens, viewState,
} from './state.js';
import { getImageExif, getStack, thumbUrl, writeFlag } from './api.js';
import { applyFlags, beginFlagMutation, flagMutationIsLatest } from './selection.js';
import { openCollectionPicker } from './panel.js';
import { requestMorePhotos } from './grid.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

const ZOOM_STEP = 1.15;
const MAX_SCALE = 4;
const PREFETCH_AHEAD = 50;
const LOAD_WAIT_MS = 5000;
const KEYBOARD_PAN_STEP = 48;
const STRIP_ITEM_PITCH = 68;
const STRIP_OVERSCAN = 12;

let index = 0;
let open = false;
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
let lightMode = 'normal';
let infoMode = 'off';
let returnLens = 'grid';
let stripSignature = '';
let stripStart = 0;
let stripEnd = 0;
let stripScrollFrame = 0;
let previousStripIndex = -1;
const exifCache = new Map();
const INFO_MODES = ['off', 'basic', 'full'];
const RAW_EXTENSIONS = new Set(['arw', 'cr2', 'cr3', 'dng', 'nef', 'orf', 'raf', 'rw2']);
let versionStack = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&#34;', "'": '&#39;',
}[c]));

function images() {
    return sessionImages || viewState.images;
}

function current() {
    return images()[index] || null;
}

function isRaw(img) {
    const filename = String(img?.filename || img?.filepath || '');
    return RAW_EXTENSIONS.has(filename.split('.').pop().toLowerCase());
}

function isGridScope() {
    return !sessionImages;
}

function scopeTotal() {
    if (!isGridScope()) return images().length;
    return viewState.visibleImages || images().length;
}

function flagLabel(flag) {
    if (flag === 'picked') return 'Pick';
    if (flag === 'rejected') return 'Reject';
    return '';
}

function flagGlyph(flag) {
    if (flag === 'picked') return icon('star');
    if (flag === 'rejected') return icon('x');
    return '';
}

function caption(img) {
    const name = img.filename || img.id;
    const elo = Math.round(Number(img.elo) || 0);
    const flag = flagLabel(img.flag || 'unflagged');
    return [name, `${index + 1} / ${scopeTotal() || images().length}`, `Rating ${elo}`, flag]
        .filter(Boolean)
        .map(esc)
        .join(' · ');
}

function bytes(value) {
    const n = Number(value) || 0;
    if (!n) return '';
    if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
    if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    if (n >= 1024) return `${Math.round(n / 1024)} KB`;
    return `${n} B`;
}

function shortDate(value) {
    if (!value) return '';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
}

function cameraLabel(img) {
    return [img.camera_make, img.camera_model].filter(Boolean).join(' ');
}

function dimLabel(img) {
    const w = Number(img.width) || naturalWidth;
    const h = Number(img.height) || naturalHeight;
    return w && h ? `${Math.round(w)} × ${Math.round(h)}` : '';
}

function exposureLine(img, exif = {}) {
    const metadata = { ...img, ...exif };
    const iso = metadata.iso ? `ISO ${String(metadata.iso).replace(/^ISO\s*/i, '')}` : '';
    return [metadata.focal_length, metadata.aperture, metadata.shutter_speed, iso]
        .filter(Boolean)
        .join(' · ');
}

async function loadExposure(img) {
    const imageId = Number(img?.id);
    if (!imageId || exifCache.has(imageId)) return;
    exifCache.set(imageId, null);
    try {
        const data = await getImageExif(imageId);
        exifCache.set(imageId, (data && data.exif) || {});
    } catch {
        exifCache.set(imageId, {});
    }
    if (open && currentIs(imageId)) updateInfoOverlay();
}

function updateInfoOverlay() {
    const host = document.getElementById('loupe-info');
    const img = current();
    updateInfoControl();
    if (!host || !img || infoMode === 'off') {
        if (host) host.hidden = true;
        return;
    }
    const name = img.filename || `Photo ${img.id}`;
    const date = shortDate(img.date_taken);
    const exposure = exposureLine(img, exifCache.get(Number(img.id)) || {});
    const basic = [date, exposure].filter(Boolean);
    const full = [
        cameraLabel(img),
        img.lens,
        [dimLabel(img), bytes(img.file_size)].filter(Boolean).join(' · '),
    ].filter(Boolean);
    host.innerHTML = `<b>${esc(name)}</b>`
        + basic.map((line) => `<span>${esc(line)}</span>`).join('')
        + (infoMode === 'full' ? full.map((line) => `<span>${esc(line)}</span>`).join('') : '');
    host.hidden = false;
    loadExposure(img);
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
    if (!root) return;
    root.classList.toggle('can-zoom', fitScale < 0.999);
    root.classList.toggle('zoomed', zoomMode !== 'fit' && scale > fitScale + 0.001);
    root.classList.toggle('dragging', Boolean(dragState?.dragging));
}

function updateInfoControl() {
    const button = document.getElementById('lp-info');
    if (!button) return;
    const active = infoMode !== 'off';
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.setAttribute('aria-label', active ? `Info: ${infoMode}` : 'Info');
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
    preloadLargeTier();
}

function relativeFocus() {
    if (!naturalWidth || !naturalHeight || !scale) return { x: 0.5, y: 0.5 };
    const rect = stageRect();
    return {
        x: Math.max(0, Math.min(1, (rect.width / 2 - panX) / (naturalWidth * scale))),
        y: Math.max(0, Math.min(1, (rect.height / 2 - panY) / (naturalHeight * scale))),
    };
}

function restoreRelativeFocus(focus) {
    const rect = stageRect();
    panX = rect.width / 2 - focus.x * naturalWidth * scale;
    panY = rect.height / 2 - focus.y * naturalHeight * scale;
    clampPan();
}

function setImageMetrics({ animate = false, focus = null } = {}) {
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
    if (focus) restoreRelativeFocus(focus);
    else clampPan();
    applyTransform({ animate });
    requestFullImage();
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

function stripMarkup(start, end) {
    let markup = '';
    const list = images();
    for (let i = start; i < end; i += 1) {
        const img = list[i];
        if (!img) {
            markup += '<span aria-hidden="true" style="flex:0 0 62px"></span>';
            continue;
        }
        const glyph = flagGlyph(img.flag || 'unflagged');
        markup += `<button class="loupe-thumb ${i === index ? 'cur' : ''}" data-index="${i}" data-id="${esc(img.id)}" aria-label="Photo ${i + 1}"${i === index ? ' aria-current="true"' : ''}>`
            + `<img loading="lazy" decoding="async" src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" alt="">`
            + `<span class="loupe-thumb-flag" aria-hidden="true">${glyph}</span>`
            + '</button>';
    }
    return markup;
}

function stripWindow(host, targetIndex) {
    const visible = Math.max(1, Math.ceil(host.clientWidth / STRIP_ITEM_PITCH));
    const size = visible + STRIP_OVERSCAN * 2;
    const start = Math.max(0, Math.min(images().length - size, targetIndex - Math.floor(size / 2)));
    return { start, end: Math.min(images().length, start + size) };
}

function paintStrip(start, end, scrollLeft) {
    const host = document.getElementById('loupe-strip');
    const before = start * STRIP_ITEM_PITCH;
    const after = Math.max(0, images().length - end) * STRIP_ITEM_PITCH;
    host.innerHTML = (before ? `<span aria-hidden="true" style="flex:0 0 ${before}px"></span>` : '')
        + stripMarkup(start, end)
        + (after ? `<span aria-hidden="true" style="flex:0 0 ${after}px"></span>` : '');
    stripStart = start;
    stripEnd = end;
    host.scrollLeft = scrollLeft;
}

function renderStrip() {
    const host = document.getElementById('loupe-strip');
    const list = images();
    const nearStart = Math.max(0, index - STRIP_OVERSCAN);
    const nearEnd = Math.min(list.length, index + STRIP_OVERSCAN + 1);
    const signature = `${list.length}:${list.slice(nearStart, nearEnd).map((img) => Number(img?.id) || 0).join(',')}`;
    if (signature !== stripSignature || index < stripStart || index >= stripEnd) {
        const { start, end } = stripWindow(host, index);
        const centered = Math.max(0, index * STRIP_ITEM_PITCH - (host.clientWidth - STRIP_ITEM_PITCH) / 2);
        paintStrip(start, end, centered);
        stripSignature = signature;
        previousStripIndex = -1;
    }
    updateStrip();
}

function virtualizeStripScroll() {
    if (!open) return;
    const host = document.getElementById('loupe-strip');
    const visibleStart = Math.max(0, Math.floor(host.scrollLeft / STRIP_ITEM_PITCH));
    const visibleEnd = visibleStart + Math.max(1, Math.ceil(host.clientWidth / STRIP_ITEM_PITCH));
    if (visibleStart >= stripStart + STRIP_OVERSCAN / 2
        && visibleEnd <= stripEnd - STRIP_OVERSCAN / 2) return;
    const { start, end } = stripWindow(host, Math.floor((visibleStart + visibleEnd) / 2));
    if (start === stripStart && end === stripEnd) return;
    paintStrip(start, end, host.scrollLeft);
    previousStripIndex = index;
}

function updateStripFlag(imageId) {
    const item = document.querySelector(`#loupe-strip .loupe-thumb[data-id="${Number(imageId)}"]`);
    const img = images().find((candidate) => Number(candidate?.id) === Number(imageId));
    if (!item || !img) return;
    item.querySelector('.loupe-thumb-flag').innerHTML = flagGlyph(img.flag || 'unflagged');
}

function updateStrip() {
    const host = document.getElementById('loupe-strip');
    const previous = host.querySelector(`.loupe-thumb[data-index="${previousStripIndex}"]`);
    const next = host.querySelector(`.loupe-thumb[data-index="${index}"]`);
    if (previous && previous !== next) {
        previous.classList.remove('cur');
        previous.removeAttribute('aria-current');
    }
    if (next) {
        next.classList.add('cur');
        next.setAttribute('aria-current', 'true');
        next.scrollIntoView({ block: 'nearest', inline: 'center' });
    }
    previousStripIndex = index;
    updateStripFlag(current()?.id);
}

function preloadNeighbors() {
    for (const neighbor of [images()[index + 1], images()[index - 1]]) {
        if (!neighbor) continue;
        const preload = new Image();
        preload.src = thumbUrl('md', neighbor.id);
    }
}

function preloadLargeTier() {
    if (!open || zoomMode !== 'fit') return;
    for (const candidate of [images()[index - 1], current(), images()[index + 1]]) {
        if (!candidate) continue;
        const preload = new Image();
        preload.src = thumbUrl('lg', candidate.id);
    }
}

function updateChrome() {
    const img = current();
    if (!img) return;
    if (isGridScope()) viewState.focusIndex = index;
    emit('focus', { image: img, index });
    document.getElementById('loupe-cap').textContent = caption(img);
    updateFlagControls();
    updateZoomChip();
    updateInfoOverlay();
    const editRaw = document.getElementById('lp-edit-raw');
    if (editRaw) {
        const hasVersion = img.stack_kind === 'version' || versionStack?.kind === 'version';
        editRaw.hidden = !(hasVersion && !isRaw(img));
    }
}

function render() {
    const img = current();
    if (!img) {
        closeLoupe({ force: true });
        return;
    }
    const stickyZoom = zoomMode !== 'fit';
    const focus = stickyZoom ? relativeFocus() : null;
    renderToken += 1;
    fullImageLoadingId = null;
    const token = renderToken;
    const image = document.getElementById('loupe-img');
    image.dataset.imageId = String(img.id);
    image.dataset.tier = 'md';
    image.onload = () => {
        if (token !== renderToken) return;
        setImageMetrics({ focus });
    };
    image.src = thumbUrl('md', img.id);
    const size = imageSizeFromMetadata(img, image);
    naturalWidth = size.width;
    naturalHeight = size.height;
    if (stickyZoom) {
        fitScale = computeFitScale();
        scale = Math.max(fitScale, Math.min(MAX_SCALE, scale));
        restoreRelativeFocus(focus);
        applyTransform();
        requestFullImage();
    } else {
        centerFit({ animate: false });
    }
    updateChrome();
    renderStrip();
    preloadNeighbors();
    preloadLargeTier();
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
    const waiting = waitForImage(targetIndex);
    requestMorePhotos();
    return waiting;
}

function maybeRequestMore() {
    if (!isGridScope()) return;
    if (scopeTotal() && index >= scopeTotal() - 1) return;
    if (images().length - index <= PREFETCH_AHEAD) requestMorePhotos();
}

export function openLoupe(target = 0) {
    returnLens = typeof target === 'object' && target.returnLens ? target.returnLens : viewState.activeLens || 'grid';
    sessionImages = Array.isArray(target.images) && target.images.length ? target.images : null;
    if (sessionImages) rememberImages(sessionImages);
    const list = images();
    const startIndex = typeof target === 'object' ? Number(target.index || 0) : Number(target);
    const id = typeof target === 'object' ? Number(target.id) : null;
    const resolvedIndex = id ? list.findIndex((img) => Number(img?.id) === id) : -1;
    index = Math.max(0, Math.min(list.length - 1, resolvedIndex >= 0 ? resolvedIndex : startIndex));
    versionStack = null;
    centerFit({ animate: false });
    open = true;
    setLightMode('normal');
    setActiveLens('loupe');
    const root = document.getElementById('loupe');
    if (!root.hidden) render();
}

export function closeLoupe(options = {}) {
    if (!open) return;
    setLightMode('normal');
    centerFit({ animate: false });
    setActiveLens(returnLens === 'loupe' ? 'grid' : returnLens);
}

export function mountLoupe() {
    if (!open) open = true;
    document.getElementById('view-loupe').classList.add('active');
    const root = document.getElementById('loupe');
    root.hidden = false;
    setLightMode(lightMode);
    render();
}

export function unmountLoupe() {
    const endedImageId = Number(current()?.id);
    open = false;
    sessionImages = null;
    versionStack = null;
    imageWaiters = [];
    const root = document.getElementById('loupe');
    root.hidden = true;
    document.getElementById('view-loupe').classList.remove('active');
    setLightMode('normal');
    stripSignature = '';
    stripStart = 0;
    stripEnd = 0;
    previousStripIndex = -1;
    requestAnimationFrame(() => {
        const target = document.querySelector(`.cell[data-id="${endedImageId}"]`)
            || document.querySelector(`[data-image-id="${endedImageId}"]`);
        if (!target) return;
        target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        target.focus({ preventScroll: true });
    });
}

export function loupeOpen() {
    return open;
}

export async function navLoupe(delta) {
    if (!open) return;
    const targetIndex = index + delta;
    if (targetIndex < 0) return;
    if (scopeTotal() && targetIndex >= scopeTotal()) return;
    const pending = !images()[targetIndex];
    if (pending) document.getElementById('loupe-cap').textContent = 'Loading next photo…';
    const available = await ensureImageAvailable(targetIndex);
    if (!available) {
        if (pending) updateChrome();
        return;
    }
    index = targetIndex;
    render();
}

export async function navLoupeTo(targetIndex) {
    if (!open || !images().length) return;
    const bounded = Math.max(0, Math.min(images().length - 1, Number(targetIndex)));
    if (bounded === index) return;
    index = bounded;
    render();
}

export function loupeImageId() {
    return Number(current()?.id) || null;
}

async function versionStackForCurrent() {
    const image = current();
    if (versionStack?.kind === 'version'
        && (versionStack.members || []).some((member) => Number(member?.id) === Number(image?.id))) {
        return versionStack;
    }
    const stackId = Number(image?.stack_id) || 0;
    if (!stackId) return null;
    try {
        const stack = await getStack(stackId);
        if (stack?.kind !== 'version') return null;
        versionStack = stack;
        return stack;
    } catch {
        showToast("Version stack couldn't load");
        return null;
    }
}

export async function toggleLoupeVersion() {
    if (!open) return false;
    const stack = await versionStackForCurrent();
    if (!stack) return false;
    const members = Array.isArray(stack.members) ? stack.members : [];
    const raw = members.find(isRaw);
    const image = current();
    const edit = members.find((member) => Number(member?.id) === Number(stack.representative?.id))
        || members.find((member) => !isRaw(member));
    const target = isRaw(image) ? edit : raw;
    if (!target) return false;
    sessionImages = members;
    rememberImages(members);
    index = Math.max(0, members.findIndex((member) => Number(member?.id) === Number(target.id)));
    render();
    showToast(isRaw(target) ? 'RAW original' : 'Finished edit');
    return true;
}

async function editRawFromLoupe() {
    const stack = await versionStackForCurrent();
    const raw = (stack?.members || []).find(isRaw);
    if (!raw) return;
    emit('develop:open-image', { image: raw });
}

export function fitLoupe() {
    if (open) centerFit();
}

export function zoomLoupeBy(direction) {
    if (!open) return;
    const rect = stageRect();
    const base = zoomMode === 'fit' ? fitScale : scale;
    zoomTo(base * (ZOOM_STEP ** Number(direction)), rect.left + rect.width / 2, rect.top + rect.height / 2);
}

export function panLoupe(dx, dy) {
    if (!open || zoomMode === 'fit') return false;
    panX += Number(dx) * KEYBOARD_PAN_STEP;
    panY += Number(dy) * KEYBOARD_PAN_STEP;
    clampPan();
    applyTransform();
    return true;
}

function setLightMode(next) {
    lightMode = next === 'dim' || next === 'lights-out' ? next : 'normal';
    document.body.classList.toggle('loupe-lights-dim', lightMode === 'dim');
    document.body.classList.toggle('loupe-lights-out', lightMode === 'lights-out');
    const root = document.getElementById('loupe');
    if (root) root.dataset.lights = lightMode;
    const button = document.getElementById('lp-lights');
    if (button) {
        button.classList.toggle('active', lightMode !== 'normal');
        const label = lightMode === 'normal' ? 'Lights' : `Lights: ${lightMode}`;
        button.setAttribute('aria-label', label);
        button.setAttribute('data-tip', `${label} (L)`);
    }
}

export function toggleLoupeLights() {
    if (!open) return;
    if (lightMode === 'normal') setLightMode('dim');
    else if (lightMode === 'dim') setLightMode('lights-out');
    else setLightMode('normal');
}

export function toggleLoupeInfo() {
    const indexOfMode = INFO_MODES.indexOf(infoMode);
    infoMode = INFO_MODES[(indexOfMode + 1) % INFO_MODES.length];
    updateInfoOverlay();
}

function toggleFitOneToOne(event) {
    const rect = stageRect();
    const clientX = event?.clientX ?? rect.left + rect.width / 2;
    const clientY = event?.clientY ?? rect.top + rect.height / 2;
    if (zoomMode === 'fit') zoomTo(1, clientX, clientY, 'one-to-one');
    else centerFit();
}

function eventHitsImage(event) {
    if (event?.target?.closest?.('#loupe-img')) return true;
    if (event && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
        return document.elementFromPoint(event.clientX, event.clientY)?.closest?.('#loupe-img');
    }
    return false;
}

function currentIs(imageId) {
    return current() && Number(current().id) === Number(imageId);
}

async function flagCurrent(flag) {
    const img = current();
    if (!img) return;
    const imageId = Number(img.id);
    const old = img.flag || 'unflagged';
    const version = beginFlagMutation(imageId);
    img.flag = flag;
    if (currentIs(imageId)) updateChrome();
    emit('flags', { imageIds: [imageId], flag });
    if (viewState.prefs.autoAdvanceFlags) navLoupe(1);
    const result = await writeFlag(imageId, flag);
    if (!result || !result.ok) {
        if (!flagMutationIsLatest(imageId, version)) return;
        img.flag = old;
        if (currentIs(imageId)) updateChrome();
        emit('flags', { imageIds: [imageId] });
        showToast('Couldn’t save flag');
        return;
    }
    emit('flags', { imageIds: [imageId], flag, committed: true });
    showToast(flag === 'picked' ? 'Picked' : flag === 'rejected' ? 'Rejected' : 'Flag cleared', {
        undo: async () => {
            const undoVersion = beginFlagMutation(imageId);
            img.flag = old;
            if (currentIs(imageId)) updateChrome();
            emit('flags', { imageIds: [imageId] });
            const undoResult = await writeFlag(imageId, old);
            if (!(undoResult && undoResult.ok) && flagMutationIsLatest(imageId, undoVersion)) {
                img.flag = flag;
                if (currentIs(imageId)) updateChrome();
                emit('flags', { imageIds: [imageId] });
                showToast('Couldn’t undo');
            }
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
        zoom.dataset.tip = 'Fit / 100% · Space';
        zoom.textContent = 'Fit';
        bar.insertBefore(zoom, actions);
    } else {
        document.getElementById('loupe-zoom').dataset.tip = 'Fit / 100% · Space';
    }
    if (close.parentElement !== actions) actions.append(close);
    if (!document.getElementById('lp-edit-raw')) {
        const editRaw = document.createElement('button');
        editRaw.id = 'lp-edit-raw';
        editRaw.className = 'icon-btn';
        editRaw.dataset.tip = 'Edit RAW original';
        editRaw.setAttribute('aria-label', 'Edit RAW original');
        editRaw.textContent = 'Edit RAW';
        editRaw.hidden = true;
        actions.insertBefore(editRaw, close);
    }
    if (strip.parentElement !== root) root.append(strip);
    if (stage.parentElement !== root) root.append(stage);
}

function bindPointer() {
    const stage = document.getElementById('loupe-stage');
    stage.addEventListener('dblclick', (event) => {
        if (!eventHitsImage(event)) return;
        toggleFitOneToOne(event);
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
        dragState = null;
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
        if (key === 'v') {
            event.preventDefault();
            event.stopImmediatePropagation();
            toggleLoupeVersion();
        } else if (event.key === ' ' || key === 'z') {
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
    const strip = document.getElementById('loupe-strip');
    strip.addEventListener('click', (event) => {
        const item = event.target.closest('.loupe-thumb[data-index]');
        if (!item) return;
        index = Number(item.dataset.index);
        render();
    });
    strip.addEventListener('scroll', () => {
        window.cancelAnimationFrame(stripScrollFrame);
        stripScrollFrame = window.requestAnimationFrame(virtualizeStripScroll);
    }, { passive: true });
    on('loupe:open', ({
        id, index: startIndex, images: sourceImages, returnLens: sourceLens,
    }) => openLoupe({
        id, index: startIndex, images: sourceImages, returnLens: sourceLens,
    }));
    document.getElementById('loupe-prev').addEventListener('click', (event) => {
        event.stopPropagation();
        navLoupe(-1);
    });
    document.getElementById('loupe-next').addEventListener('click', (event) => {
        event.stopPropagation();
        navLoupe(1);
    });
    document.getElementById('loupe-close').addEventListener('click', () => closeLoupe({ force: true }));
    document.getElementById('lp-edit-raw')?.addEventListener('click', () => { editRawFromLoupe(); });
    document.getElementById('lp-pick').addEventListener('click', () => flagCurrent('picked'));
    document.getElementById('lp-reject').addEventListener('click', () => flagCurrent('rejected'));
    document.getElementById('lp-unflag').addEventListener('click', () => flagCurrent('unflagged'));
    document.getElementById('lp-info')?.addEventListener('click', () => toggleLoupeInfo());
    document.getElementById('lp-lights')?.addEventListener('click', () => toggleLoupeLights());
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
            updateStripFlag(id);
            if (img && current() && Number(img.id) === Number(current().id)) updateChrome();
        }
    });
    on('images', () => {
        resolveImageWaiters();
        if (!open || !isGridScope()) return;
        if (index >= images().length) index = Math.max(0, images().length - 1);
        if (Number(document.getElementById('loupe-img').dataset.imageId) !== Number(current()?.id)) {
            render();
            return;
        }
        updateChrome();
        renderStrip();
    });
    window.addEventListener('resize', () => {
        if (!open || !current()) return;
        if (zoomMode === 'fit') centerFit({ animate: false });
        else setImageMetrics({ animate: false });
    });
}
