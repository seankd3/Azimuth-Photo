/**
 * Develop export dialog + settings sync (§24).
 *
 * Shared desktop export dialog plus Develop settings sync (§24).
 *
 * The dialog keeps Develop's existing Photos controls and batch pipeline, while
 * callers supply the photos currently in hand and, when needed, their legacy
 * Originals/Data export actions.
 */

import { fetchOptionsWithTimeout } from '../../api.js';
import { pollJob } from '../jobs.js';
import { esc as escapeHtml } from '../dom.js';

const READ_TIMEOUT_MS = 10_000;
const MUTATION_TIMEOUT_MS = 20_000;
const EXPORT_TAB_STORAGE_KEY = 'pa_d_export_dialog_tab';
const EXPORT_OPTIONS_STORAGE_KEY = 'pa_d_export_dialog_photos';
let batchExportPoll = null;

const SHARPEN_OPTIONS = [
    ['none', 'None'],
    ['screen_low', 'Screen · Low'],
    ['screen_standard', 'Screen · Standard'],
    ['screen_high', 'Screen · High'],
    ['print_low', 'Print · Low'],
    ['print_standard', 'Print · Standard'],
    ['print_high', 'Print · High'],
];

export const SYNC_GROUPS = [
    ['wb', 'White Balance'],
    ['tone', 'Tone'],
    ['presence', 'Presence'],
    ['curve', 'Tone Curve'],
    ['hsl', 'HSL / B&W'],
    ['grade', 'Color Grading'],
    ['detail', 'Detail'],
    ['effects', 'Effects'],
    ['masks', 'Masks'],
];

function defaultFilenamePattern(image) {
    const stem = String(image?.filename || image?.filepath || 'developed').replace(/\.[^.]+$/, '');
    return `${stem || 'developed'}-develop`;
}

function readExportOptions(root) {
    const maxRaw = root.querySelector('[data-export-size]')?.value;
    const maxPx = Number(maxRaw);
    return {
        format: root.querySelector('[data-export-format]')?.value || 'jpeg',
        quality: Number(root.querySelector('[data-export-quality]')?.value) || 92,
        max_px: Number.isFinite(maxPx) && maxPx > 0 ? maxPx : undefined,
        sharpen: root.querySelector('[data-export-sharpen]')?.value || 'none',
        filename_pattern: (root.querySelector('[data-export-filename]')?.value || '').trim() || undefined,
        save_to_library: Boolean(root.querySelector('[data-export-library]')?.checked),
        originals_size: root.querySelector('[data-export-originals-size]')?.value || 'original',
    };
}

function readStoredExportOptions() {
    try {
        const value = JSON.parse(localStorage.getItem(EXPORT_OPTIONS_STORAGE_KEY) || '{}');
        return value && typeof value === 'object' ? value : {};
    } catch {
        return {};
    }
}

export function savedOriginalsExportSize() {
    return readStoredExportOptions().originals_size || 'original';
}

function applyExportOptions(root, options) {
    const assign = (selector, value) => {
        const field = root.querySelector(selector);
        if (field && value !== undefined && value !== null) field.value = value;
    };
    assign('[data-export-format]', options.format);
    assign('[data-export-quality]', options.quality);
    assign('[data-export-size]', options.max_px ?? '');
    assign('[data-export-sharpen]', options.sharpen);
    assign('[data-export-filename]', options.filename_pattern);
    assign('[data-export-originals-size]', options.originals_size || 'original');
    const library = root.querySelector('[data-export-library]');
    if (library && options.save_to_library !== undefined) library.checked = Boolean(options.save_to_library);
}

function rememberExportOptions(root) {
    try {
        localStorage.setItem(EXPORT_OPTIONS_STORAGE_KEY, JSON.stringify(readExportOptions(root)));
    } catch { /* local storage is optional */ }
}

function readStoredTab() {
    try {
        const tab = localStorage.getItem(EXPORT_TAB_STORAGE_KEY);
        return ['photos', 'originals', 'data'].includes(tab) ? tab : 'photos';
    } catch {
        return 'photos';
    }
}

function photoExportHtml(image) {
    const sharpen = SHARPEN_OPTIONS.map(([value, label]) => (
        `<option value="${value}">${escapeHtml(label)}</option>`
    )).join('');
    const filename = image ? defaultFilenamePattern(image) : '';
    return [
        '<label>Preset<select data-export-preset data-tip="Saved export presets"><option value="">Custom</option></select></label>',
        '<label>Format<select data-export-format data-tip="Export format"><option value="jpeg">JPEG</option><option value="tiff16">16-bit TIFF</option></select></label>',
        '<label>Quality<input data-export-quality type="number" min="1" max="100" value="92" data-tip="JPEG quality"></label>',
        '<label>Long edge<input data-export-size type="number" min="256" placeholder="Full size" data-tip="Optional longest-edge resize in pixels"></label>',
        `<label>Sharpen<select data-export-sharpen data-tip="Output sharpening after resize">${sharpen}</select></label>`,
        `<label>Filename<input data-export-filename type="text" value="${escapeHtml(filename)}" data-tip="Tokens: {stem} {filename} {id} {ext} {date}"></label>`,
        '<label class="develop-export-check" data-tip="Register the JPEG under Develop Exports and keep it with this RAW"><input data-export-library type="checkbox"> Save to library</label>',
        '<button data-export-confirm class="primary" data-tip="Render and download export">Export</button>',
        '<button data-export-save-preset type="button" data-tip="Save these options as a named preset">Save preset…</button>',
    ].join('');
}

function exportDialogHtml(image, title) {
    return [
        `<strong>${escapeHtml(title || 'Export')}</strong>`,
        '<div class="export-dialog-tabs" role="tablist" aria-label="Export type">',
        '<button type="button" role="tab" aria-selected="true" data-export-tab="photos">Photos</button>',
        '<button type="button" role="tab" aria-selected="false" data-export-tab="originals">Originals</button>',
        '<button type="button" role="tab" aria-selected="false" data-export-tab="data">Data</button>',
        '</div>',
        `<section class="export-dialog-panel" role="tabpanel" data-export-panel="photos">${photoExportHtml(image)}</section>`,
        '<section class="export-dialog-panel" role="tabpanel" data-export-panel="originals" hidden>',
        '<p class="export-dialog-hint">Download the original files together as a zip.</p>',
        '<label>Size<select data-export-originals-size data-tip="Choose which stored rendition to include"><option value="original">Original</option><option value="lg">Large</option><option value="md">Medium</option></select></label>',
        '<button type="button" class="primary" data-export-originals>Download originals</button>',
        '</section>',
        '<section class="export-dialog-panel" role="tabpanel" data-export-panel="data" hidden>',
        '<p class="export-dialog-hint">Export photo metadata for the photos in hand.</p>',
        '<div class="export-dialog-actions"><button type="button" data-export-data="csv">CSV</button><button type="button" data-export-data="json">JSON</button></div>',
        '</section>',
    ].join('');
}

function syncDialogHtml() {
    const checks = SYNC_GROUPS.map(([id, label]) => (
        `<label class="develop-sync-check" data-tip="Copy ${escapeHtml(label)}"><input type="checkbox" data-sync-group="${id}" checked> ${escapeHtml(label)}</label>`
    )).join('');
    return [
        '<strong>Sync settings</strong>',
        '<p class="develop-sync-hint">Copy chosen groups from this photo to the current grid selection.</p>',
        `<div class="develop-sync-groups">${checks}</div>`,
        '<button data-sync-confirm class="primary" data-tip="Sync selected groups to the grid selection">Sync</button>',
    ].join('');
}

async function downloadExportBlob(image, options, { showToast }) {
    const body = {
        format: options.format,
        quality: options.quality,
        sharpen: options.sharpen || 'none',
        save_to_library: Boolean(options.save_to_library),
    };
    if (options.max_px) body.max_px = options.max_px;
    if (options.filename_pattern) body.filename_pattern = options.filename_pattern;
    showToast?.('Rendering full-resolution export…');
    const response = await fetch(`/api/develop/${image.id}/export`, fetchOptionsWithTimeout({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, MUTATION_TIMEOUT_MS));
    if (!response.ok) throw new Error('export failed');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.getElementById('download-link');
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    const fallbackExt = options.format === 'jpeg' ? 'jpg' : 'tif';
    const fallback = `${String(image.filename || 'developed').replace(/\.[^.]+$/, '')}.${fallbackExt}`;
    link.href = url;
    link.download = match?.[1] || options.filename_pattern || fallback;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    const libraryId = response.headers.get('X-Develop-Library-Image-Id');
    showToast?.(libraryId ? `Export ready · saved to library (#${libraryId})` : 'Export ready');
}

async function loadPresetsInto(popover) {
    try {
        const response = await fetch('/api/develop/export-presets', fetchOptionsWithTimeout({}, READ_TIMEOUT_MS));
        const payload = await response.json();
        const select = popover.querySelector('[data-export-preset]');
        if (!select) return;
        for (const preset of payload.presets || []) {
            const option = document.createElement('option');
            option.value = String(preset.id);
            option.textContent = preset.name;
            option.dataset.options = JSON.stringify(preset.options || {});
            select.appendChild(option);
        }
        select.addEventListener('change', () => {
            const selected = select.selectedOptions[0];
            if (!selected?.dataset.options) return;
            const options = JSON.parse(selected.dataset.options);
            const assign = (attr, value) => {
                const field = popover.querySelector(attr);
                if (field != null && value !== undefined && value !== null) field.value = value;
            };
            assign('[data-export-format]', options.format);
            assign('[data-export-quality]', options.quality);
            assign('[data-export-size]', options.max_px ?? '');
            assign('[data-export-sharpen]', options.sharpen);
            if (options.filename_pattern) assign('[data-export-filename]', options.filename_pattern);
        });
    } catch { /* presets are optional */ }
}

function bindPresetSave(popover, { showToast }) {
    popover.querySelector('[data-export-save-preset]')?.addEventListener('click', async () => {
        const form = document.createElement('form');
        form.className = 'develop-preset-name';
        form.innerHTML = '<label>Preset name<input data-export-preset-name type="text" maxlength="120" autocomplete="off" required></label>'
            + '<button type="submit" class="primary">Save preset</button>'
            + '<button type="button" data-export-preset-cancel>Cancel</button>';
        const opener = popover.querySelector('[data-export-save-preset]');
        opener.replaceWith(form);
        const input = form.querySelector('[data-export-preset-name]');
        input.focus({ preventScroll: true });
        form.querySelector('[data-export-preset-cancel]').addEventListener('click', () => form.replaceWith(opener));
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const name = input.value.trim();
            if (!name) {
                input.focus({ preventScroll: true });
                return;
            }
            const save = form.querySelector('[type="submit"]');
            save.disabled = true;
            input.disabled = true;
            try {
                const response = await fetch('/api/develop/export-presets', fetchOptionsWithTimeout({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, options: readExportOptions(popover) }),
                }, MUTATION_TIMEOUT_MS));
                if (!response.ok) throw new Error();
                showToast?.(`Preset “${name}” saved`);
                form.replaceWith(opener);
                opener.focus({ preventScroll: true });
            } catch {
                showToast?.("Couldn't save the preset");
                save.disabled = false;
                input.disabled = false;
                input.focus({ preventScroll: true });
            }
        });
    });
}

function idsFrom(image, imageIds) {
    const ids = imageIds || (image?.id ? [image.id] : []);
    return [...new Set(ids.map(Number).filter((id) => id > 0))];
}

// Data exports (csv/json) are small enough to fetch, so a server failure can
// toast honestly instead of the anchor silently downloading an error body.
// Zips keep the streaming anchor — buffering multi-GB archives in a blob is worse.
export async function fetchDataExport(params, { showToast, filename }) {
    showToast?.(`Exporting as ${(params.get('format') || 'csv').toUpperCase()}`);
    let response = null;
    try {
        response = await fetch(`/api/export?${params.toString()}`, fetchOptionsWithTimeout({}, MUTATION_TIMEOUT_MS));
    } catch { /* handled below */ }
    if (!response?.ok) {
        showToast?.("Couldn't export — try again");
        return false;
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.getElementById('download-link');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    return true;
}

function downloadLegacyExport(imageIds, format, { showToast, size = '' } = {}) {
    const ids = idsFrom(null, imageIds);
    if (!ids.length) return;
    const params = new URLSearchParams({ format, ids: ids.join(',') });
    if (size) params.set('size', size);
    if (format !== 'zip') {
        void fetchDataExport(params, { showToast, filename: `azimuth-photo-export.${format}` });
        return;
    }
    const link = document.getElementById('download-link');
    link.href = `/api/export?${params.toString()}`;
    link.download = 'azimuth-photo-export.zip';
    link.click();
    showToast?.('Preparing original files');
}

function setActiveTab(popover, tab, { remember = true } = {}) {
    for (const button of popover.querySelectorAll('[data-export-tab]')) {
        const active = button.dataset.exportTab === tab;
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        button.tabIndex = active ? 0 : -1;
    }
    for (const panel of popover.querySelectorAll('[data-export-panel]')) {
        panel.hidden = panel.dataset.exportPanel !== tab;
    }
    popover.dataset.exportActiveTab = tab;
    if (remember) {
        try {
            localStorage.setItem(EXPORT_TAB_STORAGE_KEY, tab);
        } catch { /* local storage is optional */ }
    }
}

export function openExportDialog({
    button,
    image = null,
    imageIds = null,
    getImageIds = null,
    anchoredPopover,
    closePopover,
    showToast,
    isRaw,
    onDataExport = null,
    onOriginalsExport = null,
    title = 'Export',
}) {
    if (image && isRaw && !isRaw(image)) return null;
    const fallbackIds = idsFrom(image, imageIds);
    if (!fallbackIds.length && !getImageIds) return null;
    const popover = anchoredPopover(button, exportDialogHtml(image, title));
    popover.classList.add('develop-export-dialog');
    applyExportOptions(popover, readStoredExportOptions());
    loadPresetsInto(popover);
    bindPresetSave(popover, { showToast });
    popover.querySelector('[data-export-panel="photos"]')?.addEventListener('input', () => rememberExportOptions(popover));
    popover.querySelector('[data-export-panel="photos"]')?.addEventListener('change', () => rememberExportOptions(popover));
    popover.querySelector('[data-export-panel="originals"]')?.addEventListener('change', () => rememberExportOptions(popover));
    const resolveIds = async () => {
        const resolved = getImageIds ? await getImageIds() : fallbackIds;
        return idsFrom(null, resolved);
    };
    const exportPhotos = async () => {
        const options = readExportOptions(popover);
        closePopover?.();
        try {
            const ids = await resolveIds();
            if (!ids.length) {
                showToast?.('Select photos to export');
            } else if (image && ids.length === 1 && ids[0] === Number(image.id)) {
                await downloadExportBlob(image, options, { showToast });
            } else {
                await queueBatchExport(ids, options, { showToast });
            }
        } catch {
            showToast?.('Export failed');
        }
    };
    popover.querySelector('[data-export-confirm]')?.addEventListener('click', exportPhotos);
    const exportOriginals = async () => {
        const size = readExportOptions(popover).originals_size;
        closePopover?.();
        try {
            const ids = await resolveIds();
            if (!ids.length) return showToast?.('Select photos to export');
            if (onOriginalsExport) await onOriginalsExport(ids, size);
            else downloadLegacyExport(ids, 'zip', { showToast, size });
        } catch {
            showToast?.('Export failed');
        }
    };
    popover.querySelector('[data-export-originals]')?.addEventListener('click', exportOriginals);
    const exportData = async (format) => {
        closePopover?.();
        try {
            const ids = await resolveIds();
            if (!ids.length) return showToast?.('Select photos to export');
            if (onDataExport) await onDataExport(format, ids);
            else downloadLegacyExport(ids, format, { showToast });
        } catch {
            showToast?.('Export failed');
        }
    };
    for (const dataButton of popover.querySelectorAll('[data-export-data]')) {
        dataButton.addEventListener('click', () => exportData(dataButton.dataset.exportData));
    }
    for (const tabButton of popover.querySelectorAll('[data-export-tab]')) {
        tabButton.addEventListener('click', () => setActiveTab(popover, tabButton.dataset.exportTab));
    }
    popover.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.target.closest('button, [data-export-preset-name]')) return;
        event.preventDefault();
        const activeTab = popover.dataset.exportActiveTab || 'photos';
        if (activeTab === 'photos') exportPhotos();
        else if (activeTab === 'originals') exportOriginals();
        else exportData('csv');
    });
    setActiveTab(popover, readStoredTab(), { remember: false });
    return popover;
}

export function openSyncDialog({
    button,
    sourceId,
    targetIds,
    anchoredPopover,
    closePopover,
    showToast,
}) {
    const ids = [...new Set((targetIds || []).map(Number).filter((id) => id > 0 && id !== Number(sourceId)))];
    if (!sourceId) return null;
    if (!ids.length) {
        showToast?.('Select other photos in the grid to sync into');
        return null;
    }
    const popover = anchoredPopover(button, syncDialogHtml());
    popover.classList.add('develop-sync-dialog');
    popover.querySelector('[data-sync-confirm]')?.addEventListener('click', async () => {
        const groups = [...popover.querySelectorAll('[data-sync-group]:checked')].map((input) => input.dataset.syncGroup);
        closePopover?.();
        if (!groups.length) {
            showToast?.('Choose at least one settings group');
            return;
        }
        showToast?.(`Syncing ${groups.length} group${groups.length === 1 ? '' : 's'} to ${ids.length} photo${ids.length === 1 ? '' : 's'}…`);
        try {
            const response = await fetch('/api/develop/sync', fetchOptionsWithTimeout({
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_id: Number(sourceId), target_ids: ids, groups }),
            }, MUTATION_TIMEOUT_MS));
            if (!response.ok) throw new Error('sync failed');
            const payload = await response.json();
            const count = payload.synced?.length || 0;
            showToast?.(count ? `Synced settings to ${count} photo${count === 1 ? '' : 's'}` : 'Nothing synced');
        } catch {
            showToast?.('Sync failed');
        }
    });
    return popover;
}

export async function queueBatchExport(imageIds, options = {}, { showToast } = {}) {
    const ids = [...new Set((imageIds || []).map(Number).filter((id) => id > 0))];
    if (!ids.length) {
        showToast?.('Select photos to export');
        return null;
    }
    const body = {
        image_ids: ids,
        format: options.format || 'jpeg',
        quality: options.quality ?? 92,
        sharpen: options.sharpen || 'screen_standard',
        save_to_library: Boolean(options.save_to_library),
    };
    if (options.max_px) body.max_px = options.max_px;
    if (options.filename_pattern) body.filename_pattern = options.filename_pattern;
    showToast?.(`Queuing develop export for ${ids.length} photo${ids.length === 1 ? '' : 's'}…`);
    const response = await fetch('/api/develop/export/batch', fetchOptionsWithTimeout({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, MUTATION_TIMEOUT_MS));
    if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error || 'batch export failed');
    }
    const payload = await response.json();
    showToast?.(`Develop export started · ${payload.queued?.length || ids.length} queued`);
    pollBatchStatus(showToast);
    return payload;
}

export function cancelBatchExportPoll() {
    batchExportPoll?.cancel();
    batchExportPoll = null;
}

function pollBatchStatus(showToast) {
    cancelBatchExportPoll();
    batchExportPoll = pollJob({
        intervalMs: 1000,
        fetchStatus: async () => {
            try {
                const response = await fetch('/api/develop/export/batch/status', fetchOptionsWithTimeout({ headers: { Accept: 'application/json' } }, READ_TIMEOUT_MS));
                if (!response.ok) return { state: 'error', error: 'Export status unavailable' };
                return response.json();
            } catch {
                return { state: 'error', error: 'Export status unavailable' };
            }
        },
        isDone: (status) => status.state !== 'running',
        onTick: (status) => {
            if (status.state === 'running') {
                showToast?.(`Develop export ${status.done}/${status.total}…`);
                return;
            }
            if (status.state === 'complete') {
                const errors = status.errors?.length || 0;
                showToast?.(errors
                    ? `Develop export completed · ${status.done} finished, ${errors} failed`
                    : `Develop export completed · ${status.done} photo${status.done === 1 ? '' : 's'}`);
                return;
            }
            showToast?.(`Develop export failed · ${status.error || 'Check Exports later'}`);
        },
    });
}

export const EXPORT_SHARPEN_OPTIONS = SHARPEN_OPTIONS;
export const EXPORT_SYNC_GROUPS = SYNC_GROUPS;
