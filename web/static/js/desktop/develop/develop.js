import { previewThumbUrl, thumbUrl } from '../api.js';
import { fetchOptionsWithTimeout } from '../../api.js';
import { on, selection, setDevelopFilmstripCollapsed, setDevelopRightCollapsed, viewState } from '../state.js';
import { applyFlags } from '../selection.js';
import { showToast } from '../toast.js';
import { releaseFocus, trapFocus } from '../focusTrap.js';
import { CropController } from './crop.js';
import { DevelopRenderer } from './gl.js';
import { DevelopHistogram } from './histogram.js';
import { sampleBasePatch, solveWhiteBalance } from './wb_picker.js';
import { DevelopPanels } from './panels.js';
import { mountPresetsPanel } from './presets.js';
import { mountHistoryPanel } from './history_panel.js';
import { openExportDialog, openSyncDialog, SYNC_GROUPS } from './export_dialog.js';
import { DevelopSettingsClipboard, applyPrevious, openCopyDialog, pasteClipboard } from './settings_clipboard.js';
import { DevelopCompareView, SoftProofPopover } from './compare_view.js';
import { ProofTileController } from './proof_tile.js';
import { markSettingsChange } from './perf_overlay.js';

const DIRECT_DEVELOP_EXTENSIONS = new Set(['dng', 'cr3', 'cr2', 'exr', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'webp']);
const RAW_DEVELOP_EXTENSIONS = new Set(['dng', 'cr3', 'cr2', 'exr']);
const stateCache = new Map();
const saveTimers = new Map();
const settingsClipboard = new DevelopSettingsClipboard();
let mounted = false;
let currentImage = null;
let renderer = null;
let panels = null;
let crop = null;
let histogram = null;
let masking = null;
let heal = null;
let loadingToken = 0;
let beforeHeld = false;
let spaceHeld = false;
let activePopover = null;
let historyPanel = null;
let compare = null;
let softProof = null;
let proofTile = null;
let panGesture = null;
let presetsPanel = null;
let transientSettingsOverride = null;
let backgroundBaseRetryTimer = 0;
let saveFailureToastShown = false;
let wbPickActive = false;
const clipOverlay = { shadow: false, highlight: false };

const DEVELOP_READ_TIMEOUT_MS = 10_000;
const DEVELOP_ENTRY_ATTEMPTS = 2;
const DEVELOP_MUTATION_TIMEOUT_MS = 20_000;
const DEVELOP_BASE_BUDGET_MS = 12_000;
const DEVELOP_BACKGROUND_RETRY_MS = 10_000;
const HUB_ORIGINAL_PENDING = 'Original is still on the hub — retrying in background';

class PendingOriginalError extends Error {
    constructor() {
        super(HUB_ORIGINAL_PENDING);
        this.name = 'PendingOriginalError';
    }
}

const zoomState = {
    mode: 'fit',
    level: 1,
    center: { u: .5, v: .5 },
};

const root = document.getElementById('view-develop');
const stage = document.getElementById('develop-stage');
const canvas = document.getElementById('develop-canvas');
const placeholder = document.getElementById('develop-placeholder');
const status = document.getElementById('develop-status');
const panelHost = document.getElementById('develop-panels');
const filmstrip = document.getElementById('develop-filmstrip');
const toolbar = document.getElementById('develop-toolbar');
const statusRetry = status.querySelector('[data-develop-retry]');

function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
}

function imageExtension(image) {
    const name = String(image?.filename || image?.filepath || image?.path || '');
    return name.split('.').pop().toLowerCase();
}

function developTip(image) {
    const extension = imageExtension(image);
    if (DIRECT_DEVELOP_EXTENSIONS.has(extension)) return 'Open in Develop';
    if (extension === 'heic') return 'Open in Develop · HEIC requires Pillow codec support';
    return 'Open in Develop · this format requires Pillow image support';
}

function isDevelopImage(image) {
    return Boolean(image);
}

function isRaw(image) {
    return RAW_DEVELOP_EXTENSIONS.has(imageExtension(image));
}

function chosenImage() {
    const selectedId = selection.values().next().value;
    if (selectedId != null) {
        const selected = viewState.images.find((image) => Number(image.id) === Number(selectedId));
        if (selected) return selected;
    }
    return viewState.images[viewState.focusIndex] || viewState.images[0] || null;
}

function setStatus(message = '', { busy = false, error = false, retry = null } = {}) {
    status.hidden = !message;
    status.classList.toggle('busy', busy);
    status.classList.toggle('error', error);
    status.querySelector('span').textContent = message;
    if (statusRetry) {
        statusRetry.hidden = typeof retry !== 'function';
        statusRetry.onclick = typeof retry === 'function' ? retry : null;
    }
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
    let response = null;
    let fetchError = null;
    for (let attempt = 0; attempt < DEVELOP_ENTRY_ATTEMPTS; attempt += 1) {
        try {
            response = await fetch(`/api/develop/${imageId}`, fetchOptionsWithTimeout({ headers: { Accept: 'application/json' } }, DEVELOP_READ_TIMEOUT_MS));
            break;
        } catch (error) {
            fetchError = error;
        }
    }
    if (!response) throw fetchError;
    if (response.status === 202) throw new PendingOriginalError();
    if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error || (response.status === 404 ? 'Develop settings are not ready for this photo.' : 'Could not load develop settings.'));
    }
    return response.json();
}

function developLoadErrorMessage(error) {
    if (['AbortError', 'TimeoutError'].includes(error?.name) || /signal timed out/i.test(error?.message || '')) {
        return 'Develop took too long to respond.';
    }
    return error?.message || 'Develop could not open this photo.';
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

async function fetchBaseWithRetry(imageId, token, scale = 1, { quiet = false } = {}) {
    const deadline = performance.now() + DEVELOP_BASE_BUDGET_MS;
    for (let attempt = 0; performance.now() < deadline; attempt += 1) {
        if (token !== loadingToken) return null;
        if (!quiet && attempt === 2) setStatus('Reading source image from disk…', { busy: true });
        if (!quiet && attempt === 8) setStatus('Developing preview…', { busy: true });
        let response = null;
        try {
            response = await fetch(`/api/develop/${imageId}/base.bin`, fetchOptionsWithTimeout({}, Math.max(1_000, Math.min(4_000, deadline - performance.now()))));
        } catch {
            if (performance.now() >= deadline) break;
            await delay(750);
            continue;
        }
        if (response.ok) return parseBase(await response.arrayBuffer(), scale);
        if (![202, 404, 503].includes(response.status)) {
            const payload = await response.json().catch(() => null);
            throw new Error(payload?.error || 'The image preview could not be loaded.');
        }
        const waitMs = response.status === 202 ? 500 : 1_000;
        if (performance.now() + waitMs < deadline) await delay(waitMs);
    }
    throw new PendingOriginalError();
}

function markDevelopPaint(token, phase) {
    if (token !== loadingToken || root.dataset.developOpenPhase) return;
    const elapsed = Math.round(performance.now() - Number(root.dataset.developOpenedAt || performance.now()));
    root.dataset.developOpenMs = String(elapsed);
    root.dataset.developOpenPhase = phase;
    console.timeStamp?.(`develop-open-${phase}:${elapsed}ms`);
}

async function paintDisplayBlob(blob, token, phase) {
    if (!renderer || token !== loadingToken) return false;
    const bitmap = await createImageBitmap(blob);
    if (token !== loadingToken) {
        bitmap.close?.();
        return false;
    }
    renderer.uploadDisplayPreview(bitmap);
    bitmap.close?.();
    applyZoomState();
    placeholder.hidden = true;
    canvas.classList.add('preview-ready');
    histogram?.setLoading(true);
    markDevelopPaint(token, phase);
    setStatus('Loading full quality…', { busy: true });
    return true;
}

async function paintPlaceholder(imageId, token) {
    try {
        const response = await fetch(`/api/develop/${imageId}/base.jpg`, fetchOptionsWithTimeout({}, 5_000));
        if (response.ok && await paintDisplayBlob(await response.blob(), token, 'base-jpg')) return;
    } catch { /* Fall through to the already-cached Library image. */ }
    try {
        // Browsed photos already have this tier. cached=1 keeps a cold Develop
        // open from doing a second RAW decode just to make a placeholder.
        const response = await fetch(`${thumbUrl('lg', imageId)}?cached=1`, fetchOptionsWithTimeout({}, 5_000));
        if (response.ok) await paintDisplayBlob(await response.blob(), token, 'library-lg');
    } catch { /* The explicit staged status remains the final fallback. */ }
}

function setControlsLoading(loading) {
    panelHost.toggleAttribute('inert', loading);
    panelHost.dataset.loading = String(loading);
}

function hasUnsavedEdits() {
    return [...stateCache.values()].some((entry) => entry.unsaved);
}

function setUnsavedIndicator() {
    let dot = toolbar.querySelector('[data-develop-unsaved]');
    if (!dot) {
        dot = document.createElement('span');
        dot.dataset.developUnsaved = '';
        dot.dataset.tip = 'Unsaved Develop edits';
        dot.setAttribute('aria-label', 'Unsaved Develop edits');
        dot.style.cssText = 'width:7px;height:7px;margin-left:4px;border-radius:50%;background:var(--danger);box-shadow:0 0 0 2px var(--danger-dim)';
        toolbar.append(dot);
    }
    dot.hidden = !hasUnsavedEdits();
}

async function saveEntry(imageId, label) {
    const entry = stateCache.get(Number(imageId));
    if (!entry) return true;
    try {
        const response = await fetch(`/api/develop/${imageId}`, fetchOptionsWithTimeout({
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: entry.settings, label }),
        }, DEVELOP_MUTATION_TIMEOUT_MS));
        if (!response.ok) throw new Error('save failed');
        entry.dirty = false;
        entry.unsaved = false;
        settingsClipboard.markSaved(imageId);
        if (Number(currentImage?.id) === Number(imageId)) historyPanel?.reload();
        setUnsavedIndicator();
        if (!hasUnsavedEdits()) saveFailureToastShown = false;
        return true;
    } catch {
        entry.unsaved = true;
        setUnsavedIndicator();
        if (!saveFailureToastShown) {
            saveFailureToastShown = true;
            showToast("Couldn't save edits — retrying");
        }
        // Make "retrying" true: re-arm the debounce timer so the flush actually
        // re-attempts, coalescing with any newer edits (scheduleSave clears it).
        if (!saveTimers.has(Number(imageId))) {
            saveTimers.set(Number(imageId), setTimeout(() => {
                saveTimers.delete(Number(imageId));
                if (stateCache.get(Number(imageId))?.unsaved) saveEntry(imageId, label);
            }, DEVELOP_BACKGROUND_RETRY_MS));
        }
        return false;
    }
}

async function flushSave(imageId, label = 'Develop adjustment') {
    const entry = stateCache.get(Number(imageId));
    if (!entry?.dirty && !entry?.unsaved) return true;
    clearTimeout(saveTimers.get(Number(imageId)));
    saveTimers.delete(Number(imageId));
    return saveEntry(imageId, label);
}

function scheduleSave(label = 'Develop adjustment') {
    if (!currentImage) return;
    const imageId = Number(currentImage.id);
    const entry = stateCache.get(imageId);
    if (!entry) return;
    entry.dirty = true;
    clearTimeout(saveTimers.get(imageId));
    saveTimers.set(imageId, setTimeout(() => {
        saveTimers.delete(imageId);
        saveEntry(imageId, label);
    }, 400));
}

function renderedSettings(entry) {
    if (beforeHeld) return entry.origin;
    return transientSettingsOverride ? { ...entry.settings, ...transientSettingsOverride } : entry.settings;
}

// This is intentionally renderer-only: transient overrides never touch history or saves.
function setTransientSettingsOverride(settings = null) {
    transientSettingsOverride = settings && typeof settings === 'object' ? clone(settings) : null;
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (entry) renderer?.setSettings(renderedSettings(entry), entry.meta);
}

function resizeRgba(source, sourceWidth, sourceHeight, maxSide) {
    const ratio = Math.min(1, maxSide / Math.max(sourceWidth, sourceHeight));
    const width = Math.max(1, Math.round(sourceWidth * ratio));
    const height = Math.max(1, Math.round(sourceHeight * ratio));
    const data = new Float32Array(width * height * 4);
    for (let y = 0; y < height; y += 1) {
        const sourceY = Math.min(sourceHeight - 1, Math.floor(y * sourceHeight / height));
        for (let x = 0; x < width; x += 1) {
            const sourceX = Math.min(sourceWidth - 1, Math.floor(x * sourceWidth / width));
            const sourceOffset = (sourceY * sourceWidth + sourceX) * 4;
            data.set(source.subarray(sourceOffset, sourceOffset + 4), (y * width + x) * 4);
        }
    }
    return { data, width, height };
}

// One shared offscreen renderer for every thumbnail/harness render: a fresh
// WebGL context per call trips the browser's context limit and evicts the
// MAIN develop canvas (blank/white canvas, dead harness).
let thumbShared = null;
let thumbBaseKey = '';
let thumbChain = Promise.resolve();

function thumbRenderer() {
    if (thumbShared) return thumbShared;
    const canvas = document.createElement('canvas');
    thumbShared = { canvas, renderer: new DevelopRenderer(canvas) };
    thumbShared.renderer.geometryEnabled = false;
    return thumbShared;
}

export function disposeThumbRenderer() {
    if (!thumbShared) return;
    thumbShared.renderer.destroy();
    thumbShared = null;
    thumbBaseKey = '';
}

async function renderCurrentImagePixels(settings = {}, { size = 112, signal } = {}) {
    const run = thumbChain.then(async () => {
        const entry = currentImage && stateCache.get(Number(currentImage.id));
        if (!entry?.base || signal?.aborted) return null;
        const shared = thumbRenderer();
        const baseKey = `${currentImage.id}:${size}`;
        const base = resizeRgba(entry.base.rgba, entry.base.width, entry.base.height, size);
        shared.canvas.width = base.width;
        shared.canvas.height = base.height;
        if (thumbBaseKey !== baseKey) {
            shared.renderer.uploadSource(base.data, base.width, base.height);
            thumbBaseKey = baseKey;
        }
        shared.renderer.setSettings({ ...entry.settings, ...clone(settings) }, entry.meta);
        await shared.renderer.waitForFilm();
        if (signal?.aborted) return null;
        shared.renderer.render();
        return { pixels: shared.renderer.readPixels(base.width, base.height), width: base.width, height: base.height };
    });
    thumbChain = run.catch(() => {});
    return run;
}

function applySettings(entry) {
    const panelSettings = entry.meta?.base_kind === 'display' && entry.settings.Temperature == null
        ? { ...entry.settings, Temperature: 6500 }
        : entry.settings;
    panels.setMeta(entry.meta);
    panels.setSettings(panelSettings);
    crop.setSettings(entry.settings);
    renderer?.setSettings(renderedSettings(entry), entry.meta);
    compare?.updateCurrent(entry);
    proofTile?.settingsChanged(currentImage?.id);
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

function restoreHistoricalSettings(settings, label) {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!entry) return;
    entry.undo.push(clone(entry.settings));
    entry.redo.length = 0;
    entry.settings = clone(settings);
    applySettings(entry);
    scheduleSave(label);
    showToast('Develop state restored');
}

export async function createVirtualCopy() {
    if (!currentImage || !isRaw(currentImage)) return;
    try {
        const response = await fetch(`/api/develop/${currentImage.id}/virtual-copy`, fetchOptionsWithTimeout({ method: 'POST' }, DEVELOP_MUTATION_TIMEOUT_MS));
        if (!response.ok) throw new Error('copy failed');
        const copy = await response.json();
        showToast('Virtual copy created');
        openImage({ ...currentImage, ...copy, id: copy.id });
    } catch {
        showToast('Could not create virtual copy');
    }
}

function applySettingsPatch(patch, label) {
    if (!currentImage || beforeHeld || !patch || !Object.keys(patch).length) return;
    const entry = stateCache.get(Number(currentImage.id));
    if (!entry) return;
    entry.undo.push(clone(entry.settings));
    if (entry.undo.length > 100) entry.undo.shift();
    entry.redo.length = 0;
    for (const [key, value] of Object.entries(patch)) {
        if (value === undefined) delete entry.settings[key];
        else entry.settings[key] = value;
    }
    renderer?.setSettings(renderedSettings(entry), entry.meta);
    compare?.updateCurrent(entry);
    proofTile?.settingsChanged(currentImage.id);
    scheduleSave(label);
}

async function requestAutoTone() {
    if (!currentImage) return;
    try {
        const response = await fetch(`/api/develop/${currentImage.id}/auto`, fetchOptionsWithTimeout({ method: 'POST' }, DEVELOP_MUTATION_TIMEOUT_MS));
        if (!response.ok) throw new Error();
        const payload = await response.json();
        applySettingsPatch(payload.patch || {}, 'Auto tone');
    } catch {
        showToast("Couldn't compute Auto tone");
    }
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
    markSettingsChange(label || key);
    renderer?.setSettings(renderedSettings(entry), entry.meta);
    compare?.updateCurrent(entry);
    proofTile?.settingsChanged(currentImage.id);
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
    const imageIds = viewState.images.map((image) => Number(image.id)).join(',');
    const currentId = Number(currentImage?.id);
    if (filmstrip.dataset.imageIds !== imageIds) {
        filmstrip.dataset.imageIds = imageIds;
        filmstrip.innerHTML = viewState.images.map((image, index) => {
            const previewSrc = previewThumbUrl(image);
            const preview = previewSrc
                ? `<img src="${previewSrc}" loading="lazy" decoding="async" alt="">`
                : '<span class="preview-thumb-pending" aria-hidden="true"></span>';
            return `<button class="develop-thumb ${previewSrc ? '' : 'preview-pending'} ${Number(image.id) === currentId ? 'cur' : ''}" data-index="${index}" data-image-id="${image.id}" data-tip="${developTip(image)}" aria-label="${String(image.filename || `Photo ${index + 1}`).replaceAll('"', '&quot;')}">${preview}</button>`;
        }).join('');
    } else {
        filmstrip.querySelector('.develop-thumb.cur')?.classList.remove('cur');
        filmstrip.querySelector(`[data-image-id="${currentId}"]`)?.classList.add('cur');
    }
    filmstrip.querySelector(`[data-image-id="${currentId}"]`)?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}

function pregenNeighbors(image) {
    const index = viewState.images.findIndex((item) => Number(item.id) === Number(image.id));
    const imageIds = viewState.images.slice(Math.max(0, index - 2), index + 3).map((item) => Number(item.id));
    if (!imageIds.length) return;
    fetch('/api/develop/pregen', fetchOptionsWithTimeout({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_ids: imageIds }) }, DEVELOP_MUTATION_TIMEOUT_MS)).catch(() => {});
}

function applyDevelopBase(entry, base, token) {
    if (!base || token !== loadingToken || !renderer) return false;
    renderer.uploadSource(base.rgba, base.width, base.height);
    entry.base = base;
    renderer.setSettings(renderedSettings(entry), entry.meta);
    applyZoomState();
    masking?.rebuildRasters();
    canvas.classList.remove('preview-ready');
    canvas.classList.add('ready');
    placeholder.hidden = true;
    histogram?.setLoading(false);
    setControlsLoading(false);
    const elapsed = Math.round(performance.now() - Number(root.dataset.developOpenedAt || performance.now()));
    root.dataset.developFullMs = String(elapsed);
    console.timeStamp?.(`develop-open-full:${elapsed}ms`);
    setStatus('');
    crop.setSettings(entry.settings);
    presetsPanel?.imageReady();
    return true;
}

function scheduleBackgroundBaseRetry(image, entry, token) {
    window.clearTimeout(backgroundBaseRetryTimer);
    backgroundBaseRetryTimer = window.setTimeout(async () => {
        if (token !== loadingToken || Number(currentImage?.id) !== Number(image.id)) return;
        try {
            const base = await fetchBaseWithRetry(image.id, token, Number(entry.meta?.hdr?.scale) || 1, { quiet: true });
            if (!applyDevelopBase(entry, base, token)) scheduleBackgroundBaseRetry(image, entry, token);
        } catch (error) {
            if (error instanceof PendingOriginalError && token === loadingToken) scheduleBackgroundBaseRetry(image, entry, token);
        }
    }, DEVELOP_BACKGROUND_RETRY_MS);
}

function scheduleBackgroundDevelopRetry(image, token) {
    window.clearTimeout(backgroundBaseRetryTimer);
    backgroundBaseRetryTimer = window.setTimeout(() => {
        if (token === loadingToken && Number(currentImage?.id) === Number(image.id)) openImage(image);
    }, DEVELOP_BACKGROUND_RETRY_MS);
}

async function openImage(image) {
    setWbPick(false);
    const previousImage = currentImage;
    if (previousImage && Number(previousImage.id) !== Number(image?.id)) await flushSave(previousImage.id);
    const token = ++loadingToken;
    currentImage = image;
    transientSettingsOverride = null;
    presetsPanel?.imageChanged();
    setZoomFit();
    proofTile?.imageChanged();
    syncFilmstrip();
    closePopover();
    placeholder.hidden = true;
    canvas.classList.remove('ready', 'preview-ready');
    root.dataset.developOpenedAt = String(performance.now());
    delete root.dataset.developOpenMs;
    delete root.dataset.developOpenPhase;
    console.timeStamp?.('develop-open-start');
    if (!image) {
        setStatus('Choose a photo in Grid, then open Develop.', { error: false });
        setControlsLoading(true);
        return;
    }
    setControlsLoading(true);
    pregenNeighbors(image);
    setStatus('Loading develop settings…', { busy: true });
    setTimeout(() => {
        if (token === loadingToken && !canvas.classList.contains('ready') && !canvas.classList.contains('preview-ready')) setStatus('Reading source image from disk…', { busy: true });
    }, 1000);
    setTimeout(() => {
        if (token === loadingToken && !canvas.classList.contains('ready') && !canvas.classList.contains('preview-ready')) setStatus('Developing preview…', { busy: true });
    }, 8000);
    paintPlaceholder(image.id, token);
    try {
        let entry = stateCache.get(Number(image.id));
        const cachedEntry = Boolean(entry);
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
        setUnsavedIndicator();
        if (token !== loadingToken) return;
        entry.imageId = Number(image.id);
        historyPanel?.setHistory(entry.serverHistory || []);
        // Cached entries can hold a pre-edit rail; refresh it from the server.
        if (cachedEntry) void historyPanel?.reload();
        applySettings(entry);
        if (!renderer) {
            setStatus('WebGL2 is required for Develop.', { error: true });
            return;
        }
        setStatus('Reading source image from disk…', { busy: true });
        const base = await fetchBaseWithRetry(image.id, token, Number(entry.meta?.hdr?.scale) || 1);
        if (!base || token !== loadingToken) return;
        applyDevelopBase(entry, base, token);
    } catch (error) {
        if (token === loadingToken) {
            setControlsLoading(false);
            histogram?.setLoading(false);
            if (error instanceof PendingOriginalError) {
                setStatus(HUB_ORIGINAL_PENDING, { error: true });
                const entry = stateCache.get(Number(image.id));
                if (entry) scheduleBackgroundBaseRetry(image, entry, token);
                else scheduleBackgroundDevelopRetry(image, token);
            } else {
                setStatus(developLoadErrorMessage(error), { error: true, retry: () => openImage(image) });
            }
        }
    }
}

function nav(delta) {
    if (!viewState.images.length) return;
    const index = currentImage ? viewState.images.findIndex((image) => Number(image.id) === Number(currentImage.id)) : viewState.focusIndex;
    const next = Math.max(0, Math.min(viewState.images.length - 1, index + delta));
    viewState.focusIndex = next;
    openImage(viewState.images[next]);
}

function fitPixelRatio() {
    if (!renderer?.ready) return 1;
    const box = canvas.getBoundingClientRect();
    return Math.max(1e-6, Math.min(box.width / renderer.width, box.height / renderer.height));
}

function zoomScale() {
    if (zoomState.mode === 'fit') return 1;
    const deviceScale = Math.max(1, window.devicePixelRatio || 1);
    return Math.max(1, (zoomState.level / deviceScale) / fitPixelRatio());
}

function clampZoomCenter(center, scale = zoomScale()) {
    const margin = .5 / Math.max(1, scale);
    const clamp = (value) => Math.max(margin, Math.min(1 - margin, Number(value) || .5));
    return { u: clamp(center?.u), v: clamp(center?.v) };
}

function syncViewDependents() {
    crop?.syncOverlay();
    heal?.drawHandles();
    stage.dispatchEvent(new CustomEvent('develop:viewchange', { detail: { ...zoomState, scale: zoomScale() } }));
    proofTile?.viewChanged();
}

function applyZoomState() {
    const scale = zoomScale();
    zoomState.center = clampZoomCenter(zoomState.center, scale);
    renderer?.setViewTransform({ scale, center: zoomState.center });
    stage.dataset.zoomMode = zoomState.mode;
    stage.dataset.zoomLevel = zoomState.mode === 'fit' ? '' : String(zoomState.level);
    const button = toolbar.querySelector('[data-action="zoom"]');
    if (button) {
        button.textContent = zoomState.mode === 'fit' ? 'Fit' : `${zoomState.level * 100}%`;
        button.setAttribute('aria-pressed', String(zoomState.mode !== 'fit'));
        button.dataset.tip = zoomState.mode === 'fit' ? 'Zoom to 100% (Z)' : 'Fit image (Z)';
    }
    if (!crop?.active && !masking?.mode && !heal?.active) {
        stage.style.cursor = panGesture ? 'grabbing' : (zoomState.mode === 'level' && scale > 1 ? 'grab' : 'default');
    }
    syncViewDependents();
}

function setZoomFit() {
    zoomState.mode = 'fit';
    zoomState.center = { u: .5, v: .5 };
    applyZoomState();
}

function setZoomLevel(level, anchor = null) {
    const oldPoint = anchor && renderer?.ready ? renderer.canvasToImage(anchor.clientX, anchor.clientY) : null;
    zoomState.mode = 'level';
    zoomState.level = [1, 2, 4].includes(Number(level)) ? Number(level) : 1;
    const scale = zoomScale();
    if (oldPoint && anchor) {
        const box = canvas.getBoundingClientRect();
        const x = (anchor.clientX - box.left) / Math.max(1, box.width);
        const y = (anchor.clientY - box.top) / Math.max(1, box.height);
        zoomState.center = { u: oldPoint.u - (x - .5) / scale, v: oldPoint.v - (y - .5) / scale };
    } else {
        zoomState.center = clampZoomCenter(zoomState.center, scale);
    }
    applyZoomState();
}

function toggleZoom(anchor = null) {
    if (zoomState.mode === 'fit') setZoomLevel(1, anchor);
    else setZoomFit();
}

function wheelZoom(event) {
    if (!renderer?.ready || !canvas.classList.contains('ready')) return;
    event.preventDefault();
    const levels = ['fit', 1, 2, 4];
    const current = zoomState.mode === 'fit' ? 0 : levels.indexOf(zoomState.level);
    const next = Math.max(0, Math.min(levels.length - 1, current + (event.deltaY < 0 ? 1 : -1)));
    if (next === 0) setZoomFit();
    else setZoomLevel(levels[next], event);
}

function beginPan(event) {
    if (pickWhiteBalance(event)) { event.preventDefault(); event.stopPropagation(); return; }
    if (event.button !== 0 || !renderer?.ready || proofTile?.held) return;
    const scale = zoomScale();
    if (scale <= 1 || (crop?.active || masking?.mode || heal?.active) && !spaceHeld) return;
    event.preventDefault();
    if (spaceHeld) event.stopPropagation();
    panGesture = { x: event.clientX, y: event.clientY, center: { ...zoomState.center } };
    stage.setPointerCapture?.(event.pointerId);
    stage.style.cursor = 'grabbing';
}

function movePan(event) {
    if (!panGesture) return;
    const box = canvas.getBoundingClientRect();
    const scale = zoomScale();
    zoomState.center = clampZoomCenter({
        u: panGesture.center.u - (event.clientX - panGesture.x) / Math.max(1, box.width) / scale,
        v: panGesture.center.v - (event.clientY - panGesture.y) / Math.max(1, box.height) / scale,
    }, scale);
    applyZoomState();
}

function endPan(event) {
    if (!panGesture) return;
    panGesture = null;
    stage.releasePointerCapture?.(event.pointerId);
    applyZoomState();
}

function showBefore(show) {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!entry || !renderer) return;
    beforeHeld = show;
    renderer.setSettings(show ? entry.origin : renderedSettings(entry), entry.meta);
    toolbar.querySelector('[data-action="before"]').setAttribute('aria-pressed', String(show));
}

function setWbPick(active) {
    wbPickActive = Boolean(active) && Boolean(currentImage);
    stage.classList.toggle('wb-picking', wbPickActive);
    stage.style.cursor = wbPickActive ? 'crosshair' : '';
    const button = document.querySelector('[data-wb-pick]');
    button?.classList.toggle('active', wbPickActive);
    button?.setAttribute('aria-pressed', String(wbPickActive));
}

function setClipOverlay(shadow, highlight) {
    clipOverlay.shadow = Boolean(shadow);
    clipOverlay.highlight = Boolean(highlight);
    renderer?.setClipOverlay(clipOverlay.shadow, clipOverlay.highlight);
    histogram?.setClipState(clipOverlay.shadow, clipOverlay.highlight);
}

function pickWhiteBalance(event) {
    if (!wbPickActive || event.button !== 0 || !renderer?.ready || !currentImage) return false;
    const entry = stateCache.get(Number(currentImage.id));
    if (!entry?.base) { showToast('The image is still loading'); return true; }
    const point = renderer.canvasToImage(event.clientX, event.clientY);
    if (point.u < 0 || point.u > 1 || point.v < 0 || point.v > 1) return true;
    const sample = sampleBasePatch(entry.base, point.u, point.v);
    const asShot = entry.meta?.as_shot || {};
    const solved = sample && solveWhiteBalance(sample, entry.meta?.color, Number(asShot.temperature) || 5500, Number(asShot.tint) || 0);
    if (!solved) { showToast('Could not sample that spot'); return true; }
    applySettingsPatch({ WhiteBalance: 'Custom', Temperature: solved.temperature, Tint: solved.tint }, 'White balance picker');
    panels?.setSettings(stateCache.get(Number(currentImage.id))?.settings || {});
    setWbPick(false);
    showToast(`White balance: ${solved.temperature}K, tint ${solved.tint > 0 ? '+' : ''}${solved.tint}`);
    return true;
}

async function comparisonPreview(image) {
    if (!image) return null;
    let entry = stateCache.get(Number(image.id));
    if (!entry) {
        const payload = await fetchDevelop(image.id);
        entry = {
            settings: clone(payload.settings), origin: originSettings(payload), meta: {
                ...(payload.meta || {}),
                as_shot_temperature: payload.meta?.as_shot?.temperature ?? payload.meta?.as_shot_temperature,
                as_shot_tint: payload.meta?.as_shot?.tint ?? payload.meta?.as_shot_tint,
                color: payload.meta?.color ?? null,
            }, undo: [], redo: [], serverHistory: payload.history || [],
        };
        stateCache.set(Number(image.id), entry);
    }
    if (!entry.base) {
        const response = await fetch(`/api/develop/${image.id}/base.bin`, fetchOptionsWithTimeout({}, DEVELOP_READ_TIMEOUT_MS));
        if (!response.ok) throw new Error('Reference preview is still being prepared.');
        entry.base = parseBase(await response.arrayBuffer(), Number(entry.meta?.hdr?.scale) || 1);
    }
    return { entry, base: entry.base };
}

export function toggleDevelopCompare(orientation = 'vertical') {
    if (mounted) compare?.toggle(orientation).catch((error) => showToast(error.message || 'Could not prepare comparison'));
}

export function holdDevelopReference(held) {
    if (mounted) compare?.holdReference(held).catch((error) => showToast(error.message || 'Could not prepare reference'));
}

function closePopover() {
    const trigger = activePopover?.trigger;
    releaseFocus(activePopover);
    activePopover?.remove();
    activePopover = null;
    trigger?.focus?.({ preventScroll: true });
}

function anchoredPopover(button, html) {
    closePopover();
    activePopover = document.createElement('div');
    activePopover.className = 'develop-popover';
    activePopover.setAttribute('role', 'dialog');
    activePopover.setAttribute('aria-modal', 'true');
    activePopover.trigger = button;
    activePopover.innerHTML = html;
    root.appendChild(activePopover);
    const buttonRect = button.getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    activePopover.style.left = `${Math.max(8, Math.min(rootRect.width - 250, buttonRect.left - rootRect.left))}px`;
    activePopover.style.top = `${buttonRect.bottom - rootRect.top + 6}px`;
    activePopover.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        event.stopPropagation();
        closePopover();
    });
    trapFocus(activePopover, activePopover.querySelector('input, select, button'));
    return activePopover;
}

function gridAnchoredPopover(button, html) {
    closePopover();
    activePopover = document.createElement('div');
    activePopover.className = 'develop-popover';
    activePopover.setAttribute('role', 'dialog');
    activePopover.setAttribute('aria-modal', 'true');
    activePopover.trigger = button;
    activePopover.innerHTML = html;
    activePopover.style.position = 'fixed';
    document.body.appendChild(activePopover);
    const rect = button?.getBoundingClientRect?.() || { left: window.innerWidth * .5, bottom: 40 };
    activePopover.style.left = `${Math.max(8, Math.min(window.innerWidth - 250, rect.left))}px`;
    activePopover.style.top = `${Math.max(8, Math.min(window.innerHeight - 360, rect.bottom + 6))}px`;
    activePopover.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        event.stopPropagation();
        closePopover();
    });
    trapFocus(activePopover, activePopover.querySelector('input, select, button'));
    return activePopover;
}

function openCopyPopover(button) {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    return openCopyDialog({
        button, sourceId: currentImage?.id, settings: entry?.settings,
        anchoredPopover, closePopover, showToast, clipboard: settingsClipboard,
    });
}

function copyAllSettings() {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    if (!settingsClipboard.copy({
        sourceId: currentImage?.id,
        settings: entry?.settings,
        groups: SYNC_GROUPS.map(([id]) => id),
    })) return;
    showToast('Settings copied');
}

function applySyncedSettings(payload) {
    const entry = currentImage && stateCache.get(Number(currentImage.id));
    const synced = payload?.synced?.find((row) => Number(row.image_id) === Number(currentImage?.id));
    if (!entry || !synced?.settings) return;
    entry.undo.push(clone(entry.settings));
    if (entry.undo.length > 100) entry.undo.shift();
    entry.settings = clone(synced.settings);
    entry.redo.length = 0;
    applySettings(entry);
    historyPanel?.reload();
}

function targetImageIds() {
    return selection.size ? [...selection].map(Number) : [Number(currentImage?.id)].filter(Boolean);
}

async function pasteSettings() {
    if (!currentImage) return;
    try {
        const payload = await pasteClipboard({ clipboard: settingsClipboard, targetIds: targetImageIds(), onApplied: applySyncedSettings });
        const count = payload.synced?.length || 0;
        showToast(count ? `Pasted settings to ${count} photo${count === 1 ? '' : 's'} · Undo with Ctrl+Z` : 'Nothing pasted');
    } catch (error) { showToast(error.message || 'Could not paste settings'); }
}

async function fromPrevious() {
    if (!currentImage) return;
    const sourceId = settingsClipboard.lastSavedOtherThan(currentImage.id);
    if (!sourceId) return showToast('Edit another photo, then save it first');
    try {
        const source = stateCache.get(sourceId) || await fetchDevelop(sourceId);
        const payload = await applyPrevious({ sourceId, sourceSettings: source.settings, targetIds: targetImageIds(), onApplied: applySyncedSettings });
        const count = payload.synced?.length || 0;
        showToast(count ? `Applied previous settings to ${count} photo${count === 1 ? '' : 's'} · Undo with Ctrl+Z` : 'Nothing applied');
    } catch (error) { showToast(error.message || 'Could not apply previous settings'); }
}

function gridTargetIds(imageIds) {
    return [...new Set((imageIds || []).map(Number).filter((id) => id > 0))];
}

/** Grid shortcuts share the same persistent clipboard and batch sync request. */
export async function copyDevelopSettingsFromGrid(image, anchor = document.getElementById('grid-flow')) {
    if (!image?.id) return;
    try {
        const payload = await fetchDevelop(image.id);
        openCopyDialog({
            button: anchor, sourceId: image.id, settings: payload.settings,
            anchoredPopover: gridAnchoredPopover, closePopover, showToast, clipboard: settingsClipboard,
        });
    } catch (error) { showToast(error.message || 'Could not read develop settings'); }
}

export async function pasteDevelopSettingsToGrid(imageIds) {
    const targetIds = gridTargetIds(imageIds);
    if (!targetIds.length) return showToast('Select photos to paste into');
    try {
        const payload = await pasteClipboard({ clipboard: settingsClipboard, targetIds });
        const count = payload.synced?.length || 0;
        showToast(count ? `Pasted settings to ${count} photo${count === 1 ? '' : 's'} · Undo with Ctrl+Z` : 'Nothing pasted');
    } catch (error) { showToast(error.message || 'Could not paste settings'); }
}

export async function applyPreviousDevelopSettingsToGrid(imageIds) {
    const targetIds = gridTargetIds(imageIds);
    const sourceId = settingsClipboard.lastSavedOtherThan(targetIds.length === 1 ? targetIds[0] : null);
    if (!sourceId) return showToast('Edit another photo, then save it first');
    try {
        const source = stateCache.get(sourceId) || await fetchDevelop(sourceId);
        const payload = await applyPrevious({ sourceId, sourceSettings: source.settings, targetIds });
        const count = payload.synced?.length || 0;
        showToast(count ? `Applied previous settings to ${count} photo${count === 1 ? '' : 's'} · Undo with Ctrl+Z` : 'Nothing applied');
    } catch (error) { showToast(error.message || 'Could not apply previous settings'); }
}

function openExportPopover(button) {
    openExportDialog({
        button,
        image: currentImage,
        anchoredPopover,
        closePopover,
        showToast,
        isRaw: isDevelopImage,
    });
}

function openSyncPopover(button) {
    if (!isDevelopImage(currentImage)) return;
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
    if (!isDevelopImage(currentImage)) return;
    try {
        const response = await fetch(`/api/develop/${currentImage.id}/reset`, fetchOptionsWithTimeout({ method: 'POST' }, DEVELOP_MUTATION_TIMEOUT_MS));
        if (!response.ok) throw new Error();
        const payload = await response.json().catch(() => null);
        const entry = stateCache.get(Number(currentImage.id));
        entry.undo.push(clone(entry.settings));
        entry.settings = clone(payload?.settings || entry.origin || {});
        entry.redo.length = 0;
        applySettings(entry);
        historyPanel?.reload();
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
    heal?.toggle(false);
    proofTile?.setHeld(false);
    presetsPanel?.endPreview();
}

export function closeDevelop() {
    unmount();
}

export function developOpen() {
    return mounted;
}

function updateTabState() {
    const tab = document.querySelector('#view-switch [data-view="develop"]');
    const image = chosenImage();
    tab.setAttribute('aria-disabled', 'false');
    tab.dataset.tip = image ? `${developTip(image)} · D` : 'Develop · D';
}

function ensureRenderer() {
    if (renderer && !renderer.gl.isContextLost()) return true;
    try {
        renderer = new DevelopRenderer(canvas);
        renderer.setViewTransform({ scale: zoomScale(), center: zoomState.center });
        canvas.addEventListener('develop:rendered', () => histogram.updateFromRenderer(renderer));
        return true;
    } catch (error) {
        renderer = null;
        setStatus(error.message || 'WebGL2 is required for Develop.', { error: true });
        return false;
    }
}

function bindUi() {
    on('develop:open-image', ({ image }) => {
        if (!image) return;
        if (!mounted) openDevelop();
        openImage(image);
    });
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
        if (compare?.mode === 'reference') {
            compare.pickReference(viewState.images[index]).catch((error) => showToast(error.message || 'Could not load reference'));
            return;
        }
        viewState.focusIndex = index;
        openImage(viewState.images[index]);
    });
    if (!toolbar.querySelector('[data-action="proof"]')) {
        const proofButton = document.createElement('button');
        proofButton.type = 'button';
        proofButton.dataset.action = 'proof';
        proofButton.dataset.tip = 'Hold for original-pixel proof (Shift+P)';
        proofButton.setAttribute('aria-label', 'Hold for original 1:1 proof');
        proofButton.setAttribute('aria-pressed', 'false');
        proofButton.textContent = '1:1';
        toolbar.querySelector('[data-action="before"]')?.before(proofButton);
        proofButton.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            proofButton.setPointerCapture?.(event.pointerId);
            proofButton.setAttribute('aria-pressed', 'true');
            proofTile?.setHeld(true);
        });
        const releaseProof = () => {
            proofButton.setAttribute('aria-pressed', 'false');
            proofTile?.setHeld(false);
        };
        proofButton.addEventListener('pointerup', releaseProof);
        proofButton.addEventListener('pointercancel', releaseProof);
        proofButton.addEventListener('lostpointercapture', releaseProof);
    }
    toolbar.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const action = button.dataset.action;
        if (action === 'prev') nav(-1);
        else if (action === 'next') nav(1);
        else if (action === 'zoom') toggleZoom();
        else if (action === 'copy') openCopyPopover(button);
        else if (action === 'paste') pasteSettings();
        else if (action === 'previous') fromPrevious();
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
    if (!toolbar.querySelector('[data-action="previous"]')) {
        const pasteButton = toolbar.querySelector('[data-action="paste"]');
        const previousButton = document.createElement('button');
        previousButton.dataset.action = 'previous';
        previousButton.dataset.tip = 'Apply the last saved other photo (Ctrl+Alt+V)';
        previousButton.textContent = 'Previous';
        pasteButton?.after(previousButton);
    }
    const beforeButton = toolbar.querySelector('[data-action="before"]');
    beforeButton.addEventListener('pointerdown', () => showBefore(true));
    for (const eventName of ['pointerup', 'pointercancel', 'pointerleave']) beforeButton.addEventListener(eventName, () => showBefore(false));
    stage.addEventListener('dblclick', (event) => {
        if (!crop.active && !masking?.mode && !heal?.active && !event.target.closest('button')) toggleZoom(event);
    });
    stage.addEventListener('wheel', wheelZoom, { passive: false });
    stage.addEventListener('pointerdown', beginPan, true);
    stage.addEventListener('pointermove', movePan);
    stage.addEventListener('pointerup', endPan);
    stage.addEventListener('pointercancel', endPan);
    window.addEventListener('resize', applyZoomState);
    document.addEventListener('pointerdown', (event) => {
        if (activePopover && !activePopover.contains(event.target) && !event.target.closest('[data-action="copy"], [data-action="export"], [data-action="sync"]')) closePopover();
    });
    document.addEventListener('keydown', handleKey, true);
    document.addEventListener('keyup', handleKeyUp, true);
    const layout = root.querySelector('.develop-layout');
    layout.classList.toggle('right-collapsed', viewState.developRightCollapsed);
    layout.classList.toggle('filmstrip-collapsed', viewState.developFilmstripCollapsed);
    document.getElementById('develop-collapse-right').addEventListener('click', () => setDevelopRightCollapsed(!viewState.developRightCollapsed));
    document.getElementById('develop-collapse-filmstrip').addEventListener('click', () => setDevelopFilmstripCollapsed(!viewState.developFilmstripCollapsed));
    on('developrightpanel', (collapsed) => {
        layout.classList.toggle('right-collapsed', collapsed);
        applyZoomState();
    });
    on('developfilmstrip', (collapsed) => {
        layout.classList.toggle('filmstrip-collapsed', collapsed);
        applyZoomState();
        if (!collapsed && mounted) syncFilmstrip();
    });
    on('selection', updateTabState);
    on('focus', updateTabState);
    on('scope', updateTabState);
}

function editingField(event) {
    return event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || event.target?.isContentEditable;
}

function flagCurrent(flag) {
    if (!currentImage) return;
    applyFlags([currentImage.id], flag);
}

function closeTransient() {
    const hasTransient = Boolean(
        activePopover || softProof?.popover || crop?.active || wbPickActive || beforeHeld || proofTile?.held || compare?.mode !== 'off',
    );
    if (!hasTransient) return false;
    closePopover();
    softProof?.popover?.remove();
    if (softProof) softProof.popover = null;
    crop?.setActive(false);
    setWbPick(false);
    showBefore(false);
    proofTile?.setHeld(false);
    compare?.holdReference(false);
    return true;
}

function canNavigateFrom(event) {
    return event.target === document.body || event.target === stage || event.target === canvas;
}

function handleKey(event) {
    if (!mounted || editingField(event)) return;
    if (heal?.keydown(event)) return;
    if (crop?.active && event.key.toLowerCase() === 'o') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) crop.cycleOverlay();
        return;
    }
    if (masking?.keydown(event)) return;
    const key = event.key.toLowerCase();
    if (event.ctrlKey || event.metaKey) {
        if (key === 'z') { event.preventDefault(); event.stopImmediatePropagation(); event.shiftKey ? redo() : undo(); }
        else if (event.shiftKey && key === 'c') { event.preventDefault(); event.stopImmediatePropagation(); copyAllSettings(); }
        else if (event.shiftKey && key === 'v') { event.preventDefault(); event.stopImmediatePropagation(); pasteSettings(); }
        else if (event.altKey && key === 'v') { event.preventDefault(); event.stopImmediatePropagation(); fromPrevious(); }
        else if (event.code === 'Quote') { event.preventDefault(); event.stopImmediatePropagation(); createVirtualCopy(); }
        return;
    }
    if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && canNavigateFrom(event)) {
        event.preventDefault(); event.stopImmediatePropagation(); nav(event.key === 'ArrowLeft' ? -1 : 1);
    } else if (event.key === '\\') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) showBefore(true);
    } else if (key === 'y') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) toggleDevelopCompare(event.altKey ? 'horizontal' : 'vertical');
    } else if (key === 'w') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) setWbPick(!wbPickActive);
    } else if (key === 'j') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) {
            const active = !(clipOverlay.shadow || clipOverlay.highlight);
            setClipOverlay(active, active);
        }
    } else if (key === 'r' && event.shiftKey) {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) holdDevelopReference(true);
    } else if (key === 'r') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) crop.setActive(!crop.active);
    } else if (key === 'k') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) masking?.togglePanel();
    } else if (key === 'z') {
        event.preventDefault(); event.stopImmediatePropagation(); if (!event.repeat) toggleZoom();
    } else if (key === ']') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) setDevelopRightCollapsed(!viewState.developRightCollapsed);
    } else if (key === 'f') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) setDevelopFilmstripCollapsed(!viewState.developFilmstripCollapsed);
    } else if (event.shiftKey && key === 'p') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) {
            toolbar.querySelector('[data-action="proof"]')?.setAttribute('aria-pressed', 'true');
            proofTile?.setHeld(true);
        }
    } else if (key === 'p') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) flagCurrent(currentImage?.flag === 'picked' ? 'unflagged' : 'picked');
    } else if (key === 'x') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) flagCurrent('rejected');
    } else if (key === 'u') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!event.repeat) flagCurrent('unflagged');
    } else if (event.code === 'Space') {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!spaceHeld) { spaceHeld = true; if (zoomScale() > 1) stage.style.cursor = 'grab'; }
    } else if (event.key === 'Escape') {
        if (closeTransient()) {
            event.preventDefault(); event.stopImmediatePropagation();
        }
    }
}

function handleKeyUp(event) {
    if (!mounted) return;
    if (event.key === '\\') showBefore(false);
    if (event.key.toLowerCase() === 'r') holdDevelopReference(false);
    if (event.key.toLowerCase() === 'p' || event.key === 'Shift') {
        toolbar.querySelector('[data-action="proof"]')?.setAttribute('aria-pressed', 'false');
        proofTile?.setHeld(false);
    }
    if (event.code === 'Space' && spaceHeld) {
        spaceHeld = false;
        applyZoomState();
    }
}

function init() {
    const histogramSlot = document.createElement('div');
    histogramSlot.id = 'develop-histogram';
    const histogramGesture = { key: null, active: false, start: 0, previous: null };
    histogram = new DevelopHistogram(histogramSlot, {
        onClipToggle: (side, active) => setClipOverlay(
            side === 'shadow' ? active : clipOverlay.shadow,
            side === 'highlight' ? active : clipOverlay.highlight,
        ),
        onAdjust: (key, delta) => {
            const entry = currentImage && stateCache.get(Number(currentImage.id));
            if (!entry) return;
            if (!histogramGesture.active || histogramGesture.key !== key) {
                histogramGesture.key = key;
                histogramGesture.active = true;
                histogramGesture.start = Number(entry.settings[key]) || 0;
                histogramGesture.previous = clone(entry.settings);
            }
            const exposure = key === 'Exposure2012';
            const low = exposure ? -5 : -100;
            const high = exposure ? 5 : 100;
            const value = Math.max(low, Math.min(high, histogramGesture.start + delta));
            settingsChanged(key, exposure ? Math.round(value * 100) / 100 : Math.round(value), 'Histogram', { history: false });
            panels?.setSettings(entry.settings);
        },
    });
    document.addEventListener('pointerup', () => {
        if (!histogramGesture.active) return;
        histogramGesture.active = false;
        const entry = currentImage && stateCache.get(Number(currentImage.id));
        if (entry && histogramGesture.previous) {
            // One undo entry for the whole histogram drag (pre-gesture snapshot).
            settingsChanged(histogramGesture.key, entry.settings[histogramGesture.key], 'Histogram', { previousSettings: histogramGesture.previous });
        }
        histogramGesture.previous = null;
    }, true);
    const cropSlot = document.createElement('div');
    cropSlot.id = 'develop-crop-controls';
    const transformSlot = document.createElement('div');
    transformSlot.id = 'develop-transform-controls';
    panels = new DevelopPanels(panelHost, {
        histogramHost: histogramSlot, cropHost: cropSlot, transformHost: transformSlot, onChange: settingsChanged, onAutoTone: requestAutoTone,
        onWbPick: () => setWbPick(!wbPickActive),
        transform: {
            stage, canvas,
            onAutoLevel: async () => {
                if (!currentImage) return null;
                // Throw on failure so the panel can tell "request failed" from
                // the honest "analysed fine, no horizon found" (null).
                const response = await fetch(`/api/develop/${currentImage.id}/transform/auto`, fetchOptionsWithTimeout({ method: 'POST' }, DEVELOP_MUTATION_TIMEOUT_MS));
                if (!response.ok) throw new Error('auto-level request failed');
                return response.json();
            },
        },
        masking: { toolbar, stage, canvas, getImageId: () => currentImage?.id, getRenderer: () => renderer },
        heal: { toolbar, stage, canvas, getRenderer: () => renderer },
    });
    masking = panels.masking;
    heal = panels.heal;
    crop = new CropController({ stage, canvas, overlay: document.getElementById('develop-crop-overlay'), controls: cropSlot, onChange: settingsChanged });
    const transformedCanvasBox = () => renderer?.imageRectToStage(0, 0, 1, 1) || { left: 0, top: 0, width: 1, height: 1 };
    crop.canvasBox = transformedCanvasBox;
    masking.canvasBox = transformedCanvasBox;
    masking.point = (event) => {
        const point = renderer?.canvasToImage(event.clientX, event.clientY) || { u: .5, v: .5 };
        return { x: Math.max(0, Math.min(1, point.u)), y: Math.max(0, Math.min(1, point.v)) };
    };
    compare = new DevelopCompareView({ stage, canvas, loadPreview: comparisonPreview, getCurrent: () => currentImage, getImages: () => viewState.images });
    softProof = new SoftProofPopover({ toolbar, onChange: (proof) => {
        renderer?.setSoftProof(proof);
        compare?.setProof(proof);
    } });
    proofTile = new ProofTileController({
        stage,
        getRenderer: () => renderer,
        getContext: () => {
            const entry = currentImage && stateCache.get(Number(currentImage.id));
            return { imageId: currentImage?.id, settings: entry?.settings, center: zoomState.center };
        },
        onDisplayChange: (show) => {
            for (const overlay of stage.querySelectorAll('#develop-crop-overlay, .develop-mask-layer, .develop-heal-layer')) {
                if (show) {
                    overlay.dataset.proofVisibility = overlay.style.visibility;
                    overlay.style.visibility = 'hidden';
                } else {
                    overlay.style.visibility = overlay.dataset.proofVisibility || '';
                    delete overlay.dataset.proofVisibility;
                }
            }
            if (show) {
                renderer?.setMaskOverlay(null);
                renderer?.setHealOverlay(false);
            } else {
                masking?.setRendererOverlay(masking.overlayShown);
                heal?.syncOverlay();
            }
        },
    });
    presetsPanel = mountPresetsPanel(root.querySelector('.develop-layout') || root, {
        getRenderer: () => renderer,
        getEntry: () => currentImage && stateCache.get(Number(currentImage.id)),
        setTransientSettingsOverride,
        renderPresetThumbnail: renderCurrentImagePixels,
        applyPresetSettings,
        saveCurrentSettings: () => {
            const entry = currentImage && stateCache.get(Number(currentImage.id));
            return clone(entry?.settings || {});
        },
        notify: showToast,
    });
    historyPanel = mountHistoryPanel(presetsPanel?.root, {
        getEntry: () => currentImage && stateCache.get(Number(currentImage.id)),
        getImageId: () => currentImage?.id,
        restoreSettings: restoreHistoricalSettings,
        notify: showToast,
    });
    bindUi();
    updateTabState();
    // Keep the established harness name, but render the current decoded image for preset thumbnails.
    window.__developRenderToPixels = renderCurrentImagePixels;
}

init();
