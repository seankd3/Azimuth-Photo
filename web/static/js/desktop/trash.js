import { emptyTrash, getTrash, previewThumbUrl, restoreImages, trashImages } from './api.js';
import {
    byId, clearSelection, emit, on, selection, selectionChanged, setActiveLens,
} from './state.js';
import {
    enterSelection, isSelectionMode, selectedIds, toggleSelection,
} from './selection.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { emptyStateHtml } from './empty_state.js';
import { gridLoadingHtml } from './loading_state.js';
import { bytes as bytesLabel, escapeHtml as esc, formatCount as fmt, photoAspect as aspect } from '../lib.js';
import {
    imageMutationOutcome, mutationFailureReason, mutationPartialSuffix,
} from './trash_outcome.js';

let root = null;
let open = false;
let images = [];
let total = 0;
let editedCopies = 0;
let totalBytes = 0;
let pendingHub = 0;
let loading = false;
let loadGeneration = 0;
let loadError = false;
let loadController = null;
let trashFocusIndex = 0;
const busyActions = new Set();

function cancelTrashLoad() {
    if (!loadController) return;
    loadController.abort();
    loadController = null;
}

async function withBusyAction(key, button, action) {
    if (busyActions.has(key) || button?.disabled) return;
    busyActions.add(key);
    if (button) button.disabled = true;
    try {
        await action();
    } finally {
        busyActions.delete(key);
        if (!button || !document.contains(button)) return;
        if (key === 'trash-restore') button.disabled = !selection.size || loading;
        else if (key === 'trash-empty') button.disabled = !total || loading;
        else button.disabled = false;
    }
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
            + `<button class="btn ${danger ? 'btn-danger' : 'primary'}" data-confirm disabled>${esc(confirmLabel)}</button>`
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

/** A lightweight, keyboard-safe confirmation for actions that do not need typed proof. */
export function confirmAction({
    title = 'Confirm action',
    message = 'Are you sure you want to continue?',
    confirmLabel = 'Confirm',
    danger = true,
} = {}) {
    return new Promise((resolve) => {
        let done = false;
        const overlay = document.createElement('div');
        overlay.className = 'typed-confirm';
        overlay.innerHTML = '<div class="typed-confirm-card" role="dialog" aria-modal="true" aria-labelledby="action-confirm-title">'
            + `<h2 id="action-confirm-title">${esc(title)}</h2>`
            + `<p>${esc(message)}</p>`
            + '<div class="typed-confirm-actions">'
            + '<button class="btn" data-cancel>Cancel</button>'
            + `<button class="btn ${danger ? 'btn-danger' : 'primary'}" data-confirm>${esc(confirmLabel)}</button>`
            + '</div></div>';
        const finish = (ok) => {
            if (done) return;
            done = true;
            releaseFocus(overlay);
            overlay.remove();
            resolve(ok);
        };
        document.body.appendChild(overlay);
        const confirm = overlay.querySelector('[data-confirm]');
        trapFocus(overlay, confirm);
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
        });
    });
}

function flagGlyph(flag) {
    if (flag === 'picked') return icon('star');
    if (flag === 'rejected') return icon('x');
    return '';
}

function cellHtml(img, index) {
    const id = Number(img.id);
    const flag = img.flag || 'unflagged';
    const pending = Boolean(img.pending_hub);
    const previewSrc = previewThumbUrl(img);
    return `<figure class="cell trash-cell ${pending ? 'pending-hub' : ''} ${selection.has(id) ? 'sel' : ''} ${index === trashFocusIndex ? 'kb-focus' : ''}" data-id="${id}" data-idx="${index}" tabindex="${index === trashFocusIndex ? '0' : '-1'}" style="--ar:${aspect(img)}">`
        + (previewSrc ? `<img src="${esc(previewSrc)}" loading="lazy" decoding="async" alt="${esc(img.filename || '')}">` : '<span class="preview-thumb-pending" aria-hidden="true"></span>')
        + `<span class="trash-thumb-fallback" hidden>${icon('image')}<span>${esc(img.filename || 'Photo preview unavailable')}</span></span>`
        + `<button class="c-check" aria-label="Select photo">${icon('check')}</button>`
        + `<span class="c-flag ${flag}">${flagGlyph(flag)}</span>`
        + `<span class="c-elo"><span class="elo-chip">${Math.round(Number(img.elo) || 0)}</span></span>`
        + (pending ? '<span class="trash-pending-badge">Removing from hub…</span>' : '')
        + '</figure>';
}

function viewHtml() {
    return '<div id="trash" hidden>'
        + '<header id="trash-head">'
        + '<div><b>Trash</b><span id="trash-count" class="num"></span><span id="trash-pending-count" class="chip" hidden></span></div>'
        + '<button class="btn" id="trash-select-all" disabled>Select all</button>'
        + '<button class="btn" id="trash-restore" disabled>Restore selected</button>'
        + '<button class="btn btn-danger" id="trash-empty" disabled>Empty trash</button>'
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
    root.querySelector('#trash-restore').addEventListener('click', (event) => {
        withBusyAction('trash-restore', event.currentTarget, restoreSelectedTrash);
    });
    root.querySelector('#trash-empty').addEventListener('click', (event) => {
        withBusyAction('trash-empty', event.currentTarget, emptyTrashWithConfirm);
    });
    root.querySelector('#trash-body').addEventListener('click', (event) => {
        const cell = event.target.closest('.cell[data-id]');
        if (!cell) return;
        const id = Number(cell.dataset.id);
        const index = Number(cell.dataset.idx);
        setTrashFocus(index, { focus: false });
        if (event.target.closest('.c-check') || isSelectionMode()) {
            if (event.target.closest('.c-check')) enterSelection(id, index);
            else toggleSelection(id, index, { range: event.shiftKey });
            return;
        }
        emit('loupe:open', { id, index, images });
    });
    on('selection', patchSelection);
    on('trash:changed', ({ loaded = false } = {}) => {
        if (open && !loaded) loadTrash();
    });
    return root;
}

function render() {
    ensureView();
    root.querySelector('#trash-count').textContent = `${fmt(total)} photos · ${bytesLabel(totalBytes)}`;
    const pendingChip = root.querySelector('#trash-pending-count');
    pendingChip.textContent = `${fmt(pendingHub)} waiting for hub`;
    pendingChip.hidden = pendingHub === 0;
    root.querySelector('#trash-select-all').disabled = !images.length || loading;
    root.querySelector('#trash-restore').disabled = !selection.size || loading;
    root.querySelector('#trash-empty').disabled = !total || loading;
    const body = root.querySelector('#trash-body');
    if (loading) {
        body.innerHTML = gridLoadingHtml({ className: 'trash-grid' });
        return;
    }
    if (loadError) {
        body.innerHTML = '<div class="load-error"><h4>Couldn\'t load Trash</h4><p>The archive did not respond. Try again.</p><button class="btn" id="trash-retry">Try again</button></div>';
        body.querySelector('#trash-retry')?.addEventListener('click', loadTrash);
        return;
    }
    if (!images.length) {
        body.innerHTML = emptyStateHtml({
            title: 'Trash is empty',
            detail: 'Photos moved to Trash stay here until you restore or permanently empty them.',
            iconName: 'trash-2',
        });
        return;
    }
    body.innerHTML = `<div class="trash-grid ${selection.size ? 'selmode' : ''}">${images.map(cellHtml).join('')}</div>`;
    for (const image of body.querySelectorAll('.trash-cell img')) {
        image.addEventListener('error', () => {
            image.hidden = true;
            image.closest('.trash-cell')?.querySelector('.trash-thumb-fallback')?.removeAttribute('hidden');
        }, { once: true });
    }
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
    cancelTrashLoad();
    const seq = ++loadGeneration;
    const controller = new AbortController();
    loadController = controller;
    loading = true;
    loadError = false;
    render();
    let data = null;
    try {
        data = await getTrash({ limit: 500, offset: 0, signal: controller.signal });
    } catch {
        if (controller.signal.aborted) return;
        if (seq !== loadGeneration || !root?.isConnected || !open) return;
        if (loadController === controller) loadController = null;
        images = [];
        total = 0;
        totalBytes = 0;
        pendingHub = 0;
        loading = false;
        loadError = true;
        render();
        return;
    }
    if (loadController === controller) loadController = null;
    if (seq !== loadGeneration || !root?.isConnected || !open) return;
    if (!data) {
        images = [];
        total = 0;
        totalBytes = 0;
        pendingHub = 0;
        loading = false;
        loadError = true;
        render();
        return;
    }
    images = (data && data.images || []).map((img) => ({ ...img, id: Number(img.id) })).filter((img) => img.id);
    trashFocusIndex = Math.max(0, Math.min(images.length - 1, trashFocusIndex));
    total = Number(data?.total) || images.length;
    editedCopies = Number(data?.edited_copy_count) || 0;
    totalBytes = Number(data?.total_bytes) || 0;
    pendingHub = Number(data?.pending_hub_count) || images.filter((image) => image.pending_hub).length;
    for (const img of images) byId.set(Number(img.id), img);
    loading = false;
    render();
}

function selectAllTrashRows() {
    if (!images.length) return 0;
    selection.clear();
    images.forEach((img) => selection.add(Number(img.id)));
    selectionChanged(images.map((img) => Number(img.id)));
    return images.length;
}

function trashColumns() {
    const cells = [...root?.querySelectorAll('.trash-cell[data-idx]') || []];
    if (cells.length < 2) return 1;
    const top = cells[0].offsetTop;
    return Math.max(1, cells.filter((cell) => Math.abs(cell.offsetTop - top) < 4).length);
}

function setTrashFocus(index, { focus = true } = {}) {
    if (!images.length || !root) return false;
    trashFocusIndex = Math.max(0, Math.min(images.length - 1, Number(index) || 0));
    const cell = root.querySelector(`.trash-cell[data-idx="${trashFocusIndex}"]`);
    if (!cell) return false;
    for (const item of root.querySelectorAll('.trash-cell.kb-focus')) {
        item.classList.remove('kb-focus');
        item.tabIndex = -1;
    }
    cell.classList.add('kb-focus');
    cell.tabIndex = 0;
    if (focus) {
        cell.focus({ preventScroll: true });
        cell.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    return true;
}

export function selectAllTrash() {
    return selectAllTrashRows();
}

export function handleTrashKey(event) {
    if (!open || loading || !images.length) return false;
    let next = trashFocusIndex;
    if (event.key === 'ArrowLeft') next -= 1;
    else if (event.key === 'ArrowRight') next += 1;
    else if (event.key === 'ArrowUp') next -= trashColumns();
    else if (event.key === 'ArrowDown') next += trashColumns();
    else if (event.key === 'Enter') {
        const image = images[trashFocusIndex];
        if (!image) return false;
        toggleSelection(image.id, trashFocusIndex);
        return true;
    } else return false;
    return setTrashFocus(next);
}

export async function trashSelectedImages() {
    const imageIds = selectedIds();
    if (!imageIds.length) return false;
    const result = await trashImages(imageIds);
    const { imageIds: trashedIds, errors } = imageMutationOutcome(result, 'trashed');
    if (!trashedIds.length) {
        showToast(mutationFailureReason(errors, "Selection couldn't be trashed"));
        return false;
    }
    clearSelection();
    emit('trash:changed', { imageIds: trashedIds });
    showToast(`Moved ${fmt(trashedIds.length)} photo${trashedIds.length === 1 ? '' : 's'} to Trash${mutationPartialSuffix(errors, 'trashed')}`, {
        undo: async () => {
            const restored = await restoreImages(trashedIds);
            const restoredOutcome = imageMutationOutcome(restored, 'restored');
            if (!restoredOutcome.imageIds.length) {
                showToast(mutationFailureReason(restoredOutcome.errors, 'Couldn’t restore'));
                return;
            }
            emit('trash:changed', { imageIds: restoredOutcome.imageIds });
            showToast(`Restored${mutationPartialSuffix(restoredOutcome.errors, 'restored')}`);
        },
    });
    return true;
}

async function restoreSelectedTrash() {
    const imageIds = selectedIds();
    if (!imageIds.length) return;
    const result = await restoreImages(imageIds);
    const { imageIds: restoredIds, errors } = imageMutationOutcome(result, 'restored');
    if (!restoredIds.length) {
        showToast(mutationFailureReason(errors, 'Couldn’t restore'));
        return;
    }
    clearSelection();
    emit('trash:changed', { imageIds: restoredIds });
    showToast(`Restored ${fmt(restoredIds.length)} photo${restoredIds.length === 1 ? '' : 's'}${mutationPartialSuffix(errors, 'restored')}`, {
        undo: async () => {
            const trashed = await trashImages(restoredIds);
            const trashedOutcome = imageMutationOutcome(trashed, 'trashed');
            if (!trashedOutcome.imageIds.length) {
                showToast(mutationFailureReason(trashedOutcome.errors, 'Couldn’t undo'));
                return;
            }
            emit('trash:changed', { imageIds: trashedOutcome.imageIds });
            showToast(`Moved back to Trash${mutationPartialSuffix(trashedOutcome.errors, 'trashed')}`);
        },
    });
}

async function emptyTrashWithConfirm() {
    if (!total) return;
    const ok = await confirmTypedCount({
        title: 'Empty trash',
        message: `Trash ${fmt(total)} photos permanently? ${bytesLabel(totalBytes)} will be freed.${editedCopies > 0 ? ` This also destroys ${fmt(editedCopies)} edited ${editedCopies === 1 ? 'copy' : 'copies'} — their edits die with their masters.` : ''} Type ${fmt(total).replace(/,/g, '')} to confirm.`,
        count: total,
        confirmLabel: 'Empty trash',
    });
    if (!ok) return;
    const response = await emptyTrash();
    if (!response?.ok || !response.data) {
        const detail = response?.data?.detail;
        showToast(typeof detail === 'string' ? detail : "Trash couldn't be emptied");
        return;
    }
    await loadTrash();
    emit('trash:changed', { loaded: true });
    const deleted = Number(response.data.deleted_count) || 0;
    const waiting = Number(response.data.hub_pending) || 0;
    const summary = `Emptied ${fmt(deleted)} photo${deleted === 1 ? '' : 's'}`;
    showToast(waiting ? `${summary} · ${fmt(waiting)} waiting for hub` : `${summary} · ${bytesLabel(response.data.freed_bytes)} freed`);
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
    trashFocusIndex = 0;
    loadTrash();
}

export function unmountTrash() {
    if (!root) return;
    open = false;
    loadGeneration += 1;
    cancelTrashLoad();
    loading = false;
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

export { bytesLabel };
