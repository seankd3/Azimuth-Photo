import { clearSelection, selection } from './state.js';
import { focusOmnibox, openCommandPalette } from './omnibox.js';
import { createStackFromSelection, moveFocus, focusColumns, currentFocusedImage, toggleFocusedStack } from './grid.js';
import { applyFlags, selectLoadedImages, toggleFocusedSelection } from './selection.js';
import {
    closeLoupe, flagLoupeOrFocused, loupeOpen, navLoupe, openLoupe, toggleLoupeInfo, toggleLoupeLights,
} from './loupe.js';
import {
    closeRefine, refineOpen, openRefine, pickByKey, undoRefine,
} from './refine.js';
import { cycleDensity, emit, on, toggleBestOf, viewState } from './state.js';
import { closeLeftDrawer, leftDrawerOpen, toggleLeftPanel } from './panel.js';
import { closeSystemDrawer, systemDrawerOpen } from './drawer.js';
import { toggleRightPanel } from './panel_right.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { activeLens, switchLens } from './lenses.js';
import { closeFilters, filtersOpen } from './filters.js';
import { closeImport, importOpen } from './importer.js';
import { closeGridContextMenu, gridContextMenuOpen } from './context_menu.js';
import { closeDuplicates, duplicatesOpen } from './duplicates.js';
import { closeTrash, trashOpen, trashSelectedImages } from './trash.js';
import { showToast, undoLatestToast } from './toast.js';

function inputFocused() {
    const el = document.activeElement;
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
}

function helpOpen() {
    return !document.getElementById('help').hidden;
}

function foregroundLayerOpen() {
    return helpOpen()
        || filtersOpen()
        || importOpen()
        || Boolean(
            document.querySelector('.typed-confirm')
            || document.querySelector('#collection-picker')
            || document.querySelector('#collection-pop-menu:not([hidden])')
            || document.querySelector('#grid-pop-menu:not([hidden])')
            || document.querySelector('#export-pop-menu:not([hidden])')
            || document.querySelector('#folder-pop-menu:not([hidden])')
            || document.querySelector('#share-overlay:not([hidden])')
            || document.querySelector('#publish-overlay:not([hidden])')
            || document.querySelector('#people-merge-pop'),
        );
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
    if (trashOpen()) {
        closeTrash();
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
            if (escapeOneLayer()) {
                event.preventDefault();
                event.stopPropagation();
            }
            return;
        }
        if (event.ctrlKey || event.metaKey) {
            const key = event.key.toLowerCase();
            if (foregroundLayerOpen() || inputFocused()) return;
            if (key === 'k') {
                if (activeLens() !== 'grid') return;
                event.preventDefault();
                openCommandPalette();
                return;
            }
            if (refineOpen() && key === 'z') {
                event.preventDefault();
                undoRefine();
                return;
            }
            if (activeLens() !== 'grid') return;
            if (key === 'z') {
                if (undoLatestToast()) event.preventDefault();
                return;
            }
            if (key === 'a') {
                const count = selectLoadedImages();
                if (count) {
                    event.preventDefault();
                    showToast(`${count.toLocaleString('en-US')} loaded photos selected`);
                }
            }
            return;
        }
        if (event.altKey || inputFocused()) return;
        if (helpOpen()) return;
        if (filtersOpen()) {
            if (event.key.toLowerCase() === 'f') {
                event.preventDefault();
                closeFilters();
            }
            return;
        }
        if (refineOpen() && pickByKey(event.key)) {
            event.preventDefault();
            return;
        }
        if (loupeOpen()) {
            const lk = event.key.toLowerCase();
            if (event.key === 'ArrowLeft') navLoupe(-1);
            else if (event.key === 'ArrowRight') navLoupe(1);
            else if (lk === 'g') closeLoupe({ force: true });
            else if (lk === 'l') toggleLoupeLights();
            else if (lk === 'i') toggleLoupeInfo();
            else if (lk === 'p') flagLoupeOrFocused('picked');
            else if (lk === 'x') flagLoupeOrFocused('rejected');
            else if (lk === 'u') flagLoupeOrFocused('unflagged');
            else return;
            event.preventDefault();
            return;
        }
        if (duplicatesOpen()) {
            if (event.key.toLowerCase() === 'g') {
                event.preventDefault();
                closeDuplicates();
            }
            return;
        }
        if (trashOpen()) {
            if (event.key.toLowerCase() === 'g') {
                event.preventDefault();
                closeTrash();
            }
            return;
        }
        if (activeLens() !== 'grid') return;
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
        } else if (key === 'f') {
            event.preventDefault();
            emit('filters:toggle');
        } else if (key === 'g') {
            event.preventDefault();
            switchLens('grid');
        } else if (key === 'e') {
            event.preventDefault();
            const img = currentFocusedImage();
            if (img) openLoupe({ id: img.id, index: viewState.focusIndex });
        } else if (key === 'o') {
            event.preventDefault();
            switchLens('people');
        } else if (key === 'y') {
            event.preventDefault();
            switchLens('events');
        } else if (key === 'm') {
            event.preventDefault();
            switchLens('map');
        } else if (key === 's') {
            event.preventDefault();
            if (selection.size > 1) createStackFromSelection();
            else toggleFocusedStack();
        } else if (key === 'j') {
            event.preventDefault();
            const next = cycleDensity();
            showToast(`Density · ${next[0].toUpperCase()}${next.slice(1)}`);
        } else if (event.key === 'Delete' || event.key === 'Backspace') {
            if (selection.size) {
                event.preventDefault();
                trashSelectedImages();
            }
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
        } else if (event.key === ' ') {
            if (viewState.activeLens !== 'grid') return;
            if (toggleFocusedSelection()) event.preventDefault();
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
