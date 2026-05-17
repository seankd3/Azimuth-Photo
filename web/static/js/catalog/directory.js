import {
    escapeHtml,
    jsString,
} from '../ui.js';


export function directoryRootsHtml(roots = []) {
    return (roots || []).map((root) =>
        `<button class="bar-btn" type="button" onclick="PhotoArchive.browseDirectory(${jsString(root.path)})">${escapeHtml(root.label)}</button>`
    ).join('');
}


export function directoryBrowserListHtml({ path = '/', entries = [] } = {}) {
    const currentPath = path || '/';
    const rows = entries || [];
    const currentName = currentPath.replace(/\/+$/, '').split('/').filter(Boolean).pop() || '/';
    const currentRow = `
            <div class="directory-tree-row current">
                <div class="directory-tree-open">
                    <span class="tree-expander">-</span>
                    <span class="tree-folder">${escapeHtml(currentName)}</span>
                    <code>${escapeHtml(currentPath)}</code>
                </div>
                <button class="bar-btn" type="button" onclick="PhotoArchive.useBrowsedDirectory()">Use</button>
            </div>
        `;
    const childRows = rows.map((entry) => `
            <div class="directory-tree-row child ${entry.readable ? '' : 'restricted'}">
                <button class="directory-tree-open" type="button" onclick="PhotoArchive.browseDirectory(${jsString(entry.path)})" title="${escapeHtml(entry.path)}">
                    <span class="tree-branch"></span>
                    <span class="tree-expander">+</span>
                    <span class="tree-folder">${escapeHtml(entry.name)}</span>
                    <small>${entry.readable ? 'Folder' : 'Restricted'}</small>
                </button>
                <button class="bar-btn" type="button" onclick="PhotoArchive.selectBrowsedDirectory(${jsString(entry.path)})">Use</button>
            </div>
        `).join('');
    const empty = rows.length ? '' : '<div class="catalog-empty tree-empty">No readable subfolders here.</div>';
    return currentRow + childRows + empty;
}


export function directoryCrumbsHtml(path) {
    const normalized = String(path || '/').replace(/\/+$/, '') || '/';
    const parts = normalized.split('/').filter(Boolean);
    const crumbs = [
        `<button type="button" onclick="PhotoArchive.browseDirectory('/')">/</button>`,
    ];
    let current = '';
    for (const part of parts) {
        current += `/${part}`;
        crumbs.push(
            `<button type="button" onclick="PhotoArchive.browseDirectory(${jsString(current)})">${escapeHtml(part)}</button>`
        );
    }
    return crumbs.join('<span>/</span>');
}


export function renderDirectoryBrowser(data) {
    const currentEl = document.getElementById('directory-browser-current');
    const rootsEl = document.getElementById('directory-browser-roots');
    const listEl = document.getElementById('directory-browser-list');
    const errEl = document.getElementById('directory-browser-error');
    if (errEl) errEl.textContent = data.error || '';
    const currentPath = data.path || '/';
    if (currentEl) currentEl.innerHTML = directoryCrumbsHtml(currentPath);
    if (rootsEl) {
        rootsEl.innerHTML = directoryRootsHtml(data.roots || []);
    }
    if (!listEl) return;
    listEl.innerHTML = directoryBrowserListHtml({
        path: currentPath,
        entries: data.entries || [],
    });
}
