// Android Back integration for the mobile shell.
// Each transient UI layer pushes one history state; Back closes the top layer.

const APP_STATE = 'photoarchive-mobile';

const handlers = new Map();
let layers = [];
let tab = 'photos';
let applyingHistory = false;

function appState() {
    return {
        app: APP_STATE,
        tab,
        layers: [...layers],
    };
}

function validLayers(raw) {
    return Array.isArray(raw) ? raw.filter((name) => handlers.has(name)) : [];
}

function replaceCurrent() {
    history.replaceState(appState(), '', location.href);
}

function closeLayerNow(name, options = {}) {
    const handler = handlers.get(name);
    if (handler && typeof handler.close === 'function') {
        handler.close(options);
    }
}

export function initHistory(initialTab = 'photos') {
    tab = initialTab;
    layers = [];
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    replaceCurrent();

    window.addEventListener('popstate', (event) => {
        const state = event.state && event.state.app === APP_STATE ? event.state : {};
        const targetLayers = validLayers(state.layers);
        const targetTab = state.tab || tab;

        applyingHistory = true;
        for (let i = layers.length - 1; i >= 0; i -= 1) {
            const name = layers[i];
            if (!targetLayers.includes(name)) closeLayerNow(name, { fromHistory: true });
        }
        layers = targetLayers;
        tab = targetTab;
        applyingHistory = false;
    });
}

export function registerLayer(name, handler) {
    handlers.set(name, handler || {});
}

export function layerActive(name) {
    return layers.includes(name);
}

export function pushLayer(name) {
    if (applyingHistory) return;
    if (layers[layers.length - 1] === name) {
        replaceCurrent();
        return;
    }
    layers = layers.filter((layer) => layer !== name);
    layers.push(name);
    history.pushState(appState(), '', location.href);
}

export function syncLayerClosed(name) {
    if (applyingHistory || !layers.includes(name)) return;
    layers = layers.filter((layer) => layer !== name);
    replaceCurrent();
}

export function dismissLayer(name, fallback = null) {
    const close = fallback || ((options) => closeLayerNow(name, options));
    if (layers[layers.length - 1] === name) {
        applyingHistory = true;
        close({ fromHistory: false });
        applyingHistory = false;
        layers = layers.filter((layer) => layer !== name);
        history.back();
        return;
    }
    close({ fromHistory: false });
    syncLayerClosed(name);
}

export function replaceTab(nextTab) {
    tab = nextTab;
    if (!applyingHistory) replaceCurrent();
}
