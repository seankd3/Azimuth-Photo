import { emit, on, scope, selection } from './state.js';
import { applyFlags } from './selection.js';
import { openCollectionPicker, removeImagesFromCollection } from './panel.js';
import { downloadExport } from './export_menu.js';
import { queueBatchExport } from './develop/export_dialog.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { icon } from '../icons.js';
import { showToast } from './toast.js';

let menu = null;
let returnEl = null;
let target = { id: 0, index: 0, ids: [] };

function moveMenuFocus(delta) {
    const items = [...menu.querySelectorAll('button:not([disabled])')];
    if (!items.length) return;
    const index = Math.max(0, items.indexOf(document.activeElement));
    items[(index + delta + items.length) % items.length].focus();
}

function idsFor(id) {
    const imageId = Number(id);
    return selection.has(imageId) ? [...selection].map(Number).filter((value) => value > 0) : [imageId];
}

function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'grid-pop-menu';
    menu.className = 'pop-menu grid-pop-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeGridContextMenu();
        } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            moveMenuFocus(event.key === 'ArrowDown' ? 1 : -1);
        }
    });
    return menu;
}

function exportIds(ids, format = 'csv', size = '') {
    const imageIds = ids.map(Number).filter((id) => id > 0);
    if (!imageIds.length) return;
    const params = new URLSearchParams({ format, ids: imageIds.join(',') });
    if (size) params.set('size', size);
    downloadExport(params, {
        count: format === 'zip' ? imageIds.length : 0,
        message: format === 'zip'
            ? `Preparing ${imageIds.length} file${imageIds.length === 1 ? '' : 's'}`
            : `Exporting ${imageIds.length} photo${imageIds.length === 1 ? '' : 's'} as ${format.toUpperCase()}`,
    });
}

async function mergeHdr(ids) {
    showToast('Preparing HDR merge\u2026');
    try {
        const response = await fetch('/api/develop/hdr/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_ids: ids }),
        });
        if (!response.ok) throw new Error();
        showToast('HDR merge is running. The result will appear in HDR Merges.');
    } catch {
        showToast('HDR merge could not start');
    }
}

async function batchDevelopExport(ids) {
    try {
        await queueBatchExport(ids, { format: 'jpeg', quality: 92, sharpen: 'screen_standard' }, { showToast });
    } catch (error) {
        showToast(error?.message || 'Develop batch export could not start');
    }
}

function render() {
    const count = target.ids.length;
    menu.innerHTML = '<div class="pm-group">'
        + `<button data-act="pick">${icon('flag')} Pick</button>`
        + `<button data-act="reject">${icon('x')} Reject</button>`
        + `<button data-act="unflag">${icon('circle')} Clear flag</button>`
        + '</div><div class="pm-group">'
        + `<button data-act="collection">${icon('plus')} Add ${count > 1 ? `${count} to collection` : 'to collection'}</button>`
        + (scope.collectionId && !scope.collectionSmart ? `<button data-act="remove-from-collection">${icon('minus')} Remove from this collection</button>` : '')
        + `<button data-act="loupe">${icon('image')} Open in Loupe</button>`
        + `<button data-act="similar">${icon('scan-search')} Find similar</button>`
        + (count >= 3 ? `<button data-act="hdr-merge">${icon('layers')} Merge ${count} to HDR</button>` : '')
        + '</div><div class="pm-group">'
        + `<div class="pm-label">Export ${count > 1 ? 'selection' : 'photo'}</div>`
        + `<button data-act="export-develop">${icon('sliders-horizontal')} Develop export${count > 1 ? ` · ${count}` : ''}</button>`
        + `<button data-act="export-csv">${icon('download')} CSV</button>`
        + `<button data-act="export-json">${icon('download')} JSON</button>`
        + `<button data-act="export-zip-original">${icon('download')} Download files · original</button>`
        + `<button data-act="export-zip-lg">${icon('download')} Download files · large</button>`
        + `<button data-act="export-zip-md">${icon('download')} Download files · medium</button>`
        + '</div>';
    for (const button of menu.querySelectorAll('[data-act]')) {
        button.setAttribute('role', 'menuitem');
        button.addEventListener('click', () => run(button.dataset.act));
    }
}

function run(action) {
    const ids = target.ids.slice();
    closeGridContextMenu();
    if (action === 'pick') applyFlags(ids, 'picked');
    else if (action === 'reject') applyFlags(ids, 'rejected');
    else if (action === 'unflag') applyFlags(ids, 'unflagged');
    else if (action === 'collection') openCollectionPicker(ids);
    else if (action === 'remove-from-collection') removeImagesFromCollection(scope.collectionId, ids, scope.collectionName);
    else if (action === 'loupe') emit('loupe:open', { id: target.id, index: target.index });
    else if (action === 'similar') emit('similar:find', { imageId: target.id });
    else if (action === 'hdr-merge') mergeHdr(ids);
    else if (action === 'export-develop') batchDevelopExport(ids);
    else if (action === 'export-csv') exportIds(ids, 'csv');
    else if (action === 'export-json') exportIds(ids, 'json');
    else if (action.startsWith('export-zip-')) exportIds(ids, 'zip', action.replace('export-zip-', ''));
}

function clampPosition(x, y) {
    const rect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - rect.width - 8, x));
    const top = Math.max(8, Math.min(window.innerHeight - rect.height - 8, y));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

export function openGridContextMenu({ id, index = 0, x = 0, y = 0, returnTo = null } = {}) {
    const imageId = Number(id);
    if (!imageId) return;
    ensureMenu();
    returnEl = returnTo || document.activeElement;
    target = { id: imageId, index: Number(index) || 0, ids: idsFor(imageId) };
    render();
    menu.hidden = false;
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    requestAnimationFrame(() => clampPosition(x, y));
    trapFocus(menu, menu.querySelector('button'));
}

export function closeGridContextMenu() {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    releaseFocus(menu);
    if (returnEl && document.contains(returnEl) && returnEl.focus) returnEl.focus({ preventScroll: true });
}

export function gridContextMenuOpen() {
    return Boolean(menu && !menu.hidden);
}

function outsideClose(event) {
    if (!menu || menu.hidden || menu.contains(event.target) || event.target.closest('.c-menu')) return;
    closeGridContextMenu();
}

export function initGridContextMenu() {
    ensureMenu();
    document.addEventListener('pointerdown', outsideClose);
    window.addEventListener('resize', closeGridContextMenu);
    on('scope', closeGridContextMenu);
}
