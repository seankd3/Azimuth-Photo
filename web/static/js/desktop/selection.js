import {
    byId, clearSelection, emit, on, selection, selectionChanged, selState, viewState,
} from './state.js';
import { writeFlags } from './api.js';
import { showToast } from './toast.js';
import { downloadExport, openExportMenu } from './export_menu.js';

let collectionPicker = null;
const flagMutationVersions = new Map();

const ids = () => [...selection].map(Number).filter((id) => id > 0);

export function beginFlagMutation(imageId) {
    const id = Number(imageId);
    const next = (flagMutationVersions.get(id) || 0) + 1;
    flagMutationVersions.set(id, next);
    return next;
}

export function flagMutationIsLatest(imageId, version) {
    return flagMutationVersions.get(Number(imageId)) === version;
}

export function setCollectionPicker(fn) {
    collectionPicker = fn;
}

export function selectedIds() {
    return ids();
}

export function isSelectionMode() {
    return selState.mode;
}

export function enterSelection(id, index) {
    const imageId = Number(id);
    const changed = selection.has(imageId) ? [] : [imageId];
    selState.mode = true;
    selection.add(imageId);
    selState.lastIndex = Number(index);
    selectionChanged(changed);
}

export function toggleSelection(id, index, { range = false } = {}) {
    const before = new Set(selection);
    const imageId = Number(id);
    if (range && selState.lastIndex != null) {
        const start = Math.min(selState.lastIndex, Number(index));
        const end = Math.max(selState.lastIndex, Number(index));
        for (let i = start; i <= end; i += 1) {
            const img = viewState.images[i];
            if (img) selection.add(Number(img.id));
        }
    } else if (selection.has(imageId)) {
        selection.delete(imageId);
    } else {
        selection.add(imageId);
    }
    selState.mode = selection.size > 0;
    selState.lastIndex = Number(index);
    const changed = [...new Set([...before, ...selection])].filter((item) => before.has(item) !== selection.has(item));
    selectionChanged(changed);
}

export async function applyFlags(rawIds, flag) {
    const imageIds = [...new Set(rawIds.map(Number))].filter((id) => id > 0);
    if (!imageIds.length) return;
    const previous = imageIds.map((id) => [id, (byId.get(id) || {}).flag || 'unflagged', beginFlagMutation(id)]);
    for (const id of imageIds) {
        const img = byId.get(id);
        if (img) img.flag = flag;
    }
    emit('flags', { imageIds, flag });
    const result = await writeFlags(imageIds, flag);
    if (!result || !result.ok) {
        const rolledBackIds = [];
        for (const [id, oldFlag, version] of previous) {
            if (!flagMutationIsLatest(id, version)) continue;
            const img = byId.get(id);
            if (img) img.flag = oldFlag;
            rolledBackIds.push(id);
        }
        if (rolledBackIds.length) {
            emit('flags', { imageIds: rolledBackIds, failed: true });
            showToast("Flag change didn't save");
        }
        return;
    }
    const label = flag === 'picked' ? 'Picked' : flag === 'rejected' ? 'Rejected' : 'Flags cleared';
    showToast(`${label} · ${imageIds.length} photos`, {
        undo: async () => {
            const undoVersions = previous.map(([id]) => [id, beginFlagMutation(id)]);
            for (const [id, oldFlag] of previous) {
                const img = byId.get(id);
                if (img) img.flag = oldFlag;
            }
            emit('flags', { imageIds });
            const undoResult = await Promise.all(previous.map(([id, oldFlag]) => writeFlags([id], oldFlag)));
            const failedIds = [];
            for (let i = 0; i < undoResult.length; i += 1) {
                const [id, version] = undoVersions[i];
                if (undoResult[i] && undoResult[i].ok) continue;
                if (!flagMutationIsLatest(id, version)) continue;
                const img = byId.get(id);
                if (img) img.flag = flag;
                failedIds.push(id);
            }
            if (failedIds.length) {
                emit('flags', { imageIds: failedIds, failed: true });
                showToast("Undo didn't save");
                return;
            }
            showToast('Undone');
        },
    });
}

function exportSelection(anchor) {
    const imageIds = ids();
    if (!imageIds.length) return;
    openExportMenu(anchor, ({ format, size }) => {
        const params = new URLSearchParams({ format, ids: imageIds.join(',') });
        if (size) params.set('size', size);
        downloadExport(params, {
            count: format === 'zip' ? imageIds.length : 0,
            message: format === 'zip' ? `Preparing ${imageIds.length} files` : `Exporting ${imageIds.length} photos as ${format.toUpperCase()}`,
        });
    });
}

function render({ imageIds = null } = {}) {
    const pill = document.getElementById('sel-pill');
    const count = document.getElementById('sel-count');
    const n = selection.size;
    pill.classList.toggle('on', n > 0);
    count.textContent = `${n} selected`;
    document.getElementById('grid-flow').classList.toggle('selmode', n > 0);
    const ids = Array.isArray(imageIds) ? imageIds.map(Number).filter((id) => id > 0) : [];
    const cells = ids.length
        ? ids.map((id) => document.querySelector(`.cell[data-id="${id}"]`)).filter(Boolean)
        : [...document.querySelectorAll('.cell[data-id]')];
    for (const cell of cells) {
        cell.classList.toggle('sel', selection.has(Number(cell.dataset.id)));
    }
}

export function initSelection() {
    on('selection', render);
    on('images', render);
    document.getElementById('sel-pick').addEventListener('click', () => {
        const imageIds = ids();
        clearSelection();
        applyFlags(imageIds, 'picked');
    });
    document.getElementById('sel-reject').addEventListener('click', () => {
        const imageIds = ids();
        clearSelection();
        applyFlags(imageIds, 'rejected');
    });
    document.getElementById('sel-clear-flags').addEventListener('click', () => {
        const imageIds = ids();
        clearSelection();
        applyFlags(imageIds, 'unflagged');
    });
    document.getElementById('sel-collection').addEventListener('click', () => {
        if (collectionPicker) collectionPicker(ids(), { onDone: clearSelection });
    });
    document.getElementById('sel-export').addEventListener('click', (event) => exportSelection(event.currentTarget));
    document.getElementById('sel-close').addEventListener('click', clearSelection);
}
