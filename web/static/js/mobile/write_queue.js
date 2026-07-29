// Durable, client-side write-ahead queue for field culling. It deliberately
// uses localStorage rather than IndexedDB so the queue works on today's HTTP
// and flaky-link deployment. On secure origins, Background Sync is an optional
// wake-up that asks this page to drain — localStorage remains the source of
// truth (service workers cannot read it).

import { emit } from './state.js';
import { showToast } from './toast.js';

const STORAGE_KEY = 'azimuth-mobile-write-queue-v1';
const LEGACY_STORAGE_KEY = 'pa-m-write-queue-v1';
const BASE_RETRY_MS = 500;
const MAX_RETRY_MS = 30000;
const WRITE_SYNC_TAG = 'azimuth-write-queue';

let queue = [];
let retryTimer = null;
let draining = false;
let syncListenerBound = false;
let nextItemId = 1;
const pendingOutcomes = new Map();

// One-time rebrand migration: writes queued under the pre-rename key were
// already confirmed in the UI — adopt them ahead of newer entries so they
// still reach the server, and retire the old key only after they are safe.
function adoptLegacyQueue() {
    try {
        const legacy = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || '[]');
        if (Array.isArray(legacy) && legacy.length) {
            const current = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
            const merged = legacy.concat(Array.isArray(current) ? current : []);
            localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
        }
        localStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch {
        // Unreadable or unwritable storage: keep the legacy key for next boot.
    }
}

function loadQueue() {
    adoptLegacyQueue();
    try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        const items = Array.isArray(saved) ? saved.filter((item) => item?.url && item?.body) : [];
        for (const item of items) {
            item.id = Number(item.id) || nextItemId++;
            nextItemId = Math.max(nextItemId, item.id + 1);
        }
        return items;
    } catch {
        return [];
    }
}

let storageWarned = false;

function saveQueue() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
    } catch {
        // The current optimistic state still works, but a reload would drop
        // the queued changes — say so once instead of pretending durability.
        if (!storageWarned && queue.length) {
            storageWarned = true;
            showToast("Storage is full — changes may not survive a reload");
        }
    }
}

function updateBadge() {
    const badge = document.getElementById('m-write-queue');
    if (badge) {
        badge.hidden = queue.length === 0;
        badge.textContent = `${queue.length} queued`;
    }
    emit('write-queue', { count: queue.length });
}

function retryDelay(attempts) {
    return Math.min(MAX_RETRY_MS, BASE_RETRY_MS * (2 ** Math.min(attempts, 6)));
}

function scheduleDrain(delay = 0) {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
        retryTimer = null;
        drainWrites();
    }, delay);
}

async function requestBackgroundSync() {
    if (!queue.length) return;
    if (!window.isSecureContext || !('serviceWorker' in navigator)) return;
    try {
        const registration = await navigator.serviceWorker.ready;
        if (registration && 'sync' in registration) {
            await registration.sync.register(WRITE_SYNC_TAG);
        }
    } catch {
        // Background Sync is optional — localStorage + online events remain.
    }
}

async function send(item) {
    const response = await fetch(item.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item.body),
    });
    if (!response.ok) {
        const error = new Error(`write failed: ${response.status}`);
        error.status = response.status;
        throw error;
    }
}

function finish(item, outcome) {
    const result = { id: item.id, url: item.url, body: item.body, ...outcome };
    pendingOutcomes.get(item.id)?.(result);
    pendingOutcomes.delete(item.id);
}

function isTerminalFailure(error) {
    const status = Number(error?.status || 0);
    return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

export async function drainWrites() {
    if (draining || !queue.length || navigator.onLine === false) return;
    draining = true;
    try {
        while (queue.length && navigator.onLine !== false) {
            const item = queue[0];
            const wait = Number(item.nextAttemptAt || 0) - Date.now();
            if (wait > 0) {
                scheduleDrain(wait);
                return;
            }
            try {
                await send(item);
                queue.shift();
                saveQueue();
                updateBadge();
                finish(item, { status: 'committed' });
            } catch (error) {
                if (isTerminalFailure(error)) {
                    queue.shift();
                    saveQueue();
                    updateBadge();
                    finish(item, { status: 'failed', statusCode: Number(error.status) });
                    showToast('Couldn’t save change');
                    continue;
                }
                item.attempts = Number(item.attempts || 0) + 1;
                item.nextAttemptAt = Date.now() + retryDelay(item.attempts);
                saveQueue();
                updateBadge();
                void requestBackgroundSync();
                scheduleDrain(item.nextAttemptAt - Date.now());
                return;
            }
        }
    } finally {
        draining = false;
    }
}

export function enqueueWrite(url, body) {
    const item = {
        id: nextItemId++,
        url,
        body,
        attempts: 0,
        nextAttemptAt: Date.now(),
    };
    queue.push(item);
    saveQueue();
    updateBadge();
    void drainWrites();
    void requestBackgroundSync();
    return new Promise((resolve) => {
        pendingOutcomes.set(item.id, resolve);
    });
}

export function queueLength() {
    return queue.length;
}

export function peekQueue() {
    return queue.map((item) => ({ url: item.url, body: item.body, attempts: item.attempts }));
}

function bindSyncListener() {
    if (syncListenerBound || !('serviceWorker' in navigator)) return;
    syncListenerBound = true;
    navigator.serviceWorker.addEventListener('message', (event) => {
        if (event.data && event.data.type === 'drain-write-queue') {
            queue.forEach((item) => { item.nextAttemptAt = Date.now(); });
            saveQueue();
            void drainWrites();
        }
    });
}

export function initWriteQueue() {
    queue = loadQueue();
    const badge = document.createElement('div');
    badge.id = 'm-write-queue';
    badge.hidden = true;
    badge.setAttribute('role', 'status');
    badge.setAttribute('aria-live', 'polite');
    document.body.appendChild(badge);
    updateBadge();
    bindSyncListener();
    window.addEventListener('online', () => {
        queue.forEach((item) => { item.nextAttemptAt = Date.now(); });
        saveQueue();
        void drainWrites();
        void requestBackgroundSync();
    });
    if (queue.length) {
        void drainWrites();
        void requestBackgroundSync();
    }
}
