import { clearSelection, selection, selectionChanged } from './state.js';
import { focusOmnibox, openCommandPalette } from './omnibox.js';
import {
    createStackFromSelection, moveFocus, focusColumns, focusPageStep, currentFocusedImage,
    setFocus, toggleFocusedStack,
} from './grid.js';
import { applyFlags, selectLoadedImages, toggleFocusedSelection } from './selection.js';
import {
    closeLoupe, fitLoupe, flagLoupeOrFocused, loupeImageId, loupeOpen, navLoupe, navLoupeTo,
    openLoupe, panLoupe, toggleLoupeInfo, toggleLoupeLights, toggleLoupeVersion, zoomLoupeBy,
} from './loupe.js';
import {
    closeRefine, refineOpen, openRefine, pickByKey, undoRefine,
} from './refine.js';
import {
    cycleDensity, emit, on, patchPrefs, patchScope, scope, toggleBestOf, viewState,
} from './state.js';
import { closeLeftDrawer, leftDrawerOpen, toggleLeftPanel } from './panel.js';
import { closeSystemDrawer, systemDrawerOpen } from './drawer.js';
import { toggleRightPanel } from './panel_right.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { activeLens, switchLens } from './lenses.js';
import { reviewPeopleMergeByKey } from './people.js';
import { closeFilters, filtersOpen } from './filters.js';
import { closeImport, importOpen } from './importer.js';
import { closeGridContextMenu, gridContextMenuOpen } from './context_menu.js';
import { closeDuplicates, duplicatesOpen } from './duplicates.js';
import { closeTrash, trashOpen, trashSelectedImages } from './trash.js';
import { showToast, undoLatestToast } from './toast.js';
import {
    applyPreviousDevelopSettingsToGrid, copyDevelopSettingsFromGrid, createVirtualCopy,
    developOpen, holdDevelopReference, openDevelop, pasteDevelopSettingsToGrid, toggleDevelopCompare,
} from './develop/develop.js';
import { shortcutSheetOpen } from './shortcut_sheet.js';

function inputFocused() {
    const el = document.activeElement;
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
}

function foregroundLayerOpen() {
    return shortcutSheetOpen()
        || filtersOpen()
        || importOpen()
        || Boolean(
            document.querySelector('.typed-confirm')
            || document.querySelector('#collection-picker')
            || document.querySelector('#collection-pop-menu:not([hidden])')
            || document.querySelector('#grid-pop-menu:not([hidden])')
            || document.querySelector('#export-pop-menu:not([hidden])')
            || document.querySelector('#folder-pop-menu:not([hidden])')
            || document.querySelector('#source-pop-menu:not([hidden])')
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
    if (img) {
        applyFlags([img.id], flag);
        if (viewState.prefs.autoAdvanceFlags) moveFocus(1);
    }
}

function stackRows() {
    return [...document.querySelectorAll('#duplicates-body .stack-row[data-stack]')]
        .filter((row) => row.offsetParent !== null);
}

function activeStackRow() {
    const focused = document.activeElement?.closest?.('.stack-row[data-stack]');
    if (focused) return focused;
    const selected = document.querySelector('#duplicates-body .stack-row.kb-focus[data-stack]');
    return selected || stackRows()[0] || null;
}

function focusStackRow(row) {
    if (!row) return false;
    for (const item of document.querySelectorAll('#duplicates-body .stack-row.kb-focus')) {
        item.classList.remove('kb-focus');
        if (item !== row) item.removeAttribute('tabindex');
    }
    row.classList.add('kb-focus');
    row.tabIndex = 0;
    row.focus({ preventScroll: true });
    row.scrollIntoView({ block: 'nearest' });
    return true;
}

function moveStackFocus(delta) {
    const rows = stackRows();
    if (!rows.length) return false;
    const current = activeStackRow();
    const index = Math.max(0, rows.indexOf(current));
    const next = rows[Math.max(0, Math.min(rows.length - 1, index + delta))];
    return focusStackRow(next);
}

function focusStackPhoto(delta) {
    const row = activeStackRow();
    if (!row) return false;
    focusStackRow(row);
    const thumbs = [...row.querySelectorAll('.stack-photo .dupe-thumb')];
    if (!thumbs.length) return false;
    const focused = document.activeElement?.closest?.('.dupe-thumb');
    const currentIndex = thumbs.indexOf(focused);
    const start = currentIndex >= 0 ? currentIndex : 0;
    const next = thumbs[Math.max(0, Math.min(thumbs.length - 1, start + delta))];
    next.focus({ preventScroll: true });
    next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    return true;
}

function flagFocusedStackPhoto(flag) {
    const photo = document.activeElement?.closest?.('.stack-photo')
        || activeStackRow()?.querySelector('.stack-photo.is-cover, .stack-photo');
    const button = photo?.querySelector(`[data-flag="${flag}"][data-id]`);
    if (!button || button.disabled) return false;
    button.click();
    return true;
}

function clickAndFocusNextStack(button, row) {
    if (!button || button.disabled) return false;
    const rows = stackRows();
    const index = Math.max(0, rows.indexOf(row));
    button.click();
    const refocus = () => {
        const nextRows = stackRows();
        focusStackRow(nextRows[Math.min(index, nextRows.length - 1)] || nextRows[nextRows.length - 1]);
    };
    window.setTimeout(refocus, 80);
    window.setTimeout(refocus, 450);
    return true;
}

function handleStackKey(event) {
    const key = event.key.toLowerCase();
    if (key === 'g') {
        closeDuplicates();
    } else if (event.key === 'ArrowDown') {
        if (!moveStackFocus(1)) return false;
    } else if (event.key === 'ArrowUp') {
        if (!moveStackFocus(-1)) return false;
    } else if (event.key === 'ArrowRight') {
        if (!focusStackPhoto(1)) return false;
    } else if (event.key === 'ArrowLeft') {
        if (!focusStackPhoto(-1)) return false;
    } else if (key === 'k' || event.key === 'Enter') {
        const row = activeStackRow();
        if (!clickAndFocusNextStack(row?.querySelector('[data-stack-keep]'), row)) return false;
    } else if (key === 'u') {
        const row = activeStackRow();
        if (!clickAndFocusNextStack(row?.querySelector('[data-stack-unstack]'), row)) return false;
    } else if (key === 'p' || key === 'x') {
        if (!flagFocusedStackPhoto(key === 'p' ? 'picked' : 'rejected')) return false;
    } else if (key === 'c') {
        const photo = document.activeElement?.closest?.('.stack-photo') || activeStackRow()?.querySelector('.stack-photo.is-cover, .stack-photo');
        const button = photo?.querySelector('[data-set-cover][data-stack-id]');
        if (!button || button.disabled) return false;
        button.click();
    } else {
        return false;
    }
    event.preventDefault();
    return true;
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
    if (shortcutSheetOpen()) {
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
    document.getElementById('auto-advance-flags')?.addEventListener('change', (event) => {
        patchPrefs({ autoAdvanceFlags: event.target.checked });
    });
    on('prefs', (prefs) => {
        const input = document.getElementById('auto-advance-flags');
        if (input) input.checked = Boolean(prefs.autoAdvanceFlags);
    });
    window.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            if (escapeOneLayer()) {
                event.preventDefault();
                event.stopPropagation();
            }
            return;
        }
        if (developOpen() && !foregroundLayerOpen() && !inputFocused()) {
            const key = event.key.toLowerCase();
            if (key === 'y') {
                event.preventDefault();
                toggleDevelopCompare(event.altKey ? 'horizontal' : 'vertical');
                return;
            }
            if (key === 'r') {
                event.preventDefault();
                if (!event.repeat) holdDevelopReference(true);
                return;
            }
        }
        if (event.ctrlKey || event.metaKey) {
            const key = event.key.toLowerCase();
            if (foregroundLayerOpen() || inputFocused()) return;
            if (event.code === 'Quote' && developOpen()) {
                event.preventDefault();
                createVirtualCopy();
                return;
            }
            if (key === 'k') {
                event.preventDefault();
                openCommandPalette();
                return;
            }
            if (refineOpen() && key === 'z') {
                event.preventDefault();
                undoRefine();
                return;
            }
            if (activeLens() === 'grid') {
                const targets = selection.size ? [...selection] : [currentFocusedImage()?.id];
                if (event.shiftKey && key === 'c') {
                    event.preventDefault();
                    copyDevelopSettingsFromGrid(currentFocusedImage(), document.getElementById('grid-flow'));
                    return;
                }
                if (event.shiftKey && key === 'v') {
                    event.preventDefault();
                    pasteDevelopSettingsToGrid(targets);
                    return;
                }
                if (event.altKey && key === 'v') {
                    event.preventDefault();
                    applyPreviousDevelopSettingsToGrid(targets);
                    return;
                }
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
        if (shortcutSheetOpen()) return;
        if (filtersOpen()) {
            if (event.key.toLowerCase() === 'f') {
                event.preventDefault();
                closeFilters();
            }
            return;
        }
        if (event.key.toLowerCase() === 'd' && !foregroundLayerOpen()) {
            event.preventDefault();
            if (!developOpen()) openDevelop();
            return;
        }
        if (event.key.toLowerCase() === 'h' && !foregroundLayerOpen()) {
            event.preventDefault();
            switchLens('shared');
            return;
        }
        if (refineOpen() && pickByKey(event.key)) {
            event.preventDefault();
            return;
        }
        if (loupeOpen()) {
            const lk = event.key.toLowerCase();
            if (event.shiftKey && event.key === 'ArrowLeft') panLoupe(1, 0);
            else if (event.shiftKey && event.key === 'ArrowRight') panLoupe(-1, 0);
            else if (event.shiftKey && event.key === 'ArrowUp') panLoupe(0, 1);
            else if (event.shiftKey && event.key === 'ArrowDown') panLoupe(0, -1);
            else if (event.key === 'ArrowLeft') navLoupe(-1);
            else if (event.key === 'ArrowRight') navLoupe(1);
            else if (event.key === 'Home') navLoupeTo(0);
            else if (event.key === 'End') navLoupeTo(Number.MAX_SAFE_INTEGER);
            else if (event.key === '+' || event.key === '=') zoomLoupeBy(1);
            else if (event.key === '-') zoomLoupeBy(-1);
            else if (event.key === '0') fitLoupe();
            else if (event.key === 'Delete' || event.key === 'Backspace') {
                const imageId = loupeImageId();
                if (!imageId) return;
                clearSelection();
                selection.add(imageId);
                selectionChanged([imageId]);
                trashSelectedImages();
            }
            else if (lk === '[') toggleLeftPanel();
            else if (lk === ']') toggleRightPanel();
            else if (lk === 'g') closeLoupe({ force: true });
            else if (lk === 'l') toggleLoupeLights();
            else if (lk === 'i') toggleLoupeInfo();
            else if (lk === 'v') toggleLoupeVersion();
            else if (lk === 'p') flagLoupeOrFocused('picked');
            else if (lk === 'x') flagLoupeOrFocused('rejected');
            else if (lk === 'u') flagLoupeOrFocused('unflagged');
            else return;
            event.preventDefault();
            return;
        }
        if (duplicatesOpen() && handleStackKey(event)) {
            return;
        }
        if (trashOpen()) {
            if (event.key.toLowerCase() === 'g') {
                event.preventDefault();
                closeTrash();
            }
            return;
        }
        const key = event.key.toLowerCase();
        // Global keys: navigation between lenses, omnibox, and help work from
        // every persistent lens — only grid-specific keys are gated below.
        if (activeLens() !== 'grid') {
            if (key === '/') { event.preventDefault(); focusOmnibox(); }
            else if (key === '?') { event.preventDefault(); openHelp(); }
            else if (key === 'g') { event.preventDefault(); switchLens('grid'); }
            else if (activeLens() === 'people' && key === 'n') {
                if (reviewPeopleMergeByKey('reject')) event.preventDefault();
            }
            else if (activeLens() === 'people' && key === 'y' && reviewPeopleMergeByKey('merge')) {
                event.preventDefault();
            }
            else if (key === 'o') { event.preventDefault(); switchLens('people'); }
            else if (key === 'm') { event.preventDefault(); switchLens('map'); }
            else if (key === 'y') { event.preventDefault(); switchLens('events'); }
            return;
        }
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
            showToast(`Density: ${next[0].toUpperCase()}${next.slice(1)}`);
        } else if (/^[1-5]$/.test(event.key)) {
            event.preventDefault();
            const rating = Number(event.key);
            const next = Number(scope.min_stars || 0) === rating ? '' : rating;
            patchScope({ min_stars: next });
            showToast(next ? `Rating ${rating}+` : 'Rating filter cleared');
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
        } else if (viewState.activeLens === 'grid' && event.key === 'Home') {
            event.preventDefault();
            setFocus(0);
        } else if (viewState.activeLens === 'grid' && event.key === 'End') {
            event.preventDefault();
            setFocus(viewState.images.length - 1);
        } else if (viewState.activeLens === 'grid' && event.key === 'PageDown') {
            event.preventDefault();
            setFocus(viewState.focusIndex + focusPageStep());
        } else if (viewState.activeLens === 'grid' && event.key === 'PageUp') {
            event.preventDefault();
            setFocus(viewState.focusIndex - focusPageStep());
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
    window.addEventListener('keyup', (event) => {
        if (developOpen() && event.key.toLowerCase() === 'r') holdDevelopReference(false);
    });
    on('help:open', openHelp);
}
