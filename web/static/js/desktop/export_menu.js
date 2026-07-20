import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { getRankings } from './api.js';
import { scope, scopeParams, viewState } from './state.js';

// Keep originals-size preference readable without pulling export_dialog at boot.
const EXPORT_OPTIONS_STORAGE_KEY = 'pa_d_export_dialog_photos';

export function savedOriginalsExportSize() {
    try {
        const value = JSON.parse(localStorage.getItem(EXPORT_OPTIONS_STORAGE_KEY) || '{}');
        return (value && typeof value === 'object' && value.originals_size) || 'original';
    } catch {
        return 'original';
    }
}

export const ZIP_EXPORT_MAX = 2000;
const SCOPE_EXPORT_PAGE_SIZE = 1000;
const SCOPE_EXPORT_PROGRESS_DELAY_MS = 1000;

let menu = null;
let returnEl = null;
let exportDialogMod = null;

async function loadExportDialog() {
    if (!exportDialogMod) exportDialogMod = await import('./develop/export_dialog.js');
    return exportDialogMod;
}

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
    const bestOf = viewState.bestOf && viewState.bestOfLimit != null;
    const maxIds = bestOf ? Math.max(0, Number(viewState.bestOfLimit) || 0) : Infinity;
    const imageIds = [];
    let offset = 0;
    let showProgress = false;
    const progressTimer = setTimeout(() => {
        showProgress = true;
        showToast('Preparing photos for export…');
    }, SCOPE_EXPORT_PROGRESS_DELAY_MS);
    try {
        // Similar sets resolve server-side with exclude_sources — never raw similarIds.
        const similarIds = scope.similarIds.map(Number).filter((id) => id > 0);
        while (offset < maxIds) {
            const limit = Math.min(SCOPE_EXPORT_PAGE_SIZE, maxIds - offset);
            const extra = {
                limit,
                offset,
                ...(bestOf ? { sort: 'elo' } : {}),
            };
            if (similarIds.length) extra.ids = similarIds.join(',');
            const payload = await getRankings(scopeParams(extra));
            const images = payload?.images || [];
            imageIds.push(...images.map((image) => Number(image.id)).filter((id) => id > 0));
            offset += images.length;
            if (showProgress) showToast(`Preparing ${imageIds.length.toLocaleString('en-US')} photos for export…`);
            if (images.length < limit) break;
        }
        return imageIds;
    } finally {
        clearTimeout(progressTimer);
    }
}

export async function openExportMenu(anchor, choose, options = {}) {
    if (!anchor || !choose) return;
    ensureMenu();
    releaseFocus(menu);
    returnEl = anchor;
    const { openExportDialog } = await loadExportDialog();
    const popover = openExportDialog({
        button: anchor,
        imageIds: options.imageIds || null,
        getImageIds: options.imageIds ? null : scopedImageIds,
        anchoredPopover,
        closePopover: closeExportMenu,
        showToast,
        onDataExport: (format) => choose({ format }),
        onOriginalsExport: (_ids, size) => choose({ format: 'zip', size }),
    });
    requestAnimationFrame(() => positionDialog(anchor));
    return popover;
}

export function closeExportMenu() {
    if (!menu || menu.hidden) return;
    exportDialogMod?.cancelBatchExportPoll();
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
    if (format !== 'zip') {
        void loadExportDialog().then(({ fetchDataExport }) => {
            void fetchDataExport(params, { showToast, filename: `azimuth-photo-export.${format}`, message });
        });
        return true;
    }
    const link = document.getElementById('download-link');
    link.href = `/api/export?${params.toString()}`;
    link.download = 'azimuth-photo-export.zip';
    link.click();
    showToast(message || 'Preparing zip download');
    return true;
}

export function exportScope(params = {}, { toast = '' } = {}) {
    const { format = 'csv', size = '', ids = [], count = null, query = {} } = params;
    const search = new URLSearchParams(query);
    const imageIds = (ids || []).map(Number).filter((id) => id > 0);
    const exportCount = count == null ? imageIds.length : Number(count) || 0;
    search.set('format', format);
    if (imageIds.length) search.set('ids', imageIds.join(','));
    if (size) search.set('size', size);
    const standardToast = format === 'zip'
        ? (exportCount ? `Preparing ${exportCount} file${exportCount === 1 ? '' : 's'} for download` : 'Preparing zip download')
        : `Exporting${exportCount ? ` ${exportCount} photo${exportCount === 1 ? '' : 's'}` : ''} as ${format.toUpperCase()}`;
    return downloadExport(search, { count: format === 'zip' ? exportCount : 0, message: toast || standardToast });
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
