import { getFolderTree, revealFolder, rescanCatalogSource } from './api.js';
import { exportScope, openExportMenu } from './export_menu.js';
import { emit, folderActive, folderValues, navigateToScope, on, scope, scopeParams } from './state.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { fileManagerMenuLabel } from './file_manager.js';
import { openSourceAddFlow } from './drawer.js';
import { initLibraryManage } from './library_manage.js';
import { isSourceQuiet, rememberSources, toggleSourceQuiet } from './quiet_sources.js';
import { icon } from '../icons.js';

const EXPANDED_KEY = 'pa_d_folder_expanded';

let sources = [];
let roots = [];
let expanded = readExpanded();
let closeDrawer = () => {};
let menu = null;
let menuReturn = null;
let loading = true;
let loadError = false;
let refreshTimer = 0;
let refreshGeneration = 0;
let filterTimer = 0;
let lastSelectedFolderPath = '';

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const leafName = (path) => String(path || '').split('/').filter(Boolean).pop() || path || 'Folder';

function readExpanded() {
    try {
        const values = JSON.parse(localStorage.getItem(EXPANDED_KEY) || '[]');
        return new Set(Array.isArray(values) ? values.filter(Boolean) : []);
    } catch {
        return new Set();
    }
}

function saveExpanded() {
    localStorage.setItem(EXPANDED_KEY, JSON.stringify([...expanded].slice(-1000)));
}

function expandedByDefault(path, openByDefault = false) {
    return expanded.has(path) || (openByDefault && !localStorage.getItem(EXPANDED_KEY));
}

function setExpanded(path, value) {
    if (!path) return;
    if (value) expanded.add(path);
    else expanded.delete(path);
    saveExpanded();
}

function normalizeSource(source) {
    return {
        ...source,
        id: Number(source?.id || 0),
        path: source?.path || '',
        display_name: source?.display_name || leafName(source?.path),
        online: Boolean(source?.online),
        reveal_available: source?.reveal_available !== false,
        library_namespace: Boolean(source?.library_namespace),
        total_count: Number(source?.total_count || 0),
        folders: Array.isArray(source?.folders) ? source.folders : [],
    };
}

/**
 * The library as one catalog, not a map of where bytes live.
 *
 * A source that is a library namespace (a mirrored hub library) has no place
 * on this machine to stand for: its folders are the library, so they join the
 * top level beside the folders of the sources that do live here.
 */
function libraryRoots(list) {
    const out = [];
    for (const source of list) {
        if (!source.library_namespace) {
            out.push(source);
            continue;
        }
        for (const folder of source.folders) {
            out.push({
                id: 0,
                source_id: source.id,
                path: folder.path,
                display_name: folder.name || leafName(folder.path),
                online: source.online,
                reveal_available: folder.reveal_available !== false,
                total_count: Number(folder.total_count || 0),
                folders: Array.isArray(folder.children) ? folder.children : [],
            });
        }
    }
    return out.sort((a, b) => a.display_name.localeCompare(b.display_name, undefined, { sensitivity: 'base' })
        || a.path.localeCompare(b.path));
}

function nodeMatches(node, query) {
    if (!query) return true;
    const haystack = `${node.name || ''} ${node.path || ''}`.toLowerCase();
    return haystack.includes(query) || (node.children || []).some((child) => nodeMatches(child, query));
}

function applyFolderScope(path, options = {}) {
    if (!path) return;
    navigateToScope({ folder: [path] }, options);
    if (!options.keepOpen) closeDrawer();
}

function toggleFolderScope(path) {
    const current = folderValues();
    const next = current.includes(path)
        ? current.filter((item) => item !== path)
        : [...current, path];
    navigateToScope({ folder: next }, { merge: true });
}

function rowScopePath(row) {
    return row?.dataset?.folderPath || row?.dataset?.folderSourcePath || '';
}

function rowParentContainer(row) {
    if (!row) return null;
    if (row.matches('.folder-source-row')) return row.closest('.folder-source')?.parentElement || null;
    return row.parentElement;
}

function siblingScopeRows(row) {
    const parent = rowParentContainer(row);
    if (!parent) return [];
    return [...parent.querySelectorAll(':scope > .folder-row, :scope > .folder-source > .folder-source-row')];
}

function rangeWithinParent(anchorPath, targetRow) {
    const anchorRow = document.querySelector(`[data-folder-path="${CSS.escape(anchorPath)}"], [data-folder-source-path="${CSS.escape(anchorPath)}"]`);
    if (!anchorRow || !targetRow || rowParentContainer(anchorRow) !== rowParentContainer(targetRow)) return [];
    const rows = siblingScopeRows(targetRow);
    const anchorIndex = rows.indexOf(anchorRow);
    const targetIndex = rows.indexOf(targetRow);
    if (anchorIndex < 0 || targetIndex < 0) return [];
    const [start, end] = anchorIndex < targetIndex ? [anchorIndex, targetIndex] : [targetIndex, anchorIndex];
    return rows.slice(start, end + 1).map(rowScopePath).filter(Boolean);
}

function selectFolderPath(path, row, event) {
    if (!path) return;
    if (event?.shiftKey && lastSelectedFolderPath) {
        const range = rangeWithinParent(lastSelectedFolderPath, row);
        if (range.length) {
            const next = event.ctrlKey || event.metaKey ? [...new Set([...folderValues(), ...range])] : range;
            navigateToScope({ folder: next }, { merge: true });
            return;
        }
    }
    lastSelectedFolderPath = path;
    if (event?.ctrlKey || event?.metaKey) {
        toggleFolderScope(path);
        return;
    }
    applyFolderScope(path);
}

function exportFolderScope(node, anchor) {
    applyFolderScope(node.path, { keepOpen: true });
    openExportMenu(anchor, ({ format, size }) => {
        exportScope({ format, size, count: Number(node.total_count || 0), query: scopeParams() });
    });
}

async function revealFolderPath(path, sourceId) {
    if (!path) return;
    const result = await revealFolder(path, sourceId);
    if (result?.ok && result?.data?.ok) {
        showToast('Opened in file manager');
        return;
    }
    showToast(result?.data?.error || 'Couldn’t open folder');
}

function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'folder-pop-menu';
    menu.className = 'pop-menu grid-pop-menu folder-pop-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeFolderMenu();
        }
    });
    return menu;
}

function positionMenu(anchor) {
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left + 18));
    const top = Math.max(8, Math.min(window.innerHeight - menuRect.height - 8, rect.top + 18));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function openFolderMenu(node, anchor) {
    if (!node || !node.path) return;
    ensureMenu();
    releaseFocus(menu);
    menuReturn = anchor;
    const isSourceRoot = Boolean(node.id) && sources.some((item) => item.path === node.path);
    const quiet = isSourceRoot && isSourceQuiet(node.id);
    menu.innerHTML = '<div class="pm-group">'
        + `<button data-act="scope">${icon('folder-tree')} Show in scope with subfolders</button>`
        + `<button data-act="refine">${icon('zap')} Open in Refine</button>`
        + (isSourceRoot
            ? `<button data-act="quiet">${icon('eye')} ${quiet ? 'Show in library views' : 'Hide from library views'}</button>`
            : '')
        + (isSourceRoot
            ? `<button data-act="rescan"${node.online === false ? ' disabled aria-disabled="true"' : ''}>${icon('refresh-cw')} Rescan</button>`
            : '')
        + (node.reveal_available !== false ? `<button data-act="reveal">${icon('folder-open')} ${esc(fileManagerMenuLabel())}</button>` : '')
        + `<button data-act="export">${icon('download')} Export view…</button>`
        + '</div>';
    menu.hidden = false;
    positionMenu(anchor);
    for (const button of menu.querySelectorAll('[data-act]')) {
        button.setAttribute('role', 'menuitem');
        button.addEventListener('click', () => {
            if (button.disabled) return;
            const action = button.dataset.act;
            closeFolderMenu();
            if (action === 'scope') applyFolderScope(node.path);
            if (action === 'refine') {
                applyFolderScope(node.path);
                emit('refine:open');
            }
            if (action === 'quiet') applyQuietToggle(node.id);
            if (action === 'rescan') startSourceRescan(node.id);
            if (action === 'reveal') revealFolderPath(node.path, node.source_id || node.id);
            if (action === 'export') exportFolderScope(node, anchor);
        });
    }
    trapFocus(menu, menu.querySelector('button:not([disabled])'));
}

function applyQuietToggle(sourceId) {
    const quiet = toggleSourceQuiet(sourceId);
    renderTree();
    emit('quiet:changed');
    emit('scope', scope);
    showToast(quiet ? 'Hidden from library views' : 'Shown in library views');
}

async function startSourceRescan(sourceId) {
    const id = Number(sourceId) || 0;
    if (!id) return;
    const result = await rescanCatalogSource(id);
    if (result && result.ok) showToast('Rescan started');
    else showToast('Couldn’t start rescan');
}

function closeFolderMenu() {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    releaseFocus(menu);
    if (menuReturn && document.contains(menuReturn) && menuReturn.focus) {
        menuReturn.focus({ preventScroll: true });
    }
}

function renderFolderNode(node, level, query = '') {
    if (query && !nodeMatches(node, query)) return '';
    const children = Array.isArray(node.children) ? node.children : [];
    const hasChildren = children.some((child) => nodeMatches(child, query));
    const isOpen = Boolean(query) || expanded.has(node.path);
    const active = folderActive(node.path) ? ' active' : '';
    const label = node.name || leafName(node.path);
    const chevron = hasChildren
        ? `<button class="folder-expander" type="button" data-folder-expand="${esc(node.path)}" aria-label="Toggle ${esc(label)}" aria-expanded="${isOpen ? 'true' : 'false'}">${icon('chevron-right')}</button>`
        : '<span class="folder-expander empty"></span>';
    const row = `<div class="folder-row${active}${isOpen ? ' open' : ''}" role="treeitem" aria-selected="${active ? 'true' : 'false'}" aria-expanded="${hasChildren ? (isOpen ? 'true' : 'false') : 'false'}" data-folder-path="${esc(node.path)}" style="--folder-level:${level}">`
        + `<button class="folder-main" type="button" data-folder-select="${esc(node.path)}" title="${esc(label)}">`
        + `<span class="folder-label" title="${esc(label)}">${esc(label)}</span>`
        + `<span class="folder-count">${fmt(node.total_count)}</span></button>`
        + chevron
        + '</div>'
        + `<div class="folder-children" data-folder-children="${esc(node.path)}"${isOpen ? '' : ' hidden'}>`
        + ((isOpen || query) ? renderFolderChildren(node, level + 1, query) : '')
        + '</div>';
    return row;
}

function renderFolderChildren(node, level, query = '') {
    const children = Array.isArray(node.children) ? node.children : [];
    return children.map((child) => renderFolderNode(child, level, query)).join('');
}

function renderLibraryRoot(source, query = '') {
    const folders = source.folders.filter((folder) => nodeMatches(folder, query));
    const sourceMatches = !query || `${source.display_name} ${source.path}`.toLowerCase().includes(query);
    if (query && !sourceMatches && !folders.length) return '';
    const isOpen = Boolean(query) || expandedByDefault(source.path, true);
    const active = folderActive(source.path) ? ' active' : '';
    const quiet = isSourceQuiet(source.id);
    const countLabel = quiet ? `(${fmt(source.total_count)})` : fmt(source.total_count);
    return `<div class="folder-source" data-folder-source="${esc(source.path)}" data-source-id="${Number(source.id) || 0}">`
        + `<div class="folder-source-row${active}${isOpen ? ' open' : ''}${quiet ? ' is-quiet' : ''}" role="treeitem" aria-selected="${active ? 'true' : 'false'}" aria-expanded="${isOpen ? 'true' : 'false'}" data-folder-source-path="${esc(source.path)}" data-source-id="${Number(source.id) || 0}" title="${esc(source.display_name)}">`
        + `<button class="folder-expander" type="button" data-folder-source-toggle="${esc(source.path)}" aria-label="Toggle ${esc(source.display_name)}" aria-expanded="${isOpen ? 'true' : 'false'}">${icon('chevron-right')}</button>`
        + `<button class="folder-main" type="button" data-folder-select="${esc(source.path)}" title="${esc(source.display_name)}">`
        + `<span class="nr-dot ${source.online ? 'on' : 'off'}"></span>`
        + `<span class="folder-label" title="${esc(source.display_name)}">${esc(source.display_name)}</span>`
        + `<span class="folder-count">${countLabel}</span></button>`
        + '</div>'
        + `<div class="folder-children source-children" data-folder-source-children="${esc(source.path)}"${isOpen ? '' : ' hidden'}>`
        + (isOpen ? folders.map((folder) => renderFolderNode(folder, 0, query)).join('') : '')
        + '</div></div>';
}

function renderTree() {
    const host = document.getElementById('folder-tree');
    if (!host) return;
    const query = (document.getElementById('folder-filter')?.value || '').trim().toLowerCase();
    if (loading) {
        host.innerHTML = Array.from({ length: 4 }, () => '<div class="chrome-skel nav-row skel"></div>').join('');
        return;
    }
    if (loadError) {
        host.innerHTML = '<div class="chrome-empty"><span class="chrome-empty-glyph">'
            + icon('folder')
            + '</span><span>Couldn\'t load folders.</span><button type="button" id="folder-tree-retry">Retry</button></div>';
        host.querySelector('#folder-tree-retry')?.addEventListener('click', () => refreshFoldersPanel());
        return;
    }
    if (!roots.length) {
        host.innerHTML = '<div class="chrome-empty"><span class="chrome-empty-glyph">'
            + icon('folder')
            + '</span><span>No folders yet.</span><button type="button" id="folders-add-source">Add a source</button></div>';
        host.querySelector('#folders-add-source')?.addEventListener('click', () => {
            openSourceAddFlow({ onSuccess: refreshFoldersPanel });
        });
        return;
    }
    const html = roots.map((root) => renderLibraryRoot(root, query)).filter(Boolean).join('');
    host.innerHTML = html || '<div class="chrome-empty"><span class="chrome-empty-glyph">'
        + icon('search')
        + '</span><span>No matching folders.</span></div>';
    bindTreeEvents(host, query);
}

function ensureRenderedChildren(row, query = '') {
    const path = row.dataset.folderPath;
    const container = document.querySelector(`[data-folder-children="${CSS.escape(path)}"]`);
    if (!container || container.dataset.rendered === '1') return;
    const node = findNode(path);
    if (!node) return;
    container.innerHTML = renderFolderChildren(node, Number(row.style.getPropertyValue('--folder-level') || 0) + 1, query);
    container.dataset.rendered = '1';
    bindTreeEvents(container, query);
}

function findNode(path) {
    const stack = roots.flatMap((root) => root.folders);
    while (stack.length) {
        const node = stack.shift();
        if (node.path === path) return node;
        stack.push(...(node.children || []));
    }
    return null;
}

function findScopeNode(path) {
    const root = roots.find((item) => item.path === path);
    if (root) return root;
    return findNode(path);
}

function bindTreeEvents(root, query = '') {
    for (const button of root.querySelectorAll('[data-folder-expand]')) {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const row = button.closest('.folder-row');
            const path = button.dataset.folderExpand;
            const nextOpen = !row.classList.contains('open');
            setExpanded(path, nextOpen);
            row.classList.toggle('open', nextOpen);
            row.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
            button.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
            const container = document.querySelector(`[data-folder-children="${CSS.escape(path)}"]`);
            if (container) {
                if (nextOpen) ensureRenderedChildren(row, query);
                container.hidden = !nextOpen;
            }
        });
    }
    for (const button of root.querySelectorAll('[data-folder-select]')) {
        button.addEventListener('click', (event) => {
            const row = button.closest('.folder-row, .folder-source-row');
            if (event.altKey) {
                event.preventDefault();
                openFolderMenu(findScopeNode(rowScopePath(row)), button);
                return;
            }
            selectFolderPath(rowScopePath(row), row, event);
        });
    }
    for (const row of root.querySelectorAll('[data-folder-path], [data-folder-source-path]')) {
        row.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            openFolderMenu(findScopeNode(rowScopePath(row)), row.querySelector('.folder-main') || row);
        });
    }
    for (const row of root.querySelectorAll('[data-folder-source-toggle]')) {
        row.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const path = row.dataset.folderSourceToggle;
            const sourceRow = row.closest('.folder-source-row');
            const nextOpen = !sourceRow.classList.contains('open');
            setExpanded(path, nextOpen);
            sourceRow.classList.toggle('open', nextOpen);
            sourceRow.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
            row.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
            const container = document.querySelector(`[data-folder-source-children="${CSS.escape(path)}"]`);
            if (!container) return;
            if (nextOpen && !container.innerHTML.trim()) {
                const libraryRoot = roots.find((item) => item.path === path);
                container.innerHTML = (libraryRoot?.folders || []).map((folder) => renderFolderNode(folder, 0)).join('');
                bindTreeEvents(container);
            }
            container.hidden = !nextOpen;
        });
    }
}

function syncActiveRows() {
    for (const row of document.querySelectorAll('[data-folder-path], [data-folder-source-path]')) {
        const path = rowScopePath(row);
        const active = folderActive(path);
        row.classList.toggle('active', active);
        row.setAttribute('aria-selected', active ? 'true' : 'false');
    }
}

export async function refreshFoldersPanel({ toastEmpty = false } = {}) {
    const seq = ++refreshGeneration;
    loading = true;
    loadError = false;
    renderTree();
    try {
        const data = await getFolderTree();
        if (seq !== refreshGeneration) return;
        sources = ((data && data.sources) || []).map(normalizeSource);
        roots = libraryRoots(sources);
        rememberSources(sources);
        if (toastEmpty && !roots.length) showToast('No folder tree yet');
    } catch {
        if (seq !== refreshGeneration) return;
        sources = [];
        roots = [];
        rememberSources([]);
        loadError = true;
    } finally {
        if (seq !== refreshGeneration) return;
        loading = false;
        renderTree();
    }
}

function scheduleFolderRefresh() {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => refreshFoldersPanel(), 300);
}

export async function initFoldersPanel(options = {}) {
    closeDrawer = options.closeDrawer || closeDrawer;
    ensureMenu();
    renderTree();
    initLibraryManage({ onSourcesChanged: () => refreshFoldersPanel() });
    const filter = document.getElementById('folder-filter');
    filter?.addEventListener('input', () => {
        window.clearTimeout(filterTimer);
        filterTimer = window.setTimeout(renderTree, 120);
    });
    document.addEventListener('pointerdown', (event) => {
        if (!menu || menu.hidden || menu.contains(event.target)) return;
        closeFolderMenu();
    });
    window.addEventListener('resize', closeFolderMenu);
    on('scope', syncActiveRows);
    on('flags', scheduleFolderRefresh);
    on('trash:changed', scheduleFolderRefresh);
    on('import:changed', scheduleFolderRefresh);

    await refreshFoldersPanel({ toastEmpty: true });
}
