import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';

export const ZIP_EXPORT_MAX = 2000;

let menu = null;
let returnEl = null;
let onChoose = null;

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
            closeExportMenu();
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
        + '<button data-format="csv">⇩ CSV</button>'
        + '<button data-format="json">⇩ JSON</button>'
        + '</div><div class="pm-group">'
        + '<div class="pm-label">Download files (zip)</div>'
        + `<button data-format="zip" data-size="original">Original${allowSizes ? '' : ''}</button>`
        + (allowSizes ? '<button data-format="zip" data-size="lg">Large</button><button data-format="zip" data-size="md">Medium</button>' : '')
        + '</div>';
    for (const button of menu.querySelectorAll('button[data-format]')) {
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

export function exportMenuOpen() {
    return Boolean(menu && !menu.hidden);
}

export function downloadExport(params, { count = 0, message = '' } = {}) {
    const format = params.get('format') || 'csv';
    if (format === 'zip' && count > ZIP_EXPORT_MAX) {
        showToast(`Zip export is limited to ${ZIP_EXPORT_MAX.toLocaleString('en-US')} photos`);
        return false;
    }
    const link = document.getElementById('download-link');
    link.href = `/api/export?${params.toString()}`;
    link.download = format === 'zip' ? 'photoarchive-export.zip' : `photoarchive-export.${format}`;
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
