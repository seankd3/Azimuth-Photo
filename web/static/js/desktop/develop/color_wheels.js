import { numberSetting } from './ops_constants.js';

const WHEELS = Object.freeze([
    ['Shadow', 'Shadows'], ['Midtone', 'Midtones'], ['Highlight', 'Highlights'], ['Global', 'Global'],
]);

function snapshot(settings) {
    return JSON.parse(JSON.stringify(settings || {}));
}

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

function discFor(canvas, cache) {
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

function drawPuck(canvas, hue, saturation, cache) {
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
            this.bindLuminance(host.querySelector(`[data-wheel-lum="${key}"]`), key);
        }
    }

    emit(suffix, value, label, options) {
        const key = `ColorGrade${suffix}`;
        this.settings[key] = value;
        this.onChange(key, value, 'Color Grading: ' + label, options);
    }

    bindLuminance(input, key) {
        let previousSettings = null;
        let changed = false;
        input.addEventListener('pointerdown', () => { previousSettings = snapshot(this.settings); changed = false; });
        input.addEventListener('input', () => {
            if (!previousSettings) previousSettings = snapshot(this.settings);
            const value = Number(input.value);
            if (value === numberSetting(this.settings, `ColorGrade${key}Lum`)) return;
            changed = true;
            this.emit(`${key}Lum`, value, `${key} luminance`, { history: false });
        });
        input.addEventListener('change', () => {
            if (changed) this.emit(`${key}Lum`, Number(input.value), `${key} luminance`, { previousSettings });
            previousSettings = null; changed = false;
        });
    }

    drag(event, key, canvas) {
        canvas.setPointerCapture(event.pointerId);
        const previousSettings = snapshot(this.settings);
        let changed = false;
        const update = (next) => {
            const value = polar(next, canvas);
            const hue = Math.round(value.hue), saturation = Math.round(value.saturation);
            if (hue === numberSetting(this.settings, `ColorGrade${key}Hue`) && saturation === numberSetting(this.settings, `ColorGrade${key}Sat`)) return;
            this.settings[`ColorGrade${key}Hue`] = hue;
            this.settings[`ColorGrade${key}Sat`] = saturation;
            this.onChange(`ColorGrade${key}Hue`, hue, 'Color Grading: ' + key + ' color', { history: false });
            drawPuck(canvas, hue, saturation, this.discs);
            changed = true;
        };
        const done = () => {
            canvas.removeEventListener('pointermove', update); canvas.removeEventListener('pointerup', done); canvas.removeEventListener('pointercancel', done);
            if (changed) this.onChange(`ColorGrade${key}Hue`, this.settings[`ColorGrade${key}Hue`], 'Color Grading: ' + key + ' color', { previousSettings });
        };
        update(event); canvas.addEventListener('pointermove', update); canvas.addEventListener('pointerup', done); canvas.addEventListener('pointercancel', done);
    }

    reset(key) {
        const previousSettings = snapshot(this.settings);
        for (const suffix of ['Hue', 'Sat']) this.settings[`ColorGrade${key}${suffix}`] = 0;
        this.emit(`${key}Lum`, 0, `${key} reset`, { previousSettings });
        this.draw();
    }

    drawWheel(key) {
        const canvas = this.host.querySelector(`[data-wheel="${key}"] canvas`);
        drawPuck(canvas, numberSetting(this.settings, `ColorGrade${key}Hue`), numberSetting(this.settings, `ColorGrade${key}Sat`), this.discs);
    }

    draw() {
        for (const [key] of WHEELS) {
            this.drawWheel(key);
            this.host.querySelector(`[data-wheel-lum="${key}"]`).value = numberSetting(this.settings, `ColorGrade${key}Lum`);
        }
    }

    setSettings(settings) { this.settings = settings || {}; this.draw(); }
}
