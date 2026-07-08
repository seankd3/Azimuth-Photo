import { thumbUrl, writeFlags } from './api.js';
import { applyFlags } from './selection.js';
import { byId, emit, on, rememberImages } from './state.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';

const DEFAULT_THRESHOLD = 0.95;
const LIMIT = 100;
const TIMEOUT_MS = 30000;

let root = null;
let abortController = null;
let open = false;
let threshold = DEFAULT_THRESHOLD;
let groups = [];
let loading = false;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function flagGlyph(flag) {
    if (flag === 'picked') return '★';
    if (flag === 'rejected') return '×';
    return '○';
}

function normalizeFlag(flag) {
    return flag === 'picked' || flag === 'rejected' ? flag : 'unflagged';
}

function currentFlag(image) {
    const cached = byId.get(Number(image.id));
    return normalizeFlag((cached && cached.flag) || image.flag);
}

function mergeImage(image) {
    const id = Number(image?.id);
    const cached = byId.get(id) || {};
    return {
        ...cached,
        ...image,
        id,
        flag: normalizeFlag(image?.flag || cached.flag),
        thumb_url: image?.thumb_url || cached.thumb_url || thumbUrl('sm', id),
    };
}

function groupPairs(pairs = []) {
    const parent = new Map();
    const images = new Map();
    const pairCounts = new Map();

    const find = (id) => {
        if (!parent.has(id)) parent.set(id, id);
        const value = parent.get(id);
        if (value !== id) parent.set(id, find(value));
        return parent.get(id);
    };
    const join = (a, b) => {
        const rootA = find(a);
        const rootB = find(b);
        if (rootA !== rootB) parent.set(rootB, rootA);
    };

    for (const pair of pairs) {
        const a = mergeImage(pair.a || {});
        const b = mergeImage(pair.b || {});
        if (!a.id || !b.id) continue;
        images.set(a.id, a);
        images.set(b.id, b);
        join(a.id, b.id);
    }

    for (const pair of pairs) {
        const id = Number(pair?.a?.id);
        if (!id) continue;
        const key = find(id);
        pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
    }

    const buckets = new Map();
    for (const image of images.values()) {
        const key = find(image.id);
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(image);
    }

    return [...buckets.entries()]
        .map(([key, items]) => ({
            key,
            pairCount: pairCounts.get(key) || 0,
            images: items.sort((a, b) => (Number(b.elo) || 0) - (Number(a.elo) || 0)),
        }))
        .filter((group) => group.images.length > 1)
        .sort((a, b) => b.images.length - a.images.length || b.pairCount - a.pairCount);
}

function allImages() {
    return groups.flatMap((group) => group.images);
}

function uniqueImages(images) {
    const seen = new Set();
    return (images || []).filter((image) => {
        const id = Number(image.id);
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
    });
}

function groupChanges(group) {
    const keeper = group.images[0];
    if (!keeper) return [];
    return group.images.map((image) => ({
        id: Number(image.id),
        flag: Number(image.id) === Number(keeper.id) ? 'picked' : 'rejected',
    }));
}

function dedupeChanges(changes) {
    const byImage = new Map();
    for (const change of changes || []) {
        const id = Number(change.id);
        if (id > 0 && change.flag) byImage.set(id, { id, flag: change.flag });
    }
    return [...byImage.values()];
}

function groupedIds(changes) {
    const result = new Map();
    for (const change of changes) {
        if (!result.has(change.flag)) result.set(change.flag, []);
        result.get(change.flag).push(change.id);
    }
    return result;
}

function setFlagsLocally(changes) {
    const imageIds = [];
    for (const { id, flag } of changes) {
        const image = byId.get(id);
        if (image) image.flag = flag;
        for (const item of allImages()) {
            if (Number(item.id) === id) item.flag = flag;
        }
        imageIds.push(id);
    }
    emit('flags', { imageIds });
}

async function writeGrouped(changes) {
    for (const [flag, imageIds] of groupedIds(changes)) {
        const result = await writeFlags(imageIds, flag);
        if (!result || !result.ok) return false;
    }
    return true;
}

async function applyKeepBest(changes, label) {
    const normalized = dedupeChanges(changes);
    if (!normalized.length) return;
    const previous = normalized.map(({ id }) => ({
        id,
        flag: normalizeFlag((byId.get(id) || {}).flag),
    }));
    setFlagsLocally(normalized);
    const ok = await writeGrouped(normalized);
    if (!ok) {
        setFlagsLocally(previous);
        await writeGrouped(previous);
        showToast("Keep-best didn't save");
        return;
    }
    showToast(`${label} · ${fmt(normalized.length)} photos`, {
        undo: async () => {
            setFlagsLocally(previous);
            const undone = await writeGrouped(previous);
            showToast(undone ? 'Undone' : "Undo didn't save");
        },
    });
}

function thresholdLabel() {
    return `${Math.round(threshold * 100)}%`;
}

function renderSkeleton() {
    const body = root.querySelector('#duplicates-body');
    body.innerHTML = '<div class="dupe-skeleton">'
        + Array.from({ length: 4 }, () => '<div class="dupe-row"><div class="dupe-row-head"><div class="skel"></div><div class="skel"></div></div><div class="dupe-photos"><div class="dupe-card skel"></div><div class="dupe-card skel"></div></div></div>').join('')
        + '</div>';
}

function photoHtml(image) {
    const flag = currentFlag(image);
    const aspect = Number(image.width) && Number(image.height)
        ? Math.max(.65, Math.min(2.2, Number(image.width) / Number(image.height)))
        : 1.5;
    return `<article class="dupe-photo" style="--dupe-ar:${aspect}">`
        + `<button class="dupe-thumb" data-open-id="${image.id}" aria-label="Open ${esc(image.filename || image.id)} in Loupe">`
        + `<img src="${esc(thumbUrl('md', image.id))}" loading="lazy" decoding="async" alt="${esc(image.filename || '')}">`
        + `<span class="dupe-elo elo-chip">${Math.round(Number(image.elo) || 0)}</span>`
        + `<span class="dupe-flag ${flag}" title="${esc(flag)}">${flagGlyph(flag)}</span>`
        + '</button>'
        + `<div class="dupe-name" title="${esc(image.filename || '')}">${esc(image.filename || image.id)}</div>`
        + '<div class="dupe-actions" aria-label="Flag photo">'
        + `<button data-flag="picked" data-id="${image.id}" aria-label="Pick ${esc(image.filename || image.id)}">P</button>`
        + `<button data-flag="rejected" data-id="${image.id}" aria-label="Reject ${esc(image.filename || image.id)}">X</button>`
        + `<button data-flag="unflagged" data-id="${image.id}" aria-label="Clear flag for ${esc(image.filename || image.id)}">U</button>`
        + '</div></article>';
}

function renderError({ title, copy, retry = true } = {}) {
    const button = retry ? '<button class="btn" id="duplicates-retry">Try again</button>' : '';
    root.querySelector('#duplicates-body').innerHTML = '<div class="load-error dupe-error">'
        + `<h4>${esc(title || "Couldn't load duplicates")}</h4>`
        + `<p>${esc(copy || 'GET /api/duplicates did not respond.')}</p>${button}</div>`;
    root.querySelector('#duplicates-retry')?.addEventListener('click', loadDuplicates);
}

function renderEmpty() {
    root.querySelector('#duplicates-body').innerHTML = '<div class="load-error dupe-empty">'
        + `<h4>No duplicates at ≥ ${thresholdLabel()} similarity.</h4>`
        + '<p>Lower the threshold to widen the scan.</p></div>';
}

function renderGroups() {
    const body = root.querySelector('#duplicates-body');
    root.querySelector('#duplicates-count').textContent = `${fmt(groups.length)} group${groups.length === 1 ? '' : 's'}`;
    root.querySelector('#duplicates-keep-all').disabled = !groups.length || loading;
    if (!groups.length) {
        renderEmpty();
        return;
    }
    body.innerHTML = groups.map((group, index) => (
        `<section class="dupe-row" data-group="${index}">`
        + '<div class="dupe-row-head">'
        + `<div><b>${fmt(group.images.length)} photos</b><span>similarity ≥ ${thresholdLabel()}</span></div>`
        + `<button class="btn" data-keep-group="${index}">Keep best</button>`
        + '</div>'
        + `<div class="dupe-photos">${group.images.map(photoHtml).join('')}</div>`
        + '</section>'
    )).join('');
}

function renderLoaded(data) {
    groups = groupPairs(data?.pairs || []);
    rememberImages(uniqueImages(allImages()));
    renderGroups();
}

async function hydrateFlags() {
    const ids = uniqueImages(allImages()).map((image) => Number(image.id)).filter((id) => id > 0);
    if (!ids.length) return;
    try {
        const response = await fetch(`/api/export?format=json&ids=${encodeURIComponent(ids.join(','))}&limit=${ids.length}`);
        if (!response.ok) return;
        const rows = await response.json();
        for (const row of rows || []) {
            const id = ids[Number(row.rank) - 1] || Number(row.id);
            const target = allImages().find((image) => Number(image.id) === Number(id));
            if (target) {
                target.flag = normalizeFlag(row.flag);
                target.width = row.width || target.width;
                target.height = row.height || target.height;
            }
            const cached = byId.get(Number(id));
            if (cached) cached.flag = normalizeFlag(row.flag);
        }
        rememberImages(uniqueImages(allImages()));
    } catch {}
}

async function loadDuplicates() {
    if (!root) return;
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const controller = abortController;
    loading = true;
    groups = [];
    root.querySelector('#duplicates-count').textContent = 'Scanning...';
    root.querySelector('#duplicates-keep-all').disabled = true;
    renderSkeleton();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
        const params = new URLSearchParams({ threshold: threshold.toFixed(2), limit: String(LIMIT) });
        const response = await fetch(`/api/duplicates?${params.toString()}`, { signal: controller.signal });
        clearTimeout(timeout);
        if (controller !== abortController) return;
        if (response.status === 503) {
            loading = false;
            renderError({
                title: 'Duplicates unavailable',
                copy: 'GET /api/duplicates returned 503. Embeddings need to be installed and indexed before duplicate scanning can run.',
            });
            return;
        }
        if (!response.ok) throw new Error(`GET /api/duplicates returned ${response.status}`);
        const data = await response.json();
        loading = false;
        renderLoaded(data);
        await hydrateFlags();
        if (controller === abortController) renderGroups();
    } catch (error) {
        clearTimeout(timeout);
        if (controller !== abortController) return;
        loading = false;
        renderError({
            title: error.name === 'AbortError' ? 'Duplicate scan timed out' : "Couldn't load duplicates",
            copy: error.name === 'AbortError'
                ? 'GET /api/duplicates took more than 30 seconds.'
                : `${error.message || 'GET /api/duplicates failed'}.`,
        });
    }
}

function overlayHtml() {
    return '<div id="duplicates" role="dialog" aria-modal="true" aria-label="Find duplicates" hidden tabindex="-1">'
        + '<div id="duplicates-panel">'
        + '<header id="duplicates-head">'
        + '<div><b>Find duplicates</b><span id="duplicates-count" class="num"></span></div>'
        + '<label class="dupe-threshold"><span>Similarity</span><output id="duplicates-threshold-value">95%</output><input id="duplicates-threshold" class="ctl-range" type="range" min="0.90" max="0.99" step="0.01" value="0.95"></label>'
        + '<button class="btn primary" id="duplicates-keep-all" disabled>Keep best everywhere</button>'
        + '<button class="icon-btn" id="duplicates-close" data-tip="Close (Esc)" aria-label="Close">×</button>'
        + '</header>'
        + '<div id="duplicates-body"></div>'
        + '</div></div>';
}

function ensureOverlay() {
    if (root) return root;
    const wrap = document.createElement('div');
    wrap.innerHTML = overlayHtml();
    root = wrap.firstElementChild;
    document.body.appendChild(root);
    root.querySelector('#duplicates-close').addEventListener('click', closeDuplicates);
    root.querySelector('#duplicates-keep-all').addEventListener('click', () => {
        applyKeepBest(groups.flatMap(groupChanges), 'Kept best everywhere');
    });
    root.querySelector('#duplicates-threshold').addEventListener('input', (event) => {
        const value = Number(event.target.value) || DEFAULT_THRESHOLD;
        root.querySelector('#duplicates-threshold-value').textContent = `${Math.round(value * 100)}%`;
    });
    root.querySelector('#duplicates-threshold').addEventListener('change', (event) => {
        threshold = Number(event.target.value) || DEFAULT_THRESHOLD;
        root.querySelector('#duplicates-threshold-value').textContent = thresholdLabel();
        if (open) loadDuplicates();
    });
    root.querySelector('#duplicates-body').addEventListener('click', (event) => {
        const openButton = event.target.closest('[data-open-id]');
        if (openButton) {
            const id = Number(openButton.dataset.openId);
            const images = uniqueImages(allImages());
            emit('loupe:open', { id, index: images.findIndex((image) => Number(image.id) === id), images });
            return;
        }
        const flagButton = event.target.closest('[data-flag][data-id]');
        if (flagButton) {
            applyFlags([Number(flagButton.dataset.id)], flagButton.dataset.flag);
            return;
        }
        const keepButton = event.target.closest('[data-keep-group]');
        if (keepButton) {
            const group = groups[Number(keepButton.dataset.keepGroup)];
            if (group) applyKeepBest(groupChanges(group), 'Kept best');
        }
    });
    on('flags', () => {
        if (open && !loading) renderGroups();
    });
    return root;
}

export function openDuplicates() {
    ensureOverlay();
    if (open) return;
    open = true;
    root.hidden = false;
    trapFocus(root, root);
    loadDuplicates();
}

export function closeDuplicates() {
    if (!open || !root) return;
    open = false;
    if (abortController) abortController.abort();
    abortController = null;
    root.hidden = true;
    releaseFocus(root);
}

export function duplicatesOpen() {
    return open;
}

export function initDuplicates() {
    ensureOverlay();
    document.getElementById('find-duplicates')?.addEventListener('click', openDuplicates);
    on('duplicates:open', openDuplicates);
}
