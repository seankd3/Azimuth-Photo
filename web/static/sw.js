/* Azimuth Photo service worker (mobile shell + shared thumb cache).
 *
 * Strategy:
 * - Precache the /m app shell (page, css, js modules, manifest, icon).
 * - Stale-while-revalidate for thumbnail GETs (/api/thumb/*), size-bounded
 *   Cache API. Only 200 image/* responses stored (never 204, never JSON).
 * - Network-only for every other /api request: rankings, counts, writes.
 * - Navigations fall back to the cached /m shell when offline.
 * - Background Sync tag `azimuth-write-queue` wakes open clients so
 *   write_queue.js can drain its localStorage queue (SW cannot read
 *   localStorage — the page queue remains the source of truth).
 *
 * Disable from the page: ?pa_sw=0 or localStorage pa_sw=0 (see sw_register.js).
 *
 * Versioning: register as `/sw.js?v=<static_version>` (same cache-bust
 * idiom as `?v={{ static_version }}` on CSS/JS). The query value becomes
 * CACHE_VERSION so shell updates drop old caches automatically.
 */

const CACHE_VERSION = (() => {
    try {
        const version = new URL(self.location.href).searchParams.get('v');
        return version ? `azimuth-mobile-${version}` : 'azimuth-mobile-dev';
    } catch {
        return 'azimuth-mobile-dev';
    }
})();
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const THUMB_CACHE = `${CACHE_VERSION}-thumbs`;
const THUMB_CACHE_MAX_ENTRIES = 4000;
const WRITE_SYNC_TAG = 'azimuth-write-queue';

const SHELL_URLS = [
    '/m',
    '/static/mobile.css',
    '/static/manifest.webmanifest',
    '/static/icons/sprite.svg',
    '/static/icons/icon.svg',
    '/static/js/icons.js',
    '/static/js/people_labels.js',
    '/static/js/previews.js',
    '/static/js/worker_state.js',
    '/static/js/api.js',
    '/static/js/sw_register.js',
    '/static/js/mobile/api.js',
    '/static/js/mobile/bootstrap.js',
    '/static/js/mobile/caption_cache.js',
    '/static/js/mobile/flag_scope.js',
    '/static/js/mobile/flags.js',
    '/static/js/mobile/haptics.js',
    '/static/js/mobile/history.js',
    '/static/js/mobile/https_origin.js',
    '/static/js/mobile/install.js',
    '/static/js/mobile/library.js',
    '/static/js/mobile/refine.js',
    '/static/js/mobile/scrubber.js',
    '/static/js/mobile/search.js',
    '/static/js/mobile/selection.js',
    '/static/js/mobile/state.js',
    '/static/js/mobile/timeline.js',
    '/static/js/mobile/toast.js',
    '/static/js/mobile/viewer.js',
    '/static/js/mobile/viewer_momentum.js',
    '/static/js/mobile/write_queue.js',
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

function isCacheableThumbResponse(response) {
    if (!response || response.status !== 200) return false;
    const type = (response.headers.get('content-type') || '').toLowerCase();
    return type.startsWith('image/');
}

async function trimThumbCache() {
    const cache = await caches.open(THUMB_CACHE);
    const keys = await cache.keys();
    if (keys.length <= THUMB_CACHE_MAX_ENTRIES) return;
    const excess = keys.length - THUMB_CACHE_MAX_ENTRIES;
    for (let i = 0; i < excess; i++) {
        await cache.delete(keys[i]);
    }
}

/** Serve stored JPEG instantly, refresh in background — thumb URLs are
 * unversioned, so pure cache-first would pin stale pixels after an edit. */
async function thumbStaleWhileRevalidate(request) {
    const cache = await caches.open(THUMB_CACHE);
    const cached = await cache.match(request);
    const refresh = fetch(request).then(async (response) => {
        if (isCacheableThumbResponse(response)) {
            await cache.put(request, response.clone());
            trimThumbCache();
        }
        return response;
    }).catch(() => null);
    if (cached) return cached;
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

async function staticNetworkFirst(request) {
    const cache = await caches.open(SHELL_CACHE);
    try {
        const response = await fetch(request);
        if (response && response.ok) {
            cache.put(request, response.clone());
        }
        return response;
    } catch {
        // Fall back only after the network fails, and prefer exact versioned
        // keys so CSS/JS deploys do not mix old and new app-shell contracts.
    }
    const cached = await cache.match(request);
    if (cached) return cached;
    const url = new URL(request.url);
    const unversioned = await cache.match(url.pathname);
    if (unversioned) return unversioned;
    return new Response('', { status: 504, statusText: 'Offline' });
}

async function notifyClientsToDrainQueue() {
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of clients) {
        client.postMessage({ type: 'drain-write-queue' });
    }
}

self.addEventListener('sync', (event) => {
    if (event.tag !== WRITE_SYNC_TAG) return;
    event.waitUntil(notifyClientsToDrainQueue());
});

self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'drain-write-queue') {
        event.waitUntil(notifyClientsToDrainQueue());
    }
});

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
        if (url.pathname === '/m' || url.pathname === '/m/') {
            event.respondWith(shellFirstNavigation(request));
        }
        return;
    }
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(staticNetworkFirst(request));
    }
});
