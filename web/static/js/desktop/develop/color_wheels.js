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

function drawWheel(canvas, hue, saturation) {
    const context = canvas.getContext('2d');
    const { width, height } = canvas;
    const center = width / 2;
    const radius = width * .39;
    const image = context.createImageData(width, height);
    for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
        const dx = x - center; const dy = center - y;
        const distance = Math.hypot(dx, dy) / radius;
        const index = (y * width + x) * 4;
        if (distance > 1) { image.data[index + 3] = 0; continue; }
        const angle = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
        // Write the wheel pixels directly; canvas HSL parsing per pixel would
        // make puck dragging visibly laggy.
        const c = (1 - Math.abs(2 * .58 - 1)) * Math.min(1, distance);
        const h = angle / 60; const q = c * (1 - Math.abs(h % 2 - 1)); const m = .58 - c / 2;
        const [r, g, b] = h < 1 ? [c, q, 0] : h < 2 ? [q, c, 0] : h < 3 ? [0, c, q] : h < 4 ? [0, q, c] : h < 5 ? [q, 0, c] : [c, 0, q];
        image.data[index] = (r + m) * 255; image.data[index + 1] = (g + m) * 255; image.data[index + 2] = (b + m) * 255; image.data[index + 3] = 255;
    }
    context.clearRect(0, 0, width, height); context.putImageData(image, 0, 0);
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
        host.innerHTML = `<div class="develop-wheels">${WHEELS.map(([key, label]) => `<div class="develop-wheel" data-wheel="${key}"><canvas width="112" height="112" aria-label="${label} color wheel" data-tip="Drag puck to grade ${label}; double-click resets"></canvas><b>${label}</b><label>Lum <input type="range" min="-100" max="100" value="0" data-wheel-lum="${key}"></label></div>`).join('')}</div>`;
        for (const [key] of WHEELS) {
            const canvas = host.querySelector(`[data-wheel="${key}"] canvas`);
            canvas.addEventListener('pointerdown', (event) => this.drag(event, key, canvas));
            canvas.addEventListener('dblclick', () => this.reset(key));
            const luminance = host.querySelector(`[data-wheel-lum="${key}"]`);
            let gesture = null;
            luminance.addEventListener('pointerdown', () => { gesture = { previousSettings: snapshotSettings(this.settings), changed: false }; });
            luminance.addEventListener('input', (event) => {
                const value = Number(event.target.value);
                gesture &&= { ...gesture, changed: gesture.changed || value !== numberSetting(gesture.previousSettings, `ColorGrade${key}Lum`) };
                this.change(`${key}Lum`, value, `${key} luminance`, gesture ? { history: false } : undefined);
            });
            const finishGesture = () => {
                if (!gesture?.changed) { gesture = null; return; }
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
        let changed = false;
        const update = (next) => {
            const value = polar(next, canvas);
            const hue = Math.round(value.hue);
            const saturation = Math.round(value.saturation);
            changed ||= hue !== numberSetting(previousSettings, `ColorGrade${key}Hue`) || saturation !== numberSetting(previousSettings, `ColorGrade${key}Sat`);
            this.change(`${key}Hue`, hue, `${key} color`, { history: false });
            this.change(`${key}Sat`, saturation, `${key} color`, { history: false });
        };
        const done = () => {
            canvas.removeEventListener('pointermove', update); canvas.removeEventListener('pointerup', done); canvas.removeEventListener('pointercancel', done);
            if (changed) this.change(`${key}Hue`, numberSetting(this.settings, `ColorGrade${key}Hue`), `${key} color`, { previousSettings });
        };
        update(event); canvas.addEventListener('pointermove', update); canvas.addEventListener('pointerup', done); canvas.addEventListener('pointercancel', done);
    }

    reset(key) {
        for (const suffix of ['Hue', 'Sat', 'Lum']) this.change(`${key}${suffix}`, 0, `${key} reset`);
    }

    draw() {
        for (const [key] of WHEELS) {
            drawWheel(this.host.querySelector(`[data-wheel="${key}"] canvas`), numberSetting(this.settings, `ColorGrade${key}Hue`), numberSetting(this.settings, `ColorGrade${key}Sat`));
            this.host.querySelector(`[data-wheel-lum="${key}"]`).value = numberSetting(this.settings, `ColorGrade${key}Lum`);
        }
    }

    setSettings(settings) { this.settings = settings || {}; this.draw(); }
}
