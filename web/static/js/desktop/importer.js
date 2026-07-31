import { getImportOptions, listImports } from './api.js';
import { emit, on, setScope } from './state.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { icon } from '../icons.js';
import { escapeHtml as esc, formatCount as fmt } from './dom.js';

let modal = null;
let selectedFiles = [];
let optionsLoaded = false;
let importOptions = null;
let recentImports = null;
let currentUpload = null;
let previewUrls = [];

const MAX_IMPORT_PREVIEWS = 12;

function entryFile(entry) {
    return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function readEntries(reader) {
    return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function traverseEntry(entry, path = '') {
    if (!entry) return [];
    if (entry.isFile) {
        const file = await entryFile(entry);
        return [{ file, relativePath: `${path}${file.name}` }];
    }
    if (!entry.isDirectory) return [];
    const reader = entry.createReader();
    const files = [];
    while (true) {
        const entries = await readEntries(reader);
        if (!entries.length) break;
        for (const child of entries) files.push(...await traverseEntry(child, `${path}${entry.name}/`));
    }
    return files;
}

async function droppedFiles(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    const entries = items
        .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
        .filter(Boolean);
    if (entries.length) {
        const files = [];
        for (const entry of entries) files.push(...await traverseEntry(entry));
        return files;
    }
    return Array.from(dataTransfer?.files || []).map((file) => ({ file, relativePath: file.name }));
}

function fileEntries(fileList) {
    return Array.from(fileList || []).map((file) => ({
        file,
        relativePath: file.webkitRelativePath || file.name,
    }));
}

function setStatus(message, tone = 'muted') {
    const status = modal?.querySelector('#import-status');
    if (!status) return;
    status.textContent = message;
    status.dataset.tone = tone;
}

function setProgress(percent) {
    const bar = modal?.querySelector('#import-progress i');
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(percent) || 0))}%`;
}

function clearImportPreviewUrls() {
    for (const url of previewUrls) URL.revokeObjectURL(url);
    previewUrls = [];
}

function renderImportPreview() {
    const grid = modal?.querySelector('#import-preview-grid');
    if (!grid) return;
    clearImportPreviewUrls();
    const entries = selectedFiles.slice(0, MAX_IMPORT_PREVIEWS);
    grid.hidden = entries.length === 0;
    if (!entries.length) {
        grid.innerHTML = '';
        return;
    }
    grid.innerHTML = entries.map((entry) => {
        const url = URL.createObjectURL(entry.file);
        previewUrls.push(url);
        const name = entry.file.name || 'Photo';
        return `<figure class="import-preview-item" title="${esc(entry.relativePath || name)}">`
            + `<span class="import-preview-media"><img src="${esc(url)}" alt=""><span class="import-preview-fallback">${icon('image')}</span></span>`
            + `<figcaption class="import-preview-name">${esc(name)}</figcaption></figure>`;
    }).join('');
    for (const image of grid.querySelectorAll('img')) {
        image.addEventListener('error', () => image.closest('.import-preview-item')?.classList.add('preview-unavailable'), { once: true });
    }
}

function setSelectedFiles(files) {
    selectedFiles = (files || []).filter((item) => item?.file);
    const summary = modal.querySelector('#import-selection-summary');
    if (summary) summary.textContent = selectedFiles.length
        ? `${fmt(selectedFiles.length)} item${selectedFiles.length === 1 ? '' : 's'} ready`
        : 'No photos selected';
    const start = modal.querySelector('#import-start');
    if (start) start.disabled = selectedFiles.length === 0;
    renderImportPreview();
    setStatus(selectedFiles.length ? 'Ready to import.' : 'Choose photos to import.');
    setProgress(0);
}

function destinationPanels() {
    const mode = modal.querySelector('#import-destination-mode')?.value || 'date_shoot';
    for (const panel of modal.querySelectorAll('[data-import-mode-panel]')) {
        panel.hidden = panel.dataset.importModePanel !== mode;
    }
}

function renderOptions() {
    if (!modal || !importOptions) return;
    const root = modal.querySelector('#import-root');
    const date = modal.querySelector('#import-shoot-date');
    const preset = modal.querySelector('#import-preset-path');
    if (root && importOptions.import_root && !root.value) root.value = importOptions.import_root;
    if (date && importOptions.today && !date.value) date.value = importOptions.today;
    if (preset) {
        preset.innerHTML = (importOptions.presets || []).map((item) => (
            `<option value="${esc(item.path || '')}">${esc(item.label || item.path || 'Destination')}</option>`
        )).join('');
    }
    destinationPanels();
}

function timeLabel(value) {
    const raw = Number(value || 0);
    if (!raw) return 'Unknown time';
    const date = new Date(raw * 1000);
    if (Number.isNaN(date.getTime())) return 'Unknown time';
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function renderImportHistory() {
    const host = modal?.querySelector('#import-history-list');
    if (!host) return;
    if (recentImports == null) {
        host.innerHTML = '<div class="import-history-empty">Loading past imports…</div>';
        return;
    }
    if (!recentImports.length) {
        host.innerHTML = '<div class="import-history-empty">No past imports yet.</div>';
        return;
    }
    host.innerHTML = recentImports.map((batch) => {
        const id = Number(batch.id || 0);
        const imported = Number(batch.imported_files || 0);
        const total = Number(batch.total_files || 0);
        const skipped = Number(batch.skipped_files || 0);
        const collisions = Number(batch.collision_count || 0);
        const cancelled = batch.status === 'cancelled';
        const counts = cancelled
            ? `Cancelled - ${fmt(imported)} of ${fmt(total)} imported${skipped ? ` · ${fmt(skipped)} skipped` : ''}`
            : `${fmt(imported)} imported${total ? ` / ${fmt(total)} files` : ''}${skipped ? ` · ${fmt(skipped)} skipped` : ''}${collisions ? ` · ${fmt(collisions)} renamed` : ''}`;
        const label = batch.name || `Import ${id}`;
        return `<button class="import-history-row" data-import-batch="${id}" data-tip="Scope the grid to this import batch">`
            + `<span><b>${esc(label)}</b><small>${esc(timeLabel(batch.started_at || batch.created_at))}${cancelled ? '' : ` · ${esc(batch.status || 'complete')}`}</small></span>`
            + `<em>${esc(counts)}</em></button>`;
    }).join('');
    for (const row of host.querySelectorAll('[data-import-batch]')) {
        row.addEventListener('click', () => {
            const id = row.dataset.importBatch;
            const label = row.querySelector('b')?.textContent || `Import ${id}`;
            setScope({
                import_batch: String(id),
                importBatchLabel: label,
                sort: 'date_taken',
            });
            closeImport();
        });
    }
}

async function loadImportHistory() {
    recentImports = null;
    renderImportHistory();
    const data = await listImports(12);
    recentImports = (data && data.imports) || [];
    renderImportHistory();
}

async function loadOptions() {
    if (optionsLoaded) return;
    const data = await getImportOptions();
    if (!data) {
        setStatus('Import options unavailable.', 'error');
        return;
    }
    importOptions = data;
    optionsLoaded = true;
    renderOptions();
}

function appendFields(formData) {
    formData.set('destination_mode', modal.querySelector('#import-destination-mode')?.value || 'date_shoot');
    formData.set('import_root', modal.querySelector('#import-root')?.value || '');
    formData.set('manual_destination', modal.querySelector('#import-manual-destination')?.value || '');
    formData.set('preset_path', modal.querySelector('#import-preset-path')?.value || '');
    formData.set('shoot_date', modal.querySelector('#import-shoot-date')?.value || '');
    formData.set('shoot_name', modal.querySelector('#import-shoot-name')?.value || '');
    formData.set('preserve_structure', modal.querySelector('#import-preserve-structure')?.checked ? 'true' : 'false');
}

function upload(formData) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        currentUpload = xhr;
        xhr.open('POST', '/api/imports');
        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) {
                setStatus('Uploading…');
                return;
            }
            const percent = Math.round((event.loaded / event.total) * 100);
            setProgress(percent);
            setStatus(`Uploading… ${percent}%`);
        };
        xhr.onload = () => {
            if (currentUpload === xhr) currentUpload = null;
            let data = {};
            try {
                data = JSON.parse(xhr.responseText || '{}');
            } catch {}
            if (xhr.status < 200 || xhr.status >= 300 || data.error || data.ok === false) {
                reject(new Error(data.error || 'Import failed'));
                return;
            }
            resolve(data);
        };
        xhr.onerror = () => {
            if (currentUpload === xhr) currentUpload = null;
            reject(new Error('Import failed'));
        };
        xhr.onabort = () => {
            if (currentUpload === xhr) currentUpload = null;
            reject(new DOMException('Import cancelled', 'AbortError'));
        };
        xhr.send(formData);
    });
}

async function startImport() {
    if (!selectedFiles.length) {
        setStatus('Choose photos before importing.', 'error');
        return;
    }
    const start = modal.querySelector('#import-start');
    if (start) start.disabled = true;
    const formData = new FormData();
    appendFields(formData);
    for (const item of selectedFiles) {
        formData.append('files', item.file, item.file.name);
        formData.append('relative_paths', item.relativePath || item.file.name);
    }
    try {
        const data = await upload(formData);
        setProgress(100);
        const imported = Number(data.imported_files || 0);
        showToast(`Imported ${fmt(imported)} photo${imported === 1 ? '' : 's'}`);
        closeImport();
        emit('import:changed', data);
        document.dispatchEvent(new CustomEvent('azimuth:import-complete', { detail: data }));
        if (data.batch_id) {
            setScope({
                import_batch: String(data.batch_id),
                importBatchLabel: `Import ${data.batch_id}`,
                sort: 'date_taken',
            });
        }
    } catch (error) {
        if (error && error.name === 'AbortError') {
            showToast('Import cancelled');
            return;
        }
        setStatus(`Couldn’t import: ${error.message}`, 'error');
        showToast('Couldn’t import');
        if (start) start.disabled = false;
    }
}

function modalHtml() {
    return '<div id="import-modal" class="modal-card" role="dialog" aria-modal="true" aria-label="Import photos" tabindex="-1">'
        + `<div class="mo-head"><h2>Import</h2><button class="icon-btn" id="import-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
        + '<div class="mo-body">'
        + '<div id="import-drop-zone" class="import-drop" tabindex="0"><b>Drop photos or folders</b><span id="import-selection-summary">No photos selected</span></div>'
        + '<div class="import-actions"><button class="btn" id="import-files">Choose files</button><button class="btn" id="import-folder">Choose folder</button></div>'
        + '<input id="import-file-input" type="file" multiple accept="image/*,.dng,.cr3,.cr2,.arw,.nef,.orf,.raf,.rw2,.tif,.tiff,.webp" hidden><input id="import-folder-input" type="file" webkitdirectory directory multiple hidden>'
        + '<div id="import-preview-grid" class="import-preview-grid" aria-label="Selected photo previews" hidden></div>'
        + '<div class="import-grid">'
        + '<label>Shoot name<input id="import-shoot-name" autocomplete="off"></label>'
        + '<label>Date<input id="import-shoot-date" type="date"></label>'
        + '<label>Destination<select id="import-destination-mode"><option value="date_shoot">Date + shoot</option><option value="preset">Preset</option><option value="manual">Manual</option></select></label>'
        + '<label>Root<input id="import-root" autocomplete="off"></label>'
        + '<label data-import-mode-panel="preset">Preset path<select id="import-preset-path"></select></label>'
        + '<label data-import-mode-panel="manual">Manual path<input id="import-manual-destination" autocomplete="off"></label>'
        + '<label class="check"><input id="import-preserve-structure" type="checkbox" checked> Preserve folder structure</label>'
        + '</div>'
        + '<div id="import-progress"><i></i></div><div id="import-status" data-tone="muted">Choose photos to import.</div>'
        + '<div class="mo-actions"><button class="btn primary" id="import-start" disabled>Import</button></div>'
        + '<section class="import-history"><div class="import-history-head"><b>Past imports</b><span data-tip="Click a batch to show only those imported photos in the grid.">Scope grid</span></div><div id="import-history-list"></div></section>'
        + '</div></div>';
}

function ensureModal() {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'import-scrim';
    modal.className = 'modal-scrim';
    modal.hidden = true;
    document.body.appendChild(modal);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeImport();
    });
    modal.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeImport();
        }
    });
    return modal;
}

function bindModal() {
    modal.querySelector('#import-close').addEventListener('click', closeImport);
    modal.querySelector('#import-files').addEventListener('click', () => modal.querySelector('#import-file-input').click());
    modal.querySelector('#import-folder').addEventListener('click', () => modal.querySelector('#import-folder-input').click());
    modal.querySelector('#import-file-input').addEventListener('change', (event) => setSelectedFiles(fileEntries(event.target.files)));
    modal.querySelector('#import-folder-input').addEventListener('change', (event) => setSelectedFiles(fileEntries(event.target.files)));
    modal.querySelector('#import-destination-mode').addEventListener('change', destinationPanels);
    modal.querySelector('#import-start').addEventListener('click', startImport);
    const zone = modal.querySelector('#import-drop-zone');
    for (const eventName of ['dragenter', 'dragover']) {
        zone.addEventListener(eventName, (event) => {
            event.preventDefault();
            zone.classList.add('is-dragging');
        });
    }
    for (const eventName of ['dragleave', 'drop']) {
        zone.addEventListener(eventName, () => zone.classList.remove('is-dragging'));
    }
    zone.addEventListener('drop', async (event) => {
        event.preventDefault();
        setSelectedFiles(await droppedFiles(event.dataTransfer));
    });
    zone.addEventListener('click', () => modal.querySelector('#import-file-input').click());
    zone.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        modal.querySelector('#import-file-input').click();
    });
}

export function openImport() {
    ensureModal();
    selectedFiles = [];
    modal.innerHTML = modalHtml();
    modal.hidden = false;
    bindModal();
    setSelectedFiles([]);
    renderOptions();
    loadOptions();
    loadImportHistory();
    trapFocus(modal, modal.querySelector('#import-drop-zone'));
}

export function closeImport() {
    if (!modal || modal.hidden) return;
    if (currentUpload) currentUpload.abort();
    clearImportPreviewUrls();
    modal.hidden = true;
    releaseFocus(modal);
}

export function importOpen() {
    return Boolean(modal && !modal.hidden);
}

export function initImporter() {
    ensureModal();
    document.getElementById('import-view')?.addEventListener('click', openImport);
    on('import:open', openImport);
    emit('import:ready');
}
