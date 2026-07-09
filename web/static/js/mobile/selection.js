// Selection bar + bottom sheet. Batch actions are real writes:
// flags via /api/images/flag, collections via /api/user-collections,
// export via /api/export?ids=.

import {
    addToCollection, createCollection, exportUrl,
    listCollections, removeFromCollection, thumbUrl, writeFailureMessage,
} from './api.js';
import { applyFlags } from './flags.js';
import { clearSelection, on, selection } from './state.js';
import { showToast } from './toast.js';
import {
    dismissLayer, dismissLayerThen, layerActive, pushLayer, registerLayer, syncLayerClosed,
} from './history.js';
import { icon } from '../icons.js';

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const ZIP_EXPORT_MAX = 2000;

/* ---------- bottom sheet ---------- */
let sheetOpen = false;

export function openSheet(html) {
    const sheet = document.getElementById('m-sheet');
    const scrim = document.getElementById('m-sheet-scrim');
    sheet.innerHTML = `<div class="sheet-grab"></div>${html}`;
    sheet.style.transform = '';
    sheet.classList.remove('dragging');
    sheet.hidden = false;
    scrim.hidden = false;
    sheetOpen = true;
    pushLayer('sheet');
    queueMicrotask(() => document.dispatchEvent(new CustomEvent('sheet-mutated')));
    return sheet;
}

export function closeSheet({ fromHistory = false } = {}) {
    if (!sheetOpen) return;
    const sheet = document.getElementById('m-sheet');
    sheet.hidden = true;
    sheet.style.transform = '';
    sheet.classList.remove('dragging', 'settling');
    document.getElementById('m-sheet-scrim').hidden = true;
    sheetOpen = false;
    document.body.classList.remove('sheet-keyboard');
    document.documentElement.style.setProperty('--keyboard-offset', '0px');
    if (!fromHistory) syncLayerClosed('sheet');
}

function dismissSheet() {
    dismissLayer('sheet', closeSheet);
}

export function dismissSheetThen(afterClose = null) {
    dismissLayerThen('sheet', closeSheet, afterClose);
}

function installSheetKeyboardLift() {
    const sheet = document.getElementById('m-sheet');
    if (!sheet || !window.visualViewport) return;
    let focused = null;

    const isField = (el) => el && el.matches && el.matches('input, textarea, select');

    const adjust = () => {
        if (!sheetOpen || !focused || !sheet.contains(focused)) return;
        const vv = window.visualViewport;
        const keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
        document.body.classList.toggle('sheet-keyboard', keyboard > 24);
        document.documentElement.style.setProperty('--keyboard-offset', `${Math.ceil(keyboard)}px`);
        document.documentElement.style.setProperty('--visual-vh', `${Math.ceil(vv.height)}px`);

        requestAnimationFrame(() => {
            const primary = sheet.querySelector('.sheet-btn:not([hidden]):not(:disabled)');
            const targets = [focused, primary].filter(Boolean);
            if (!targets.length) return;
            let bottom = 0;
            for (const el of targets) {
                bottom = Math.max(bottom, el.offsetTop + el.offsetHeight);
            }
            const viewBottom = sheet.scrollTop + sheet.clientHeight - 14;
            if (bottom > viewBottom) {
                sheet.scrollTo({ top: bottom - sheet.clientHeight + 14, behavior: 'smooth' });
            }
        });
    };

    sheet.addEventListener('focusin', (e) => {
        if (!isField(e.target)) return;
        focused = e.target;
        adjust();
    });
    sheet.addEventListener('focusout', (e) => {
        if (sheet.contains(e.relatedTarget) && isField(e.relatedTarget)) return;
        focused = null;
        document.body.classList.remove('sheet-keyboard');
        document.documentElement.style.setProperty('--keyboard-offset', '0px');
        document.documentElement.style.setProperty('--visual-vh', '100dvh');
    });
    window.visualViewport.addEventListener('resize', adjust);
    window.visualViewport.addEventListener('scroll', adjust);
}

function installSheetSwipe() {
    const sheet = document.getElementById('m-sheet');
    let drag = null;

    const releaseDrag = (e) => {
        if (!drag) return;
        try {
            if (sheet.hasPointerCapture && sheet.hasPointerCapture(drag.id)) {
                sheet.releasePointerCapture(drag.id);
            } else if (e && sheet.releasePointerCapture) {
                sheet.releasePointerCapture(e.pointerId);
            }
        } catch {
            // Pointer capture can already be gone after cancel/resize.
        }
        sheet.classList.remove('dragging');
        drag = null;
    };

    sheet.addEventListener('pointerdown', (e) => {
        if (!sheetOpen || !e.isPrimary) return;
        const fromHandle = Boolean(e.target.closest('.sheet-grab, h3'));
        if (!fromHandle && e.target.closest('button, a, input, textarea, select, label')) return;
        if (!fromHandle && sheet.scrollTop > 0) return;
        const now = performance.now();
        drag = {
            id: e.pointerId,
            y: e.clientY,
            prevY: e.clientY,
            prevT: now,
            v: 0,
            fromHandle,
            started: fromHandle,
        };
        sheet.setPointerCapture(e.pointerId);
        if (fromHandle) sheet.classList.add('dragging');
    });

    sheet.addEventListener('pointermove', (e) => {
        if (!drag || e.pointerId !== drag.id) return;
        const now = performance.now();
        const dt = Math.max(1, now - drag.prevT);
        drag.v = (e.clientY - drag.prevY) / dt;
        drag.prevY = e.clientY;
        drag.prevT = now;
        let dy = e.clientY - drag.y;
        if (!drag.started) {
            if (dy < -2 || sheet.scrollTop > 0) {
                releaseDrag(e);
                return;
            }
            if (dy < 6) return;
            drag.started = true;
            sheet.classList.add('dragging');
        }
        if (dy < 0) dy *= 0.18;
        sheet.style.transform = `translateY(${Math.max(-18, dy)}px)`;
        e.preventDefault();
    });

    const end = (e) => {
        if (!drag || e.pointerId !== drag.id) return;
        if (!drag.started) {
            releaseDrag(e);
            return;
        }
        const dy = e.clientY - drag.y;
        const close = dy > sheet.getBoundingClientRect().height * 0.3 || (dy > 28 && drag.v > 0.55);
        releaseDrag(e);
        sheet.classList.remove('dragging');
        if (close) {
            dismissSheet();
            return;
        }
        sheet.classList.add('settling');
        sheet.style.transform = '';
        window.setTimeout(() => sheet.classList.remove('settling'), 220);
    };
    sheet.addEventListener('pointerup', end);
    sheet.addEventListener('pointercancel', end);
}

/* ---------- collection picker ---------- */
export async function openCollectionSheet(rawIds, { onDone = null } = {}) {
    const ids = [...new Set(rawIds.map(Number))].filter((id) => id > 0);
    if (!ids.length) return;

    const sheet = openSheet(
        '<h3>Add to collection</h3>'
        + '<input class="sheet-input" id="sheet-new-name" type="text" placeholder="New collection name" autocomplete="off">'
        + '<button class="sheet-btn" id="sheet-new-btn" data-mutating>Create &amp; add</button>'
        + '<div id="sheet-coll-list"><div class="skel-row"></div><div class="skel-row"></div></div>'
    );

    const finish = () => {
        dismissSheetThen(onDone);
    };

    sheet.querySelector('#sheet-new-btn').addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (!button || button.disabled) return;
        const name = sheet.querySelector('#sheet-new-name').value.trim();
        if (!name) return;
        button.disabled = true;
        try {
            finish();
            const result = await createCollection(name, ids);
            if (result && result.ok) {
                const coll = result.collection || {};
                showToast(`Created “${name}” · ${ids.length} photos`, {
                    undo: async () => {
                        if (coll.id) await removeFromCollection(coll.id, ids);
                        showToast('Removed from collection');
                    },
                });
            } else {
                showToast(writeFailureMessage());
            }
        } finally {
            if (document.contains(button)) button.disabled = false;
        }
    });

    let data = null;
    try {
        data = await listCollections();
    } catch {
        const listEl = sheet.querySelector('#sheet-coll-list');
        if (listEl) {
            listEl.innerHTML = '<div class="ms-empty">Couldn\'t load collections.</div>';
        }
        return;
    }
    const listEl = sheet.querySelector('#sheet-coll-list');
    if (!listEl) return;
    const collections = (data && data.collections) || [];
    if (!collections.length) {
        listEl.innerHTML = '<div class="ms-empty">No collections yet — name one above.</div>';
        return;
    }
    listEl.innerHTML = collections.map((c, i) =>
        `<button class="sheet-row" data-ci="${i}" data-mutating>`
        + `<span class="g">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="" style="width:24px;height:24px;border-radius:6px;object-fit:cover">` : icon('folder')}</span>`
        + `<span>${esc(c.name)}</span><span class="n num">${c.image_count || 0}</span></button>`
    ).join('');
    document.dispatchEvent(new CustomEvent('sheet-mutated'));
    for (const row of listEl.querySelectorAll('.sheet-row')) {
        row.addEventListener('click', async () => {
            const coll = collections[Number(row.dataset.ci)];
            if (!coll) return;
            finish();
            const result = await addToCollection(coll.id, ids);
            if (result && result.ok) {
                showToast(`Added ${ids.length} to “${coll.name}”`, {
                    undo: async () => {
                        await removeFromCollection(coll.id, ids);
                        showToast('Removed again');
                    },
                });
            } else {
                showToast(writeFailureMessage());
            }
        });
    }
}

/* ---------- export ---------- */
export function exportImages(ids, format = 'csv', size = '') {
    if (!ids.length) return;
    if (format === 'zip' && ids.length > ZIP_EXPORT_MAX) {
        showToast(`Zip export is limited to ${ZIP_EXPORT_MAX.toLocaleString('en-US')} photos`);
        return;
    }
    const anchor = document.getElementById('m-dl');
    anchor.href = exportUrl(ids, format, size);
    anchor.download = format === 'zip' ? 'photoarchive-export.zip' : `photoarchive-export.${format}`;
    anchor.click();
    showToast(format === 'zip' ? `Preparing ${ids.length} files…` : `Exporting ${ids.length} photos…`);
}

/* ---------- selection bar ---------- */
export function initSelection() {
    const bar = document.getElementById('m-selbar');
    const count = document.getElementById('msb-count');
    const bottomBar = document.createElement('div');
    bottomBar.id = 'm-sel-actions';
    bottomBar.innerHTML =
        `<button type="button" data-action="pick">${icon('star')}<span>Pick</span></button>`
        + `<button type="button" data-action="reject">${icon('x')}<span>Reject</span></button>`
        + `<button type="button" data-action="collection" aria-label="Add to collection">${icon('plus')}</button>`
        + `<button type="button" data-action="more" aria-label="More selection actions">${icon('ellipsis')}</button>`;
    document.body.appendChild(bottomBar);
    document.dispatchEvent(new CustomEvent('selection-actions-mutated'));

    const pickSelection = () => {
        const ids = [...selection];
        dismissLayerThen('selection', clearSelection, () => applyFlags(ids, 'picked'));
    };
    const rejectSelection = () => {
        const ids = [...selection];
        dismissLayerThen('selection', clearSelection, () => applyFlags(ids, 'rejected'));
    };
    const openMoreActions = () => {
        const ids = [...selection];
        const sheet = openSheet(
            `<h3>${ids.length} selected</h3>`
            + `<button class="sheet-row" data-act="unflag" data-mutating><span class="g">${icon('circle')}</span>Unflag</button>`
            + `<button class="sheet-row" data-act="csv"><span class="g">${icon('download')}</span>Export CSV</button>`
            + `<button class="sheet-row" data-act="json"><span class="g">${icon('download')}</span>Export JSON</button>`
            + `<button class="sheet-row" data-act="zip"><span class="g">${icon('download')}</span>Download files (zip)</button>`
        );
        for (const row of sheet.querySelectorAll('.sheet-row[data-act]')) {
            row.addEventListener('click', () => {
                dismissSheetThen(() => {
                    dismissLayerThen('selection', clearSelection, () => {
                        if (row.dataset.act === 'unflag') applyFlags(ids, 'unflagged');
                        else if (row.dataset.act === 'zip') exportImages(ids, 'zip', 'original');
                        else exportImages(ids, row.dataset.act);
                    });
                });
            });
        }
    };

    on('selection', () => {
        const n = selection.size;
        bar.classList.toggle('on', n > 0);
        bottomBar.classList.toggle('on', n > 0);
        document.body.classList.toggle('m-selecting', n > 0);
        count.textContent = `${n} selected`;
        if (n > 0 && !layerActive('selection')) pushLayer('selection');
        else if (n === 0) syncLayerClosed('selection');
    });

    registerLayer('sheet', { close: closeSheet });
    registerLayer('selection', { close: clearSelection });
    installSheetSwipe();
    installSheetKeyboardLift();

    document.getElementById('msb-clear').addEventListener('click', () => dismissLayer('selection', clearSelection));
    document.getElementById('msb-pick').addEventListener('click', pickSelection);
    document.getElementById('msb-reject').addEventListener('click', rejectSelection);
    document.getElementById('msb-coll').addEventListener('click', () => {
        openCollectionSheet([...selection], {
            onDone: () => dismissLayer('selection', clearSelection),
        });
    });
    document.getElementById('msb-more').addEventListener('click', openMoreActions);
    bottomBar.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        if (btn.dataset.action === 'pick') pickSelection();
        else if (btn.dataset.action === 'reject') rejectSelection();
        else if (btn.dataset.action === 'collection') {
            openCollectionSheet([...selection], {
                onDone: () => dismissLayer('selection', clearSelection),
            });
        }
        else if (btn.dataset.action === 'more') openMoreActions();
    });

    document.getElementById('m-sheet-scrim').addEventListener('click', dismissSheet);
    window.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (sheetOpen) dismissSheet();
        else if (selection.size) dismissLayer('selection', clearSelection);
    });
}
