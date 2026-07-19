/** Compact Manage library popover — add/remove sources from the Folders header. */

import { getCatalog, removeCatalogSource } from './api.js';
import { confirmAction } from './trash.js';
import { openSourceAddFlow } from './drawer.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { icon } from '../icons.js';
import { showToast } from './toast.js';

let popover = null;
let popoverReturn = null;
let onChanged = async () => {};

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

function normalizePath(path) {
    return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
}

/** True when this source root sits inside another registered source's tree. */
export function isNestedSource(source, allSources = []) {
    const path = normalizePath(source?.path);
    if (!path || path.toLowerCase().startsWith('hub:')) return false;
    return (allSources || []).some((other) => {
        if (Number(other?.id) === Number(source?.id)) return false;
        const parent = normalizePath(other?.path);
        if (!parent || parent.toLowerCase().startsWith('hub:')) return false;
        return path.startsWith(`${parent}/`);
    });
}

function ensurePopover() {
    if (popover) return popover;
    popover = document.createElement('div');
    popover.id = 'library-manage-popover';
    popover.className = 'library-manage-popover';
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', 'Manage library');
    popover.hidden = true;
    document.body.appendChild(popover);
    popover.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeLibraryManage();
        }
    });
    document.addEventListener('pointerdown', (event) => {
        if (!popover || popover.hidden) return;
        if (popover.contains(event.target)) return;
        if (event.target.closest?.('#folders-manage-btn')) return;
        closeLibraryManage();
    });
    return popover;
}

function positionPopover(anchor) {
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 16);
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
    const top = Math.max(8, Math.min(window.innerHeight - 24, rect.bottom + 6));
    popover.style.width = `${width}px`;
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
}

function sourceLabel(source) {
    return source.display_name || source.path || 'Source';
}

function rowHtml(source, allSources) {
    const nested = isNestedSource(source, allSources);
    const online = Number(source.online) === 1 || source.online === true;
    return `<div class="library-manage-row${nested ? ' is-nested' : ''}" data-source-id="${Number(source.id) || 0}">`
        + `<span class="nr-dot ${online ? 'on' : 'off'}"></span>`
        + `<div class="library-manage-meta">`
        + `<span class="library-manage-name" title="${esc(sourceLabel(source))}">${esc(sourceLabel(source))}</span>`
        + (nested
            ? '<span class="library-manage-flag">nested — merge into parent</span>'
            : `<span class="library-manage-path" title="${esc(source.path || '')}">${esc(source.path || '')}</span>`)
        + '</div>'
        + `<button type="button" class="mini-btn btn-danger" data-manage-remove>Remove</button>`
        + '</div>';
}

async function loadSources() {
    const catalog = await getCatalog().catch(() => null);
    return (catalog && catalog.sources) || [];
}

async function renderPopoverBody() {
    const sources = await loadSources();
    const rows = sources.length
        ? sources.map((source) => rowHtml(source, sources)).join('')
        : '<p class="library-manage-empty">No sources yet.</p>';
    popover.innerHTML = '<div class="library-manage-head"><strong>Manage library</strong>'
        + '<button type="button" class="mini-btn" data-manage-close aria-label="Close">Close</button></div>'
        + `<div class="library-manage-list">${rows}</div>`
        + '<div class="library-manage-actions">'
        + `<button type="button" class="mini-btn" data-manage-add>${icon('folder-plus')} Add folder</button>`
        + '</div>';
    popover.querySelector('[data-manage-close]')?.addEventListener('click', closeLibraryManage);
    popover.querySelector('[data-manage-add]')?.addEventListener('click', () => {
        closeLibraryManage();
        openSourceAddFlow({
            onSuccess: async () => {
                await onChanged();
            },
        });
    });
    for (const row of popover.querySelectorAll('[data-source-id]')) {
        row.querySelector('[data-manage-remove]')?.addEventListener('click', async () => {
            const sourceId = Number(row.dataset.sourceId) || 0;
            if (!sourceId) return;
            const source = (await loadSources()).find((item) => Number(item.id) === sourceId);
            const count = source?.active_image_count ?? source?.image_count ?? 0;
            const ok = await confirmAction({
                title: `Remove ${sourceLabel(source || {})}?`,
                message: count
                    ? `${Number(count).toLocaleString()} photos leave the library. Their ranking history is kept and restored if you add this folder back.`
                    : 'This folder is removed from the library. Re-adding it restores everything.',
                confirmLabel: 'Remove',
            });
            if (!ok) return;
            const button = row.querySelector('[data-manage-remove]');
            if (button) button.disabled = true;
            const result = await removeCatalogSource(sourceId, 'keep');
            if (result && result.ok) {
                showToast('Source removed — ranking history kept');
                await onChanged();
                await renderPopoverBody();
                trapFocus(popover, popover.querySelector('[data-manage-add], [data-manage-close]'));
            } else {
                showToast('Couldn’t remove source');
                if (button) button.disabled = false;
            }
        });
    }
}

export function closeLibraryManage() {
    if (!popover || popover.hidden) return;
    popover.hidden = true;
    releaseFocus(popover);
    if (popoverReturn && document.contains(popoverReturn) && popoverReturn.focus) {
        popoverReturn.focus({ preventScroll: true });
    }
    popoverReturn = null;
}

export async function openLibraryManage(anchor, { onSourcesChanged } = {}) {
    if (!anchor) return;
    onChanged = onSourcesChanged || onChanged;
    ensurePopover();
    releaseFocus(popover);
    popoverReturn = anchor;
    popover.innerHTML = '<div class="library-manage-head"><strong>Manage library</strong></div>'
        + '<p class="library-manage-empty">Loading…</p>';
    popover.hidden = false;
    positionPopover(anchor);
    await renderPopoverBody();
    trapFocus(popover, popover.querySelector('[data-manage-add], [data-manage-close]'));
}

export function initLibraryManage({ onSourcesChanged } = {}) {
    if (onSourcesChanged) onChanged = onSourcesChanged;
    const button = document.getElementById('folders-manage-btn');
    button?.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (popover && !popover.hidden) {
            closeLibraryManage();
            return;
        }
        openLibraryManage(button, { onSourcesChanged: onChanged });
    });
    window.addEventListener('resize', closeLibraryManage);
}
