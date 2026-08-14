// The offline service worker is gone — deliberately, permanently.
//
// A local-first app whose engine lives on the same machine has no
// meaningful "offline": when the engine is down the app is down, and the
// worker that pretended otherwise served synthetic "504 Offline" for every
// module and tile whose ?v= stamp changed — which is every engine swap.
// It broke the app it was built to protect (2026-08-14: "0 photos" on a
// healthy engine, bootstrap.js itself refused by the worker's own cache
// miss).
//
// This stub stays at the old URL so every installed client — the desktop
// webview and any phone that registered the mobile worker — sheds the
// poisoned worker and its caches on its next load, then gets out of the
// way forever. No fetch handler: every request goes straight to the
// engine.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
        await self.registration.unregister();
        const windows = await self.clients.matchAll({ type: 'window' });
        windows.forEach((client) => client.navigate(client.url));
    })());
});
