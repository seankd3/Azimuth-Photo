import {
    createStack, getStackRebuildStatus, listStacks, rebuildStacks, restoreImages, setStackRepresentative,
    thumbUrl, trashImages, unstack, writeFlags,
} from './api.js';
import { applyFlags } from './selection.js';
import {
    byId, emit, on, rememberImages, setActiveLens,
} from './state.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { keepCoverRejectRest } from './stack_cull.js';

const DEFAULT_THRESHOLD = 0.95;
const LIMIT = 100;
const STACK_LIMIT = 50;
const TIMEOUT_MS = 30000;
const STACK_KINDS = [
    ['all', '', 'All'],
    ['burst', 'burst', 'Bursts'],
    ['variant', 'variant', 'Variants'],
    ['crosssource', 'crosssource', 'Cross-source'],
    ['manual', 'manual', 'Manual'],
];

let root = null;
let abortController = null;
let open = false;
let threshold = DEFAULT_THRESHOLD;
let groups = [];
let mode = 'stacks';
let stackKind = '';
let stacks = [];
let stackCounts = {};
let stackTotal = 0;
let stackOffset = 0;
let stackDone = false;
let stackLoading = false;
let stackGeneration = 0;
let stackSentinel = null;
let stackObserver = null;
let loading = false;
let stackRescanning = false;
let bulkNonCoverIds = null;
let bulkCountGeneration = 0;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const RAW_EXTS = new Set(['arw', 'cr2', 'cr3', 'dng', 'nef', 'orf', 'raf', 'rw2']);

function flagGlyph(flag) {
    if (flag === 'picked') return icon('star');
    if (flag === 'rejected') return icon('x');
    return icon('circle');
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

function normalizedExt(image) {
    const filename = String(image?.filename || '');
    const fallback = filename.includes('.') ? filename.split('.').pop() : '';
    return String(image?.file_ext || fallback || '')
        .replace(/^\./, '')
        .toLowerCase();
}

function fileType(image) {
    const ext = normalizedExt(image);
    if (RAW_EXTS.has(ext)) return 'RAW';
    if (ext === 'jpg' || ext === 'jpeg') return 'JPG';
    if (ext === 'tif' || ext === 'tiff') return 'TIFF';
    return ext ? ext.toUpperCase() : '—';
}

function typeRank(image) {
    const type = fileType(image);
    if (type === 'RAW') return 4;
    if (type === 'TIFF') return 3;
    if (type === 'JPG') return 2;
    return type === '—' ? 0 : 1;
}

function pixels(image) {
    return (Number(image?.width) || 0) * (Number(image?.height) || 0);
}

function bytesLabel(value) {
    const n = Number(value) || 0;
    if (!n) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = n;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function dimensionsLabel(image) {
    const width = Number(image?.width) || 0;
    const height = Number(image?.height) || 0;
    return width && height ? `${fmt(width)} × ${fmt(height)}` : '—';
}

function dateLabel(value) {
    const text = String(value || '').trim();
    return text ? text.slice(0, 10) : '—';
}

function folderLabel(image) {
    const folder = String(image?.folder || '').trim();
    if (folder) {
        const folderParts = folder.replace(/\\/g, '/').split('/').filter(Boolean);
        return folderParts.length ? folderParts[folderParts.length - 1] : folder;
    }
    const path = String(image?.filepath || '').replace(/\\/g, '/');
    const parts = path.split('/').filter(Boolean);
    return parts.length > 1 ? parts[parts.length - 2] : '—';
}

function fieldValues(image) {
    return {
        filename: image?.filename || String(image?.id || ''),
        type: fileType(image),
        dimensions: dimensionsLabel(image),
        size: bytesLabel(image?.file_size),
        date: dateLabel(image?.date_taken),
        folder: folderLabel(image),
    };
}

function bestBy(group, score) {
    return group.images.reduce((winner, image) => (score(image) > score(winner) ? image : winner), group.images[0]);
}

function largestFileKeeper(group) {
    return group.images.slice().sort((a, b) => (
        pixels(b) - pixels(a)
        || (Number(b.file_size) || 0) - (Number(a.file_size) || 0)
        || (Number(b.elo) || 0) - (Number(a.elo) || 0)
    ))[0];
}

function groupDiffs(group) {
    const values = group.images.map(fieldValues);
    const differs = {};
    for (const field of ['filename', 'type', 'dimensions', 'size', 'date', 'folder']) {
        differs[field] = new Set(values.map((value) => value[field])).size > 1;
    }
    const winners = {
        type: bestBy(group, typeRank)?.id,
        dimensions: bestBy(group, pixels)?.id,
        size: bestBy(group, (image) => Number(image.file_size) || 0)?.id,
        date: bestBy(group, (image) => Date.parse(image.date_taken || '') || 0)?.id,
    };
    return { differs, winners };
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

function changesForKeeper(group, keeper) {
    if (!keeper) return [];
    return group.images.map((image) => ({
        id: Number(image.id),
        flag: Number(image.id) === Number(keeper.id) ? 'picked' : 'rejected',
    }));
}

function groupChanges(group) {
    return changesForKeeper(group, group.images[0]);
}

function largestFileChanges(group) {
    return changesForKeeper(group, largestFileKeeper(group));
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
        showToast('Couldn’t keep best');
        return;
    }
    showToast(`${label} · ${fmt(normalized.length)} photos`, {
        undo: async () => {
            setFlagsLocally(previous);
            const undone = await writeGrouped(previous);
            showToast(undone ? 'Undone' : 'Couldn’t undo');
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

function metaClass(field, image, diff) {
    if (!diff.differs[field]) return 'muted';
    return Number(diff.winners[field]) === Number(image.id) ? 'win' : 'diff';
}

function metaRow(field, label, value, image, diff) {
    return `<div class="dupe-meta-row ${metaClass(field, image, diff)}"><span>${esc(label)}</span><b title="${esc(value)}">${esc(value)}</b></div>`;
}

function photoHtml(image, diff) {
    const flag = currentFlag(image);
    const aspect = Number(image.width) && Number(image.height)
        ? Math.max(.65, Math.min(2.2, Number(image.width) / Number(image.height)))
        : 1.5;
    const values = fieldValues(image);
    return `<article class="dupe-photo" style="--dupe-ar:${aspect}">`
        + `<button class="dupe-thumb" data-open-id="${image.id}" aria-label="Open ${esc(image.filename || image.id)} in Loupe">`
        + `<img src="${esc(thumbUrl('md', image.id))}" loading="lazy" decoding="async" alt="${esc(image.filename || '')}">`
        + `<span class="dupe-elo elo-chip">${Math.round(Number(image.elo) || 0)}</span>`
        + `<span class="dupe-flag ${flag}" title="${esc(flag)}">${flagGlyph(flag)}</span>`
        + '</button>'
        + '<div class="dupe-facts">'
        + `<div class="dupe-name ${diff.differs.filename ? 'diff' : 'muted'}" title="${esc(values.filename)}">${esc(values.filename)}</div>`
        + `<div class="dupe-type ${metaClass('type', image, diff)}">${esc(values.type)}</div>`
        + metaRow('dimensions', 'Dimensions', values.dimensions, image, diff)
        + metaRow('size', 'File size', values.size, image, diff)
        + metaRow('date', 'Taken', values.date, image, diff)
        + metaRow('folder', 'Folder', values.folder, image, diff)
        + '</div>'
        + '<div class="dupe-actions" aria-label="Flag photo">'
        + `<button data-flag="picked" data-id="${image.id}" aria-label="Pick ${esc(image.filename || image.id)}">${icon('star')}</button>`
        + `<button data-flag="rejected" data-id="${image.id}" aria-label="Reject ${esc(image.filename || image.id)}">${icon('x')}</button>`
        + `<button data-flag="unflagged" data-id="${image.id}" aria-label="Clear flag for ${esc(image.filename || image.id)}">${icon('circle')}</button>`
        + '</div></article>';
}

function renderError({ title, copy, retry = true } = {}) {
    const button = retry ? '<button class="btn" id="duplicates-retry">Try again</button>' : '';
    root.querySelector('#duplicates-body').innerHTML = '<div class="load-error dupe-error">'
        + `<h4>${esc(title || "Couldn't load duplicates")}</h4>`
        + `<p>${esc(copy || 'The archive did not respond. Try again.')}</p>${button}</div>`;
    root.querySelector('#duplicates-retry')?.addEventListener('click', loadDuplicates);
}

function renderEmpty() {
    root.querySelector('#duplicates-body').innerHTML = '<div class="grid-empty dupe-empty">'
        + `<h3>No duplicates at ${thresholdLabel()} similarity or higher.</h3>`
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
        + '<div class="dupe-row-actions">'
        + `<button class="btn" data-keep-group="${index}">Keep highest rated</button>`
        + '<details class="dupe-more">'
        + `<summary class="icon-btn" data-tip="More keeper choices" aria-label="More keeper choices">${icon('ellipsis')}</summary>`
        + `<div class="dupe-menu"><button data-keep-largest-group="${index}">Keep largest file</button></div>`
        + '</details></div>'
        + '</div>'
        + `<div class="dupe-photos">${group.images.map((image) => photoHtml(image, groupDiffs(group))).join('')}</div>`
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
                target.file_ext = row.file_ext || target.file_ext;
                target.file_size = row.file_size || target.file_size;
                target.date_taken = row.date_taken || target.date_taken;
                target.filepath = row.filepath || target.filepath;
            }
            const cached = byId.get(Number(id));
            if (cached) {
                Object.assign(cached, {
                    flag: normalizeFlag(row.flag),
                    width: row.width || cached.width,
                    height: row.height || cached.height,
                    file_ext: row.file_ext || cached.file_ext,
                    file_size: row.file_size || cached.file_size,
                    date_taken: row.date_taken || cached.date_taken,
                    filepath: row.filepath || cached.filepath,
                });
            }
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
                copy: 'Duplicate matching needs the visual search index to be installed and ready first.',
            });
            return;
        }
        if (!response.ok) throw new Error(`Duplicate scan returned ${response.status}`);
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
                ? 'The scan took more than 30 seconds. Try again when the archive is less busy.'
                : 'The archive did not respond. Try again.',
        });
    }
}

function stackMembers(stack) {
    return (stack?.members && stack.members.length ? stack.members : stack?.members_preview || [])
        .map(mergeImage)
        .filter((image) => Number(image.id) > 0);
}

function representativeId(stack) {
    return Number(stack?.representative?.id || stack?.representative_id || stackMembers(stack)[0]?.id || 0);
}

function stackKindLabel(kind) {
    return ({
        burst: 'Burst',
        variant: 'Variant',
        crosssource: 'Cross-source',
        manual: 'Manual',
    })[kind] || 'Stack';
}

function renderStackSkeleton() {
    const body = root.querySelector('#duplicates-body');
    body.innerHTML = '<div class="dupe-skeleton">'
        + Array.from({ length: 4 }, () => '<div class="dupe-row"><div class="dupe-row-head"><div class="skel"></div><div class="skel"></div></div><div class="dupe-photos"><div class="dupe-card skel"></div><div class="dupe-card skel"></div><div class="dupe-card skel"></div></div></div>').join('')
        + '</div><div id="stacks-sentinel"></div>';
}

function renderKindChips() {
    const host = root.querySelector('#stacks-kind-chips');
    if (!host) return;
    host.innerHTML = STACK_KINDS.map(([id, value, label]) => {
        const count = id === 'all' ? stackCounts.all : stackCounts[value];
        const active = stackKind === value;
        return `<button class="filter-pill ${active ? 'active' : ''}" data-kind="${esc(value)}">${esc(label)} <span>${count == null ? '…' : fmt(count)}</span></button>`;
    }).join('');
}

function metaNote(stack) {
    const kind = stackKindLabel(stack.kind);
    const count = Number(stack.member_count) || stackMembers(stack).length;
    const auto = stack.auto === false ? 'manual' : 'auto';
    return `${kind} · ${fmt(count)} photos · ${auto}`;
}

function stackPhotoHtml(stack, image, diff) {
    const repId = representativeId(stack);
    const isCover = Number(image.id) === repId;
    const flag = currentFlag(image);
    const aspect = Number(image.width) && Number(image.height)
        ? Math.max(.65, Math.min(2.2, Number(image.width) / Number(image.height)))
        : 1.5;
    const values = fieldValues(image);
    return `<article class="dupe-photo stack-photo ${isCover ? 'is-cover' : ''}" style="--dupe-ar:${aspect}">`
        + `<button class="dupe-thumb" data-open-id="${image.id}" data-stack-open="${stack.id}" aria-label="Open ${esc(image.filename || image.id)} in Loupe">`
        + `<img src="${esc(thumbUrl('md', image.id))}" loading="lazy" decoding="async" alt="${esc(image.filename || '')}">`
        + `<span class="dupe-elo elo-chip">${Math.round(Number(image.elo) || 0)}</span>`
        + `<span class="dupe-flag ${flag}" title="${esc(flag)}">${flagGlyph(flag)}</span>`
        + (isCover ? `<span class="stack-cover-badge">${icon('image')} Cover</span>` : '')
        + '</button>'
        + '<div class="dupe-facts">'
        + `<div class="dupe-name ${diff.differs.filename ? 'diff' : 'muted'}" title="${esc(values.filename)}">${esc(values.filename)}</div>`
        + `<div class="dupe-type ${metaClass('type', image, diff)}">${esc(values.type)}</div>`
        + metaRow('dimensions', 'Dimensions', values.dimensions, image, diff)
        + metaRow('size', 'File size', values.size, image, diff)
        + metaRow('date', 'Taken', values.date, image, diff)
        + metaRow('folder', 'Folder', values.folder, image, diff)
        + '</div>'
        + '<div class="dupe-actions stack-member-actions" aria-label="Stack photo actions">'
        + (isCover ? `<span class="stack-cover-text">${icon('image')} Cover</span>` : `<button data-set-cover="${image.id}" data-stack-id="${stack.id}" aria-label="Make cover" data-tip="Make cover">${icon('image')}</button>`)
        + `<button data-flag="picked" data-id="${image.id}" aria-label="Pick ${esc(image.filename || image.id)}">${icon('star')}</button>`
        + `<button data-flag="rejected" data-id="${image.id}" aria-label="Reject ${esc(image.filename || image.id)}">${icon('x')}</button>`
        + `<button data-flag="unflagged" data-id="${image.id}" aria-label="Clear flag for ${esc(image.filename || image.id)}">${icon('circle')}</button>`
        + '</div></article>';
}

function stackRowHtml(stack) {
    const members = stackMembers(stack);
    const diff = groupDiffs({ images: members });
    const actionDisabled = stackRescanning ? ' disabled' : '';
    return `<section class="dupe-row stack-row" data-stack="${stack.id}">`
        + '<div class="dupe-row-head">'
        + `<div><b>${fmt(members.length)} photos</b><span>${esc(metaNote(stack))}</span></div>`
        + '<div class="dupe-row-actions">'
        + `<button class="btn btn-danger" data-stack-reject="${stack.id}"${actionDisabled}>Keep cover, reject rest</button>`
        + `<button class="btn btn-danger" data-stack-keep="${stack.id}"${actionDisabled}>Keep cover, trash rest</button>`
        + `<button class="btn" data-stack-unstack="${stack.id}"${actionDisabled}>Unstack</button>`
        + '</div></div>'
        + `<div class="dupe-photos">${members.map((image) => stackPhotoHtml(stack, image, diff)).join('')}</div>`
        + '</section>';
}

function renderStacks({ append = false } = {}) {
    root.querySelector('#duplicates-count').textContent = stackRescanning
        ? `Rescanning... ${fmt(stackTotal)} stack${stackTotal === 1 ? '' : 's'} shown`
        : `${fmt(stackTotal)} stack${stackTotal === 1 ? '' : 's'}`;
    root.querySelector('#stacks-keep-covers').disabled = !stackTotal || stackLoading || stackRescanning;
    root.querySelector('#stacks-rescan').disabled = stackRescanning;
    renderKindChips();
    const body = root.querySelector('#duplicates-body');
    const banner = stackRescanning ? '<div class="stack-status-banner">Rescanning stacks. Review actions are paused until fresh results are ready.</div>' : '';
    if (!append) {
        if (!stacks.length && !stackLoading) {
            body.innerHTML = banner + '<div class="grid-empty dupe-empty"><h3>No stacks in this view.</h3><p>Add photos first, or try another kind after the library is scanned.</p></div><div id="stacks-sentinel"></div>';
        } else {
            body.innerHTML = banner + stacks.map(stackRowHtml).join('') + `<div id="stacks-sentinel">${stackLoading && stacks.length ? 'Loading more stacks...' : ''}</div>`;
        }
    } else {
        const sentinel = root.querySelector('#stacks-sentinel');
        sentinel?.insertAdjacentHTML('beforebegin', stacks.slice(Math.max(0, stacks.length - STACK_LIMIT)).map(stackRowHtml).join(''));
        if (sentinel) sentinel.textContent = stackLoading && stacks.length ? 'Loading more stacks...' : '';
    }
    bindStackSentinel();
}

function invalidateBulkNonCoverCount() {
    bulkNonCoverIds = null;
    bulkCountGeneration += 1;
    const button = root?.querySelector('#stacks-keep-covers');
    if (button) button.textContent = 'Trash non-covers';
}

async function refreshBulkNonCoverCount() {
    if (!root || mode !== 'stacks' || stackLoading || stackRescanning || !stackTotal) return;
    const generation = bulkCountGeneration + 1;
    bulkCountGeneration = generation;
    bulkNonCoverIds = null;
    const button = root.querySelector('#stacks-keep-covers');
    if (!button) return;
    button.textContent = 'Counting...';
    button.disabled = true;
    const imageIds = await collectNonCoverIdsForCurrentFilter();
    if (generation !== bulkCountGeneration || !root?.isConnected || mode !== 'stacks') return;
    bulkNonCoverIds = imageIds;
    button.textContent = imageIds.length ? `Trash ${fmt(imageIds.length)} photos` : 'No non-covers';
    button.disabled = !imageIds.length || stackLoading || stackRescanning;
}

async function loadStackCounts() {
    const seq = stackGeneration;
    const entries = await Promise.all(STACK_KINDS.map(async ([id, value]) => {
        const data = await listStacks({ kind: value, limit: 1, offset: 0 });
        return [id === 'all' ? 'all' : value, Number(data?.total) || 0];
    }));
    if (seq !== stackGeneration || !root?.isConnected || !open || mode !== 'stacks') return;
    stackCounts = Object.fromEntries(entries);
    renderKindChips();
}

async function loadStackPage({ reset = false } = {}) {
    if (!root || mode !== 'stacks' || (!reset && stackLoading) || (stackDone && !reset)) return;
    const seq = stackGeneration;
    const requestMode = mode;
    const requestKind = stackKind;
    if (reset) {
        stackOffset = 0;
        stackDone = false;
        stacks = [];
        renderStackSkeleton();
    }
    stackLoading = true;
    const requestOffset = stackOffset;
    const data = await listStacks({ kind: requestKind, limit: STACK_LIMIT, offset: requestOffset });
    if (seq !== stackGeneration || !root?.isConnected || !open || requestMode !== mode || mode !== 'stacks' || requestKind !== stackKind) return;
    stackLoading = false;
    if (!data) {
        root.querySelector('#duplicates-body').innerHTML = '<div class="load-error dupe-error"><h4>Couldn\'t load stacks</h4><p>The archive did not respond. Try again.</p><button class="btn" id="stacks-retry">Try again</button></div>';
        root.querySelector('#stacks-retry')?.addEventListener('click', () => loadStackPage({ reset: true }));
        return;
    }
    const incoming = data.stacks || [];
    stackTotal = Number(data.total) || incoming.length;
    for (const stack of incoming) rememberImages(stackMembers(stack));
    stacks = reset ? incoming : stacks.concat(incoming);
    stackOffset += incoming.length;
    stackDone = incoming.length < STACK_LIMIT || stackOffset >= stackTotal;
    renderStacks({ append: !reset });
}

function bindStackSentinel() {
    const nextSentinel = root.querySelector('#stacks-sentinel');
    if (!nextSentinel) {
        resetStackObserver();
        return;
    }
    if (stackSentinel === nextSentinel && stackObserver) return;
    if (stackObserver) stackObserver.disconnect();
    stackSentinel = nextSentinel;
    stackObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadStackPage();
    }, { root: root.querySelector('#duplicates-body'), rootMargin: '600px 0px' });
    stackObserver.observe(stackSentinel);
}

function resetStackObserver() {
    if (stackObserver) stackObserver.disconnect();
    stackObserver = null;
    stackSentinel = null;
}

async function reloadStacks() {
    stackGeneration += 1;
    stackLoading = false;
    invalidateBulkNonCoverCount();
    const seq = stackGeneration;
    resetStackObserver();
    await loadStackCounts();
    if (seq !== stackGeneration || mode !== 'stacks') return;
    await loadStackPage({ reset: true });
    if (seq === stackGeneration && mode === 'stacks') refreshBulkNonCoverCount();
}

function findStack(stackId) {
    const id = Number(stackId);
    return stacks.find((stack) => Number(stack.id) === id);
}

function removeFinishedStack(stackId) {
    const id = Number(stackId);
    const finished = findStack(id);
    const previousLength = stacks.length;
    stacks = stacks.filter((stack) => Number(stack.id) !== id);
    if (stacks.length === previousLength) return;
    stackTotal = Math.max(0, stackTotal - 1);
    if (stackCounts.all != null) stackCounts.all = Math.max(0, stackCounts.all - 1);
    if (finished?.kind && stackCounts[finished.kind] != null) {
        stackCounts[finished.kind] = Math.max(0, stackCounts[finished.kind] - 1);
    }
    invalidateBulkNonCoverCount();
    renderStacks();
    refreshBulkNonCoverCount();
}

async function setCover(stackId, imageId) {
    const stack = findStack(stackId);
    if (!stack) return;
    const result = await setStackRepresentative(stackId, imageId);
    if (!result) {
        showToast('Couldn’t save cover');
        return;
    }
    const member = stackMembers(stack).find((image) => Number(image.id) === Number(imageId));
    stack.representative = member || { id: Number(imageId) };
    renderStacks();
    showToast('Cover updated');
}

async function unstackOne(stackId) {
    const stack = findStack(stackId);
    const members = stackMembers(stack);
    const result = await unstack(stackId);
    if (!result) {
        showToast('Couldn’t unstack photos');
        return;
    }
    const imageIds = members.map((image) => Number(image.id)).filter((id) => id > 0);
    const coverId = representativeId(stack);
    removeFinishedStack(stackId);
    showToast('Stack removed', {
        undo: async () => {
            const restored = await createStack(imageIds, coverId);
            if (restored && open && mode === 'stacks') await reloadStacks();
            showToast(restored ? 'Stack restored' : 'Couldn’t restore stack');
        },
    });
}

async function keepCoverForStack(stackId) {
    const stack = findStack(stackId);
    const repId = representativeId(stack);
    const imageIds = stackMembers(stack).map((image) => Number(image.id)).filter((id) => id && id !== repId);
    if (!imageIds.length) return;
    const result = await trashImages(imageIds);
    if (!result) {
        showToast('Couldn’t move photos to Trash');
        return;
    }
    emit('trash:changed', { imageIds });
    removeFinishedStack(stackId);
    showToast(`Trashed ${fmt(imageIds.length)} stack member${imageIds.length === 1 ? '' : 's'}`, {
        undo: async () => {
            const restored = await restoreImages(imageIds);
            emit('trash:changed', { imageIds });
            if (open && mode === 'stacks') await reloadStacks();
            showToast(restored ? 'Restored' : 'Couldn’t restore');
        },
    });
}

async function collectNonCoverIdsForCurrentFilter() {
    const ids = [];
    let offset = 0;
    while (true) {
        const data = await listStacks({ kind: stackKind, limit: 200, offset });
        const incoming = data?.stacks || [];
        for (const stack of incoming) {
            const repId = representativeId(stack);
            ids.push(...stackMembers(stack).map((image) => Number(image.id)).filter((id) => id && id !== repId));
        }
        offset += incoming.length;
        if (!incoming.length || incoming.length < 200 || offset >= Number(data?.total || 0)) break;
    }
    return [...new Set(ids)];
}

async function keepCoversEverywhere() {
    const button = root.querySelector('#stacks-keep-covers');
    button.disabled = true;
    if (!bulkNonCoverIds) {
        button.textContent = 'Counting...';
        bulkNonCoverIds = await collectNonCoverIdsForCurrentFilter();
    }
    const imageIds = bulkNonCoverIds || [];
    button.textContent = imageIds.length ? `Trash ${fmt(imageIds.length)} photos` : 'No non-covers';
    button.disabled = !imageIds.length;
    if (!imageIds.length) {
        showToast('No other photos in this filter');
        return;
    }
    const result = await trashImages(imageIds);
    if (!result) {
        showToast('Couldn’t move photos to Trash');
        return;
    }
    emit('trash:changed', { imageIds });
    invalidateBulkNonCoverCount();
    await reloadStacks();
    showToast(`Trashed ${fmt(imageIds.length)} non-cover photos`, {
        undo: async () => {
            const restored = await restoreImages(imageIds);
            emit('trash:changed', { imageIds });
            if (open && mode === 'stacks') await reloadStacks();
            showToast(restored ? 'Restored' : 'Couldn’t restore');
        },
    });
}

async function rescanStacks() {
    const button = root.querySelector('#stacks-rescan');
    stackRescanning = true;
    button.disabled = true;
    button.textContent = 'Rescanning...';
    renderStacks();
    const started = await rebuildStacks();
    if (!started) {
        stackRescanning = false;
        button.disabled = false;
        button.textContent = 'Rescan stacks';
        renderStacks();
        showToast('Couldn’t start rescan');
        return;
    }
    const poll = async () => {
        const status = await getStackRebuildStatus();
        const state = String(status?.state || status?.status || '').toLowerCase();
        if (state && !['done', 'idle', 'complete', 'completed'].includes(state)) {
            setTimeout(poll, 1200);
            return;
        }
        stackRescanning = false;
        button.disabled = false;
        button.textContent = 'Rescan stacks';
        await reloadStacks();
        const counts = STACK_KINDS.slice(1).map(([, value, label]) => `${label} ${fmt(stackCounts[value] || 0)}`).join(' · ');
        showToast(`Stacks rescanned · ${counts}`);
    };
    poll();
}

function switchMode(nextMode) {
    stackGeneration += 1;
    stackLoading = false;
    resetStackObserver();
    mode = nextMode === 'adhoc' ? 'adhoc' : 'stacks';
    root.querySelector('#stacks-review-tools').hidden = mode !== 'stacks';
    root.querySelector('#duplicates-adhoc-tools').hidden = mode !== 'adhoc';
    for (const button of root.querySelectorAll('[data-stack-mode]')) {
        button.classList.toggle('active', button.dataset.stackMode === mode);
    }
    if (mode === 'stacks') reloadStacks();
    else loadDuplicates();
}

function viewHtml() {
    return '<div id="duplicates" hidden>'
        + '<div id="duplicates-panel">'
        + '<header id="duplicates-head">'
        + '<div><b>Stacks</b><span id="duplicates-count" class="num"></span></div>'
        + '<div class="seg-compact" role="group" aria-label="Stacks mode"><button class="active" data-stack-mode="stacks">Review</button><button data-stack-mode="adhoc">Ad-hoc scan</button></div>'
        + '<div id="stacks-review-tools"><button class="btn btn-danger" id="stacks-keep-covers" disabled>Trash non-covers</button><button class="btn" id="stacks-rescan">Rescan stacks</button></div>'
        + '<div id="duplicates-adhoc-tools" hidden>'
        + '<label class="dupe-threshold"><span>Similarity</span><output id="duplicates-threshold-value">95%</output><input id="duplicates-threshold" class="ctl-range" type="range" min="0.90" max="0.99" step="0.01" value="0.95"></label>'
        + '<button class="btn primary" id="duplicates-keep-all" disabled>Keep highest rated everywhere</button>'
        + '</div>'
        + `<button class="icon-btn" id="duplicates-close" data-tip="Grid (G / Esc)" aria-label="Return to Grid">${icon('x')}</button>`
        + '</header>'
        + '<div id="stacks-kind-chips"></div>'
        + '<div id="duplicates-body"></div>'
        + '</div></div>';
}

function ensureView() {
    if (root) return root;
    const wrap = document.createElement('div');
    wrap.innerHTML = viewHtml();
    root = wrap.firstElementChild;
    document.getElementById('view-duplicates').appendChild(root);
    root.querySelector('#duplicates-close').addEventListener('click', closeDuplicates);
    root.querySelector('#stacks-keep-covers').addEventListener('click', keepCoversEverywhere);
    root.querySelector('#stacks-rescan').addEventListener('click', rescanStacks);
    for (const button of root.querySelectorAll('[data-stack-mode]')) {
        button.addEventListener('click', () => switchMode(button.dataset.stackMode));
    }
    root.querySelector('#stacks-kind-chips').addEventListener('click', (event) => {
        const button = event.target.closest('[data-kind]');
        if (!button) return;
        if (stackKind === (button.dataset.kind || '')) return;
        stackKind = button.dataset.kind || '';
        reloadStacks();
    });
    root.querySelector('#duplicates-keep-all').addEventListener('click', () => {
        applyKeepBest(groups.flatMap(groupChanges), 'Kept highest rated everywhere');
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
        const setCoverButton = event.target.closest('[data-set-cover][data-stack-id]');
        if (setCoverButton) {
            setCover(setCoverButton.dataset.stackId, setCoverButton.dataset.setCover);
            return;
        }
        const keepStackButton = event.target.closest('[data-stack-keep]');
        if (keepStackButton) {
            keepCoverForStack(keepStackButton.dataset.stackKeep);
            return;
        }
        const rejectStackButton = event.target.closest('[data-stack-reject]');
        if (rejectStackButton) {
            const stack = findStack(rejectStackButton.dataset.stackReject);
            if (stack) {
                keepCoverRejectRest(stackMembers(stack), representativeId(stack)).then((kept) => {
                    if (kept) removeFinishedStack(stack.id);
                });
            }
            return;
        }
        const unstackButton = event.target.closest('[data-stack-unstack]');
        if (unstackButton) {
            unstackOne(unstackButton.dataset.stackUnstack);
            return;
        }
        const openButton = event.target.closest('[data-open-id]');
        if (openButton) {
            const id = Number(openButton.dataset.openId);
            const stack = openButton.dataset.stackOpen ? findStack(openButton.dataset.stackOpen) : null;
            const images = stack ? stackMembers(stack) : uniqueImages(allImages());
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
            if (group) applyKeepBest(groupChanges(group), 'Kept highest rated');
            return;
        }
        const largestButton = event.target.closest('[data-keep-largest-group]');
        if (largestButton) {
            const group = groups[Number(largestButton.dataset.keepLargestGroup)];
            largestButton.closest('details')?.removeAttribute('open');
            if (group) applyKeepBest(largestFileChanges(group), 'Kept largest file');
        }
    });
    on('flags', () => {
        if (!open) return;
        if (mode === 'adhoc' && !loading) renderGroups();
        if (mode === 'stacks' && !stackLoading) renderStacks();
    });
    on('trash:changed', () => {
        if (open && mode === 'stacks' && !stackLoading) reloadStacks();
    });
    return root;
}

export function openDuplicates() {
    ensureView();
    if (open) return;
    open = true;
    setActiveLens('duplicates');
}

export function closeDuplicates() {
    if (!open || !root) return;
    setActiveLens('grid');
}

export function mountDuplicates() {
    ensureView();
    if (!open) open = true;
    document.getElementById('view-duplicates').classList.add('active');
    root.hidden = false;
    switchMode(mode);
}

export function unmountDuplicates() {
    if (!root) return;
    open = false;
    if (abortController) abortController.abort();
    abortController = null;
    resetStackObserver();
    root.hidden = true;
    document.getElementById('view-duplicates').classList.remove('active');
}

export function duplicatesOpen() {
    return open;
}

export function initDuplicates() {
    ensureView();
    document.getElementById('find-duplicates')?.addEventListener('click', openDuplicates);
    on('duplicates:open', openDuplicates);
}
