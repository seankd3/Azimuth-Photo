"use strict";

import { fetchOptionsWithTimeout } from '../api.js';
import { emit } from './state.js';
import { showToast } from './toast.js';
import { openSystemSettings } from './drawer.js';

const POLL_MS = 3000;
let timer = null;
let root = null;
let currentStatus = null;
let controlInFlight = 0;
let statusGeneration = 0;
let pollOnly = false;

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
    const response = await fetch(url, fetchOptionsWithTimeout(options, 5_000));
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
        <div class="sync-chip-contract" data-sync-contract hidden></div>
        <div class="sync-chip-current" data-sync-pending hidden></div>
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
    if (!root) return;
    currentStatus = status;
    const depth = Number(status.queue_depth) || 0;
    const pendingOps = Number(status.pending_ops) || 0;
    const libraryTotal = Number(status.prefetch?.library_total) || 0;
    const libraryCached = Number(status.prefetch?.library_cached) || 0;
    const thumbPercent = libraryTotal ? Math.round((libraryCached / libraryTotal) * 100) : 0;
    const action = status.paused ? 'Resume' : 'Pause';
    const count = pendingOps
        ? `${pendingOps} change${pendingOps === 1 ? '' : 's'} pending`
        : `${depth} photo${depth === 1 ? '' : 's'}`;
    const errors = (status.recent_errors || []).slice(0, 3);
    const skipped = Number(status.mirror?.skipped_unhashed) || 0;
    const pendingHubTrash = Number(status.pending_hub_trash) || 0;
    const button = root.querySelector('.sync-chip-button');
    const hubHealth = status.hub_health || 'ok';
    const needsUpdate = hubHealth === 'needs_update';
    const unreachable = hubHealth === 'unreachable';
    const hubSystemHealth = String(status.hub_system_health || 'unknown');
    const degraded = hubSystemHealth === 'warn' || hubSystemHealth === 'bad';
    const updateMessage = String(status.update_message || '');
    const updateState = String(status.update_state || '');
    const contractMessage = 'The hub is running an older version — some actions are paused until it updates.';
    root.classList.toggle('needs-update', needsUpdate);
    root.classList.toggle('hub-unreachable', unreachable);
    root.classList.toggle('hub-degraded', degraded);
    button.classList.remove('offline');
    button.title = needsUpdate ? contractMessage
        : unreachable ? 'Hub unavailable — sync will retry.'
        : degraded ? 'The hub needs attention — open System Health on the hub.'
        : mirrorTooltip(status);
    const recovering = status.state === 'recovering' && !status.paused;
    const backoff = Math.round(Number(status.backoff_seconds) || 0);
    patchText('.sync-chip-arrow', needsUpdate ? '!' : status.paused ? 'Ⅱ' : recovering ? '↻' : depth || pendingOps ? '↑' : '✓');
    patchText('[data-sync-count]', count);
    patchText('[data-sync-bytes]', `${formatBytes(status.bytes_remaining)} left`);
    patchText('[data-sync-rate]', status.paused ? 'Paused' : recovering ? 'Retrying' : formatRate(status.throughput_bps));
    patchText('[data-sync-current]', updateMessage ? updateMessage
        : needsUpdate ? 'Some actions are paused until the hub updates.'
        : recovering ? `Backup interrupted — retrying${backoff ? ` in ${backoff}s` : ''}${depth ? ` · ${depth} still to back up` : ''}`
        : status.current_file ? `Uploading ${status.current_file}`
        : depth ? 'Waiting to upload'
        : pendingHubTrash ? `${pendingHubTrash} photo${pendingHubTrash === 1 ? '' : 's'} waiting to be removed from hub`
        : pendingOps ? 'Sync needs attention'
        : degraded ? 'Everything is synced · hub needs attention'
        : 'Everything is synced');
    const contract = root.querySelector('[data-sync-contract]');
    if (contract) {
        patchText('[data-sync-contract]', updateMessage && updateState !== 'idle' ? updateMessage : contractMessage);
        contract.hidden = !(needsUpdate || (updateMessage && updateState !== 'idle'));
    }
    const pending = root.querySelector('[data-sync-pending]');
    if (pending) {
        patchText('[data-sync-pending]', `${pendingOps} change${pendingOps === 1 ? '' : 's'} waiting to retry`);
        pending.hidden = pendingOps === 0;
    }
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
    const generation = statusGeneration;
    try {
        const since = (() => {
            try {
                const raw = Number(localStorage.getItem('pa_lr_exports_seen_at') || 0);
                return Number.isFinite(raw) && raw > 0 ? raw : 0;
            } catch {
                return 0;
            }
        })();
        const url = since > 0
            ? `/api/sync/status?lr_exports_since=${encodeURIComponent(since)}`
            : '/api/sync/status';
        const status = await json(url);
        if (controlInFlight || generation !== statusGeneration) return;
        emit('sync:status', status);
        if (!pollOnly) patch(status);
    } catch (_error) {
        if (controlInFlight || generation !== statusGeneration) return;
        if (!pollOnly) patchOffline();
    }
}

async function control(url) {
    const generation = ++statusGeneration;
    controlInFlight += 1;
    try {
        const status = await json(url, { method: 'POST' });
        if (generation === statusGeneration) patch(status);
    } catch (_error) {
        // A failed user command is not a hub outage — say so, and let the next
        // status poll decide whether the chip should show offline.
        showToast(url.includes('/pause') || url.includes('/resume')
            ? 'Couldn’t change sync state'
            : 'Couldn’t start sync');
        // Re-poll after the finally block settles generation/controlInFlight,
        // so the refresh isn't discarded by its own guards.
        setTimeout(refresh, 0);
    } finally {
        if (generation === statusGeneration) statusGeneration += 1;
        controlInFlight -= 1;
    }
}

function patchOffline() {
    if (!root) return;
    root.querySelector('.sync-chip-button')?.classList.add('offline');
    patchText('.sync-chip-arrow', '•');
    patchText('[data-sync-count]', 'Hub offline');
    patchText('[data-sync-bytes]', 'retrying');
    patchText('[data-sync-rate]', 'Offline');
    patchText('[data-sync-current]', 'Hub unavailable — changes will retry');
}

function mountNotConnected(slot) {
    const chip = document.createElement('div');
    chip.className = 'sync-chip not-connected';
    chip.innerHTML = '<button class="sync-chip-button" type="button" '
        + 'title="This computer is not connected to your hub — photos stored there cannot load">'
        + '<span class="sync-chip-arrow" aria-hidden="true">!</span>'
        + '<span data-sync-count>Not connected</span></button>';
    chip.querySelector('button').addEventListener('click', () => openSystemSettings('connectivity'));
    slot.replaceChildren(chip);
}

export async function initSyncChip() {
    const slot = document.getElementById('sync-chip-slot');
    if (!slot) return;
    let mode = 'hub';
    let hasHub = false;
    try {
        const bootstrap = await json('/api/settings');
        mode = bootstrap?.sync?.mode || 'hub';
        hasHub = bootstrap?.sync?.has_hub !== false && Boolean(bootstrap?.sync?.has_hub || bootstrap?.sync?.hub_url);
        if (mode !== 'satellite') return;
    } catch (_error) {
        return;
    }
    // Always poll on satellite so LR quiet signals (new exports / health) piggyback.
    pollOnly = !hasHub;
    if (hasHub) {
        root = document.createElement('div');
        root.className = 'sync-chip';
        slot.replaceChildren(root);
        mount();
    } else {
        // A library mirrored from a hub, with no hub attached, used to show
        // nothing here at all — which is how a laptop ended up displaying
        // 150,635 photos it could not fetch one pixel of. Say it, and put the
        // way back one click away. A library that never had a hub is fine and
        // gets no chip: the server decides, from the catalog, not the run mode.
        const status = await json('/api/sync/status').catch(() => null);
        if (status?.hub_health === 'not_connected') mountNotConnected(slot);
    }
    await refresh();
    timer = window.setInterval(refresh, POLL_MS);
    window.addEventListener('pagehide', () => timer && window.clearInterval(timer), { once: true });
}
