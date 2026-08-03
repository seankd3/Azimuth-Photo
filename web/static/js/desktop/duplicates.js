import {
    cleanupIdenticalStacks, createStack, getIdenticalSummary, getIdenticalVerificationStatus, getStackRebuildStatus,
    listIdenticalStacks, listStacks, rebuildStacks, restoreImages, setStackRepresentative,
    previewThumbUrl, thumbUrl, trashImages, unstack, verifyIdenticalStacks, writeFlags,
} from './api.js';
import { applyFlags } from './selection.js';
import {
    byId, emit, on, rememberImages, setActiveLens,
} from './state.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { keepCoverRejectRest } from './stack_cull.js';
import { escapeHtml as esc, formatCount as fmt } from '../lib.js';
import {
    imageMutationOutcome, mutationFailureReason, mutationPartialSuffix,
} from './trash_outcome.js';

const DEFAULT_THRESHOLD = 0.95;
const LIMIT = 100;
const STACK_LIMIT = 50;
const IDENTICAL_LIMIT = 25;
const TIMEOUT_MS = 30000;
const STACK_KINDS = [
    ['identical', 'identical', 'Identical'],
    ['burst', 'burst', 'Bursts'],
    ['versions', 'versions', 'Versions'],
    ['similar', 'crosssource', 'Similar'],
    ['manual', 'manual', 'Manual'],
];

let root = null;
let abortController = null;
let open = false;
let threshold = DEFAULT_THRESHOLD;
let groups = [];
let mode = 'stacks';
let stackKind = 'identical';
let stacks = [];
let stackCounts = {};
let identicalSummary = null;
let identicalStatus = null;
let stackTotal = 0;
let stackOffset = 0;
let stackDone = false;
let stackLoading = false;
let stackGeneration = 0;
let stackSentinel = null;
let stackObserver = null;
let loading = false;
let stackRescanning = false;
let identicalPollTimer = null;
let identicalPollMisses = 0;

const POLL_MAX_MISSES = 5;

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
    const merged = {
        ...cached,
        ...image,
        id,
        flag: normalizeFlag(image?.flag || cached.flag),
    };
    merged.thumb_url = previewThumbUrl(merged);
    return merged;
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
        source: image?.source_name || '—',
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
    for (const field of ['filename', 'type', 'dimensions', 'size', 'date', 'folder', 'source']) {
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
            if (!undone) setFlagsLocally(normalized);
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
        + (diff.differs.type ? `<div class="dupe-type ${metaClass('type', image, diff)}">${esc(values.type)}</div>` : '')
        + (diff.differs.dimensions ? metaRow('dimensions', 'Dimensions', values.dimensions, image, diff) : '')
        + (diff.differs.size ? metaRow('size', 'File size', values.size, image, diff) : '')
        + (diff.differs.date ? metaRow('date', 'Taken', values.date, image, diff) : '')
        + (diff.differs.folder ? metaRow('folder', 'Folder', values.folder, image, diff) : '')
        + (diff.differs.source ? metaRow('source', 'Source', values.source, image, diff) : '')
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
        identical: 'Identical',
        burst: 'Burst',
        variant: 'Variant',
        version: 'Version',
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
    host.innerHTML = STACK_KINDS.map(([, value, label]) => {
        const count = stackCounts[value];
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
        + (diff.differs.type ? `<div class="dupe-type ${metaClass('type', image, diff)}">${esc(values.type)}</div>` : '')
        + (diff.differs.dimensions ? metaRow('dimensions', 'Dimensions', values.dimensions, image, diff) : '')
        + (diff.differs.size ? metaRow('size', 'File size', values.size, image, diff) : '')
        + (diff.differs.date ? metaRow('date', 'Taken', values.date, image, diff) : '')
        + (diff.differs.folder ? metaRow('folder', 'Folder', values.folder, image, diff) : '')
        + (diff.differs.source ? metaRow('source', 'Source', values.source, image, diff) : '')
        + '</div>'
        + '<div class="dupe-actions stack-member-actions" aria-label="Stack photo actions">'
        + (isCover ? `<span class="stack-cover-text">${icon('image')} Cover</span>` : `<button data-set-cover="${image.id}" data-stack-id="${stack.id}" aria-label="Make cover" data-tip="Make cover">${icon('image')}</button>`)
        + `<button data-flag="picked" data-id="${image.id}" aria-label="Pick ${esc(image.filename || image.id)}">${icon('star')}</button>`
        + `<button data-flag="rejected" data-id="${image.id}" aria-label="Reject ${esc(image.filename || image.id)}">${icon('x')}</button>`
        + `<button data-flag="unflagged" data-id="${image.id}" aria-label="Clear flag for ${esc(image.filename || image.id)}">${icon('circle')}</button>`
        + '</div></article>';
}

function identicalPhotoHtml(group, image, diff) {
    const verification = group.verification || { state: 'candidate' };
    const keeperId = Number(verification.keeper_id || group.representative?.id || 0);
    const isKeeper = Number(image.id) === keeperId;
    const aspect = Number(image.width) && Number(image.height)
        ? Math.max(.65, Math.min(2.2, Number(image.width) / Number(image.height)))
        : 1.5;
    const values = fieldValues(image);
    const modified = Number(image.file_modified_at) > 0
        ? new Date(Number(image.file_modified_at) * 1000).toISOString().slice(0, 10)
        : 'Unknown';
    const badge = isKeeper
        ? `<span class="stack-cover-badge identical-keeper">${icon('image')} Oldest · ${esc(modified)}</span>`
        : (verification.state === 'ready' ? '<span class="identical-copy-badge">Verified copy</span>' : '');
    return `<article class="dupe-photo identical-photo ${isKeeper ? 'is-keeper' : ''}" style="--dupe-ar:${aspect}">`
        + `<button class="dupe-thumb" data-open-id="${image.id}" data-identical-key="${esc(group.key)}" aria-label="Open ${esc(image.filename || image.id)} in Loupe">`
        + `<img src="${esc(thumbUrl('md', image.id))}" loading="lazy" decoding="async" alt="${esc(image.filename || '')}">`
        + badge
        + '</button>'
        + '<div class="dupe-facts">'
        + `<div class="dupe-name ${diff.differs.filename ? 'diff' : 'muted'}" title="${esc(values.filename)}">${esc(values.filename)}</div>`
        + metaRow('date', 'File modified', modified, image, { differs: { date: true }, winners: { date: isKeeper ? image.id : 0 } })
        + (diff.differs.folder ? metaRow('folder', 'Folder', values.folder, image, diff) : '')
        + (diff.differs.source ? metaRow('source', 'Source', values.source, image, diff) : '')
        + (diff.differs.size ? metaRow('size', 'File size', values.size, image, diff) : '')
        + '</div></article>';
}

function identicalRowHtml(group) {
    const members = stackMembers(group);
    const diff = groupDiffs({ images: members });
    const verification = group.verification || { state: 'candidate' };
    const stateCopy = verification.state === 'ready'
        ? `Byte-identical · ${fmt(verification.removable_count || members.length - 1)} safe to remove`
        : verification.state === 'exception'
            ? `Needs review · ${verification.reason || 'could not verify every file'}`
            : 'Candidate match · full-byte verification required';
    const stateClass = verification.state === 'ready' ? 'ready' : verification.state === 'exception' ? 'exception' : 'candidate';
    return `<section class="dupe-row stack-row identical-row" data-identical="${esc(group.key)}">`
        + '<div class="dupe-row-head">'
        + `<div><b>${fmt(members.length)} copies</b><span>${bytesLabel(group.potential_reclaim_bytes)} potentially recoverable</span></div>`
        + `<span class="identical-state ${stateClass}">${esc(stateCopy)}</span>`
        + '</div>'
        + `<div class="dupe-photos">${members.map((image) => identicalPhotoHtml(group, image, diff)).join('')}</div>`
        + '</section>';
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

function syncStackTools() {
    const identical = stackKind === 'identical';
    const verifyButton = root.querySelector('#stacks-verify-identical');
    const cleanupButton = root.querySelector('#stacks-cleanup-identical');
    const rescanButton = root.querySelector('#stacks-rescan');
    verifyButton.hidden = !identical;
    cleanupButton.hidden = !identical;
    rescanButton.hidden = identical || stackKind === 'manual';
    if (!identical) return;
    const status = identicalStatus || {};
    const running = status.state === 'running';
    const complete = status.state === 'complete';
    verifyButton.disabled = running;
    verifyButton.textContent = running
        ? `Verifying ${fmt(status.scanned_groups || 0)} / ${fmt(status.total_groups || 0)}`
        : complete ? 'Verify again' : 'Verify identicals';
    const removable = Number(status.removable_count) || 0;
    cleanupButton.textContent = removable
        ? `Move ${fmt(removable)} verified copies to Trash`
        : 'Nothing verified yet';
    cleanupButton.disabled = !complete || !removable;
}

function identicalHeroHtml() {
    const summary = identicalSummary || {};
    const status = identicalStatus || {};
    const active = Number(summary.active_images) || 0;
    const hashed = Number(summary.hashed_images) || 0;
    const coverage = active ? Math.round((hashed / active) * 1000) / 10 : 0;
    const running = status.state === 'running';
    const complete = status.state === 'complete';
    const hasEstimate = Number(summary.potential_removable_count) > 0;
    const title = complete
        ? `${fmt(status.removable_count || 0)} verified copies · ${bytesLabel(status.reclaim_bytes)} recoverable`
        : hasEstimate
            ? `${fmt(summary.potential_removable_count)} candidate copies · ${bytesLabel(summary.potential_reclaim_bytes)} potential`
            : `${fmt(summary.total_groups || stackTotal)}${summary.pending ? '+' : ''} candidate groups`;
    const detail = running
        ? `Reading every byte before Azimuth makes a cleanup plan · ${fmt(status.scanned_groups || 0)} of ${fmt(status.total_groups || 0)} groups checked`
        : complete
            ? `${fmt(status.ready_groups || 0)} groups are safe · ${fmt(status.exception_groups || 0)} deferred for review`
            : hasEstimate
                ? `${coverage}% of the library has a fast identity. Candidates remain read-only until every byte is verified.`
                : 'Loading the archive-wide storage estimate. Candidates remain read-only until every byte is verified.';
    return `<section class="identical-hero ${running ? 'is-running' : ''}">`
        + `<div><span class="identical-eyebrow">IDENTICAL</span><h3>${esc(title)}</h3><p>${esc(detail)}</p></div>`
        + (running ? `<progress max="${Math.max(1, Number(status.total_groups) || 1)}" value="${Number(status.scanned_groups) || 0}"></progress>` : '')
        + '</section>';
}

async function loadIdenticalSummary() {
    const summary = await getIdenticalSummary();
    if (!summary || !open || stackKind !== 'identical') return;
    identicalSummary = summary;
    stackTotal = Number(summary.total_groups) || stackTotal;
    stackCounts.identical = stackTotal;
    root.querySelector('#duplicates-count').textContent = `${fmt(stackTotal)} group${stackTotal === 1 ? '' : 's'}`;
    renderKindChips();
    const current = root.querySelector('.identical-hero');
    if (current) current.outerHTML = identicalHeroHtml();
}

function renderStacks({ append = false } = {}) {
    const pendingCount = stackKind === 'identical' && identicalSummary?.pending;
    root.querySelector('#duplicates-count').textContent = stackRescanning
        ? `Rescanning... ${fmt(stackTotal)} stack${stackTotal === 1 ? '' : 's'} shown`
        : `${fmt(stackTotal)}${pendingCount ? '+' : ''} group${stackTotal === 1 ? '' : 's'}`;
    root.querySelector('#stacks-rescan').disabled = stackRescanning;
    syncStackTools();
    renderKindChips();
    const body = root.querySelector('#duplicates-body');
    const banner = stackRescanning ? '<div class="stack-status-banner">Rescanning stacks. Review actions are paused until fresh results are ready.</div>' : '';
    if (!append) {
        if (!stacks.length && !stackLoading) {
            body.innerHTML = (stackKind === 'identical' ? identicalHeroHtml() : banner)
                + '<div class="grid-empty dupe-empty"><h3>No groups in this view.</h3><p>Try another stack type, or rescan after adding photos.</p></div><div id="stacks-sentinel"></div>';
        } else {
            const rows = stackKind === 'identical' ? stacks.map(identicalRowHtml) : stacks.map(stackRowHtml);
            body.innerHTML = (stackKind === 'identical' ? identicalHeroHtml() : banner)
                + rows.join('') + `<div id="stacks-sentinel">${stackLoading && stacks.length ? 'Loading more groups...' : ''}</div>`;
        }
    } else {
        const sentinel = root.querySelector('#stacks-sentinel');
        const pageSize = stackKind === 'identical' ? IDENTICAL_LIMIT : STACK_LIMIT;
        const added = stacks.slice(Math.max(0, stacks.length - pageSize));
        sentinel?.insertAdjacentHTML('beforebegin', added.map(stackKind === 'identical' ? identicalRowHtml : stackRowHtml).join(''));
        if (sentinel) sentinel.textContent = stackLoading && stacks.length ? 'Loading more groups...' : '';
    }
    bindStackSentinel();
}

async function loadStackCounts() {
    const seq = stackGeneration;
    const entries = await Promise.all(STACK_KINDS.filter(([, value]) => value !== 'identical').map(async ([, value]) => {
        const data = await listStacks({ kind: value, limit: 1, offset: 0 });
        return [value, Number(data?.total) || 0];
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
    try {
        const data = requestKind === 'identical'
            ? await listIdenticalStacks({ limit: IDENTICAL_LIMIT, offset: requestOffset })
            : await listStacks({ kind: requestKind, limit: STACK_LIMIT, offset: requestOffset });
        if (seq !== stackGeneration || !root?.isConnected || !open || requestMode !== mode || mode !== 'stacks' || requestKind !== stackKind) return;
        if (!data) throw new Error('Stacks response was empty');
        const incoming = requestKind === 'identical' ? data.groups || [] : data.stacks || [];
        if (requestKind === 'identical') {
            identicalSummary = data.summary || null;
            identicalStatus = data.verification_status || null;
            stackCounts.identical = Number(data.total) || incoming.length;
            // Resume polling after a remount/kind switch while a verification is still running.
            if (identicalStatus?.state === 'running') scheduleIdenticalPoll();
        }
        stackTotal = Number(data.total) || incoming.length;
        for (const stack of incoming) rememberImages(stackMembers(stack));
        stacks = reset ? incoming : stacks.concat(incoming);
        stackOffset += incoming.length;
        const pageSize = requestKind === 'identical' ? IDENTICAL_LIMIT : STACK_LIMIT;
        stackDone = requestKind === 'identical'
            ? data.has_more === false
            : incoming.length < pageSize || stackOffset >= stackTotal;
        stackLoading = false;
        renderStacks({ append: !reset });
        if (reset && requestKind === 'identical') loadIdenticalSummary();
    } catch {
        if (seq !== stackGeneration || !root?.isConnected || !open || requestMode !== mode || mode !== 'stacks' || requestKind !== stackKind) return;
        root.querySelector('#duplicates-body').innerHTML = '<div class="load-error dupe-error"><h4>Couldn\'t load stacks</h4><p>The archive did not respond. Try again.</p><button class="btn" id="stacks-retry">Try again</button></div>';
        root.querySelector('#stacks-retry')?.addEventListener('click', () => loadStackPage({ reset: true }));
    } finally {
        if (seq === stackGeneration) stackLoading = false;
    }
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
    const seq = stackGeneration;
    resetStackObserver();
    await loadStackPage({ reset: true });
    if (seq !== stackGeneration || mode !== 'stacks') return;
    loadStackCounts();
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
    if (finished?.kind && stackCounts[finished.kind] != null) {
        stackCounts[finished.kind] = Math.max(0, stackCounts[finished.kind] - 1);
    }
    renderStacks();
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
    const { imageIds: trashedIds, errors } = imageMutationOutcome(result, 'trashed');
    if (!trashedIds.length) {
        showToast(mutationFailureReason(errors, 'Couldn’t move photos to Trash'));
        return;
    }
    emit('trash:changed', { imageIds: trashedIds });
    const trashComplete = errors.length === 0 && trashedIds.length === imageIds.length;
    if (trashComplete) {
        removeFinishedStack(stackId);
    } else {
        await reloadStacks();
    }
    showToast(`Trashed ${fmt(trashedIds.length)} stack member${trashedIds.length === 1 ? '' : 's'}${mutationPartialSuffix(errors, 'trashed')}`, {
        undo: async () => {
            const restored = await restoreImages(trashedIds);
            const restoredOutcome = imageMutationOutcome(restored, 'restored');
            if (!restoredOutcome.imageIds.length) {
                showToast(mutationFailureReason(restoredOutcome.errors, 'Couldn’t restore'));
                return;
            }
            emit('trash:changed', { imageIds: restoredOutcome.imageIds });
            if (open && mode === 'stacks') await reloadStacks();
            showToast(`Restored${mutationPartialSuffix(restoredOutcome.errors, 'restored')}`);
        },
    });
}

function scheduleIdenticalPoll(delayMs = 1200) {
    if (identicalPollTimer) clearTimeout(identicalPollTimer);
    identicalPollTimer = setTimeout(pollIdenticalVerification, delayMs);
}

async function pollIdenticalVerification() {
    identicalPollTimer = null;
    if (!open || mode !== 'stacks' || stackKind !== 'identical') return;
    let status = null;
    try {
        status = await getIdenticalVerificationStatus();
    } catch {
        // A thrown poll must not kill the chain: verification keeps running server-side.
    }
    if (!status) {
        identicalPollMisses += 1;
        if (identicalPollMisses < POLL_MAX_MISSES) {
            scheduleIdenticalPoll(2500);
            return;
        }
        identicalPollMisses = 0;
        identicalStatus = null;
        syncStackTools();
        const hero = root.querySelector('.identical-hero');
        if (hero) hero.outerHTML = identicalHeroHtml();
        showToast('Couldn’t read verification progress');
        return;
    }
    identicalPollMisses = 0;
    identicalStatus = status;
    syncStackTools();
    if (status.state === 'running') {
        scheduleIdenticalPoll();
        return;
    }
    if (status.state === 'complete') {
        await loadStackPage({ reset: true });
        showToast(`Verified ${fmt(status.ready_groups || 0)} identical groups · ${fmt(status.exception_groups || 0)} deferred`);
        return;
    }
    if (status.state === 'error') showToast(status.error || 'Identical verification failed');
}

async function startIdenticalVerification() {
    const button = root.querySelector('#stacks-verify-identical');
    button.disabled = true;
    button.textContent = 'Starting verification...';
    const result = await verifyIdenticalStacks();
    if (!result?.ok) {
        button.disabled = false;
        button.textContent = 'Verify identicals';
        showToast(result?.data?.error || 'Couldn’t start identical verification');
        return;
    }
    identicalStatus = result.data?.verification_status || { state: 'running' };
    identicalPollMisses = 0;
    syncStackTools();
    scheduleIdenticalPoll();
}

async function cleanupVerifiedIdenticals() {
    const token = identicalStatus?.token;
    const button = root.querySelector('#stacks-cleanup-identical');
    if (!token || !button) return;
    button.disabled = true;
    button.textContent = 'Moving verified copies...';
    const result = await cleanupIdenticalStacks(token);
    const { imageIds: trashedIds, errors } = imageMutationOutcome(result, 'trashed');
    if (!trashedIds.length) {
        syncStackTools();
        showToast(result?.data?.error || mutationFailureReason(errors, 'Couldn’t move verified copies'));
        return;
    }
    const skippedGroups = Array.isArray(result?.data?.skipped_groups) ? result.data.skipped_groups.length : 0;
    try {
        emit('trash:changed', { imageIds: trashedIds });
        identicalStatus = null;
        await reloadStacks();
        showToast(`Moved ${fmt(trashedIds.length)} verified copies to Trash${skippedGroups ? ` · ${fmt(skippedGroups)} changed groups deferred` : ''}${mutationPartialSuffix(errors, 'trashed')}`, {
            undo: async () => {
                const restored = await restoreImages(trashedIds);
                const restoredOutcome = imageMutationOutcome(restored, 'restored');
                if (!restoredOutcome.imageIds.length) {
                    showToast(mutationFailureReason(restoredOutcome.errors, 'Couldn’t restore'));
                    return;
                }
                emit('trash:changed', { imageIds: restoredOutcome.imageIds });
                if (open && mode === 'stacks') await reloadStacks();
                showToast(`Restored ${fmt(restoredOutcome.imageIds.length)} copies${mutationPartialSuffix(restoredOutcome.errors, 'restored')}`);
            },
        });
    } finally {
        if (button.isConnected) button.disabled = false;
    }
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
    const poll = async (misses = 0) => {
        let status = null;
        try {
            status = await getStackRebuildStatus();
        } catch {
            // A thrown poll must not leave review actions locked forever: retry, then unlock.
            if (misses + 1 < POLL_MAX_MISSES) {
                setTimeout(() => poll(misses + 1), 2500);
                return;
            }
            stackRescanning = false;
            button.disabled = false;
            button.textContent = 'Rescan stacks';
            renderStacks();
            showToast('Couldn’t read rescan progress');
            return;
        }
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

function switchMode() {
    stackGeneration += 1;
    stackLoading = false;
    resetStackObserver();
    mode = 'stacks';
    reloadStacks();
}

function viewHtml() {
    return '<div id="duplicates" hidden>'
        + '<div id="duplicates-panel">'
        + '<header id="duplicates-head">'
        + '<div><b>Stacks</b><span id="duplicates-count" class="num"></span></div>'
        + '<div id="stacks-review-tools"><button class="btn primary" id="stacks-verify-identical">Verify identicals</button><button class="btn btn-danger" id="stacks-cleanup-identical" disabled>Nothing verified yet</button><button class="btn" id="stacks-rescan" hidden>Rescan stacks</button></div>'
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
    root.querySelector('#stacks-verify-identical').addEventListener('click', startIdenticalVerification);
    root.querySelector('#stacks-cleanup-identical').addEventListener('click', cleanupVerifiedIdenticals);
    root.querySelector('#stacks-rescan').addEventListener('click', rescanStacks);
    root.querySelector('#stacks-kind-chips').addEventListener('click', (event) => {
        const button = event.target.closest('[data-kind]');
        if (!button) return;
        if (stackKind === (button.dataset.kind || '')) return;
        stackKind = button.dataset.kind || '';
        reloadStacks();
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
            const stack = openButton.dataset.stackOpen
                ? findStack(openButton.dataset.stackOpen)
                : stacks.find((item) => item.key === openButton.dataset.identicalKey);
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
    if (identicalPollTimer) clearTimeout(identicalPollTimer);
    identicalPollTimer = null;
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
