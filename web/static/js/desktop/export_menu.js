import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

export const ZIP_EXPORT_MAX = 2000;

let menu = null;
let returnEl = null;
let onChoose = null;

function moveMenuFocus(delta) {
    const items = [...menu.querySelectorAll('button:not([disabled])')];
    if (!items.length) return;
    const index = Math.max(0, items.indexOf(document.activeElement));
    items[(index + delta + items.length) % items.length].focus();
}

function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'export-pop-menu';
    menu.className = 'pop-menu grid-pop-menu export-pop-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeExportMenu();
        } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            moveMenuFocus(event.key === 'ArrowDown' ? 1 : -1);
        }
    });
    return menu;
}

function clampPosition(anchor) {
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left));
    const top = Math.max(8, Math.min(window.innerHeight - menuRect.height - 8, rect.bottom + 6));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function render({ allowSizes = true } = {}) {
    menu.innerHTML = '<div class="pm-group">'
        + `<button data-format="csv">${icon('download')} CSV</button>`
        + `<button data-format="json">${icon('download')} JSON</button>`
        + '</div><div class="pm-group">'
        + '<div class="pm-label">Download files (zip)</div>'
        + `<button data-format="zip" data-size="original">${icon('download')} Original${allowSizes ? '' : ''}</button>`
        + (allowSizes ? `<button data-format="zip" data-size="lg">${icon('download')} Large</button><button data-format="zip" data-size="md">${icon('download')} Medium</button>` : '')
        + '</div>';
    for (const button of menu.querySelectorAll('button[data-format]')) {
        button.setAttribute('role', 'menuitem');
        button.addEventListener('click', () => {
            const format = button.dataset.format;
            const size = button.dataset.size || '';
            closeExportMenu();
            if (onChoose) onChoose({ format, size });
        });
    }
}

export function openExportMenu(anchor, choose, options = {}) {
    if (!anchor || !choose) return;
    ensureMenu();
    releaseFocus(menu);
    returnEl = anchor;
    onChoose = choose;
    render(options);
    menu.hidden = false;
    clampPosition(anchor);
    trapFocus(menu, menu.querySelector('button'));
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
    const url = `/api/export?${params.toString()}`;
    const fallbackName = format === 'zip' ? 'photoarchive-export.zip' : `photoarchive-export.${format}`;
    showToast(message || (format === 'zip' ? 'Preparing zip download' : `Exporting as ${format.toUpperCase()}`));
    void (async () => {
        try {
            const response = await fetch(url);
            if (!response.ok) {
                const payload = (response.headers.get('content-type') || '').includes('application/json')
                    ? await response.json().catch(() => null)
                    : null;
                showToast(payload?.detail || `Export failed (${response.status})`);
                return;
            }
            const blob = await response.blob();
            const objectUrl = URL.createObjectURL(blob);
            const link = document.getElementById('download-link');
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = /filename="?([^";]+)"?/i.exec(disposition);
            link.href = objectUrl;
            link.download = match?.[1] || fallbackName;
            link.click();
            setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
        } catch {
            showToast('Export failed');
        }
    })();
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
