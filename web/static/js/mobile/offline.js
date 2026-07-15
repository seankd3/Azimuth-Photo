// Explicit phone availability for viewer previews. The service worker already
// owns thumbnail delivery; this module pins all viewer sizes in its active
// thumbnail cache and keeps a tiny local index for clear, instant status.

import { thumbUrl } from './api.js';
import { emit } from './state.js';
import { openSheet } from './selection.js';
import { queueLength } from './write_queue.js';
import { showToast } from './toast.js';

const STORAGE_KEY = 'pa-m-offline-photos-v1';
const SIZES = ['sm', 'md', 'lg'];

function loadIndex() {
    try {
        const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
        return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
    } catch {
        return {};
    }
}

let index = loadIndex();

function saveIndex() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(index));
    } catch {
        // Cache state remains valid for this session when storage is unavailable.
    }
    emit('offline-availability', offlineSummary());
}

function cacheVersion() {
    return document.documentElement.dataset.staticVersion || 'dev';
}

async function thumbCache() {
    if (!('caches' in window)) return null;
    const expected = `pa-mobile-${cacheVersion()}-thumbs`;
    const names = await caches.keys();
    const active = names.find((name) => name === expected)
        || names.find((name) => name.startsWith(`pa-mobile-${cacheVersion()}`) && name.endsWith('-thumbs'))
        || expected;
    return caches.open(active);
}

function urlsFor(imageId) {
    return SIZES.map((size) => thumbUrl(size, imageId));
}

export function isAvailableOffline(imageId) {
    return Boolean(index[String(Number(imageId))]);
}

export function offlineSummary() {
    const entries = Object.values(index);
    return {
        count: entries.length,
        bytes: entries.reduce((total, entry) => total + Number(entry?.bytes || 0), 0),
    };
}

async function removeCached(imageId) {
    if ('caches' in window) {
        const names = await caches.keys();
        const thumbnailCaches = names.filter((name) => name.startsWith('pa-mobile-') && name.endsWith('-thumbs'));
        await Promise.all(thumbnailCaches.map(async (name) => {
            const cache = await caches.open(name);
            await Promise.all(urlsFor(imageId).map((url) => cache.delete(url)));
        }));
    }
    delete index[String(Number(imageId))];
    saveIndex();
}

async function cacheImage(imageId) {
    const cache = await thumbCache();
    if (!cache) throw new Error('offline cache unavailable');
    let bytes = 0;
    const inserted = [];
    try {
        for (const url of urlsFor(imageId)) {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`preview unavailable (${response.status})`);
            const copy = response.clone();
            await cache.put(url, response);
            inserted.push(url);
            bytes += (await copy.arrayBuffer()).byteLength;
        }
    } catch (error) {
        await Promise.all(inserted.map((url) => cache.delete(url)));
        throw error;
    }
    index[String(Number(imageId))] = { bytes, savedAt: Date.now() };
    saveIndex();
}

export async function toggleOfflineAvailability(image) {
    const imageId = Number(image?.id || 0);
    if (!imageId) return false;
    if (isAvailableOffline(imageId)) {
        await removeCached(imageId);
        showToast('Removed from this phone');
        return false;
    }
    if (navigator.onLine === false) {
        showToast('Connect to save this photo offline');
        return false;
    }
    try {
        await cacheImage(imageId);
        showToast('Available offline');
        return true;
    } catch {
        showToast("Couldn't save this photo offline");
        return false;
    }
}

function formatBytes(value) {
    let amount = Number(value || 0);
    const units = ['B', 'KB', 'MB', 'GB'];
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
        amount /= 1024;
        unit += 1;
    }
    return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

export function openOfflineStatusSheet() {
    const summary = offlineSummary();
    const queued = queueLength();
    const sheet = openSheet(
        '<h3>Available offline</h3>'
        + '<div class="sheet-meta">'
        + `<div><span>Photos on this phone</span><b>${summary.count.toLocaleString('en-US')}</b></div>`
        + `<div><span>Preview storage</span><b>${formatBytes(summary.bytes)}</b></div>`
        + `<div><span>Changes waiting to sync</span><b>${queued.toLocaleString('en-US')}</b></div>`
        + '</div>'
        + (summary.count
            ? '<button class="sheet-row" id="m-offline-clear"><span class="g">×</span>Remove all offline photos</button>'
            : '<div class="ms-empty">Use the download button in the viewer to keep photos available without a connection.</div>')
    );
    sheet.querySelector('#m-offline-clear')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        const ids = Object.keys(index);
        await Promise.all(ids.map((id) => removeCached(Number(id))));
        showToast('Offline photos removed');
        openOfflineStatusSheet();
    });
}
