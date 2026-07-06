/* photoArchive mobile service worker.
 *
 * Strategy:
 * - Precache the /m app shell (page, css, js modules, manifest, icon).
 * - Stale-while-revalidate for thumbnails (/api/thumb/*), capped.
 * - Network-only for every other /api request: writes and live data
 *   must never be answered from a cache.
 * - Navigations fall back to the cached /m shell when offline.
 */

const CACHE_VERSION = 'pa-mobile-v1';
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const THUMB_CACHE = `${CACHE_VERSION}-thumbs`;
const THUMB_CACHE_MAX_ENTRIES = 1500;

const SHELL_URLS = [
    '/m',
    '/static/mobile.css',
    '/static/manifest.webmanifest',
    '/static/icons/icon.svg',
    '/static/js/api.js',
    '/static/js/mobile/api.js',
    '/static/js/mobile/bootstrap.js',
    '/static/js/mobile/flags.js',
    '/static/js/mobile/library.js',
    '/static/js/mobile/refine.js',
    '/static/js/mobile/scrubber.js',
    '/static/js/mobile/search.js',
    '/static/js/mobile/selection.js',
    '/static/js/mobile/state.js',
    '/static/js/mobile/timeline.js',
    '/static/js/mobile/toast.js',
    '/static/js/mobile/viewer.js',
];

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(SHELL_CACHE);
        await cache.addAll(SHELL_URLS);
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(
            names
                .filter((name) => !name.startsWith(CACHE_VERSION))
                .map((name) => caches.delete(name))
        );
        await self.clients.claim();
    })());
});

async function trimThumbCache() {
    const cache = await caches.open(THUMB_CACHE);
    const keys = await cache.keys();
    if (keys.length <= THUMB_CACHE_MAX_ENTRIES) return;
    const excess = keys.length - THUMB_CACHE_MAX_ENTRIES;
    for (let i = 0; i < excess; i++) {
        await cache.delete(keys[i]);
    }
}

async function thumbStaleWhileRevalidate(request) {
    const cache = await caches.open(THUMB_CACHE);
    const cached = await cache.match(request);
    const refresh = fetch(request).then((response) => {
        if (response && response.ok) {
            cache.put(request, response.clone());
            trimThumbCache();
        }
        return response;
    }).catch(() => null);
    if (cached) {
        return cached;
    }
    const fresh = await refresh;
    if (fresh) return fresh;
    return new Response('', { status: 504, statusText: 'Offline' });
}

async function shellFirstNavigation(request) {
    try {
        const response = await fetch(request);
        if (response && response.ok) {
            const cache = await caches.open(SHELL_CACHE);
            cache.put('/m', response.clone());
        }
        return response;
    } catch (error) {
        const cache = await caches.open(SHELL_CACHE);
        const cached = await cache.match('/m');
        if (cached) return cached;
        throw error;
    }
}

async function staticStaleWhileRevalidate(request) {
    const cache = await caches.open(SHELL_CACHE);
    const cached = await cache.match(request, { ignoreSearch: true });
    const refresh = fetch(request).then((response) => {
        if (response && response.ok) {
            cache.put(request, response.clone());
        }
        return response;
    }).catch(() => null);
    if (cached) return cached;
    const fresh = await refresh;
    if (fresh) return fresh;
    return new Response('', { status: 504, statusText: 'Offline' });
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;

    if (url.pathname.startsWith('/api/thumb/')) {
        event.respondWith(thumbStaleWhileRevalidate(request));
        return;
    }
    if (url.pathname.startsWith('/api/')) {
        return; // network-only: live data and writes are never cached
    }
    if (request.mode === 'navigate') {
        if (url.pathname === '/m') {
            event.respondWith(shellFirstNavigation(request));
        }
        return;
    }
    if (url.pathname.startsWith('/static/') || url.pathname === '/sw.js') {
        event.respondWith(staticStaleWhileRevalidate(request));
    }
});
