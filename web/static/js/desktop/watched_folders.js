import { fetchJson, requestJson } from './api.js';
import { emit, patchScope } from './state.js';
import { showToast } from './toast.js';
import { escapeHtml as esc } from './dom.js';

const endpoint = '/api/watched-folders';
let mountedHost = null;

function timestamp(value) {
    const seconds = Number(value || 0);
    if (!seconds) return 'Not scanned yet';
    const date = new Date(seconds * 1000);
    return Number.isNaN(date.getTime()) ? 'Not scanned yet' : `Last scan ${date.toLocaleString()}`;
}

function rowHtml(folder) {
    const state = folder.online ? (folder.enabled ? 'Watching' : 'Paused') : 'Offline';
    return `<article class="watched-folder" data-watched-folder="${Number(folder.id)}">`
        + `<div class="watched-folder-copy"><b>${esc(folder.path)}</b><small>${esc(state)} · ${esc(timestamp(folder.last_scan_at))}</small></div>`
        + `<label class="watched-switch"><input type="checkbox" data-watched-enabled ${folder.enabled ? 'checked' : ''}> ${folder.enabled ? 'On' : 'Off'}</label>`
        + '<button class="mini-btn" type="button" data-watched-scan>Scan now</button>'
        + '<button class="mini-btn btn-danger" type="button" data-watched-remove>Remove</button>'
        + '</article>';
}

function render(host, folders) {
    host.innerHTML = '<div class="watched-folder-add">'
        + '<label>Watch a folder<input id="watched-folder-path" placeholder="/photos/inbox" autocomplete="off"></label>'
        + '<label class="check"><input id="watched-folder-recursive" type="checkbox" checked> Include subfolders</label>'
        + '<button class="mini-btn" type="button" id="watched-folder-add">Add folder</button></div>'
        + '<div id="watched-folder-list">'
        + (folders.length ? folders.map(rowHtml).join('') : '<p class="setting-hint">No watched folders yet. Add an import inbox and new photos will appear automatically.</p>')
        + '</div>';
}

async function refresh(host) {
    const data = await fetchJson(endpoint, { defaultValue: { folders: [] } });
    render(host, data.folders || []);
    bind(host);
}

function folderId(element) {
    return Number(element.closest('[data-watched-folder]')?.dataset.watchedFolder || 0);
}

function bind(host) {
    host.querySelector('#watched-folder-add')?.addEventListener('click', async () => {
        const path = host.querySelector('#watched-folder-path')?.value.trim() || '';
        if (!path) {
            showToast('Choose a folder to watch');
            return;
        }
        try {
            await requestJson(endpoint, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path, recursive: Boolean(host.querySelector('#watched-folder-recursive')?.checked) }),
            });
            showToast('Watched folder added');
            await refresh(host);
        } catch (error) {
            showToast(error.message);
        }
    });

    for (const toggle of host.querySelectorAll('[data-watched-enabled]')) {
        toggle.addEventListener('change', async () => {
            try {
                await requestJson(`${endpoint}/${folderId(toggle)}`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: toggle.checked }),
                });
                await refresh(host);
            } catch (error) {
                showToast(error.message);
                await refresh(host);
            }
        });
    }

    for (const button of host.querySelectorAll('[data-watched-scan]')) {
        button.addEventListener('click', async () => {
            button.disabled = true;
            button.textContent = 'Scanning…';
            try {
                const result = await requestJson(`${endpoint}/${folderId(button)}/scan`, { method: 'POST' });
                showToast(`${Number(result.registered || 0)} photo${Number(result.registered || 0) === 1 ? '' : 's'} found`);
                if (Number(result.registered || 0)) {
                    emit('import:changed', result);
                    patchScope({});
                }
            } catch (error) {
                showToast(error.message);
            }
            await refresh(host);
        });
    }

    for (const button of host.querySelectorAll('[data-watched-remove]')) {
        button.addEventListener('click', async () => {
            try {
                await requestJson(`${endpoint}/${folderId(button)}`, { method: 'DELETE' });
                showToast('Watched folder removed');
                await refresh(host);
            } catch (error) {
                showToast(error.message);
            }
        });
    }
}

export function initWatchedFolders() {
    const mount = () => {
        const host = document.getElementById('watched-folders-settings');
        if (!host || host === mountedHost) return;
        mountedHost = host;
        refresh(host).catch(() => {
            host.innerHTML = '<p class="setting-hint">Watched folders are unavailable right now.</p>';
        });
    };
    mount();
    new MutationObserver(mount).observe(document.getElementById('drawer-body'), { childList: true, subtree: true });
}
