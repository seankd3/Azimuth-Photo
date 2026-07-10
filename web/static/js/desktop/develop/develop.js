import { thumbUrl } from '../api.js';
import { on, selection, viewState } from '../state.js';
import { showToast } from '../toast.js';
import { CropController } from './crop.js';
import { DevelopRenderer, renderSyntheticPixels } from './gl.js';
import { DevelopHistogram } from './histogram.js';
import { DevelopPanels } from './panels.js';
import { mountPresetsPanel } from './presets.js';
import { openExportDialog, openSyncDialog } from './export_dialog.js';

const RAW_EXTENSIONS = new Set(['dng', 'cr3', 'cr2', 'exr']);
const stateCache = new Map();
const saveTimers = new Map();
let clipboardSettings = null;
let mounted = false;
let currentImage = null;
let renderer = null;
let panels = null;
let crop = null;
let histogram = null;
let masking = null;
let loadingToken = 0;
let beforeHeld = false;
let spaceHeld = false;
let activePopover = null;

const root = document.getElementById('view-develop');
const stage = document.getElementById('develop-stage');
const canvas = document.getElementById('develop-canvas');
const placeholder = document.getElementById('develop-placeholder');
const status = document.getElementById('develop-status');
const panelHost = document.getElementById('develop-panels');
const filmstrip = document.getElementById('develop-filmstrip');
const toolbar = document.getElementById('develop-toolbar');

function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
}

function isRaw(image) {
    const name = String(image?.filename || image?.filepath || image?.path || '');
    return RAW_EXTENSIONS.has(name.split('.').pop().toLowerCase());
}

function chosenImage() {
    const selectedId = selection.values().next().value;
    if (selectedId != null) {
        const selected = viewState.images.find((image) => Number(image.id) === Number(selectedId));
        if (selected) return selected;
    }
    return viewState.images[viewState.focusIndex] || viewState.images[0] || null;
}

function setStatus(message = '', { busy = false, error = false } = {}) {
    status.hidden = !message;
    status.classList.toggle('busy', busy);
    status.classList.toggle('error', error);
    status.querySelector('span').textContent = message;
}

function originSettings(payload) {
    const direct = payload?.origin_settings || payload?.meta?.origin_settings;
    if (direct && typeof direct === 'object') return clone(direct);
    const history = Array.isArray(payload?.history) ? [...payload.history].reverse() : [];
    const origin = history.find((entry) => /import|xmp|origin/i.test(entry.label || '')) || history.at(-1);
    if (!origin?.settings) return {};
    try { return typeof origin.settings === 'string' ? JSON.parse(origin.settings) : clone(origin.settings); }
    catch { return {}; }
}

async function fetchDevelop(imageId) {
    const response = await fetch(`/api/develop/${imageId}`, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(response.status === 404 ? 'Develop settings are not ready for this photo.' : 'Could not load develop settings.');
    return response.json();
}

function parseBase(buffer, scale = 1) {
    if (buffer.byteLength < 16) throw new Error('Develop base preview is incomplete.');
    const bytes = new Uint8Array(buffer, 0, 8);
    const magic = String.fromCharCode(...bytes);
    if (magic !== 'PABASE1\0') throw new Error('Develop base preview has an invalid header.');
    const header = new DataView(buffer, 8, 8);
    const width = header.getUint32(0, true);
    const height = header.getUint32(4, true);
    const rgb = new Uint16Array(buffer, 16);
    if (!width || !height || rgb.length < width * height * 3) throw new Error('Develop base preview has invalid dimensions.');
    const rgba = new Float32Array(width * height * 4);
    for (let source = 0, target = 0; target < rgba.length; source += 3, target += 4) {
        const linearScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
        rgba[target] = (rgb[source] / 65535) * linearScale;
        rgba[target + 1] = (rgb[source + 1] / 65535) * linearScale;
        rgba[target + 2] = (rgb[source + 2] / 65535) * linearScale;
        rgba[target + 3] = 1;
    }
    return { width, height, rgba };
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchBaseWithRetry(imageId, token, scale = 1) {
    for (let attempt = 0; attempt <= 20; attempt += 1) {
        if (token !== loadingToken) return null;
        if (attempt === 2) setStatus('Reading RAW from disk…', { busy: true });
        if (attempt === 8) setStatus('Developing preview…', { busy: true });
        const response = await fetch(`/api/develop/${imageId}/base.bin`);
        if (response.ok) return parseBase(await response.arrayBuffer(), scale);
        if (![404, 503].includes(response.status)) throw new Error('The RAW preview could not be loaded.');
        if (attempt < 20) await delay(1000);
    }
    throw new Error('The RAW preview is still being prepared. Try again in a moment.');
}

async function paintPlaceholder(imageId, token) {
    try {
        const response = await fetch(`/api/develop/${imageId}/base.jpg`);
        if (!response.ok || token !== loadingToken) return;
        const url = URL.createObjectURL(await response.blob());
        const previous = placeholder.dataset.objectUrl;
        placeholder.dataset.objectUrl = url;
        placeholder.src = url;
        placeholder.hidden = false;
        if (previous) URL.revokeObjectURL(previous);
    } catch { /* The staged base loader remains visible. */ }
}

function scheduleSave(label = 'Develop adjustment') {
    if (!currentImage) return;
    const imageId = Number(currentImage.id);
    clearTimeout(saveTimers.get(imageId));
    saveTimers.set(imageId, setTimeout(() => {
        const entry = stateCache.get(imageId);
        if (!entry) return;
        fetch(`/api/develop/${imageId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: entry.settings, label }),
        }).catch(() => {});
    }, 400));
}

function applySettings(entry) {
    panels.setSettings(entry.settings);
    crop.setSettings(entry.settings);
    renderer?.setSettings(entry.settings, entry.meta);
}

function applyPresetSettings(settings, label = 'Preset') {
    if (!currentImage) return;
    const entry = stateCache.get(Number(currentImage.id));
    if (!entry) return;
    entry.undo.push(clone(entry.settings));
    if (entry.undo.length > 100) entry.undo.shift();
    entry.redo.length = 0;
    entry.settings = { ...entry.settings, ...clone(settings) };
    applySettings(entry);
    scheduleSave(label);
}

function settingsChanged(key, value, label, { history = true, previousSettings = null } = {}) {
    if (!currentImage || beforeHeld) return;
    const entry = stateCache.get(Number(currentImage.id));
    if (!entry) return;
    if (history) {
        entry.undo.push(clone(previousSettings || entry.settings));
        if (entry.undo.length > 100) entry.undo.shift();
        entry.redo.length = 0;
    }
    if (value === undefined) delete entry.settings[key];
    else entry.settings[key] = value;
    renderer?.setSettings(entry.settings, entry.meta);
    scheduleSave(label);
}

function undo() {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!entry?.undo.length) return;
    entry.redo.push(clone(entry.settings));
    entry.settings = entry.undo.pop();
    applySettings(entry);
    scheduleSave('Undo');
}

function redo() {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!entry?.redo.length) return;
    entry.undo.push(clone(entry.settings));
    entry.settings = entry.redo.pop();
    applySettings(entry);
    scheduleSave('Redo');
}

function syncFilmstrip() {
    filmstrip.innerHTML = viewState.images.map((image, index) => `<button class="develop-thumb ${Number(image.id) === Number(currentImage?.id) ? 'cur' : ''} ${isRaw(image) ? '' : 'not-raw'}" data-index="${index}" data-tip="${isRaw(image) ? 'Open in Develop' : 'RAW editing only (for now)'}" aria-label="${String(image.filename || `Photo ${index + 1}`).replaceAll('"', '&quot;')}"><img src="${image.thumb_url || thumbUrl('sm', image.id)}" loading="lazy" decoding="async" alt=""></button>`).join('');
    filmstrip.querySelector('.cur')?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}

function pregenNeighbors(image) {
    const index = viewState.images.findIndex((item) => Number(item.id) === Number(image.id));
    const imageIds = viewState.images.slice(Math.max(0, index - 2), index + 3).filter(isRaw).map((item) => Number(item.id));
    if (!imageIds.length) return;
    fetch('/api/develop/pregen', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_ids: imageIds }) }).catch(() => {});
}

async function openImage(image) {
    const token = ++loadingToken;
    currentImage = image;
    syncFilmstrip();
    closePopover();
    placeholder.hidden = true;
    canvas.classList.remove('ready');
    if (!image) {
        setStatus('Choose a RAW photo in Grid, then open Develop.', { error: false });
        panelHost.toggleAttribute('inert', true);
        return;
    }
    if (!isRaw(image)) {
        setStatus('RAW editing only (for now)', { error: false });
        panelHost.toggleAttribute('inert', true);
        return;
    }
    panelHost.toggleAttribute('inert', false);
    pregenNeighbors(image);
    setStatus('Loading develop settings…', { busy: true });
    setTimeout(() => {
        if (token === loadingToken && !canvas.classList.contains('ready')) setStatus('Reading RAW from disk…', { busy: true });
    }, 1000);
    setTimeout(() => {
        if (token === loadingToken && !canvas.classList.contains('ready')) setStatus('Developing preview…', { busy: true });
    }, 8000);
    paintPlaceholder(image.id, token);
    try {
        let entry = stateCache.get(Number(image.id));
        if (!entry) {
            const payload = await fetchDevelop(image.id);
            entry = {
                settings: clone(payload.settings), origin: originSettings(payload), meta: {
                    ...(payload.meta || {}),
                    as_shot_temperature: payload.meta?.as_shot?.temperature ?? payload.meta?.as_shot_temperature,
                    as_shot_tint: payload.meta?.as_shot?.tint ?? payload.meta?.as_shot_tint,
                    color: payload.meta?.color ?? null,
                },
                undo: [], redo: [], serverHistory: payload.history || [],
            };
            stateCache.set(Number(image.id), entry);
        }
        if (token !== loadingToken) return;
        applySettings(entry);
        if (!renderer) {
            setStatus('WebGL2 is required for Develop.', { error: true });
            return;
        }
        setStatus('Reading RAW from disk…', { busy: true });
        const base = await fetchBaseWithRetry(image.id, token, Number(entry.meta?.hdr?.scale) || 1);
        if (!base || token !== loadingToken) return;
        renderer.uploadSource(base.rgba, base.width, base.height);
        renderer.setSettings(entry.settings, entry.meta);
        masking?.rebuildRasters();
        canvas.classList.add('ready');
        placeholder.hidden = true;
        setStatus('');
        crop.setSettings(entry.settings);
    } catch (error) {
        if (token === loadingToken) setStatus(error.message || 'Develop could not open this photo.', { error: true });
    }
}

function nav(delta) {
    if (!viewState.images.length) return;
    const index = currentImage ? viewState.images.findIndex((image) => Number(image.id) === Number(currentImage.id)) : viewState.focusIndex;
    const next = Math.max(0, Math.min(viewState.images.length - 1, index + delta));
    viewState.focusIndex = next;
    openImage(viewState.images[next]);
}

function setZoom(zoomed) {
    stage.classList.toggle('zoomed', Boolean(zoomed));
    toolbar.querySelector('[data-action="zoom"]').setAttribute('aria-pressed', String(Boolean(zoomed)));
}

function showBefore(show) {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!entry || !renderer) return;
    beforeHeld = show;
    renderer.setSettings(show ? entry.origin : entry.settings, entry.meta);
    toolbar.querySelector('[data-action="before"]').setAttribute('aria-pressed', String(show));
}

function closePopover() {
    activePopover?.remove();
    activePopover = null;
}

function anchoredPopover(button, html) {
    closePopover();
    activePopover = document.createElement('div');
    activePopover.className = 'develop-popover';
    activePopover.innerHTML = html;
    root.appendChild(activePopover);
    const buttonRect = button.getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    activePopover.style.left = `${Math.max(8, Math.min(rootRect.width - 250, buttonRect.left - rootRect.left))}px`;
    activePopover.style.top = `${buttonRect.bottom - rootRect.top + 6}px`;
    return activePopover;
}

function openCopyPopover(button) {
    const popover = anchoredPopover(button, '<strong>Copy settings</strong><label data-tip="Copy all rendered settings"><input type="checkbox" checked disabled> All adjustments</label><button data-copy-confirm data-tip="Copy all settings">Copy</button>');
    popover.querySelector('[data-copy-confirm]').addEventListener('click', () => {
        const entry = currentImage && stateCache.get(Number(currentImage.id));
        clipboardSettings = clone(entry?.settings || {});
        closePopover();
        showToast('Develop settings copied');
    });
}

function pasteSettings() {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!entry || !clipboardSettings) return showToast('Copy develop settings first');
    entry.undo.push(clone(entry.settings));
    entry.settings = clone(clipboardSettings);
    entry.redo.length = 0;
    applySettings(entry);
    scheduleSave('Paste Settings');
    showToast('Develop settings pasted');
}

function openExportPopover(button) {
    openExportDialog({
        button,
        image: currentImage,
        anchoredPopover,
        closePopover,
        showToast,
        isRaw,
    });
}

function openSyncPopover(button) {
    if (!currentImage || !isRaw(currentImage)) return;
    const targetIds = selection.size
        ? [...selection].map(Number)
        : viewState.images.map((image) => Number(image.id)).filter((id) => id > 0);
    openSyncDialog({
        button,
        sourceId: Number(currentImage.id),
        targetIds,
        anchoredPopover,
        closePopover,
        showToast,
    });
}

async function resetCurrent() {
    if (!currentImage || !isRaw(currentImage)) return;
    try {
        const response = await fetch(`/api/develop/${currentImage.id}/reset`, { method: 'POST' });
        if (!response.ok) throw new Error();
        const payload = await response.json().catch(() => null);
        const entry = stateCache.get(Number(currentImage.id));
        entry.undo.push(clone(entry.settings));
        entry.settings = clone(payload?.settings || entry.origin || {});
        entry.redo.length = 0;
        applySettings(entry);
        showToast('Develop settings reset');
    } catch { showToast('Could not reset develop settings'); }
}

export function openDevelop() {
    mounted = true;
    root.classList.add('active');
    document.body.classList.add('develop-active');
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        const active = button.dataset.view === 'develop';
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', String(active));
    }
    requestAnimationFrame(() => {
        ensureRenderer();
        openImage(chosenImage());
    });
}

function unmount() {
    mounted = false;
    ++loadingToken;
    closePopover();
    showBefore(false);
    root.classList.remove('active');
    document.body.classList.remove('develop-active');
    crop.setActive(false);
}

export function developOpen() {
    return mounted;
}

function updateTabState() {
    const tab = document.querySelector('#view-switch [data-view="develop"]');
    const image = chosenImage();
    const enabled = !image || isRaw(image);
    tab.setAttribute('aria-disabled', String(!enabled));
    tab.dataset.tip = enabled ? 'Develop · D' : 'RAW editing only (for now)';
}

function ensureRenderer() {
    if (renderer && !renderer.gl.isContextLost()) return true;
    try {
        renderer = new DevelopRenderer(canvas);
        canvas.addEventListener('develop:rendered', () => histogram.updateFromRenderer(renderer));
        return true;
    } catch (error) {
        renderer = null;
        setStatus(error.message || 'WebGL2 is required for Develop.', { error: true });
        return false;
    }
}

function bindUi() {
    document.querySelector('#view-switch [data-view="develop"]').addEventListener('click', (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
        openDevelop();
    }, true);
    for (const button of document.querySelectorAll('#view-switch button[data-view]:not([data-view="develop"])')) {
        button.addEventListener('click', () => { if (mounted) unmount(); });
    }
    filmstrip.addEventListener('click', (event) => {
        const item = event.target.closest('[data-index]');
        if (!item) return;
        const index = Number(item.dataset.index);
        viewState.focusIndex = index;
        openImage(viewState.images[index]);
    });
    toolbar.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const action = button.dataset.action;
        if (action === 'prev') nav(-1);
        else if (action === 'next') nav(1);
        else if (action === 'zoom') setZoom(!stage.classList.contains('zoomed'));
        else if (action === 'copy') openCopyPopover(button);
        else if (action === 'paste') pasteSettings();
        else if (action === 'reset') resetCurrent();
        else if (action === 'sync') openSyncPopover(button);
        else if (action === 'export') openExportPopover(button);
    });
    if (!toolbar.querySelector('[data-action="sync"]')) {
        const exportButton = toolbar.querySelector('[data-action="export"]');
        const syncButton = document.createElement('button');
        syncButton.dataset.action = 'sync';
        syncButton.dataset.tip = 'Sync settings to grid selection';
        syncButton.textContent = 'Sync…';
        exportButton?.parentNode?.insertBefore(syncButton, exportButton);
    }
    const beforeButton = toolbar.querySelector('[data-action="before"]');
    beforeButton.addEventListener('pointerdown', () => showBefore(true));
    for (const eventName of ['pointerup', 'pointercancel', 'pointerleave']) beforeButton.addEventListener(eventName, () => showBefore(false));
    stage.addEventListener('dblclick', (event) => {
        if (!crop.active && !masking?.mode && !event.target.closest('button')) setZoom(!stage.classList.contains('zoomed'));
    });
    document.addEventListener('pointerdown', (event) => {
        if (activePopover && !activePopover.contains(event.target) && !event.target.closest('[data-action="copy"], [data-action="export"], [data-action="sync"]')) closePopover();
    });
    document.addEventListener('keydown', handleKey, true);
    document.addEventListener('keyup', handleKeyUp, true);
    on('selection', updateTabState);
    on('focus', updateTabState);
    on('scope', updateTabState);
}

function editingField(event) {
    return event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || event.target?.isContentEditable;
}

function handleKey(event) {
    if (!mounted || editingField(event)) return;
    if (masking?.keydown(event)) return;
    const key = event.key.toLowerCase();
    if (event.ctrlKey || event.metaKey) {
        if (key === 'z') { event.preventDefault(); event.stopImmediatePropagation(); event.shiftKey ? redo() : undo(); }
        else if (event.shiftKey && key === 'c') { event.preventDefault(); event.stopImmediatePropagation(); openCopyPopover(toolbar.querySelector('[data-action="copy"]')); }
        else if (event.shiftKey && key === 'v') { event.preventDefault(); event.stopImmediatePropagation(); pasteSettings(); }
        return;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); event.stopImmediatePropagation(); nav(event.key === 'ArrowLeft' ? -1 : 1);
    } else if (event.key === '\\') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) showBefore(true);
    } else if (key === 'z') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) setZoom(!stage.classList.contains('zoomed'));
    } else if (event.code === 'Space') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!spaceHeld) { spaceHeld = true; stage.dataset.previousZoom = String(stage.classList.contains('zoomed')); setZoom(true); }
    } else if (event.key === 'Escape') {
        closePopover(); crop.setActive(false);
    }
}

function handleKeyUp(event) {
    if (!mounted) return;
    if (event.key === '\\') showBefore(false);
    if (event.code === 'Space' && spaceHeld) {
        spaceHeld = false;
        setZoom(stage.dataset.previousZoom === 'true');
    }
}

function init() {
    const histogramSlot = document.createElement('div');
    histogramSlot.id = 'develop-histogram';
    histogram = new DevelopHistogram(histogramSlot);
    const cropSlot = document.createElement('div');
    cropSlot.id = 'develop-crop-controls';
    panels = new DevelopPanels(panelHost, {
        histogramHost: histogramSlot, cropHost: cropSlot, onChange: settingsChanged,
        masking: { toolbar, stage, canvas, getImageId: () => currentImage?.id, getRenderer: () => renderer },
    });
    masking = panels.masking;
    crop = new CropController({ stage, canvas, overlay: document.getElementById('develop-crop-overlay'), controls: cropSlot, onChange: settingsChanged });
    mountPresetsPanel(root.querySelector('.develop-layout') || root, {
        getRenderer: () => renderer,
        getEntry: () => currentImage && stateCache.get(Number(currentImage.id)),
        applyPresetSettings,
        saveCurrentSettings: () => {
            const entry = currentImage && stateCache.get(Number(currentImage.id));
            return clone(entry?.settings || {});
        },
    });
    bindUi();
    updateTabState();
    window.__developRenderToPixels = renderSyntheticPixels;
}

init();
