import { numberSetting } from './ops_constants.js';

const WHEELS = Object.freeze([
    ['Shadow', 'Shadows'], ['Midtone', 'Midtones'], ['Highlight', 'Highlights'], ['Global', 'Global'],
]);

function polar(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width * canvas.width;
    const y = (event.clientY - rect.top) / rect.height * canvas.height;
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const dx = x - cx;
    const dy = cy - y;
    return { hue: (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, saturation: Math.min(100, Math.hypot(dx, dy) / (canvas.width * .39) * 100) };
}

function snapshotSettings(settings) {
    return JSON.parse(JSON.stringify(settings || {}));
}

function discFor(canvas, cache) {
    // The hue disc never changes — regenerating its per-pixel ImageData on
    // every puck move makes dragging visibly laggy, so cache one per canvas.
    const cached = cache.get(canvas);
    if (cached?.width === canvas.width && cached.height === canvas.height) return cached.canvas;
    const disc = document.createElement('canvas');
    const { width, height } = canvas;
    disc.width = width; disc.height = height;
    const image = disc.getContext('2d').createImageData(width, height);
    const center = width / 2;
    const radius = width * .39;
    for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
        const dx = x - center; const dy = center - y;
        const distance = Math.hypot(dx, dy) / radius;
        const index = (y * width + x) * 4;
        if (distance > 1) continue;
        const angle = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
        const c = (1 - Math.abs(2 * .58 - 1)) * Math.min(1, distance);
        const h = angle / 60; const q = c * (1 - Math.abs(h % 2 - 1)); const m = .58 - c / 2;
        const [r, g, b] = h < 1 ? [c, q, 0] : h < 2 ? [q, c, 0] : h < 3 ? [0, c, q] : h < 4 ? [0, q, c] : h < 5 ? [q, 0, c] : [c, 0, q];
        image.data[index] = (r + m) * 255; image.data[index + 1] = (g + m) * 255; image.data[index + 2] = (b + m) * 255; image.data[index + 3] = 255;
    }
    disc.getContext('2d').putImageData(image, 0, 0);
    cache.set(canvas, { canvas: disc, width, height });
    return disc;
}

function drawWheel(canvas, hue, saturation, cache) {
    const context = canvas.getContext('2d');
    const { width, height } = canvas;
    const center = width / 2;
    const radius = width * .39;
    context.clearRect(0, 0, width, height);
    context.drawImage(discFor(canvas, cache), 0, 0);
    const angle = hue * Math.PI / 180;
    const distance = radius * saturation / 100;
    context.beginPath(); context.arc(center + Math.cos(angle) * distance, center - Math.sin(angle) * distance, 5, 0, Math.PI * 2);
    context.fillStyle = '#fff'; context.fill(); context.lineWidth = 2; context.strokeStyle = '#111'; context.stroke();
}

export class ColorWheels {
    constructor(host, onChange) {
        this.host = host;
        this.onChange = onChange;
        this.settings = {};
        this.discs = new WeakMap();
        host.innerHTML = `<div class="develop-wheels">${WHEELS.map(([key, label]) => `<div class="develop-wheel" data-wheel="${key}"><canvas width="112" height="112" aria-label="${label} color wheel" data-tip="Drag puck to grade ${label}; double-click resets"></canvas><b>${label}</b><label>Lum <input type="range" min="-100" max="100" value="0" aria-label="${label} luminance" data-wheel-lum="${key}"></label></div>`).join('')}</div>`;
        for (const [key] of WHEELS) {
            const canvas = host.querySelector(`[data-wheel="${key}"] canvas`);
            canvas.addEventListener('pointerdown', (event) => this.drag(event, key, canvas));
            canvas.addEventListener('dblclick', () => this.reset(key));
            const luminance = host.querySelector(`[data-wheel-lum="${key}"]`);
            let gesture = null;
            luminance.addEventListener('pointerdown', (event) => {
                luminance.setPointerCapture(event.pointerId);
                gesture = { previousSettings: snapshotSettings(this.settings), settings: this.settings, changed: false };
            });
            luminance.addEventListener('input', (event) => {
                if (gesture && gesture.settings !== this.settings) { gesture = null; return; }
                const value = Number(event.target.value);
                gesture &&= { ...gesture, changed: gesture.changed || value !== numberSetting(gesture.previousSettings, `ColorGrade${key}Lum`) };
                this.change(`${key}Lum`, value, `${key} luminance`, gesture ? { history: false } : undefined);
            });
            const finishGesture = () => {
                if (!gesture?.changed || gesture.settings !== this.settings) { gesture = null; return; }
                this.change(`${key}Lum`, numberSetting(this.settings, `ColorGrade${key}Lum`), `${key} luminance`, { previousSettings: gesture.previousSettings });
                gesture = null;
            };
            luminance.addEventListener('pointerup', finishGesture);
            luminance.addEventListener('pointercancel', finishGesture);
        }
    }

    change(suffix, value, label, options) {
        const key = `ColorGrade${suffix}`;
        this.settings[key] = value;
        this.onChange(key, value, 'Color Grading: ' + label, options);
        this.draw();
    }

    drag(event, key, canvas) {
        canvas.setPointerCapture(event.pointerId);
        const previousSettings = snapshotSettings(this.settings);
        const gestureSettings = this.settings;
        let changed = false;
        const update = (next) => {
            if (this.settings !== gestureSettings) return;
            const value = polar(next, canvas);
            const hue = Math.round(value.hue);
            const saturation = Math.round(value.saturation);
            changed ||= hue !== numberSetting(previousSettings, `ColorGrade${key}Hue`) || saturation !== numberSetting(previousSettings, `ColorGrade${key}Sat`);
            // One emit + one puck redraw per move: two change() calls would
            // double-render and repaint all four wheels mid-drag.
            this.settings[`ColorGrade${key}Hue`] = hue;
            this.settings[`ColorGrade${key}Sat`] = saturation;
            this.onChange(`ColorGrade${key}Hue`, hue, 'Color Grading: ' + key + ' color', { history: false });
            drawWheel(canvas, hue, saturation, this.discs);
        };
        const done = () => {
            canvas.removeEventListener('pointermove', update); canvas.removeEventListener('pointerup', done); canvas.removeEventListener('pointercancel', done);
            if (changed && this.settings === gestureSettings) this.change(`${key}Hue`, numberSetting(this.settings, `ColorGrade${key}Hue`), `${key} color`, { previousSettings });
        };
        update(event); canvas.addEventListener('pointermove', update); canvas.addEventListener('pointerup', done); canvas.addEventListener('pointercancel', done);
    }

    reset(key) {
        for (const suffix of ['Hue', 'Sat', 'Lum']) this.change(`${key}${suffix}`, 0, `${key} reset`);
    }

    draw() {
        for (const [key] of WHEELS) {
            drawWheel(this.host.querySelector(`[data-wheel="${key}"] canvas`), numberSetting(this.settings, `ColorGrade${key}Hue`), numberSetting(this.settings, `ColorGrade${key}Sat`), this.discs);
            this.host.querySelector(`[data-wheel-lum="${key}"]`).value = numberSetting(this.settings, `ColorGrade${key}Lum`);
        }
    }

    setSettings(settings) { this.settings = settings || {}; this.draw(); }
}
