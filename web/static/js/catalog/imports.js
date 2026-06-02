function responseDataOrError(response, fallbackMessage) {
    return response.json().catch(() => ({})).then((data) => {
        if (!response.ok || data.error || data.ok === false) {
            throw new Error(data.error || fallbackMessage);
        }
        return data;
    });
}


function formatCount(count) {
    return Number(count || 0).toLocaleString();
}


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
        for (const child of entries) {
            files.push(...await traverseEntry(child, `${path}${entry.name}/`));
        }
    }
    return files;
}


async function droppedFiles(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    if (!items.length) {
        return Array.from(dataTransfer?.files || []).map(file => ({ file, relativePath: file.name }));
    }

    const entries = items
        .map(item => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
        .filter(Boolean);
    if (!entries.length) {
        return Array.from(dataTransfer?.files || []).map(file => ({ file, relativePath: file.name }));
    }

    const files = [];
    for (const entry of entries) {
        files.push(...await traverseEntry(entry));
    }
    return files;
}


function fileListEntries(fileList) {
    return Array.from(fileList || []).map(file => ({
        file,
        relativePath: file.webkitRelativePath || file.name,
    }));
}


export function createCatalogImportController({
    documentImpl = document,
    windowImpl = window,
    setSettingsStatus,
    showToast,
    refreshSettingsMeta = async () => {},
} = {}) {
    let selectedFiles = [];
    let optionsLoaded = false;

    const el = id => documentImpl.getElementById(id);

    function setImportStatus(message, tone = 'muted') {
        const status = el('import-status');
        if (status) {
            status.textContent = message;
            status.dataset.tone = tone;
        }
    }

    function updateSelectionSummary() {
        const summary = el('import-selection-summary');
        const button = el('import-start-btn');
        if (summary) {
            summary.textContent = selectedFiles.length
                ? `${formatCount(selectedFiles.length)} item${selectedFiles.length === 1 ? '' : 's'} ready`
                : 'No photos selected';
        }
        if (button) button.disabled = selectedFiles.length === 0;
    }

    function setSelectedFiles(files) {
        selectedFiles = (files || []).filter(item => item?.file);
        updateSelectionSummary();
        setImportStatus(selectedFiles.length ? 'Ready to import.' : 'Choose photos to import.', 'muted');
    }

    function renderOptions(data = {}) {
        const root = el('import_root');
        const date = el('import-shoot-date');
        const preset = el('import-preset-path');
        if (root && data.import_root && !root.value) root.value = data.import_root;
        if (date && data.today && !date.value) date.value = data.today;
        if (preset) {
            preset.innerHTML = '';
            for (const item of data.presets || []) {
                const option = documentImpl.createElement('option');
                option.value = item.path || '';
                option.textContent = item.label || item.path || 'Destination';
                preset.appendChild(option);
            }
        }
        optionsLoaded = true;
        updateDestinationMode();
    }

    async function loadImportOptions() {
        try {
            const response = await fetch('/api/imports/options');
            renderOptions(await responseDataOrError(response, 'Import options unavailable'));
        } catch (error) {
            setImportStatus(`Import options unavailable: ${error.message}`, 'error');
        }
    }

    function updateDestinationMode() {
        const mode = el('import-destination-mode')?.value || 'date_shoot';
        documentImpl.querySelectorAll('[data-import-mode-panel]').forEach(panel => {
            panel.classList.toggle('hidden', panel.dataset.importModePanel !== mode);
        });
    }

    function selectFiles() {
        el('import-file-input')?.click();
    }

    function selectFolder() {
        el('import-folder-input')?.click();
    }

    function appendFields(formData) {
        formData.set('destination_mode', el('import-destination-mode')?.value || 'date_shoot');
        formData.set('import_root', el('import_root')?.value || '');
        formData.set('manual_destination', el('import-manual-destination')?.value || '');
        formData.set('preset_path', el('import-preset-path')?.value || '');
        formData.set('shoot_date', el('import-shoot-date')?.value || '');
        formData.set('shoot_name', el('import-shoot-name')?.value || '');
        formData.set('preserve_structure', el('import-preserve-structure')?.checked ? 'true' : 'false');
    }

    function uploadWithProgress(formData) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/imports');
            xhr.upload.onprogress = (event) => {
                if (!event.lengthComputable) {
                    setImportStatus('Uploading to Omarchy...', 'muted');
                    return;
                }
                const percent = Math.max(0, Math.min(100, Math.round((event.loaded / event.total) * 100)));
                setImportStatus(`Uploading to Omarchy... ${percent}%`, 'muted');
            };
            xhr.onload = () => {
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
            xhr.onerror = () => reject(new Error('Import failed'));
            xhr.send(formData);
        });
    }

    async function startImport() {
        if (!selectedFiles.length) {
            setImportStatus('Choose photos before importing.', 'error');
            return;
        }
        const button = el('import-start-btn');
        if (button) button.disabled = true;
        const formData = new FormData();
        appendFields(formData);
        for (const item of selectedFiles) {
            formData.append('files', item.file, item.file.name);
            formData.append('relative_paths', item.relativePath || item.file.name);
        }

        try {
            setImportStatus('Uploading to Omarchy...', 'muted');
            const data = await uploadWithProgress(formData);
            setImportStatus(`Imported ${formatCount(data.imported_files)} photo${data.imported_files === 1 ? '' : 's'}.`, 'success');
            await refreshSettingsMeta();
            windowImpl.location.href = data.library_url || `/library?import_batch=${data.batch_id}`;
        } catch (error) {
            setImportStatus(`Import failed: ${error.message}`, 'error');
            showToast?.('Import failed');
            if (button) button.disabled = false;
        }
    }

    function bindDropZone() {
        const zone = el('import-drop-zone');
        if (!zone || zone.dataset.paImportBound === '1') return;
        zone.dataset.paImportBound = '1';
        ['dragenter', 'dragover'].forEach(eventName => {
            zone.addEventListener(eventName, (event) => {
                event.preventDefault();
                zone.classList.add('is-dragging');
            });
        });
        ['dragleave', 'drop'].forEach(eventName => {
            zone.addEventListener(eventName, () => zone.classList.remove('is-dragging'));
        });
        zone.addEventListener('drop', async (event) => {
            event.preventDefault();
            setSelectedFiles(await droppedFiles(event.dataTransfer));
        });
        zone.addEventListener('click', selectFiles);
    }

    function bindInputs() {
        el('import-file-input')?.addEventListener('change', (event) => {
            setSelectedFiles(fileListEntries(event.target.files));
        });
        el('import-folder-input')?.addEventListener('change', (event) => {
            setSelectedFiles(fileListEntries(event.target.files));
        });
        el('import-destination-mode')?.addEventListener('change', updateDestinationMode);
    }

    function initImportPanel() {
        bindDropZone();
        bindInputs();
        updateSelectionSummary();
        if (!optionsLoaded) loadImportOptions();
    }

    return {
        initImportPanel,
        loadImportOptions,
        selectFiles,
        selectFolder,
        startImport,
        updateDestinationMode,
    };
}
