"use strict";

const POLL_MS = 3000;
let timer = null;
let root = null;

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

function render(status) {
    const depth = Number(status.queue_depth) || 0;
    const libraryTotal = Number(status.prefetch?.library_total) || 0;
    const libraryCached = Number(status.prefetch?.library_cached) || 0;
    const thumbPercent = libraryTotal ? Math.round((libraryCached / libraryTotal) * 100) : 0;
    const action = status.paused ? 'Resume' : 'Pause';
    const count = `${depth} photo${depth === 1 ? '' : 's'}`;
    const errors = (status.recent_errors || []).slice(0, 3);
    const skipped = Number(status.mirror?.skipped_unhashed) || 0;
    const tooltip = escapeHtml(mirrorTooltip(status));
    root.innerHTML = `<button class="sync-chip-button" type="button" title="${tooltip}" aria-expanded="false" aria-haspopup="dialog">
        <span class="sync-chip-arrow" aria-hidden="true">↑</span><span>${count}</span><span class="sync-chip-sep">·</span><span>${formatBytes(status.bytes_remaining)} left</span>
    </button><div class="sync-chip-popover" hidden role="dialog" aria-label="Satellite sync">
        <div class="sync-chip-popover-title">Satellite sync <span>${status.paused ? 'Paused' : formatRate(status.throughput_bps)}</span></div>
        <div class="sync-chip-current">${status.current_file ? `Uploading ${status.current_file}` : depth ? 'Waiting to upload' : 'Everything is synced'}</div>
        <div class="sync-chip-current">Library: ${libraryTotal} photos · thumbs ${thumbPercent}%</div>
        ${skipped ? `<div class="sync-chip-current">Mirror skipped ${skipped} unhashed hub photo${skipped === 1 ? '' : 's'}</div>` : ''}
        ${errors.length ? `<div class="sync-chip-errors">${errors.map((error) => `<div>${escapeHtml(error)}</div>`).join('')}</div>` : ''}
        <div class="sync-chip-actions"><button type="button" data-sync-action="now">Sync now</button><button type="button" data-sync-action="toggle">${action}</button></div>
    </div>`;
    const button = root.querySelector('.sync-chip-button');
    const popover = root.querySelector('.sync-chip-popover');
    button.addEventListener('click', () => {
        const open = popover.hidden;
        popover.hidden = !open;
        button.setAttribute('aria-expanded', String(open));
    });
    root.querySelector('[data-sync-action="now"]').addEventListener('click', () => control('/api/sync/now'));
    root.querySelector('[data-sync-action="toggle"]').addEventListener('click', () => control(status.paused ? '/api/sync/resume' : '/api/sync/pause'));
}

function escapeHtml(value) {
    const span = document.createElement('span');
    span.textContent = String(value || '');
    return span.innerHTML;
}

async function refresh() {
    try {
        render(await json('/api/sync/status'));
    } catch (error) {
        console.warn('sync status unavailable', error);
    }
}

async function control(url) {
    try {
        render(await json(url, { method: 'POST' }));
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
    } catch (_error) {
        return;
    }
    root = document.createElement('div');
    root.className = 'sync-chip';
    slot.replaceChildren(root);
    await refresh();
    timer = window.setInterval(refresh, POLL_MS);
    window.addEventListener('pagehide', () => timer && window.clearInterval(timer), { once: true });
}
