import { bytes as formatBytes } from '../lib.js';
// Explicit phone availability for viewer previews. The service worker owns
// thumbnail delivery; this module pins all viewer sizes into the dedicated
// pinned cache (served first by sw.js, exempt from version rotation and
// trimming) and keeps a tiny local index for clear, instant status.

import { thumbUrl } from './api.js';
import { emit } from './state.js';
import { openSheet } from './selection.js';
import { queueLength } from './write_queue.js';
import { showToast } from './toast.js';

const STORAGE_KEY = 'azimuth-mobile-offline-photos-v1';
const LEGACY_STORAGE_KEY = 'pa-m-offline-photos-v1';
// Pins live in their own version-independent cache: sw.js serves it first,
// never trims it, and keeps it across shell version rotations, so a deploy
// can never delete photos the user explicitly saved to this phone.
const PIN_CACHE = 'azimuth-mobile-pinned-thumbs';
const SIZES = ['sm', 'md', 'lg'];

// One-time rebrand migration: adopt the pre-rename index, then retire the old
// key. reconcileIndex() below drops any adopted entries whose bytes are gone.
function adoptLegacyIndex() {
    try {
        const legacy = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || 'null');
        if (legacy && typeof legacy === 'object' && !Array.isArray(legacy)) {
            const current = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...legacy, ...current }));
        }
        localStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch {
        // Unreadable or unwritable storage: keep the legacy key for next boot.
    }
}

function loadIndex() {
    adoptLegacyIndex();
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

async function pinnedCache() {
    if (!('caches' in window)) return null;
    return caches.open(PIN_CACHE);
}

function urlsFor(imageId) {
    return SIZES.map((size) => thumbUrl(size, imageId));
}

// The index is a claim; the pin cache is the truth. On boot, re-adopt pinned
// previews still sitting in older thumb caches (pre-pin-cache pins lived in
// the version-rotated SWR cache) and drop entries whose bytes are actually
// gone, so "Available offline" never claims photos this phone no longer has.
async function reconcileIndex() {
    if (!('caches' in window)) return;
    try {
        const cache = await caches.open(PIN_CACHE);
        const donorNames = (await caches.keys())
            .filter((name) => name !== PIN_CACHE && name.endsWith('-thumbs'));
        const donors = await Promise.all(donorNames.map((name) => caches.open(name)));
        let changed = false;
        for (const id of Object.keys(index)) {
            let present = true;
            for (const url of urlsFor(id)) {
                if (await cache.match(url)) continue;
                let adopted = null;
                for (const donor of donors) {
                    adopted = await donor.match(url);
                    if (adopted) break;
                }
                if (adopted) {
                    await cache.put(url, adopted);
                    continue;
                }
                present = false;
                break;
            }
            if (!present) {
                delete index[id];
                changed = true;
            }
        }
        if (changed) saveIndex();
    } catch {
        // Cache API failed mid-check — keep the current claim rather than
        // dropping pins we could not actually verify.
    }
}

void reconcileIndex();

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
        const thumbnailCaches = names.filter((name) => name.startsWith('azimuth-mobile-') && name.endsWith('-thumbs'));
        await Promise.all(thumbnailCaches.map(async (name) => {
            const cache = await caches.open(name);
            await Promise.all(urlsFor(imageId).map((url) => cache.delete(url)));
        }));
    }
    delete index[String(Number(imageId))];
    saveIndex();
}

async function cacheImage(imageId) {
    const cache = await pinnedCache();
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
        try {
            await Promise.all(ids.map((id) => removeCached(Number(id))));
            showToast('Offline photos removed');
        } catch {
            // A silent throw left the button dead and the photos half-removed.
            showToast("Couldn't remove offline photos — try again");
        }
        openOfflineStatusSheet();
    });
}
