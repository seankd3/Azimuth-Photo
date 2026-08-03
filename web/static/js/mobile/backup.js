import { bytes as formatBytes, esc } from '../lib.js';
// Phone-facing backup glass box. It reports only the existing sync worker's
// truth plus browser storage; hub mode is an explicit ready state, not a fake
// upload queue.

import { getSyncStatus, setSyncPaused, writeFailureMessage } from './api.js';
import { offlineSummary } from './offline.js';
import { queueLength } from './write_queue.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

const POLL_MS = 2500;

let timer = null;
let generation = 0;
let firstDepth = null;
let host = null;
let back = null;
let status = null;
let storage = null;



function formatRate(value) {
    const speed = Number(value || 0);
    return speed > 0 ? `${formatBytes(speed)}/s` : '—';
}

function progress(statusPayload) {
    const depth = Number(statusPayload?.queue_depth || 0);
    if (!depth) return 100;
    if (firstDepth == null) firstDepth = depth;
    firstDepth = Math.max(firstDepth, depth);
    return firstDepth ? Math.max(0, Math.min(99, Math.round(((firstDepth - depth) / firstDepth) * 100))) : 0;
}

function storagePayload(estimate) {
    const usage = Number(estimate?.usage || 0);
    const quota = Number(estimate?.quota || 0);
    return {
        usage,
        quota,
        percent: quota ? Math.max(0, Math.min(100, Math.round((usage / quota) * 100))) : 0,
    };
}

function loadingHtml() {
    return '<div class="ml-head"><button class="ml-back" id="mb-back">Back</button>'
        + '<h3>Backup</h3><span class="ml-head-spacer"></span></div>'
        + '<div class="m-backup-loading"><div class="skel-row"></div><div class="skel-row"></div><div class="skel-row"></div></div>';
}

function hubHtml() {
    const phone = storagePayload(storage);
    const offline = offlineSummary();
    return '<section class="m-backup-hero ready">'
        + `<span class="m-backup-glyph">${icon('shield-check', 'icon icon-lg')}</span>`
        + '<div><b>Azimuth hub ready</b><span>Backups from connected devices arrive here automatically.</span></div></section>'
        + '<section class="m-backup-section"><h4>This phone</h4>'
        + `<div class="m-backup-storage"><div><b>${formatBytes(phone.usage)}</b><span>App storage</span></div>`
        + `<div><b>${offline.count.toLocaleString('en-US')}</b><span>Offline</span></div>`
        + `<div><b>${queueLength().toLocaleString('en-US')}</b><span>Waiting</span></div></div>`
        + `<div class="m-backup-meter"><span style="width:${phone.percent}%"></span></div>`
        + `<p>${phone.quota ? `${formatBytes(phone.quota)} browser quota.` : 'Storage quota is managed by this phone.'}</p></section>`
        + '<section class="m-backup-note"><b>No phone upload queue is attached</b>'
        + '<span>This web app is viewing the hub. Device upload progress appears when Azimuth is running as a connected satellite.</span></section>';
}

function satelliteHtml(payload) {
    const depth = Number(payload.queue_depth || 0);
    const paused = Boolean(payload.paused);
    const connected = Boolean(payload.last_sync_at || payload.hub_version);
    const hasWorker = Boolean(connected || depth || payload.current_file || (payload.recent_errors || []).length);
    const percent = hasWorker ? progress(payload) : 0;
    const phone = storagePayload(storage);
    const offline = offlineSummary();
    const state = paused ? 'Paused' : depth ? 'Backing up' : connected ? 'Backed up' : 'Waiting for hub';
    const detail = payload.current_file
        ? `Uploading ${payload.current_file}`
        : depth ? `${depth.toLocaleString('en-US')} ${depth === 1 ? 'photo' : 'photos'} waiting`
            : connected ? 'Everything is safely on the hub' : 'Connect this device to start backup';
    const completed = firstDepth == null ? 0 : Math.max(0, firstDepth - depth);
    const errors = (payload.recent_errors || []).slice(0, 3);
    return `<section class="m-backup-hero${paused ? ' paused' : depth ? ' active' : ' ready'}">`
        + `<div class="m-backup-ring" style="--backup-p:${percent}"><b>${percent}%</b></div>`
        + `<div><b>${state}</b><span>${esc(detail)}</span></div></section>`
        + '<section class="m-backup-stats">'
        + `<div><b>${completed.toLocaleString('en-US')}</b><span>Backed up now</span></div>`
        + `<div><b>${depth.toLocaleString('en-US')}</b><span>Pending</span></div>`
        + `<div><b>${formatBytes(payload.bytes_remaining)}</b><span>Remaining</span></div>`
        + `<div><b>${formatRate(payload.throughput_bps)}</b><span>Speed</span></div></section>`
        + (hasWorker ? `<button class="sheet-btn m-backup-action" id="mb-toggle" data-mutating>${paused ? 'Resume backup' : 'Pause backup'}</button>` : '')
        + '<section class="m-backup-section"><h4>This phone</h4>'
        + `<div class="m-backup-storage"><div><b>${formatBytes(phone.usage)}</b><span>App storage</span></div>`
        + `<div><b>${offline.count.toLocaleString('en-US')}</b><span>Offline</span></div>`
        + `<div><b>${queueLength().toLocaleString('en-US')}</b><span>Waiting</span></div></div>`
        + `<div class="m-backup-meter"><span style="width:${phone.percent}%"></span></div>`
        + `<p>${phone.quota ? `${formatBytes(phone.quota)} browser quota.` : 'Storage quota is managed by this phone.'}</p></section>`
        + (errors.length
            ? '<section class="m-backup-errors"><h4>Needs attention</h4>'
                + errors.map((error) => `<p>${esc(error)}</p>`).join('') + '</section>'
            : '');
}

function bindBack() {
    host?.querySelector('#mb-back')?.addEventListener('click', () => {
        const onBack = back;
        stopBackupView();
        onBack?.();
    });
}

function render() {
    if (!host) return;
    host.innerHTML = '<div class="ml-head"><button class="ml-back" id="mb-back">Back</button>'
        + '<h3>Backup</h3><span class="ml-head-spacer"></span></div>'
        + '<div class="m-backup-view">'
        + (!status
            ? '<div class="m-feature-empty"><b>Backup status is unavailable</b><span>Check the connection, then try again.</span><button class="sheet-btn" id="mb-retry">Try again</button></div>'
            : status.mode === 'satellite' ? satelliteHtml(status) : hubHtml())
        + '</div>';
    bindBack();
    host.querySelector('#mb-retry')?.addEventListener('click', () => {
        const token = generation;
        host.querySelector('.m-backup-view').innerHTML = '<div class="skel-row"></div><div class="skel-row"></div>';
        void refresh(token);
    });
    host.querySelector('#mb-toggle')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        const nextPaused = !Boolean(status?.paused);
        const result = await setSyncPaused(nextPaused);
        if (result) {
            status = result;
            showToast(nextPaused ? 'Backup paused' : 'Backup resumed');
            render();
        } else {
            button.disabled = false;
            showToast(writeFailureMessage());
        }
    });
}

async function refresh(token) {
    let nextStatus = null;
    let estimate = null;
    try {
        [nextStatus, estimate] = await Promise.all([
            getSyncStatus(),
            navigator.storage?.estimate ? navigator.storage.estimate().catch(() => null) : null,
        ]);
    } catch {
        // getSyncStatus throws on network failure/non-2xx; a null status must
        // reach render() so the unavailable/retry card replaces the skeleton.
    }
    if (token !== generation || !host) return;
    status = nextStatus;
    storage = estimate;
    render();
}

export function renderBackupView(target, onBack) {
    stopBackupView();
    host = target;
    back = onBack;
    firstDepth = null;
    status = null;
    storage = null;
    const token = ++generation;
    host.innerHTML = loadingHtml();
    bindBack();
    void refresh(token);
    timer = window.setInterval(() => void refresh(token), POLL_MS);
}

export function stopBackupView() {
    if (timer) window.clearInterval(timer);
    timer = null;
    generation += 1;
    host = null;
    back = null;
}
