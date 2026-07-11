/**
 * Develop export dialog + settings sync (§24).
 *
 * Mounted from develop.js. Owns the expanded export popover (format, quality,
 * long-edge resize, output sharpening, filename pattern, save-to-library),
 * the Sync… checkbox popover, and grid batch-export queue helpers.
 */

const SHARPEN_OPTIONS = [
    ['none', 'None'],
    ['screen_low', 'Screen · Low'],
    ['screen_standard', 'Screen · Standard'],
    ['screen_high', 'Screen · High'],
    ['print_low', 'Print · Low'],
    ['print_standard', 'Print · Standard'],
    ['print_high', 'Print · High'],
];

const SYNC_GROUPS = [
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

function escapeHtml(text) {
    return String(text ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

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
    };
}

function exportDialogHtml(image) {
    const sharpen = SHARPEN_OPTIONS.map(([value, label]) => (
        `<option value="${value}">${escapeHtml(label)}</option>`
    )).join('');
    return [
        '<strong>Export developed photo</strong>',
        '<label>Preset<select data-export-preset data-tip="Saved export presets"><option value="">Custom</option></select></label>',
        '<label>Format<select data-export-format data-tip="Export format"><option value="jpeg">JPEG</option><option value="tiff16">16-bit TIFF</option></select></label>',
        '<label>Quality<input data-export-quality type="number" min="1" max="100" value="92" data-tip="JPEG quality"></label>',
        '<label>Long edge<input data-export-size type="number" min="256" placeholder="Full size" data-tip="Optional longest-edge resize in pixels"></label>',
        `<label>Sharpen<select data-export-sharpen data-tip="Output sharpening after resize">${sharpen}</select></label>`,
        `<label>Filename<input data-export-filename type="text" value="${escapeHtml(defaultFilenamePattern(image))}" data-tip="Tokens: {stem} {filename} {id} {ext} {date}"></label>`,
        '<label class="develop-export-check" data-tip="Register the JPEG under Develop Exports and keep it with this RAW"><input data-export-library type="checkbox"> Save to library</label>',
        '<button data-export-confirm class="primary" data-tip="Render and download export">Export</button>',
        '<button data-export-save-preset type="button" data-tip="Save these options as a named preset">Save preset…</button>',
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
    const response = await fetch(`/api/develop/${image.id}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
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
        const response = await fetch('/api/develop/export-presets');
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
        const name = prompt('Preset name');
        if (!name?.trim()) return;
        try {
            const response = await fetch('/api/develop/export-presets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim(), options: readExportOptions(popover) }),
            });
            if (!response.ok) throw new Error();
            showToast?.(`Preset “${name.trim()}” saved`);
        } catch {
            showToast?.("Couldn't save the preset");
        }
    });
}

export function openExportDialog({ button, image, anchoredPopover, closePopover, showToast, isRaw }) {
    if (!image || (isRaw && !isRaw(image))) return null;
    const popover = anchoredPopover(button, exportDialogHtml(image));
    popover.classList.add('develop-export-dialog');
    loadPresetsInto(popover);
    bindPresetSave(popover, { showToast });
    popover.querySelector('[data-export-confirm]')?.addEventListener('click', async () => {
        const options = readExportOptions(popover);
        closePopover?.();
        try {
            await downloadExportBlob(image, options, { showToast });
        } catch {
            showToast?.('Export failed');
        }
    });
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
            const response = await fetch('/api/develop/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_id: Number(sourceId), target_ids: ids, groups }),
            });
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
    const response = await fetch('/api/develop/export/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error || 'batch export failed');
    }
    const payload = await response.json();
    showToast?.(`Develop export started · ${payload.queued?.length || ids.length} queued`);
    pollBatchStatus(showToast);
    return payload;
}

async function pollBatchStatus(showToast) {
    for (let attempt = 0; attempt < 600; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        try {
            const response = await fetch('/api/develop/export/batch/status', { headers: { Accept: 'application/json' } });
            if (!response.ok) return;
            const status = await response.json();
            if (status.state === 'running') {
                showToast?.(`Develop export ${status.done}/${status.total}…`);
                continue;
            }
            if (status.state === 'complete') {
                const errors = status.errors?.length || 0;
                showToast?.(
                    errors
                        ? `Develop export done · ${status.done} finished, ${errors} failed`
                        : `Develop export done · ${status.done} photo${status.done === 1 ? '' : 's'}`,
                );
            }
            return;
        } catch {
            return;
        }
    }
}

export const EXPORT_SHARPEN_OPTIONS = SHARPEN_OPTIONS;
export const EXPORT_SYNC_GROUPS = SYNC_GROUPS;
