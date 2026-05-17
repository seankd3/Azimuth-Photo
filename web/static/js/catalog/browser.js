import { renderDirectoryBrowser } from './directory.js';

let catalogBrowsePath = '';


export function currentCatalogBrowsePath() {
    return catalogBrowsePath;
}


export function selectBrowsedDirectory(path) {
    const input = document.getElementById('scan-folder');
    if (input) input.value = path;
}


export function useBrowsedDirectory() {
    if (catalogBrowsePath) selectBrowsedDirectory(catalogBrowsePath);
}


export async function browseDirectory(path = '', { fetchImpl = fetch, showToast } = {}) {
    try {
        const query = path ? `?path=${encodeURIComponent(path)}` : '';
        const res = await fetchImpl(`/api/catalog/browse${query}`);
        const data = await res.json();
        catalogBrowsePath = data.path || '';
        renderDirectoryBrowser(data);
    } catch (err) {
        const errEl = document.getElementById('directory-browser-error');
        if (errEl) errEl.textContent = err.message;
        showToast?.('Directory browse failed');
    }
}


export function toggleDirectoryBrowser({ browseDirectoryImpl = browseDirectory } = {}) {
    const browser = document.getElementById('directory-browser');
    const input = document.getElementById('scan-folder');
    if (!browser) return false;
    const willOpen = browser.classList.contains('hidden');
    browser.classList.toggle('hidden', !willOpen);
    if (willOpen) browseDirectoryImpl(input?.value?.trim() || catalogBrowsePath || '');
    return willOpen;
}


export function browseDirectoryParent({ browseDirectoryImpl = browseDirectory } = {}) {
    if (!catalogBrowsePath) return false;
    browseDirectoryImpl(catalogBrowsePath.replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/');
    return true;
}


export async function chooseCatalogFolder({
    fetchImpl = fetch,
    setSettingsStatus,
    showToast,
} = {}) {
    const input = document.getElementById('scan-folder');
    const button = document.getElementById('choose-folder-btn');
    const currentPath = input?.value?.trim() || '';
    if (button) button.disabled = true;
    setSettingsStatus?.('Opening folder chooser...', 'muted');
    try {
        const res = await fetchImpl('/api/catalog/select-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentPath }),
        });
        const data = await res.json();
        if (data.cancelled) {
            setSettingsStatus?.('Folder selection cancelled.', 'muted');
            return;
        }
        if (!res.ok || !data.ok) throw new Error(data.error || 'Native folder chooser is unavailable');
        selectBrowsedDirectory(data.path);
        catalogBrowsePath = data.path || catalogBrowsePath;
        setSettingsStatus?.('Folder selected. Add + Scan when ready.', 'success');
    } catch (err) {
        setSettingsStatus?.(`Folder chooser unavailable: ${err.message}. Paste a folder path instead.`, 'error');
        showToast?.('Folder chooser failed');
    } finally {
        if (button) button.disabled = false;
    }
}
