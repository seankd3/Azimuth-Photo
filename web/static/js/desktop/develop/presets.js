/**
 * Develop Presets panel — left slim browser with folders, hover live-preview,
 * click-to-apply, save-current, rename/delete.
 *
 * Mounted from develop.js (see PATCH). Expects a host API:
 *   mountPresetsPanel(hostEl, {
 *     getRenderer, getEntry, getMeta, applyPresetSettings, saveCurrentSettings
 *   })
 */
const API = '/api/develop/presets';

function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
}

function escapeHtml(text) {
    return String(text ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

function groupByFolder(presets) {
    const groups = new Map();
    for (const preset of presets) {
        const folder = preset.folder || 'User';
        if (!groups.has(folder)) groups.set(folder, []);
        groups.get(folder).push(preset);
    }
    return [...groups.entries()]
        .map(([folder, items]) => [folder, items.sort((a, b) => shortPresetName(a).localeCompare(shortPresetName(b), undefined, { sensitivity: 'base' }))])
        .sort((a, b) => shortFolderName(a[0]).localeCompare(shortFolderName(b[0]), undefined, { sensitivity: 'base' }));
}

function shortFolderName(folder) {
    const leaf = String(folder || 'User').split(/[\\/]/).filter(Boolean).pop() || 'User';
    return leaf
        .replace(/\([^)]*\)/g, '')
        .replace(/lightroom\s+presets?/ig, '')
        .replace(/[–—-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim() || 'User';
}

function shortPresetName(preset) {
    const name = String(preset?.name || 'Untitled').trim();
    const folder = String(preset?.folder || '').trim();
    const leaf = name.split(/[\\/]/).filter(Boolean).pop()?.trim() || name;
    if (leaf !== name) return leaf;
    if (folder && name.toLocaleLowerCase().startsWith(folder.toLocaleLowerCase())) {
        return name.slice(folder.length).replace(/^[\\/\s–—-]+/, '') || name;
    }
    return name;
}

export function mountPresetsPanel(host, api) {
    if (!host || host.dataset.presetsMounted === '1') return host?.__presetsController || null;

    const root = document.createElement('aside');
    root.id = 'develop-presets';
    root.setAttribute('aria-label', 'Develop presets');
    root.innerHTML = [
        '<header class="develop-presets-head">',
        '  <strong>Presets</strong>',
        '  <button type="button" data-preset-save data-tip="Save current settings as a preset">Save</button>',
        '</header>',
        '<div class="develop-presets-body">',
        '  <label class="develop-preset-search"><span class="sr-only">Search presets</span><input type="search" data-preset-search placeholder="Search presets" autocomplete="off"></label>',
        '  <div data-preset-list></div>',
        '</div>',
        '<div class="develop-presets-empty" data-preset-empty hidden>No presets yet. Save the look you like.</div>',
    ].join('');

    // Left of the develop layout — insert before workspace when possible.
    const layout = host.closest('.develop-layout') || host;
    if (layout.classList?.contains('develop-layout')) {
        layout.insertBefore(root, layout.firstElementChild);
    } else {
        host.prepend(root);
    }

    const listEl = root.querySelector('[data-preset-list]');
    const emptyEl = root.querySelector('[data-preset-empty]');
    const searchEl = root.querySelector('[data-preset-search]');
    let presets = [];
    let activePresetId = null;
    let filterQuery = '';
    const collapsedFolders = new Set();
    let previewing = false;
    let previewBaseline = null;
    let activePopover = null;
    let loadToken = 0;

    function closePopover() {
        activePopover?.remove();
        activePopover = null;
    }

    function currentEntry() {
        return api.getEntry?.() || null;
    }

    function renderer() {
        return api.getRenderer?.() || null;
    }

    function endPreview() {
        if (!previewing) return;
        previewing = false;
        const gl = renderer();
        const entry = currentEntry();
        if (gl && entry && previewBaseline) {
            gl.setSettings(previewBaseline, entry.meta, { preview: true });
        }
        previewBaseline = null;
    }

    function startPreview(preset) {
        const gl = renderer();
        const entry = currentEntry();
        if (!gl || !entry || !preset?.settings) return;
        if (!previewing) {
            previewBaseline = clone(entry.settings);
            previewing = true;
        }
        const merged = { ...previewBaseline, ...preset.settings };
        gl.setSettings(merged, entry.meta, { preview: true });
    }

    function applyPreset(preset) {
        endPreview();
        if (!preset?.settings) return;
        activePresetId = Number(preset.id);
        render();
        api.applyPresetSettings?.(clone(preset.settings), `Preset: ${preset.name}`);
    }

    async function load() {
        const token = ++loadToken;
        try {
            const response = await fetch(API, { headers: { Accept: 'application/json' } });
            if (!response.ok) throw new Error('list failed');
            const payload = await response.json();
            if (token !== loadToken) return;
            presets = Array.isArray(payload.presets) ? payload.presets : [];
            render();
        } catch {
            if (token !== loadToken) return;
            presets = [];
            render();
        }
    }

    function render() {
        closePopover();
        emptyEl.hidden = presets.length > 0;
        if (!presets.length) {
            listEl.innerHTML = '';
            return;
        }
        const query = filterQuery.trim().toLocaleLowerCase();
        const groups = groupByFolder(presets).map(([folder, items]) => [
            folder,
            items.filter((preset) => !query || `${folder} ${preset.name}`.toLocaleLowerCase().includes(query)),
        ]).filter(([, items]) => items.length);
        if (!groups.length) {
            listEl.innerHTML = '<p class="develop-presets-no-results">No matching presets.</p>';
            return;
        }
        listEl.innerHTML = groups.map(([folder, items]) => {
            const rows = items.map((preset) => (
                `<div class="develop-preset-row ${Number(preset.id) === activePresetId ? 'active' : ''}" data-preset-id="${preset.id}" draggable="false">`
                + `<button type="button" class="develop-preset-name" data-preset-apply data-tip="Apply ${escapeHtml(preset.name)}"${Number(preset.id) === activePresetId ? ' aria-current="true"' : ''}>${escapeHtml(shortPresetName(preset))}</button>`
                + `<button type="button" class="develop-preset-edit" data-preset-rename data-tip="Rename">✎</button>`
                + `<button type="button" class="develop-preset-edit" data-preset-delete data-tip="Delete">×</button>`
                + `</div>`
            )).join('');
            return `<details class="develop-preset-folder"${collapsedFolders.has(folder) && !query ? '' : ' open'}>`
                + `<summary data-folder="${escapeHtml(folder)}" data-tip="${escapeHtml(folder)}"><b>${escapeHtml(shortFolderName(folder))}</b><span>${items.length}</span></summary>`
                + `<div class="develop-preset-folder-body">${rows}</div></details>`;
        }).join('');
        listEl.querySelectorAll('.develop-preset-folder').forEach((details) => {
            details.addEventListener('toggle', () => {
                const folder = details.querySelector('summary')?.dataset.folder;
                if (!folder) return;
                if (details.open) collapsedFolders.delete(folder);
                else collapsedFolders.add(folder);
            });
        });
    }

    async function saveCurrent() {
        const entry = currentEntry();
        if (!entry) return;
        closePopover();
        activePopover = document.createElement('div');
        activePopover.className = 'develop-presets-popover';
        activePopover.innerHTML = [
            '<strong>Save preset</strong>',
            '<label>Name<input data-save-name type="text" maxlength="200" placeholder="My look" autofocus></label>',
            '<label>Folder<input data-save-folder type="text" maxlength="200" placeholder="User" value="User"></label>',
            '<div class="develop-presets-popover-actions">',
            '  <button type="button" data-save-cancel>Cancel</button>',
            '  <button type="button" class="primary" data-save-confirm>Save</button>',
            '</div>',
        ].join('');
        root.appendChild(activePopover);
        const nameInput = activePopover.querySelector('[data-save-name]');
        nameInput?.focus();
        activePopover.querySelector('[data-save-cancel]').addEventListener('click', closePopover);
        activePopover.querySelector('[data-save-confirm]').addEventListener('click', async () => {
            const name = String(nameInput.value || '').trim() || 'Untitled';
            const folder = String(activePopover.querySelector('[data-save-folder]').value || '').trim() || 'User';
            const settings = api.saveCurrentSettings?.() || clone(entry.settings);
            closePopover();
            try {
                const response = await fetch(API, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({ name, folder, settings }),
                });
                if (!response.ok) throw new Error('save failed');
                await load();
            } catch { /* toast owned by host if desired */ }
        });
    }

    async function renamePreset(preset) {
        const row = listEl.querySelector(`[data-preset-id="${preset.id}"]`);
        if (!row) return;
        const nameBtn = row.querySelector('[data-preset-apply]');
        const input = document.createElement('input');
        input.className = 'develop-preset-rename-input';
        input.value = preset.name;
        input.maxLength = 200;
        nameBtn.replaceWith(input);
        input.focus();
        input.select();
        const finish = async (commit) => {
            const next = String(input.value || '').trim();
            if (commit && next && next !== preset.name) {
                try {
                    await fetch(`${API}/${preset.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: next }),
                    });
                } catch { /* keep list */ }
            }
            await load();
        };
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') { event.preventDefault(); finish(true); }
            if (event.key === 'Escape') { event.preventDefault(); finish(false); }
        });
        input.addEventListener('blur', () => finish(true));
    }

    async function deletePreset(preset) {
        try {
            await fetch(`${API}/${preset.id}`, { method: 'DELETE' });
        } catch { /* ignore */ }
        endPreview();
        await load();
    }

    root.querySelector('[data-preset-save]').addEventListener('click', saveCurrent);
    searchEl.addEventListener('input', () => {
        filterQuery = searchEl.value;
        render();
    });

    listEl.addEventListener('pointerover', (event) => {
        const row = event.target.closest('[data-preset-id]');
        if (!row || event.target.closest('[data-preset-rename], [data-preset-delete]')) return;
        const preset = presets.find((item) => Number(item.id) === Number(row.dataset.presetId));
        if (preset) startPreview(preset);
    });
    listEl.addEventListener('pointerout', (event) => {
        const row = event.target.closest('[data-preset-id]');
        if (!row) return;
        const next = event.relatedTarget?.closest?.('[data-preset-id]');
        if (next && next === row) return;
        endPreview();
    });
    listEl.addEventListener('click', (event) => {
        const row = event.target.closest('[data-preset-id]');
        if (!row) return;
        const preset = presets.find((item) => Number(item.id) === Number(row.dataset.presetId));
        if (!preset) return;
        if (event.target.closest('[data-preset-delete]')) {
            event.preventDefault();
            deletePreset(preset);
            return;
        }
        if (event.target.closest('[data-preset-rename]')) {
            event.preventDefault();
            renamePreset(preset);
            return;
        }
        if (event.target.closest('[data-preset-apply]')) {
            applyPreset(preset);
        }
    });

    document.addEventListener('pointerdown', (event) => {
        if (activePopover && !activePopover.contains(event.target) && !event.target.closest('[data-preset-save]')) {
            closePopover();
        }
    });

    host.dataset.presetsMounted = '1';
    const controller = { reload: load, root, endPreview };
    host.__presetsController = controller;
    load();
    return controller;
}
