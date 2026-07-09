import { emptyTrash, getTrash, restoreImages, thumbUrl, trashImages } from './api.js';
import {
    byId, clearSelection, emit, on, selection, selectionChanged, setActiveLens,
} from './state.js';
import {
    enterSelection, isSelectionMode, selectedIds, toggleSelection,
} from './selection.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

let root = null;
let open = false;
let images = [];
let total = 0;
let totalBytes = 0;
let loading = false;
let loadGeneration = 0;
let loadError = false;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

export function bytesLabel(value) {
    const n = Number(value) || 0;
    if (!n) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = n;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

export function confirmTypedCount({
    title,
    message,
    count,
    confirmLabel = 'Confirm',
    danger = true,
} = {}) {
    const required = String(Number(count) || 0);
    return new Promise((resolve) => {
        let done = false;
        const overlay = document.createElement('div');
        overlay.className = 'typed-confirm';
        overlay.innerHTML = '<div class="typed-confirm-card" role="dialog" aria-modal="true">'
            + `<h2>${esc(title || 'Confirm')}</h2>`
            + `<p>${esc(message || `Type ${required} to continue.`)}</p>`
            + `<input type="text" autocomplete="off" spellcheck="false" aria-label="Type ${required} to confirm">`
            + '<div class="typed-confirm-actions">'
            + '<button class="btn" data-cancel>Cancel</button>'
            + `<button class="btn ${danger ? 'danger' : 'primary'}" data-confirm disabled>${esc(confirmLabel)}</button>`
            + '</div></div>';
        const finish = (ok) => {
            if (done) return;
            done = true;
            releaseFocus(overlay);
            overlay.remove();
            resolve(ok);
        };
        document.body.appendChild(overlay);
        const input = overlay.querySelector('input');
        const confirm = overlay.querySelector('[data-confirm]');
        trapFocus(overlay, input);
        input.addEventListener('input', () => {
            confirm.disabled = input.value.trim() !== required;
        });
        overlay.querySelector('[data-cancel]').addEventListener('click', () => finish(false));
        confirm.addEventListener('click', () => finish(true));
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) finish(false);
        });
        overlay.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                finish(false);
            }
            if (event.key === 'Enter' && !confirm.disabled) {
                event.preventDefault();
                event.stopPropagation();
                finish(true);
            }
        });
    });
}

function aspect(img) {
    const ar = Number(img?.aspect_ratio) || (Number(img?.width) && Number(img?.height) ? Number(img.width) / Number(img.height) : 1.5);
    return Math.max(.45, Math.min(3.8, ar));
}

function flagGlyph(flag) {
    if (flag === 'picked') return '★';
    if (flag === 'rejected') return '×';
    return '';
}

function cellHtml(img, index) {
    const id = Number(img.id);
    const flag = img.flag || 'unflagged';
    return `<figure class="cell trash-cell ${selection.has(id) ? 'sel' : ''}" data-id="${id}" data-idx="${index}" tabindex="-1" style="--ar:${aspect(img)}">`
        + `<img src="${esc(img.thumb_url || thumbUrl('sm', id))}" loading="lazy" decoding="async" alt="${esc(img.filename || '')}">`
        + `<button class="c-check" aria-label="Select photo">${icon('check')}</button>`
        + `<span class="c-flag ${flag}">${flagGlyph(flag)}</span>`
        + `<span class="c-elo"><span class="elo-chip">${Math.round(Number(img.elo) || 0)}</span></span>`
        + '</figure>';
}

function viewHtml() {
    return '<div id="trash" hidden>'
        + '<header id="trash-head">'
        + '<div><b>Trash</b><span id="trash-count" class="num"></span></div>'
        + '<button class="btn" id="trash-select-all" disabled>Select all</button>'
        + '<button class="btn primary" id="trash-restore" disabled>Restore selected</button>'
        + '<button class="btn danger" id="trash-empty" disabled>Empty trash</button>'
        + `<button class="icon-btn" id="trash-close" data-tip="Grid (G / Esc)" aria-label="Return to Grid">${icon('x')}</button>`
        + '</header>'
        + '<div id="trash-body"></div>'
        + '</div>';
}

function ensureView() {
    if (root) return root;
    const wrap = document.createElement('div');
    wrap.innerHTML = viewHtml();
    root = wrap.firstElementChild;
    document.getElementById('view-trash').appendChild(root);
    root.querySelector('#trash-close').addEventListener('click', closeTrash);
    root.querySelector('#trash-select-all').addEventListener('click', selectAllTrash);
    root.querySelector('#trash-restore').addEventListener('click', restoreSelectedTrash);
    root.querySelector('#trash-empty').addEventListener('click', emptyTrashWithConfirm);
    root.querySelector('#trash-body').addEventListener('click', (event) => {
        const cell = event.target.closest('.cell[data-id]');
        if (!cell) return;
        const id = Number(cell.dataset.id);
        const index = Number(cell.dataset.idx);
        if (event.target.closest('.c-check') || isSelectionMode()) {
            if (event.target.closest('.c-check')) enterSelection(id, index);
            else toggleSelection(id, index, { range: event.shiftKey });
            return;
        }
        emit('loupe:open', { id, index, images });
    });
    on('selection', patchSelection);
    on('trash:changed', () => {
        if (open) loadTrash();
    });
    return root;
}

function render() {
    ensureView();
    root.querySelector('#trash-count').textContent = `${fmt(total)} photos · ${bytesLabel(totalBytes)}`;
    root.querySelector('#trash-select-all').disabled = !images.length || loading;
    root.querySelector('#trash-restore').disabled = !selection.size || loading;
    root.querySelector('#trash-empty').disabled = !total || loading;
    const body = root.querySelector('#trash-body');
    if (loading) {
        body.innerHTML = '<div class="trash-grid">' + Array.from({ length: 18 }, () => '<div class="cell skel-cell" style="--ar:1.4"></div>').join('') + '</div>';
        return;
    }
    if (loadError) {
        body.innerHTML = '<div class="load-error"><h4>Couldn\'t load Trash</h4><p>The archive did not respond. Try again.</p><button class="btn" id="trash-retry">Try again</button></div>';
        body.querySelector('#trash-retry')?.addEventListener('click', loadTrash);
        return;
    }
    if (!images.length) {
        body.innerHTML = '<div class="grid-empty"><h3>Trash is empty</h3><p>Deleted photos will appear here until restored or emptied.</p></div>';
        return;
    }
    body.innerHTML = `<div class="trash-grid ${selection.size ? 'selmode' : ''}">${images.map(cellHtml).join('')}</div>`;
}

function patchSelection() {
    if (!root) return;
    root.querySelector('#trash-restore').disabled = !selection.size || loading;
    root.querySelector('#trash-select-all').disabled = !images.length || loading;
    const grid = root.querySelector('.trash-grid');
    if (grid) grid.classList.toggle('selmode', selection.size > 0);
    for (const cell of root.querySelectorAll('.cell[data-id]')) {
        cell.classList.toggle('sel', selection.has(Number(cell.dataset.id)));
    }
}

async function loadTrash() {
    ensureView();
    const seq = ++loadGeneration;
    loading = true;
    loadError = false;
    render();
    const data = await getTrash({ limit: 500, offset: 0 });
    if (seq !== loadGeneration || !root?.isConnected || !open) return;
    if (!data) {
        images = [];
        total = 0;
        totalBytes = 0;
        loading = false;
        loadError = true;
        render();
        return;
    }
    images = (data && data.images || []).map((img) => ({ ...img, id: Number(img.id) })).filter((img) => img.id);
    total = Number(data?.total) || images.length;
    totalBytes = Number(data?.total_bytes) || 0;
    for (const img of images) byId.set(Number(img.id), img);
    loading = false;
    render();
}

function selectAllTrash() {
    if (!images.length) return;
    selection.clear();
    images.forEach((img) => selection.add(Number(img.id)));
    selectionChanged(images.map((img) => Number(img.id)));
}

export async function trashSelectedImages() {
    const imageIds = selectedIds();
    if (!imageIds.length) return false;
    const result = await trashImages(imageIds);
    if (!result) {
        showToast("Selection couldn't be trashed");
        return false;
    }
    clearSelection();
    emit('trash:changed', { imageIds });
    showToast(`Moved ${fmt(imageIds.length)} photo${imageIds.length === 1 ? '' : 's'} to Trash`, {
        undo: async () => {
            const restored = await restoreImages(imageIds);
            emit('trash:changed', { imageIds });
            showToast(restored ? 'Restored' : "Restore didn't save");
        },
    });
    return true;
}

async function restoreSelectedTrash() {
    const imageIds = selectedIds();
    if (!imageIds.length) return;
    const result = await restoreImages(imageIds);
    if (!result) {
        showToast("Restore didn't save");
        return;
    }
    clearSelection();
    emit('trash:changed', { imageIds });
    showToast(`Restored ${fmt(imageIds.length)} photo${imageIds.length === 1 ? '' : 's'}`, {
        undo: async () => {
            const trashed = await trashImages(imageIds);
            emit('trash:changed', { imageIds });
            showToast(trashed ? 'Moved back to Trash' : "Undo didn't save");
        },
    });
}

async function emptyTrashWithConfirm() {
    if (!total) return;
    const ok = await confirmTypedCount({
        title: 'Empty trash',
        message: `Trash ${fmt(total)} photos permanently? ${bytesLabel(totalBytes)} will be freed. Type ${fmt(total).replace(/,/g, '')} to confirm.`,
        count: total,
        confirmLabel: 'Empty trash',
    });
    if (!ok) return;
    const freed = totalBytes;
    const result = await emptyTrash();
    if (!result) {
        showToast("Trash couldn't be emptied");
        return;
    }
    emit('trash:changed', {});
    showToast(`Trash emptied · ${bytesLabel(freed)} freed`);
}

export function openTrash() {
    ensureView();
    if (open) return;
    open = true;
    setActiveLens('trash');
}

export function closeTrash() {
    if (!open) return;
    setActiveLens('grid');
}

export function mountTrash() {
    ensureView();
    open = true;
    document.getElementById('view-trash').classList.add('active');
    root.hidden = false;
    clearSelection();
    loadTrash();
}

export function unmountTrash() {
    if (!root) return;
    open = false;
    loadGeneration += 1;
    root.hidden = true;
    document.getElementById('view-trash').classList.remove('active');
}

export function trashOpen() {
    return open;
}

export function initTrash() {
    ensureView();
    on('trash:open', openTrash);
}
