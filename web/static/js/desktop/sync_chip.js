"use strict";

const POLL_MS = 3000;
let timer = null;
let root = null;
let currentStatus = null;

function formatBytes(bytes) {
    const value = Math.max(0, Number(bytes) || 0);
    if (value < 1024) return `${value} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let amount = value / 1024;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
        amount /= 1024;
        index += 1;
    }
    return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function formatRate(bytesPerSecond) {
    const rate = Number(bytesPerSecond) || 0;
    return rate > 0 ? `${formatBytes(rate)}/s` : 'waiting';
}

async function json(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`Sync request failed (${response.status})`);
    return response.json();
}

function mirrorTooltip(status) {
    const mirror = status.mirror || {};
    const skipped = Number(mirror.skipped_unhashed) || 0;
    const applied = Number(mirror.rows_applied) || 0;
    if (skipped > 0) {
        return `${skipped} hub photo${skipped === 1 ? '' : 's'} skipped — missing content hash (mirror looks empty until hub finishes hashing)`;
    }
    if (applied > 0) {
        return `Mirror applied ${applied} photo${applied === 1 ? '' : 's'}`;
    }
    return 'Satellite sync';
}

function mount() {
    root.innerHTML = `<button class="sync-chip-button" type="button" title="Satellite sync" aria-expanded="false" aria-haspopup="dialog">
        <span class="sync-chip-arrow" aria-hidden="true">↑</span><span data-sync-count>0 photos</span><span class="sync-chip-sep">·</span><span data-sync-bytes>0 B left</span>
    </button><div class="sync-chip-popover" hidden role="dialog" aria-label="Satellite sync">
        <div class="sync-chip-popover-title">Satellite sync <span data-sync-rate>waiting</span></div>
        <div class="sync-chip-current" data-sync-current>Everything is synced</div>
        <div class="sync-chip-current" data-sync-library>Library: 0 photos · thumbs 0%</div>
        <div class="sync-chip-current" data-sync-mirror hidden></div>
        <div class="sync-chip-errors" data-sync-errors hidden></div>
        <div class="sync-chip-actions"><button type="button" data-sync-action="now">Sync now</button><button type="button" data-sync-action="toggle">Pause</button></div>
    </div>`;
    const button = root.querySelector('.sync-chip-button');
    const popover = root.querySelector('.sync-chip-popover');
    button.addEventListener('click', () => {
        const open = popover.hidden;
        popover.hidden = !open;
        button.setAttribute('aria-expanded', String(open));
    });
    root.querySelector('[data-sync-action="now"]').addEventListener('click', () => control('/api/sync/now'));
    root.querySelector('[data-sync-action="toggle"]').addEventListener('click', () => (
        control(currentStatus?.paused ? '/api/sync/resume' : '/api/sync/pause')
    ));
}

function patchText(selector, value) {
    const node = root.querySelector(selector);
    const text = String(value);
    if (node && node.textContent !== text) node.textContent = text;
}

function patchErrors(errors) {
    const host = root.querySelector('[data-sync-errors]');
    if (!host) return;
    errors.forEach((error, index) => {
        let row = host.children[index];
        if (!row) {
            row = document.createElement('div');
            host.append(row);
        }
        const text = String(error || '');
        if (row.textContent !== text) row.textContent = text;
    });
    while (host.children.length > errors.length) host.lastElementChild.remove();
    host.hidden = errors.length === 0;
}

function patch(status) {
    currentStatus = status;
    const depth = Number(status.queue_depth) || 0;
    const libraryTotal = Number(status.prefetch?.library_total) || 0;
    const libraryCached = Number(status.prefetch?.library_cached) || 0;
    const thumbPercent = libraryTotal ? Math.round((libraryCached / libraryTotal) * 100) : 0;
    const action = status.paused ? 'Resume' : 'Pause';
    const count = `${depth} photo${depth === 1 ? '' : 's'}`;
    const errors = (status.recent_errors || []).slice(0, 3);
    const skipped = Number(status.mirror?.skipped_unhashed) || 0;
    const button = root.querySelector('.sync-chip-button');
    button.title = mirrorTooltip(status);
    patchText('.sync-chip-arrow', status.paused ? 'Ⅱ' : depth ? '↑' : '✓');
    patchText('[data-sync-count]', count);
    patchText('[data-sync-bytes]', `${formatBytes(status.bytes_remaining)} left`);
    patchText('[data-sync-rate]', status.paused ? 'Paused' : formatRate(status.throughput_bps));
    patchText('[data-sync-current]', status.current_file ? `Uploading ${status.current_file}` : depth ? 'Waiting to upload' : 'Everything is synced');
    patchText('[data-sync-library]', `Library: ${libraryTotal} photos · thumbs ${thumbPercent}%`);
    const mirror = root.querySelector('[data-sync-mirror]');
    if (mirror) {
        patchText('[data-sync-mirror]', `Mirror skipped ${skipped} unhashed hub photo${skipped === 1 ? '' : 's'}`);
        mirror.hidden = skipped === 0;
    }
    patchErrors(errors);
    patchText('[data-sync-action="toggle"]', action);
}

async function refresh() {
    try {
        patch(await json('/api/sync/status'));
    } catch (error) {
        console.warn('sync status unavailable', error);
    }
}

async function control(url) {
    try {
        patch(await json(url, { method: 'POST' }));
    } catch (error) {
        console.warn('sync control unavailable', error);
    }
}

export async function initSyncChip() {
    const slot = document.getElementById('sync-chip-slot');
    if (!slot) return;
    try {
        const bootstrap = await json('/api/settings');
        if (bootstrap?.sync?.mode !== 'satellite') return;
        if (bootstrap?.sync?.has_hub === false) return; // standalone: nothing to sync with
    } catch (_error) {
        return;
    }
    root = document.createElement('div');
    root.className = 'sync-chip';
    slot.replaceChildren(root);
    mount();
    await refresh();
    timer = window.setInterval(refresh, POLL_MS);
    window.addEventListener('pagehide', () => timer && window.clearInterval(timer), { once: true });
}
