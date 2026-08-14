"use strict";

/**
 * Quiet ranking chip for newly-linked Lightroom exports.
 * Piggybacks the sync status poll — no dedicated loop.
 */

import { getLrStatus } from './api.js';
import { clearSelection, emit, on, setActiveLens, setScope } from './state.js';
import { closeLoupe } from './loupe.js';

const DISMISS_KEY = 'pa_lr_exports_dismissed';
const SEEN_KEY = 'pa_lr_exports_seen_at';
const SLOT_ID = 'lr-ranking-chip-slot';

let root = null;
let currentBatch = null;

function dismissedIds() {
    try {
        const raw = JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]');
        return new Set(Array.isArray(raw) ? raw.map(String) : []);
    } catch {
        return new Set();
    }
}

function rememberDismiss(batchId) {
    const ids = dismissedIds();
    ids.add(String(batchId));
    localStorage.setItem(DISMISS_KEY, JSON.stringify([...ids].slice(-40)));
}

function seenSince() {
    const raw = Number(localStorage.getItem(SEEN_KEY) || 0);
    return Number.isFinite(raw) && raw > 0 ? raw : 0;
}

function rememberSeen(newestAt) {
    const value = Number(newestAt) || 0;
    if (value > seenSince()) localStorage.setItem(SEEN_KEY, String(value));
}

function ensureSlot() {
    let slot = document.getElementById(SLOT_ID);
    if (slot) return slot;
    const host = document.getElementById('top-right') || document.getElementById('sync-chip-slot')?.parentElement;
    if (!host) return null;
    slot = document.createElement('div');
    slot.id = SLOT_ID;
    const sync = document.getElementById('sync-chip-slot');
    if (sync) host.insertBefore(slot, sync);
    else host.prepend(slot);
    return slot;
}

function hide() {
    currentBatch = null;
    if (root) {
        root.hidden = true;
        root.replaceChildren();
    }
}

function render(batch) {
    const slot = ensureSlot();
    if (!slot) return;
    if (!root) {
        root = document.createElement('div');
        root.className = 'lr-ranking-chip';
        root.hidden = true;
        slot.replaceChildren(root);
    }
    const count = Number(batch.count) || (batch.image_ids || []).length;
    if (!count) {
        hide();
        return;
    }
    currentBatch = batch;
    root.hidden = false;
    root.innerHTML = `<button class="lr-ranking-chip-button" type="button" data-lr-rank>`
        + `<span>${count} new edit${count === 1 ? '' : 's'} from Lightroom — rank them</span>`
        + `</button>`
        + `<button class="lr-ranking-chip-dismiss" type="button" data-lr-dismiss aria-label="Dismiss">×</button>`;
    root.querySelector('[data-lr-rank]')?.addEventListener('click', openBatch);
    root.querySelector('[data-lr-dismiss]')?.addEventListener('click', dismissBatch);
}

function openBatch() {
    if (!currentBatch) return;
    const ids = (currentBatch.image_ids || []).map(Number).filter((id) => id > 0);
    if (!ids.length) return;
    rememberDismiss(currentBatch.batch_id);
    rememberSeen(currentBatch.newest_at);
    closeLoupe();
    clearSelection();
    setScope({
        similarIds: ids,
        similarSourceId: '',
        similarLimit: ids.length,
        similarLabel: 'New Lightroom edits',
        sort: 'elo',
    }, { pushHash: false });
    emit('similar:loaded', { imageId: 0, images: ids.map((id) => ({ id })) });
    setActiveLens('refine');
    hide();
}

function dismissBatch() {
    if (!currentBatch) return;
    rememberDismiss(currentBatch.batch_id);
    rememberSeen(currentBatch.newest_at);
    hide();
}

export function applyLrStatus(status) {
    const batch = status && status.lr && status.lr.new_exports;
    if (!batch || !batch.batch_id || !batch.count) {
        hide();
        return;
    }
    if (dismissedIds().has(String(batch.batch_id))) {
        hide();
        return;
    }
    render(batch);
}

async function refreshFromSync() {
    try {
        const status = await getLrStatus(seenSince() || undefined);
        applyLrStatus(status);
    } catch {
        // Silent — bridge stays quiet on transient failures.
    }
}

export function initLrRankingChip() {
    ensureSlot();
    refreshFromSync();
    // A quiet poll of the bridge's own status route: new Lightroom exports
    // surface within a minute without any push machinery behind them.
    setInterval(refreshFromSync, 60_000);
}
