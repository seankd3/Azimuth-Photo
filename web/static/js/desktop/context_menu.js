import { emit, on, selection } from './state.js';
import { applyFlags } from './selection.js';
import { openCollectionPicker } from './panel.js';
import { showToast } from './toast.js';

let menu = null;
let returnEl = null;
let target = { id: 0, index: 0, ids: [] };

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
            closeGridContextMenu();
        }
    });
    return menu;
}

function exportIds(ids) {
    const imageIds = ids.map(Number).filter((id) => id > 0);
    if (!imageIds.length) return;
    const link = document.getElementById('download-link');
    link.href = `/api/export?format=csv&ids=${imageIds.join(',')}`;
    link.download = 'photoarchive-export.csv';
    link.click();
    showToast(`Exporting ${imageIds.length} photo${imageIds.length === 1 ? '' : 's'}`);
}

function render() {
    const count = target.ids.length;
    menu.innerHTML = '<div class="pm-group">'
        + '<button data-act="pick">★ Pick</button>'
        + '<button data-act="reject">× Reject</button>'
        + '<button data-act="unflag">○ Unflag</button>'
        + '</div><div class="pm-group">'
        + `<button data-act="collection">⊞ Add ${count > 1 ? `${count} to collection` : 'to collection'}</button>`
        + '<button data-act="loupe">Open in Loupe</button>'
        + '<button data-act="similar">≈ Find similar</button>'
        + `<button data-act="export">⇩ Export ${count > 1 ? 'selection' : 'photo'}</button>`
        + '</div>';
    for (const button of menu.querySelectorAll('[data-act]')) {
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
    else if (action === 'loupe') emit('loupe:open', { id: target.id, index: target.index });
    else if (action === 'similar') emit('similar:find', { imageId: target.id });
    else if (action === 'export') exportIds(ids);
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
    menu.querySelector('button')?.focus({ preventScroll: true });
}

export function closeGridContextMenu() {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
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
