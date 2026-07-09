// Selection bar + bottom sheet. Batch actions are real writes:
// flags via /api/images/flag, collections via /api/user-collections,
// export via /api/export?ids=.

import {
    addToCollection, createCollection, exportUrl,
    listCollections, removeFromCollection, thumbUrl,
} from './api.js';
import { applyFlags } from './flags.js';
import { clearSelection, on, selection } from './state.js';
import { showToast } from './toast.js';
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
    sheet.hidden = false;
    scrim.hidden = false;
    sheetOpen = true;
    return sheet;
}

export function closeSheet() {
    if (!sheetOpen) return;
    document.getElementById('m-sheet').hidden = true;
    document.getElementById('m-sheet-scrim').hidden = true;
    sheetOpen = false;
}

/* ---------- collection picker ---------- */
export async function openCollectionSheet(rawIds, { onDone = null } = {}) {
    const ids = [...new Set(rawIds.map(Number))].filter((id) => id > 0);
    if (!ids.length) return;

    const sheet = openSheet(
        '<h3>Add to collection</h3>'
        + '<input class="sheet-input" id="sheet-new-name" type="text" placeholder="New collection name" autocomplete="off">'
        + '<button class="sheet-btn" id="sheet-new-btn">Create &amp; add</button>'
        + '<div id="sheet-coll-list"><div class="skel-row"></div><div class="skel-row"></div></div>'
    );

    const finish = () => {
        closeSheet();
        if (onDone) onDone();
    };

    sheet.querySelector('#sheet-new-btn').addEventListener('click', async () => {
        const name = sheet.querySelector('#sheet-new-name').value.trim();
        if (!name) return;
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
            showToast("Couldn't create collection");
        }
    });

    const data = await listCollections();
    const listEl = sheet.querySelector('#sheet-coll-list');
    if (!listEl) return;
    const collections = (data && data.collections) || [];
    if (!collections.length) {
        listEl.innerHTML = '<div class="ms-empty">No collections yet — name one above.</div>';
        return;
    }
    listEl.innerHTML = collections.map((c, i) =>
        `<button class="sheet-row" data-ci="${i}">`
        + `<span class="g">${c.cover_image_id ? `<img src="${esc(thumbUrl('sm', c.cover_image_id))}" alt="" style="width:24px;height:24px;border-radius:6px;object-fit:cover">` : icon('folder')}</span>`
        + `<span>${esc(c.name)}</span><span class="n num">${c.image_count || 0}</span></button>`
    ).join('');
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
                showToast("Couldn't add to collection");
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

    on('selection', () => {
        const n = selection.size;
        bar.classList.toggle('on', n > 0);
        count.textContent = `${n} selected`;
    });

    document.getElementById('msb-clear').addEventListener('click', clearSelection);
    document.getElementById('msb-pick').addEventListener('click', () => {
        const ids = [...selection];
        clearSelection();
        applyFlags(ids, 'picked');
    });
    document.getElementById('msb-reject').addEventListener('click', () => {
        const ids = [...selection];
        clearSelection();
        applyFlags(ids, 'rejected');
    });
    document.getElementById('msb-coll').addEventListener('click', () => {
        openCollectionSheet([...selection], { onDone: clearSelection });
    });
    document.getElementById('msb-more').addEventListener('click', () => {
        const ids = [...selection];
        const sheet = openSheet(
            `<h3>${ids.length} selected</h3>`
            + `<button class="sheet-row" data-act="unflag"><span class="g">${icon('circle')}</span>Unflag</button>`
            + `<button class="sheet-row" data-act="csv"><span class="g">${icon('download')}</span>Export CSV</button>`
            + `<button class="sheet-row" data-act="json"><span class="g">${icon('download')}</span>Export JSON</button>`
            + `<button class="sheet-row" data-act="zip"><span class="g">${icon('download')}</span>Download files (zip)</button>`
        );
        for (const row of sheet.querySelectorAll('.sheet-row[data-act]')) {
            row.addEventListener('click', () => {
                closeSheet();
                clearSelection();
                if (row.dataset.act === 'unflag') applyFlags(ids, 'unflagged');
                else if (row.dataset.act === 'zip') exportImages(ids, 'zip', 'original');
                else exportImages(ids, row.dataset.act);
            });
        }
    });

    document.getElementById('m-sheet-scrim').addEventListener('click', closeSheet);
    window.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (sheetOpen) closeSheet();
        else if (selection.size) clearSelection();
    });
}
