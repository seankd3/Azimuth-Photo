// Durable, client-side write-ahead queue for field culling. It deliberately
// uses localStorage rather than a service worker so it works on today's HTTP
// and flaky-link deployment.

import { emit } from './state.js';

const STORAGE_KEY = 'pa-m-write-queue-v1';
const BASE_RETRY_MS = 500;
const MAX_RETRY_MS = 30000;

let queue = [];
let retryTimer = null;
let draining = false;

function loadQueue() {
    try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        return Array.isArray(saved) ? saved.filter((item) => item?.url && item?.body) : [];
    } catch {
        return [];
    }
}

function saveQueue() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
    } catch {
        // The current optimistic state still works if storage is unavailable.
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

async function send(item) {
    const response = await fetch(item.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item.body),
    });
    if (!response.ok) throw new Error(`write failed: ${response.status}`);
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
            } catch {
                item.attempts = Number(item.attempts || 0) + 1;
                item.nextAttemptAt = Date.now() + retryDelay(item.attempts);
                saveQueue();
                updateBadge();
                scheduleDrain(item.nextAttemptAt - Date.now());
                return;
            }
        }
    } finally {
        draining = false;
    }
}

export function enqueueWrite(url, body) {
    queue.push({
        url,
        body,
        attempts: 0,
        nextAttemptAt: Date.now(),
    });
    saveQueue();
    updateBadge();
    void drainWrites();
    return { queued: true };
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
    window.addEventListener('online', () => {
        queue.forEach((item) => { item.nextAttemptAt = Date.now(); });
        saveQueue();
        void drainWrites();
    });
    if (queue.length) void drainWrites();
}
