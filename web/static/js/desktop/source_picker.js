import { browseCatalogFolders } from './api.js';
import { icon } from '../icons.js';
import { escapeHtml as esc } from '../lib.js';

const state = {
    open: false,
    loading: false,
    path: '',
    selectedPath: '',
    data: null,
    error: '',
    addError: '',
    manualOpen: false,
};

let requestId = 0;
let boundRoot = null;
let boundCallbacks = {};

function pathLabel(path) {
    const clean = String(path || '').replace(/\/+$/, '');
    return clean.split('/').filter(Boolean).pop() || clean || '/';
}

function renderRoots(roots = []) {
    if (!roots.length) return '';
    return '<div class="source-picker-roots" aria-label="Quick folders">'
        + roots.map((root) => `<button type="button" data-source-picker-path="${esc(root.path)}">${esc(root.label || pathLabel(root.path))}</button>`).join('')
        + '</div>';
}

function renderEntries(data) {
    if (state.loading) {
        return '<div class="source-picker-status" aria-live="polite">Loading folders...</div>';
    }
    if (state.error) {
        return `<div class="source-picker-status error" role="status">${esc(state.error)}</div>`;
    }
    if (!data) {
        return '<div class="source-picker-status">Choose a quick folder to start browsing.</div>';
    }
    if (data.error) {
        return `<div class="source-picker-status error" role="status">${esc(data.error)}</div>`;
    }
    const entries = (Array.isArray(data.entries) ? data.entries : [])
        .filter((entry) => !String(entry.name || '').startsWith('.'));
    if (!entries.length) {
        return '<div class="source-picker-status">No readable subfolders here.</div>';
    }
    return '<div class="source-picker-list" role="list">'
        + entries.map((entry) => {
            const disabled = entry.readable ? '' : ' disabled aria-disabled="true"';
            const note = entry.readable ? '' : '<span class="source-picker-note">Unreadable</span>';
            return `<button type="button" role="listitem" data-source-picker-path="${esc(entry.path)}"${disabled}>`
                + `<span>${icon('folder-open')}<b>${esc(entry.name || pathLabel(entry.path))}</b></span>${note}</button>`;
        }).join('')
        + '</div>';
}

function renderBrowser() {
    if (!state.open) return '';
    const data = state.data || {};
    const current = data.path || state.path || '';
    const parent = data.parent || '';
    const canUse = Boolean(data.exists && data.is_dir && data.readable && current);
    const canGoUp = Boolean(parent && parent !== current);
    return '<div class="source-picker-browser" id="source-picker-browser">'
        + renderRoots(data.roots || [])
        + '<div class="source-picker-current">'
        + `<button class="mini-btn" type="button" data-source-picker-up ${canGoUp ? '' : 'disabled'}>${icon('chevron-left')} Up</button>`
        + `<code title="${esc(current)}">${esc(current || 'Choose a folder')}</code>`
        + '</div>'
        + renderEntries(data)
        + '<div class="source-picker-browser-actions">'
        + `<button class="btn primary" type="button" data-source-picker-use ${canUse ? '' : 'disabled'}>Use this folder</button>`
        + '<span>Cataloging reads this folder in place. Original photo files are never moved or changed.</span>'
        + '</div>'
        + '</div>';
}

export function renderSourceAddUi() {
    const selected = state.selectedPath;
    const selectedCopy = selected
        ? `<div class="source-selected"><span>Selected folder</span><code title="${esc(selected)}">${esc(selected)}</code></div>`
        : '<div class="source-selected empty"><span>No folder selected yet</span></div>';
    const expanded = state.open ? 'true' : 'false';
    return '<form class="source-add-ui" id="add-source-form">'
        + '<input type="hidden" name="path" value="' + esc(selected) + '">'
        + '<p class="source-add-copy">Choose a photo folder on the computer running Azimuth Photo. Originals stay where they are and are never moved or changed.</p>'
        + selectedCopy
        + '<div class="source-add-actions">'
        + `<button class="btn primary" type="button" data-source-picker-toggle aria-expanded="${expanded}" aria-controls="source-picker-browser">${icon('folder-plus')} Choose photo folder</button>`
        + `<button class="btn" type="submit" ${selected ? '' : 'disabled'}>Add & scan</button>`
        + '</div>'
        + renderBrowser()
        + (state.addError ? `<div class="source-add-error" role="alert">${esc(state.addError)}</div>` : '')
        + `<details class="source-manual"${state.manualOpen ? ' open' : ''}><summary>Enter a path manually</summary>`
        + `<label><span>Exact server path</span><input class="drawer-input" name="manual_path" value="${esc(selected)}" placeholder="/path/to/photos" autocomplete="off"></label>`
        + '<p>The path must exist on the computer running Azimuth Photo.</p></details>'
        + '</form>';
}

function renderBound() {
    if (!boundRoot) return;
    const host = boundRoot.querySelector('#add-source-form');
    if (!host) return;
    host.outerHTML = renderSourceAddUi();
    bindSourcePicker(boundRoot, boundCallbacks);
}

async function loadPath(path = '') {
    const token = ++requestId;
    state.open = true;
    state.loading = true;
    state.error = '';
    state.path = path;
    renderBound();
    try {
        const data = await browseCatalogFolders(path);
        if (token !== requestId) return;
        state.data = data;
        state.path = data?.path || path;
        state.error = '';
    } catch {
        if (token !== requestId) return;
        state.error = 'Could not load folders from the computer running Azimuth Photo. Check the connection and try again.';
    } finally {
        if (token !== requestId) return;
        state.loading = false;
        renderBound();
    }
}

function chooseCurrent() {
    const current = state.data?.path || state.path || '';
    if (!current) return;
    state.selectedPath = current;
    state.open = false;
    state.addError = '';
    renderBound();
    requestAnimationFrame(() => boundRoot?.querySelector('#add-source-form button[type="submit"]')?.focus());
}

export function clearSourcePickerSelection() {
    state.selectedPath = '';
    state.addError = '';
    state.open = false;
    renderBound();
}

export function setSourceAddError(message) {
    state.addError = message || '';
    renderBound();
}

export function bindSourcePicker(root, callbacks = {}) {
    boundRoot = root;
    boundCallbacks = callbacks;
    const form = root.querySelector('#add-source-form');
    if (!form) return;
    form.querySelector('[data-source-picker-toggle]')?.addEventListener('click', () => {
        state.open = !state.open;
        state.addError = '';
        if (state.open && !state.data && !state.loading) loadPath(state.selectedPath || state.path || '');
        else renderBound();
    });
    form.querySelector('[data-source-picker-up]')?.addEventListener('click', () => {
        const parent = state.data?.parent;
        if (parent) loadPath(parent);
    });
    for (const button of form.querySelectorAll('[data-source-picker-path]')) {
        button.addEventListener('click', () => {
            if (button.disabled) return;
            loadPath(button.dataset.sourcePickerPath || '');
        });
    }
    form.querySelector('[data-source-picker-use]')?.addEventListener('click', chooseCurrent);
    form.querySelector('.source-manual')?.addEventListener('toggle', (event) => {
        state.manualOpen = event.currentTarget.open;
    });
    form.querySelector('input[name="manual_path"]')?.addEventListener('input', (event) => {
        state.selectedPath = event.currentTarget.value.trim();
        state.addError = '';
        const selected = state.selectedPath;
        const hidden = form.querySelector('input[name="path"]');
        const submit = form.querySelector('button[type="submit"]');
        const selectedBox = form.querySelector('.source-selected');
        if (hidden) hidden.value = selected;
        if (submit) submit.disabled = !selected;
        if (selectedBox) {
            selectedBox.classList.toggle('empty', !selected);
            selectedBox.innerHTML = selected
                ? `<span>Selected folder</span><code title="${esc(selected)}">${esc(selected)}</code>`
                : '<span>No folder selected yet</span>';
        }
        form.querySelector('.source-add-error')?.remove();
    });
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const path = state.selectedPath.trim();
        if (!path) return;
        callbacks.onSubmit?.({ path, form });
    });
}
