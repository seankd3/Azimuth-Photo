/**
 * Register (or disable) the photo-archive service worker.
 *
 * Disable without redeploy:
 *   - URL query: ?pa_sw=0  (also ?pa_sw=off / false / no)
 *   - localStorage: pa_sw=0
 * Force on despite localStorage: ?pa_sw=1
 *
 * Requires a secure context (HTTPS or localhost). Plain HTTP is a no-op —
 * browser HTTP cache headers still apply.
 */

const STORAGE_KEY = 'pa_sw';

function queryFlag() {
    try {
        return new URLSearchParams(window.location.search).get('pa_sw');
    } catch {
        return null;
    }
}

function normalizeFlag(value) {
    if (value == null || value === '') return null;
    const normalized = String(value).trim().toLowerCase();
    if (['0', 'off', 'false', 'no', 'disable', 'disabled'].includes(normalized)) return false;
    if (['1', 'on', 'true', 'yes', 'enable', 'enabled'].includes(normalized)) return true;
    return null;
}

export function isServiceWorkerDesired() {
    const fromQuery = normalizeFlag(queryFlag());
    if (fromQuery != null) return fromQuery;
    try {
        return normalizeFlag(localStorage.getItem(STORAGE_KEY)) !== false;
    } catch {
        return true;
    }
}

async function unregisterAll() {
    if (!('serviceWorker' in navigator)) return;
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
}

export async function registerPhotoArchiveServiceWorker() {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return { ok: false, reason: 'unsupported' };

    if (!isServiceWorkerDesired()) {
        await unregisterAll().catch(() => {});
        return { ok: false, reason: 'disabled' };
    }

    const version = document.documentElement.getAttribute('data-static-version') || '';
    const url = version
        ? `/sw.js?v=${encodeURIComponent(version)}`
        : '/sw.js';
    try {
        await navigator.serviceWorker.register(url);
        return { ok: true, url };
    } catch (error) {
        return { ok: false, reason: 'register-failed', error };
    }
}

export function scheduleServiceWorkerRegistration() {
    const run = () => {
        registerPhotoArchiveServiceWorker().catch(() => {});
    };
    if (document.readyState === 'complete') run();
    else window.addEventListener('load', run, { once: true });
}
