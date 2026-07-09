// Android Back integration for the mobile shell.
// Each transient UI layer pushes one history state; Back closes the top layer.

const APP_STATE = 'photoarchive-mobile';

const handlers = new Map();
let layers = [];
let tab = 'photos';
let applyingHistory = false;
let afterPopCallbacks = [];
let tabHandler = null;

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

function pushRootGuards(count = 2) {
    for (let i = 0; i < count; i += 1) {
        history.pushState(appState(), '', location.href);
    }
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
    // Root guard: rapid Android Back after layer cleanup should land on the
    // shell, not fall through to the browser's blank/pre-app entry.
    pushRootGuards();

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
        const tabChanged = targetTab !== tab;
        tab = targetTab;
        if (tabChanged && typeof tabHandler === 'function') tabHandler(targetTab);
        applyingHistory = false;
        const callbacks = afterPopCallbacks;
        afterPopCallbacks = [];
        for (const callback of callbacks) callback();
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
    if (!layers.length) pushRootGuards();
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
    dismissLayerThen(name, fallback);
}

export function dismissLayerThen(name, fallback = null, afterClose = null) {
    const close = fallback || ((options) => closeLayerNow(name, options));
    if (layers[layers.length - 1] === name) {
        applyingHistory = true;
        close({ fromHistory: false });
        applyingHistory = false;
        layers = layers.filter((layer) => layer !== name);
        if (typeof afterClose === 'function') afterPopCallbacks.push(afterClose);
        history.back();
        return;
    }
    close({ fromHistory: false });
    syncLayerClosed(name);
    if (typeof afterClose === 'function') afterClose();
}

export function replaceTab(nextTab) {
    tab = nextTab;
    if (!applyingHistory) replaceCurrent();
}

export function onHistoryTab(handler) {
    tabHandler = typeof handler === 'function' ? handler : null;
}
