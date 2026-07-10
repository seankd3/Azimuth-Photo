import { DEFAULTS, numberSetting } from './ops_constants.js';

const TRANSFORM_SLIDERS = [
    ['PerspectiveVertical', 'Vertical', -100, 100, 1, 0],
    ['PerspectiveHorizontal', 'Horizontal', -100, 100, 1, 0],
    ['PerspectiveRotate', 'Rotate', -45, 45, .1, 0],
    ['PerspectiveScale', 'Scale', 1, 200, 1, 100],
    ['PerspectiveAspect', 'Aspect', -100, 100, 1, 0],
    ['PerspectiveX', 'Offset X', -100, 100, 1, 0],
    ['PerspectiveY', 'Offset Y', -100, 100, 1, 0],
];

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const sliderHtml = ([key, label, min, max, step, fallback]) => `<label class="develop-crop-control develop-transform-control"><span>${label}</span><input type="range" data-transform-setting="${key}" min="${min}" max="${max}" step="${step}" value="${fallback}" aria-label="${label}"><output></output></label>`;

function canvasPoint(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    return [clamp((event.clientX - rect.left) / rect.width, 0, 1), clamp((event.clientY - rect.top) / rect.height, 0, 1)];
}

/**
 * Narrow UI controller; the Develop mount supplies preview detection and owns
 * the stage overlay.  It writes Adobe-native Perspective* keys only.
 */
export class TransformPanel {
    constructor({ host, stage, canvas, onChange, onAutoLevel }) {
        this.host = host;
        this.stage = stage;
        this.canvas = canvas;
        this.onChange = onChange;
        this.onAutoLevel = onAutoLevel;
        this.settings = {};
        this.guided = false;
        this.lines = [];
        host.innerHTML = '<div class="develop-transform-actions" style="display:flex;gap:6px;margin-bottom:8px"><button type="button" data-transform-auto>Auto</button><button type="button" data-transform-level>Level</button><button type="button" data-transform-guided aria-pressed="false">Guided</button></div>'
            + '<p data-transform-status aria-live="polite" style="margin:0 0 10px">Use Auto for the horizon, or draw 2–4 lines to guide Upright.</p>'
            + TRANSFORM_SLIDERS.map(sliderHtml).join('');
        this.overlay = document.createElement('canvas');
        this.overlay.className = 'develop-transform-guides';
        this.overlay.hidden = true;
        Object.assign(this.overlay.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', touchAction: 'none', cursor: 'crosshair' });
        stage.append(this.overlay);
        this.bind();
        this.resize = () => this.draw();
        window.addEventListener('resize', this.resize);
        this.setSettings({});
    }

    bind() {
        for (const input of this.host.querySelectorAll('[data-transform-setting]')) {
            input.addEventListener('input', () => this.set(input.dataset.transformSetting, Number(input.value), input.closest('label').querySelector('span').textContent));
            input.addEventListener('dblclick', () => this.set(input.dataset.transformSetting, Number(input.defaultValue), input.closest('label').querySelector('span').textContent));
        }
        this.host.querySelector('[data-transform-auto]').addEventListener('click', async () => {
            const result = await this.onAutoLevel?.();
            if (!result) return this.status('No clear horizon found. Try Guided.');
            this.apply(result, 'Auto Upright');
            this.status('Horizon levelled.');
        });
        this.host.querySelector('[data-transform-level]').addEventListener('click', () => {
            this.set('PerspectiveRotate', 0, 'Level');
            this.set('PerspectiveUpright', 'Level', 'Level');
            this.status('Level reset. Draw a guide for a measured correction.');
        });
        this.host.querySelector('[data-transform-guided]').addEventListener('click', () => this.setGuided(!this.guided));
        this.overlay.addEventListener('pointerdown', (event) => this.beginGuide(event));
    }

    status(message) { this.host.querySelector('[data-transform-status]').textContent = message; }

    set(key, value, label) {
        this.settings[key] = value;
        this.onChange(key, value, label);
        this.sync();
    }

    apply(values, label) { Object.entries(values).forEach(([key, value]) => this.set(key, value, label)); }

    setSettings(settings) { this.settings = { ...settings }; this.sync(); }

    sync() {
        for (const [key, , min, max, step, fallback] of TRANSFORM_SLIDERS) {
            const input = this.host.querySelector(`[data-transform-setting="${key}"]`);
            const value = clamp(numberSetting(this.settings, key, DEFAULTS[key] ?? fallback), min, max);
            input.value = value;
            input.nextElementSibling.value = step < 1 ? value.toFixed(1) : String(Math.round(value));
        }
    }

    setGuided(active) {
        this.guided = active;
        this.lines = [];
        this.overlay.hidden = !active;
        this.host.querySelector('[data-transform-guided]').setAttribute('aria-pressed', String(active));
        this.status(active ? 'Draw 2–4 horizontal or vertical guides.' : 'Guided Upright cancelled.');
        this.draw();
    }

    beginGuide(event) {
        if (!this.guided) return;
        const start = canvasPoint(event, this.overlay);
        const move = (next) => { this.pending = { start, end: canvasPoint(next, this.overlay) }; this.draw(); };
        const end = (next) => {
            this.overlay.removeEventListener('pointermove', move);
            this.overlay.removeEventListener('pointerup', end);
            const line = { start, end: canvasPoint(next, this.overlay) };
            if (Math.hypot(line.end[0] - start[0], line.end[1] - start[1]) > .025) this.lines.push(line);
            this.pending = null;
            this.draw();
            if (this.lines.length >= 2) this.commitGuides();
        };
        this.overlay.setPointerCapture(event.pointerId);
        this.overlay.addEventListener('pointermove', move);
        this.overlay.addEventListener('pointerup', end);
    }

    commitGuides() {
        const errors = this.lines.map(({ start, end }) => {
            const angle = Math.atan2(end[1] - start[1], end[0] - start[0]) * 180 / Math.PI;
            const targets = [0, 90, -90, 180, -180];
            return targets.map((target) => ((angle - target + 90) % 180) - 90).sort((a, b) => Math.abs(a) - Math.abs(b))[0];
        });
        const rotate = -errors.sort((a, b) => a - b)[Math.floor(errors.length / 2)];
        this.apply({ PerspectiveRotate: clamp(rotate, -45, 45), PerspectiveUpright: 'Guided' }, 'Guided Upright');
        this.status(`${this.lines.length} guides applied. Add up to 4, or press Guided to start again.`);
        if (this.lines.length >= 4) this.setGuided(false);
    }

    draw() {
        if (this.overlay.hidden) return;
        const rect = this.overlay.getBoundingClientRect();
        const ratio = devicePixelRatio || 1;
        this.overlay.width = Math.max(1, Math.round(rect.width * ratio));
        this.overlay.height = Math.max(1, Math.round(rect.height * ratio));
        const context = this.overlay.getContext('2d');
        context.scale(ratio, ratio);
        context.lineWidth = 2;
        context.strokeStyle = '#f7ca55';
        for (const line of [...this.lines, this.pending].filter(Boolean)) {
            context.beginPath(); context.moveTo(line.start[0] * rect.width, line.start[1] * rect.height); context.lineTo(line.end[0] * rect.width, line.end[1] * rect.height); context.stroke();
        }
    }

    destroy() { window.removeEventListener('resize', this.resize); this.overlay.remove(); }
}
