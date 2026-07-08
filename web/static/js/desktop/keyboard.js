import { clearSelection, selection } from './state.js';
import { focusOmnibox, openCommandPalette } from './omnibox.js';
import { moveFocus, focusColumns, currentFocusedImage } from './grid.js';
import { applyFlags } from './selection.js';
import {
    closeLoupe, flagLoupeOrFocused, loupeOpen, navLoupe, openLoupe,
} from './loupe.js';
import {
    closeRefine, refineOpen, openRefine, pickByKey, undoRefine,
} from './refine.js';
import { on, toggleBestOf, viewState } from './state.js';
import { closeLeftDrawer, leftDrawerOpen, toggleLeftPanel } from './panel.js';
import { closeSystemDrawer, systemDrawerOpen } from './drawer.js';
import { toggleRightPanel } from './panel_right.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { switchLens } from './lenses.js';
import { closeFilters, filtersOpen } from './filters.js';
import { closeImport, importOpen } from './importer.js';
import { closeGridContextMenu, gridContextMenuOpen } from './context_menu.js';
import { closeDuplicates, duplicatesOpen } from './duplicates.js';

function inputFocused() {
    const el = document.activeElement;
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
}

function helpOpen() {
    return !document.getElementById('help').hidden;
}

export function openHelp() {
    const root = document.getElementById('help');
    if (!root.hidden) return;
    root.hidden = false;
    trapFocus(root, document.getElementById('help-close'));
}

export function closeHelp() {
    const root = document.getElementById('help');
    if (root.hidden) return;
    root.hidden = true;
    releaseFocus(root);
}

function flagTarget(flag) {
    if (loupeOpen()) {
        flagLoupeOrFocused(flag);
        return;
    }
    if (selection.size) {
        const ids = [...selection];
        clearSelection();
        applyFlags(ids, flag);
        return;
    }
    const img = currentFocusedImage();
    if (img) applyFlags([img.id], flag);
}

function escapeOneLayer() {
    if (gridContextMenuOpen()) {
        closeGridContextMenu();
        return true;
    }
    if (filtersOpen()) {
        closeFilters();
        return true;
    }
    if (importOpen()) {
        closeImport();
        return true;
    }
    const picker = document.getElementById('collection-picker');
    if (picker) {
        picker.querySelector('.picker-head button').click();
        return true;
    }
    const scopebox = document.getElementById('scopebox');
    if (scopebox.classList.contains('open')) {
        scopebox.classList.remove('open');
        document.getElementById('scope-input').blur();
        return true;
    }
    if (helpOpen()) {
        closeHelp();
        return true;
    }
    if (loupeOpen()) {
        closeLoupe();
        return true;
    }
    if (duplicatesOpen()) {
        closeDuplicates();
        return true;
    }
    if (refineOpen()) {
        closeRefine();
        return true;
    }
    if (systemDrawerOpen()) {
        closeSystemDrawer();
        return true;
    }
    if (leftDrawerOpen()) {
        closeLeftDrawer();
        return true;
    }
    if (selection.size) {
        clearSelection();
        return true;
    }
    return false;
}

export function initKeyboard() {
    document.getElementById('help-btn').addEventListener('click', openHelp);
    document.getElementById('help-close').addEventListener('click', closeHelp);
    document.getElementById('help').addEventListener('click', (event) => {
        if (event.target.id === 'help') closeHelp();
    });
    window.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            if (escapeOneLayer()) event.preventDefault();
            return;
        }
        if (event.ctrlKey || event.metaKey) {
            if (event.key.toLowerCase() === 'k') {
                event.preventDefault();
                openCommandPalette();
                return;
            }
            if (duplicatesOpen()) return;
            if (refineOpen() && event.key.toLowerCase() === 'z') {
                event.preventDefault();
                undoRefine();
            }
            return;
        }
        if (event.altKey || inputFocused()) return;
        if (refineOpen() && pickByKey(event.key)) {
            event.preventDefault();
            return;
        }
        if (loupeOpen()) {
            if (event.key === 'ArrowLeft') navLoupe(-1);
            else if (event.key === 'ArrowRight') navLoupe(1);
            else if (event.key.toLowerCase() === 'p') flagLoupeOrFocused('picked');
            else if (event.key.toLowerCase() === 'x') flagLoupeOrFocused('rejected');
            else if (event.key.toLowerCase() === 'u') flagLoupeOrFocused('unflagged');
            return;
        }
        if (duplicatesOpen()) return;
        const key = event.key.toLowerCase();
        if (key === '/') {
            event.preventDefault();
            focusOmnibox();
        } else if (key === '?') {
            event.preventDefault();
            openHelp();
        } else if (key === 'r') {
            event.preventDefault();
            openRefine();
        } else if (key === 'b') {
            event.preventDefault();
            toggleBestOf();
        } else if (key === 'g') {
            event.preventDefault();
            switchLens('grid');
        } else if (key === 'e') {
            event.preventDefault();
            switchLens('events');
        } else if (key === 'o') {
            event.preventDefault();
            switchLens('people');
        } else if (key === 'm') {
            event.preventDefault();
            switchLens('map');
        } else if (key === '[') {
            event.preventDefault();
            toggleLeftPanel();
        } else if (key === ']') {
            event.preventDefault();
            toggleRightPanel();
        } else if (key === 'p') {
            flagTarget('picked');
        } else if (key === 'x') {
            flagTarget('rejected');
        } else if (key === 'u') {
            flagTarget('unflagged');
        } else if (event.key === 'Enter') {
            const img = currentFocusedImage();
            if (img) openLoupe({ id: img.id, index: viewState.focusIndex });
        } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            moveFocus(1);
        } else if (event.key === 'ArrowLeft') {
            event.preventDefault();
            moveFocus(-1);
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            moveFocus(focusColumns());
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            moveFocus(-focusColumns());
        }
    });
    on('help:open', openHelp);
}
