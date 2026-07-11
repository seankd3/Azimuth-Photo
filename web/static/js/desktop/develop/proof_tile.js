const TILE_EDGE = 1024;
const PAN_DEBOUNCE_MS = 250;
const SETTINGS_DEBOUNCE_MS = 700;
const MAX_BROWSER_TILES = 12;

function stableSettings(value) {
    if (Array.isArray(value)) return `[${value.map(stableSettings).join(',')}]`;
    if (value && typeof value === 'object') {
        return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableSettings(value[key])}`).join(',')}}`;
    }
    return JSON.stringify(value);
}

function settingsHash(settings) {
    const text = stableSettings(settings || {});
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
}

function numberHeader(response, name, fallback) {
    const value = Number(response.headers.get(name));
    return Number.isFinite(value) ? value : fallback;
}

export class ProofTileController {
    constructor({ stage, getContext, getRenderer, onDisplayChange }) {
        this.stage = stage;
        this.getContext = getContext;
        this.getRenderer = getRenderer;
        this.onDisplayChange = onDisplayChange;
        this.cache = new Map();
        this.held = false;
        this.timer = 0;
        this.token = 0;
        this.abort = null;
        this.makeLayer();
    }

    makeLayer() {
        this.layer = document.createElement('div');
        this.layer.className = 'develop-proof-tile';
        this.layer.hidden = true;
        this.layer.style.cssText = 'position:absolute;z-index:6;inset:0;overflow:hidden;pointer-events:none';
        this.image = document.createElement('img');
        this.image.alt = '';
        this.image.style.cssText = 'position:absolute;display:block;max-width:none;max-height:none;box-shadow:0 0 0 1px rgba(255,255,255,.2),0 10px 35px rgba(0,0,0,.5);image-rendering:auto';
        this.badge = document.createElement('span');
        this.badge.className = 'develop-proof-badge';
        this.badge.textContent = 'PROOF · ORIGINAL 1:1';
        this.badge.style.cssText = 'position:absolute;right:18px;top:18px;padding:4px 7px;border:1px solid rgba(255,255,255,.2);border-radius:3px;color:#fff;font:650 9px/1.2 system-ui;background:rgba(0,0,0,.68);letter-spacing:.06em';
        this.layer.append(this.image, this.badge);
        this.stage.append(this.layer);
    }

    setHeld(held) {
        const next = Boolean(held);
        if (next === this.held) return;
        this.held = next;
        if (next) this.schedule(PAN_DEBOUNCE_MS);
        else this.hide();
    }

    viewChanged() {
        if (this.held) this.schedule(PAN_DEBOUNCE_MS);
    }

    settingsChanged(imageId = null) {
        if (imageId == null) this.clearCache();
        else {
            for (const [key, tile] of this.cache) {
                if (key.startsWith(`${Number(imageId)}:`)) {
                    URL.revokeObjectURL(tile.url);
                    this.cache.delete(key);
                }
            }
        }
        if (this.held) {
            this.hide(false);
            this.schedule(SETTINGS_DEBOUNCE_MS);
        }
    }

    imageChanged() {
        this.hide(false);
        if (this.held) this.schedule(PAN_DEBOUNCE_MS);
    }

    schedule(delay = PAN_DEBOUNCE_MS) {
        clearTimeout(this.timer);
        this.abort?.abort();
        const token = ++this.token;
        this.timer = setTimeout(() => {
            this.load(token).catch((error) => {
                if (error?.name !== 'AbortError' && token === this.token) this.hide(false);
            });
        }, delay);
    }

    async load(token) {
        const context = this.getContext?.();
        if (!this.held || !context?.imageId || !context?.settings) return;
        const u = Math.max(0, Math.min(1, Number(context.center?.u) || .5));
        const v = Math.max(0, Math.min(1, Number(context.center?.v) || .5));
        const location = `${u.toFixed(5)},${v.toFixed(5)}`;
        const key = `${Number(context.imageId)}:${settingsHash(context.settings)}:${location}:${TILE_EDGE}`;
        let tile = this.cache.get(key);
        if (!tile) {
            this.abort = new AbortController();
            const response = await fetch(`/api/develop/${Number(context.imageId)}/proof-tile?u=${u.toFixed(6)}&v=${v.toFixed(6)}&edge=${TILE_EDGE}`, {
                headers: { Accept: 'image/png' }, signal: this.abort.signal,
            });
            if (!response.ok) throw new Error('Original proof is unavailable');
            const blob = await response.blob();
            tile = {
                url: URL.createObjectURL(blob),
                left: numberHeader(response, 'X-Proof-Left', 0),
                top: numberHeader(response, 'X-Proof-Top', 0),
                width: numberHeader(response, 'X-Proof-Width', TILE_EDGE),
                height: numberHeader(response, 'X-Proof-Height', TILE_EDGE),
                sourceWidth: numberHeader(response, 'X-Proof-Source-Width', TILE_EDGE),
                sourceHeight: numberHeader(response, 'X-Proof-Source-Height', TILE_EDGE),
            };
            this.cache.set(key, tile);
            while (this.cache.size > MAX_BROWSER_TILES) {
                const [oldestKey, oldest] = this.cache.entries().next().value;
                URL.revokeObjectURL(oldest.url);
                this.cache.delete(oldestKey);
            }
        } else {
            this.cache.delete(key);
            this.cache.set(key, tile);
        }
        if (!this.held || token !== this.token) return;
        this.show(tile);
    }

    show(tile) {
        const renderer = this.getRenderer?.();
        if (!renderer) return;
        const center = renderer.imageToCanvas(
            (tile.left + tile.width * .5) / tile.sourceWidth,
            (tile.top + tile.height * .5) / tile.sourceHeight,
        );
        const canvas = renderer.canvas.getBoundingClientRect();
        const stage = this.stage.getBoundingClientRect();
        const deviceScale = Math.max(1, window.devicePixelRatio || 1);
        const width = tile.width / deviceScale;
        const height = tile.height / deviceScale;
        const centerX = canvas.left - stage.left + center.x * canvas.width;
        const centerY = canvas.top - stage.top + center.y * canvas.height;
        this.image.src = tile.url;
        this.image.style.left = `${centerX - width * .5}px`;
        this.image.style.top = `${centerY - height * .5}px`;
        this.image.style.width = `${width}px`;
        this.image.style.height = `${height}px`;
        this.layer.hidden = false;
        this.stage.classList.add('proof-active');
        this.onDisplayChange?.(true);
    }

    hide(cancel = true) {
        clearTimeout(this.timer);
        if (cancel) {
            ++this.token;
            this.abort?.abort();
        }
        this.layer.hidden = true;
        this.stage.classList.remove('proof-active');
        this.onDisplayChange?.(false);
    }

    clearCache() {
        for (const tile of this.cache.values()) URL.revokeObjectURL(tile.url);
        this.cache.clear();
    }
}

export { settingsHash };
