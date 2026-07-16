const CHANNELS = [
    { key: 0, color: '#f45b69' },
    { key: 1, color: '#56d68b' },
    { key: 2, color: '#5aa9ff' },
];

const TONE_ZONES = [
    { key: 'Blacks2012', label: 'Blacks', start: 0, end: .06, min: -100, max: 100 },
    { key: 'Shadows2012', label: 'Shadows', start: .06, end: .25, min: -100, max: 100 },
    { key: 'Exposure2012', label: 'Exposure', start: .25, end: .75, min: -5, max: 5 },
    { key: 'Highlights2012', label: 'Highlights', start: .75, end: .94, min: -100, max: 100 },
    { key: 'Whites2012', label: 'Whites', start: .94, end: 1, min: -100, max: 100 },
];

export function histogramZoneAtPosition(position) {
    const normalized = Math.max(0, Math.min(1, position));
    return TONE_ZONES.find((zone) => normalized < zone.end) || TONE_ZONES.at(-1);
}

export class DevelopHistogram {
    constructor(host, { onClipToggle, onAdjust } = {}) {
        this.host = host;
        this.onClipToggle = onClipToggle;
        this.onAdjust = onAdjust || ((key, delta) => this.host.dispatchEvent(new CustomEvent('histogram:adjust', {
            bubbles: true,
            detail: { key, delta },
        })));
        this.host.innerHTML = '<div class="develop-hist-wrap">'
            + '<button class="develop-clip develop-clip-shadow" data-tip="Shadow clipping" aria-label="Shadow clipping">◢</button>'
            + '<canvas class="develop-hist" width="288" height="112" aria-label="RGB histogram"></canvas>'
            + '<button class="develop-clip develop-clip-highlight" data-tip="Highlight clipping" aria-label="Highlight clipping">◣</button>'
            + '</div>';
        for (const side of ['shadow', 'highlight']) {
            const button = this.host.querySelector(`.develop-clip-${side}`);
            button.addEventListener('click', () => {
                const active = button.classList.toggle('active');
                button.setAttribute('aria-pressed', String(active));
                this.onClipToggle?.(side, active);
            });
        }
        this.canvas = this.host.querySelector('canvas');
        this.context = this.canvas.getContext('2d');
        this.sampleCanvas = document.createElement('canvas');
        this.sampleContext = this.sampleCanvas.getContext('2d', { willReadFrequently: true });
        this.lastUpdate = 0;
        this.activeZone = null;
        this.drag = null;
        this.bindToneDrag();
    }

    bindToneDrag() {
        this.canvas.addEventListener('pointermove', (event) => {
            if (this.drag) this.adjustFromPointer(event);
            else this.setActiveZone(this.zoneForEvent(event));
        });
        this.canvas.addEventListener('pointerleave', () => {
            if (!this.drag) this.setActiveZone(null);
        });
        this.canvas.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            const zone = this.zoneForEvent(event);
            event.preventDefault();
            this.drag = { pointerId: event.pointerId, startX: event.clientX, zone };
            this.canvas.setPointerCapture(event.pointerId);
            this.setActiveZone(zone);
        });
        const finishDrag = (event) => {
            if (!this.drag || event.pointerId !== this.drag.pointerId) return;
            this.drag = null;
            this.setActiveZone(this.zoneForEvent(event));
        };
        this.canvas.addEventListener('pointerup', finishDrag);
        this.canvas.addEventListener('pointercancel', finishDrag);
    }

    zoneForEvent(event) {
        const rect = this.canvas.getBoundingClientRect();
        return histogramZoneAtPosition((event.clientX - rect.left) / Math.max(1, rect.width));
    }

    setActiveZone(zone) {
        if (this.activeZone === zone) return;
        this.activeZone = zone;
        this.canvas.dataset.zone = zone?.key || '';
        this.canvas.title = zone ? `${zone.label} — drag horizontally to adjust` : '';
        this.canvas.setAttribute('aria-label', zone ? `${zone.label} histogram region — drag horizontally to adjust` : 'RGB histogram');
        this.draw();
    }

    adjustFromPointer(event) {
        const { zone, startX } = this.drag;
        const width = Math.max(1, this.canvas.getBoundingClientRect().width);
        const sensitivity = event.shiftKey ? .1 : 1;
        const delta = (event.clientX - startX) / width * (zone.max - zone.min) * sensitivity;
        this.onAdjust(zone.key, delta);
    }

    interactionActive() {
        return Boolean(this.drag || this.host.ownerDocument.querySelector('.develop-slider.dragging'));
    }

    updateFromRenderer(renderer) {
        if (this.interactionActive()) return;
        const now = performance.now();
        if (now - this.lastUpdate < 120) return;
        this.lastUpdate = now;
        const sourceWidth = renderer.canvas.width;
        const sourceHeight = renderer.canvas.height;
        if (!sourceWidth || !sourceHeight) return;
        const sampleWidth = Math.min(256, sourceWidth);
        const sampleHeight = Math.min(144, sourceHeight);
        this.sampleCanvas.width = sampleWidth;
        this.sampleCanvas.height = sampleHeight;
        this.sampleContext.drawImage(renderer.canvas, 0, 0, sampleWidth, sampleHeight);
        this.update(this.sampleContext.getImageData(0, 0, sampleWidth, sampleHeight).data);
    }

    setClipState(shadow, highlight) {
        for (const [side, active] of [['shadow', shadow], ['highlight', highlight]]) {
            const button = this.host.querySelector(`.develop-clip-${side}`);
            button.classList.toggle('active', Boolean(active));
            button.setAttribute('aria-pressed', String(Boolean(active)));
        }
    }

    setLoading(loading) {
        this.host.dataset.loading = String(Boolean(loading));
    }

    update(bytes) {
        const bins = CHANNELS.map(() => new Uint32Array(256));
        for (let i = 0; i < bytes.length; i += 4) {
            bins[0][bytes[i]] += 1;
            bins[1][bytes[i + 1]] += 1;
            bins[2][bytes[i + 2]] += 1;
        }
        this.bins = bins;
        this.peak = Math.max(1, ...bins.flatMap((values) => [...values.slice(1, 255)]));
        this.updateClipIndicators(bins, bytes.length / 4);
        this.draw();
    }

    draw() {
        const bins = this.bins || CHANNELS.map(() => new Uint32Array(256));
        const peak = this.peak || 1;
        const ctx = this.context;
        const { width, height } = this.canvas;
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = '#111317';
        ctx.fillRect(0, 0, width, height);
        ctx.globalCompositeOperation = 'screen';
        for (const channel of CHANNELS) {
            ctx.beginPath();
            ctx.moveTo(0, height);
            for (let x = 0; x < width; x += 1) {
                const bin = Math.min(255, Math.floor(x / Math.max(1, width - 1) * 255));
                const y = height - Math.sqrt(bins[channel.key][bin] / peak) * (height - 5);
                ctx.lineTo(x, y);
            }
            ctx.lineTo(width, height);
            ctx.closePath();
            ctx.globalAlpha = .48;
            ctx.fillStyle = channel.color;
            ctx.fill();
        }
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
        if (this.activeZone) {
            const { start, end, label } = this.activeZone;
            ctx.fillStyle = 'rgba(255,255,255,.14)';
            ctx.fillRect(start * width, 0, (end - start) * width, height);
            ctx.fillStyle = 'rgba(255,255,255,.82)';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(label, (start + end) / 2 * width, 15);
        }
    }

    updateClipIndicators(bins, total) {
        const sampleCount = Math.max(1, total);
        this.host.querySelector('.develop-clip-shadow').classList.toggle('clipped', bins.some((b) => b[0] / sampleCount > .01));
        this.host.querySelector('.develop-clip-highlight').classList.toggle('clipped', bins.some((b) => b[255] / sampleCount > .01));
    }
}
