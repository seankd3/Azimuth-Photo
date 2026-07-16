import { revealFolder } from './api.js';
import { exportScope, openExportMenu } from './export_menu.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { fileManagerMenuLabel } from './file_manager.js';
import { icon } from '../icons.js';
import { emit, navigateToScope, scopeParams } from './state.js';
import { showToast } from './toast.js';

let menu = null;
let menuReturn = null;

function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'source-pop-menu';
    menu.className = 'pop-menu grid-pop-menu folder-pop-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeSourceRevealMenu();
        }
    });
    document.addEventListener('pointerdown', (event) => {
        if (!menu || menu.hidden || menu.contains(event.target)) return;
        closeSourceRevealMenu();
    });
    return menu;
}

function closeSourceRevealMenu() {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    releaseFocus(menu);
    if (menuReturn && document.contains(menuReturn) && menuReturn.focus) {
        menuReturn.focus({ preventScroll: true });
    }
}

async function revealSourcePath(path, sourceId) {
    const result = await revealFolder(path, sourceId);
    if (result?.ok && result?.data?.ok) {
        showToast('Opened in file manager');
        return;
    }
    showToast(result?.data?.error || 'Couldn’t open folder');
}

function applySourceScope(path) {
    navigateToScope({ folder: [path] });
}

function exportSourceScope(path, count, anchor) {
    applySourceScope(path);
    openExportMenu(anchor, ({ format, size }) => {
        exportScope({ format, size, count: Number(count) || 0, query: scopeParams() });
    });
}

export function openSourceRevealMenu(path, anchor, count = 0, options = {}) {
    if (!path || !anchor) return;
    const revealAvailable = options.revealAvailable !== false && !String(path).toLowerCase().startsWith('hub:');
    const sourceId = Number(options.sourceId) || null;
    ensureMenu();
    releaseFocus(menu);
    menuReturn = anchor;
    menu.innerHTML = '<div class="pm-group">'
        + `<button data-act="scope">${icon('folder-tree')} Show in scope with subfolders</button>`
        + `<button data-act="refine">${icon('zap')} Open in Refine</button>`
        + (revealAvailable ? `<button data-act="reveal">${icon('folder-open')} ${fileManagerMenuLabel()}</button>` : '')
        + `<button data-act="export">${icon('download')} Export view…</button>`
        + '</div>';
    menu.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left + 18))}px`;
    menu.style.top = `${Math.max(8, Math.min(window.innerHeight - menuRect.height - 8, rect.top + 18))}px`;
    for (const button of menu.querySelectorAll('[data-act]')) {
        button.setAttribute('role', 'menuitem');
        button.addEventListener('click', () => {
            const action = button.dataset.act;
            closeSourceRevealMenu();
            if (action === 'scope') applySourceScope(path);
            if (action === 'refine') {
                applySourceScope(path);
                emit('refine:open');
            }
            if (action === 'reveal') revealSourcePath(path, sourceId);
            if (action === 'export') exportSourceScope(path, count, anchor);
        });
    }
    trapFocus(menu, menu.querySelector('button'));
}
