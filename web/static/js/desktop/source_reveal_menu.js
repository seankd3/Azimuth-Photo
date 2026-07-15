import { revealFolder } from './api.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { icon } from '../icons.js';
import { showToast } from './toast.js';

let menu = null;
let menuReturn = null;

function revealMenuLabel() {
    const platform = navigator.platform || '';
    if (/Win/i.test(platform)) return 'Reveal in Explorer';
    if (/Mac/i.test(platform)) return 'Reveal in Finder';
    return 'Open in file manager';
}

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

async function revealSourcePath(path) {
    const result = await revealFolder(path);
    if (result?.ok && result?.data?.ok) {
        showToast('Opened in file manager');
        return;
    }
    showToast(result?.data?.error || 'Couldn’t open folder');
}

export function openSourceRevealMenu(path, anchor) {
    if (!path || !anchor) return;
    ensureMenu();
    releaseFocus(menu);
    menuReturn = anchor;
    menu.innerHTML = '<div class="pm-group">'
        + `<button data-act="reveal" role="menuitem">${icon('folder-open')} ${revealMenuLabel()}</button>`
        + '</div>';
    menu.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left + 18))}px`;
    menu.style.top = `${Math.max(8, Math.min(window.innerHeight - menuRect.height - 8, rect.top + 18))}px`;
    menu.querySelector('[data-act="reveal"]')?.addEventListener('click', () => {
        closeSourceRevealMenu();
        revealSourcePath(path);
    });
    trapFocus(menu, menu.querySelector('button'));
}
