import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { getRankings } from './api.js';
import { scope, scopeParams, viewState } from './state.js';
import { openExportDialog } from './develop/export_dialog.js';

export const ZIP_EXPORT_MAX = 2000;

let menu = null;
let returnEl = null;
function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'export-pop-menu';
    menu.className = 'develop-popover export-pop-menu';
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-modal', 'true');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeExportMenu();
        }
    });
    return menu;
}

function positionDialog(anchor) {
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left));
    const top = Math.max(8, Math.min(window.innerHeight - menuRect.height - 8, rect.bottom + 6));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function anchoredPopover(anchor, html) {
    const popover = ensureMenu();
    releaseFocus(popover);
    popover.innerHTML = html;
    popover.hidden = false;
    popover.style.position = 'fixed';
    positionDialog(anchor);
    trapFocus(popover, popover.querySelector('button, input, select'));
    return popover;
}

async function scopedImageIds() {
    if (scope.similarIds.length) return scope.similarIds.map(Number).filter((id) => id > 0);
    const bestOf = viewState.bestOf && viewState.bestOfLimit != null;
    const payload = await getRankings(scopeParams({
        limit: bestOf ? viewState.bestOfLimit : 50000,
        ...(bestOf ? { sort: 'elo' } : {}),
    }));
    return (payload?.images || []).map((image) => Number(image.id)).filter((id) => id > 0);
}

export function openExportMenu(anchor, choose, options = {}) {
    if (!anchor || !choose) return;
    ensureMenu();
    releaseFocus(menu);
    returnEl = anchor;
    const popover = openExportDialog({
        button: anchor,
        imageIds: options.imageIds || null,
        getImageIds: options.imageIds ? null : scopedImageIds,
        anchoredPopover,
        closePopover: closeExportMenu,
        showToast,
        onDataExport: (format) => choose({ format }),
        onOriginalsExport: () => choose({ format: 'zip', size: 'original' }),
    });
    requestAnimationFrame(() => positionDialog(anchor));
    return popover;
}

export function closeExportMenu() {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    releaseFocus(menu);
    if (returnEl && document.contains(returnEl) && returnEl.focus) returnEl.focus({ preventScroll: true });
}

export function downloadExport(params, { count = 0, message = '' } = {}) {
    const format = params.get('format') || 'csv';
    if (format === 'zip' && count > ZIP_EXPORT_MAX) {
        showToast(`Zip export tops out at ${ZIP_EXPORT_MAX.toLocaleString('en-US')} photos`);
        return false;
    }
    const link = document.getElementById('download-link');
    link.href = `/api/export?${params.toString()}`;
    link.download = format === 'zip' ? 'azimuth-photo-export.zip' : `azimuth-photo-export.${format}`;
    link.click();
    showToast(message || (format === 'zip' ? 'Preparing zip download' : `Exporting as ${format.toUpperCase()}`));
    return true;
}

function outsideClose(event) {
    if (!menu || menu.hidden || menu.contains(event.target)) return;
    closeExportMenu();
}

export function initExportMenu() {
    ensureMenu();
    document.addEventListener('pointerdown', outsideClose);
    window.addEventListener('resize', closeExportMenu);
}
