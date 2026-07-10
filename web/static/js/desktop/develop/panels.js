import { BAND_NAMES, DEFAULTS, boolSetting, numberSetting } from './ops_constants.js';
import { buildCurveLut, normalizeCurve } from './curve_lut.js';

const slider = (key, label, min, max, step = 1, fallback = DEFAULTS[key] ?? 0) => ({ key, label, min, max, step, fallback });
const BASIC = [slider('Temperature', 'Temp', 2000, 50000, 50, 5500), slider('Tint', 'Tint', -150, 150)];
const TONE = [
    slider('Exposure2012', 'Exposure', -5, 5, .01), slider('Contrast2012', 'Contrast', -100, 100),
    slider('Highlights2012', 'Highlights', -100, 100), slider('Shadows2012', 'Shadows', -100, 100),
    slider('Whites2012', 'Whites', -100, 100), slider('Blacks2012', 'Blacks', -100, 100),
];
const PRESENCE = [
    slider('Texture', 'Texture', -100, 100), slider('Clarity2012', 'Clarity', -100, 100),
    slider('Dehaze', 'Dehaze', -100, 100), slider('Vibrance', 'Vibrance', -100, 100),
    slider('Saturation', 'Saturation', -100, 100),
];
const DETAIL = [
    slider('Sharpness', 'Amount', 0, 150, 1, 40), slider('SharpenRadius', 'Radius', .5, 3, .1, 1),
    slider('SharpenDetail', 'Detail', 0, 100, 1, 25), slider('SharpenEdgeMasking', 'Masking', 0, 100),
];
const EFFECTS = [
    slider('PostCropVignetteAmount', 'Vignette', -100, 100), slider('PostCropVignetteMidpoint', 'Midpoint', 0, 100, 1, 50),
    slider('PostCropVignetteFeather', 'Feather', 0, 100, 1, 50), slider('PostCropVignetteRoundness', 'Roundness', -100, 100),
    slider('GrainAmount', 'Grain', 0, 100), slider('GrainSize', 'Size', 0, 100, 1, 25),
    slider('GrainFrequency', 'Roughness', 0, 100, 1, 50),
];

function displayValue(value, step) {
    const decimals = step < .1 ? 2 : step < 1 ? 1 : 0;
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '0';
    return `${numeric > 0 ? '+' : ''}${numeric.toFixed(decimals)}`;
}

function section(title, id, inner, open = true) {
    return `<details class="develop-section" data-section="${id}" ${open ? 'open' : ''}>`
        + `<summary data-tip="Expand or collapse ${title}"><span>${title}</span><span aria-hidden="true">⌄</span></summary>`
        + `<div class="develop-section-body">${inner}</div></details>`;
}

function sliderHtml(config) {
    return `<div class="develop-slider" data-setting="${config.key}" data-min="${config.min}" data-max="${config.max}" data-step="${config.step}" data-default="${config.fallback}" data-tip="Drag to adjust ${config.label}; Shift for fine; double-click to reset">`
        + `<span class="develop-slider-label">${config.label}</span><span class="develop-slider-track"><i></i><b></b></span>`
        + `<input class="develop-slider-value" inputmode="decimal" aria-label="${config.label} value" data-tip="Click to type ${config.label}">`
        + '</div>';
}

function slidersHtml(configs) {
    return configs.map(sliderHtml).join('');
}

function hslHtml(prefix) {
    return BAND_NAMES.map((name) => sliderHtml(slider(`${prefix}${name}`, name, -100, 100))).join('');
}

class CurveEditor {
    constructor(root, onChange) {
        this.root = root;
        this.onChange = onChange;
        this.canvas = root.querySelector('canvas');
        this.ctx = this.canvas.getContext('2d');
        this.channel = 'ToneCurvePV2012';
        this.settings = {};
        this.dragIndex = -1;
        root.querySelector('select').addEventListener('change', (event) => {
            this.channel = event.target.value;
            this.draw();
        });
        this.canvas.addEventListener('pointerdown', (event) => this.pointerDown(event));
        this.canvas.addEventListener('dblclick', (event) => this.addPoint(event));
        root.querySelector('[data-curve-reset]').addEventListener('click', () => {
            delete this.settings[this.channel];
            this.onChange(this.channel, undefined, 'Tone Curve');
            this.draw();
        });
    }

    setSettings(settings) {
        this.settings = settings;
        this.draw();
    }

    coords(event) {
        const rect = this.canvas.getBoundingClientRect();
        return [
            Math.max(0, Math.min(255, (event.clientX - rect.left) / rect.width * 255)),
            Math.max(0, Math.min(255, (1 - (event.clientY - rect.top) / rect.height) * 255)),
        ];
    }

    points() {
        const points = normalizeCurve(this.settings[this.channel]);
        return points.length >= 2 ? points : [[0, 0], [255, 255]];
    }

    pointerDown(event) {
        const [x, y] = this.coords(event);
        const points = this.points();
        let nearest = 0;
        let distance = Infinity;
        points.forEach((point, index) => {
            const d = Math.hypot(point[0] - x, point[1] - y);
            if (d < distance) { distance = d; nearest = index; }
        });
        if (distance > 18) return;
        this.dragIndex = nearest;
        this.canvas.setPointerCapture(event.pointerId);
        const move = (next) => {
            const [nx, ny] = this.coords(next);
            const min = nearest === 0 ? 0 : points[nearest - 1][0] + 1;
            const max = nearest === points.length - 1 ? 255 : points[nearest + 1][0] - 1;
            points[nearest] = [Math.max(min, Math.min(max, nx)), ny];
            this.commit(points);
        };
        const up = () => {
            this.canvas.removeEventListener('pointermove', move);
            this.canvas.removeEventListener('pointerup', up);
            this.canvas.removeEventListener('pointercancel', up);
            this.dragIndex = -1;
        };
        this.canvas.addEventListener('pointermove', move);
        this.canvas.addEventListener('pointerup', up);
        this.canvas.addEventListener('pointercancel', up);
    }

    addPoint(event) {
        const points = this.points();
        const [x, y] = this.coords(event);
        points.push([x, y]);
        this.commit(points);
    }

    commit(points) {
        const value = normalizeCurve(points).map(([x, y]) => `${Math.round(x)}, ${Math.round(y)}`);
        this.settings[this.channel] = value;
        this.onChange(this.channel, value, 'Tone Curve');
        this.draw();
    }

    draw() {
        const ctx = this.ctx;
        const { width, height } = this.canvas;
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = '#14171b';
        ctx.fillRect(0, 0, width, height);
        ctx.strokeStyle = 'rgba(255,255,255,.09)';
        ctx.lineWidth = 1;
        for (let i = 1; i < 4; i += 1) {
            const p = i / 4;
            ctx.beginPath(); ctx.moveTo(p * width, 0); ctx.lineTo(p * width, height); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(0, p * height); ctx.lineTo(width, p * height); ctx.stroke();
        }
        const colors = { ToneCurvePV2012: '#e5e7eb', ToneCurvePV2012Red: '#ff6b71', ToneCurvePV2012Green: '#5ce397', ToneCurvePV2012Blue: '#63aefb' };
        const lut = buildCurveLut(this.settings[this.channel]);
        ctx.strokeStyle = colors[this.channel];
        ctx.lineWidth = 2;
        ctx.beginPath();
        lut.forEach((value, index) => {
            const x = index / 255 * width;
            const y = (1 - value) * height;
            if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.fillStyle = colors[this.channel];
        for (const [x, y] of this.points()) {
            ctx.beginPath(); ctx.arc(x / 255 * width, (1 - y / 255) * height, 4, 0, Math.PI * 2); ctx.fill();
        }
    }
}

export class DevelopPanels {
    constructor(host, { histogramHost, cropHost, onChange }) {
        this.host = host;
        this.onChange = onChange;
        this.settings = {};
        host.innerHTML = section('Histogram', 'histogram', '<div id="develop-histogram-slot"></div>')
            + section('Basic', 'basic', '<div class="develop-wb-row"><select id="develop-wb" data-tip="White balance mode" aria-label="White balance"><option>As Shot</option><option>Custom</option><option>Daylight</option><option>Cloudy</option><option>Shade</option><option>Tungsten</option><option>Fluorescent</option><option>Flash</option></select><button data-wb-reset data-tip="Reset white balance to As Shot">As Shot</button></div>' + slidersHtml(BASIC))
            + section('Tone', 'tone', slidersHtml(TONE))
            + section('Presence', 'presence', slidersHtml(PRESENCE))
            + section('Tone Curve', 'curve', '<div class="develop-curve-tools"><select data-tip="Tone curve channel" aria-label="Tone curve channel"><option value="ToneCurvePV2012">RGB</option><option value="ToneCurvePV2012Red">Red</option><option value="ToneCurvePV2012Green">Green</option><option value="ToneCurvePV2012Blue">Blue</option></select><button data-curve-reset data-tip="Reset selected curve">Reset</button></div><canvas class="develop-curve" width="288" height="180" tabindex="0" data-tip="Drag points; double-click to add" aria-label="Tone curve editor"></canvas>')
            + section('HSL / B&W', 'hsl', '<label class="develop-toggle" data-tip="Convert to black and white"><span>Black & White</span><input id="develop-bw" type="checkbox" data-tip="Toggle black and white"><i></i></label><div id="develop-hsl-controls"><div class="develop-tabs" role="tablist"><button class="active" data-hsl-tab="Hue" data-tip="Hue adjustments">Hue</button><button data-hsl-tab="Saturation" data-tip="Saturation adjustments">Sat</button><button data-hsl-tab="Luminance" data-tip="Luminance adjustments">Lum</button></div><div data-hsl-panel="Hue">' + hslHtml('HueAdjustment') + '</div><div data-hsl-panel="Saturation" hidden>' + hslHtml('SaturationAdjustment') + '</div><div data-hsl-panel="Luminance" hidden>' + hslHtml('LuminanceAdjustment') + '</div></div><div id="develop-gray-controls" hidden>' + hslHtml('GrayMixer') + '</div>')
            + section('Detail', 'detail', slidersHtml(DETAIL), false)
            + section('Effects', 'effects', slidersHtml(EFFECTS), false)
            + section('Crop', 'crop', '<div id="develop-crop-slot"></div>', false)
            + '<p class="develop-v1-note">Stored but not rendered in v1: masks, lens corrections, chromatic aberration, noise reduction, color grading, spot removal, pano/HDR.</p>';
        host.querySelector('#develop-histogram-slot').replaceWith(histogramHost);
        host.querySelector('#develop-crop-slot').replaceWith(cropHost);
        this.curve = new CurveEditor(host.querySelector('[data-section="curve"]'), (key, value, label) => this.change(key, value, label));
        this.bindSliders();
        this.bindOtherControls();
    }

    bindSliders() {
        for (const row of this.host.querySelectorAll('.develop-slider')) {
            const input = row.querySelector('input');
            const updateFromPointer = (event, startX, startValue) => {
                const min = Number(row.dataset.min);
                const max = Number(row.dataset.max);
                const step = Number(row.dataset.step);
                const sensitivity = event.shiftKey ? .1 : 1;
                const delta = (event.clientX - startX) / Math.max(120, row.clientWidth) * (max - min) * sensitivity;
                const value = Math.max(min, Math.min(max, Math.round((startValue + delta) / step) * step));
                this.change(row.dataset.setting, value, row.querySelector('.develop-slider-label').textContent);
                this.syncSlider(row);
            };
            row.addEventListener('pointerdown', (event) => {
                if (event.target === input) return;
                event.preventDefault();
                const startX = event.clientX;
                const startValue = numberSetting(this.settings, row.dataset.setting, Number(row.dataset.default));
                row.setPointerCapture(event.pointerId);
                const move = (next) => updateFromPointer(next, startX, startValue);
                const up = () => {
                    row.removeEventListener('pointermove', move);
                    row.removeEventListener('pointerup', up);
                    row.removeEventListener('pointercancel', up);
                    row.classList.remove('dragging');
                };
                row.classList.add('dragging');
                row.addEventListener('pointermove', move);
                row.addEventListener('pointerup', up);
                row.addEventListener('pointercancel', up);
            });
            row.addEventListener('dblclick', (event) => {
                if (event.target === input) return;
                this.change(row.dataset.setting, Number(row.dataset.default), row.querySelector('.develop-slider-label').textContent);
                this.syncSlider(row);
            });
            input.addEventListener('change', () => {
                const value = Math.max(Number(row.dataset.min), Math.min(Number(row.dataset.max), Number(input.value)));
                if (Number.isFinite(value)) this.change(row.dataset.setting, value, row.querySelector('.develop-slider-label').textContent);
                this.syncSlider(row);
            });
            input.addEventListener('focus', () => input.select());
        }
    }

    bindOtherControls() {
        const wb = this.host.querySelector('#develop-wb');
        wb.addEventListener('change', () => this.change('WhiteBalance', wb.value, 'White Balance'));
        this.host.querySelector('[data-wb-reset]').addEventListener('click', () => {
            this.change('WhiteBalance', 'As Shot', 'White Balance');
            delete this.settings.Temperature;
            delete this.settings.Tint;
            this.setSettings(this.settings);
        });
        const bw = this.host.querySelector('#develop-bw');
        bw.addEventListener('change', () => {
            this.change('ConvertToGrayscale', bw.checked, 'Black & White');
            this.syncHslMode();
        });
        for (const tab of this.host.querySelectorAll('[data-hsl-tab]')) {
            tab.addEventListener('click', () => {
                this.host.querySelectorAll('[data-hsl-tab]').forEach((button) => button.classList.toggle('active', button === tab));
                this.host.querySelectorAll('[data-hsl-panel]').forEach((panel) => { panel.hidden = panel.dataset.hslPanel !== tab.dataset.hslTab; });
            });
        }
    }

    change(key, value, label) {
        if (value === undefined) delete this.settings[key];
        else this.settings[key] = value;
        this.onChange(key, value, label);
    }

    syncSlider(row) {
        const min = Number(row.dataset.min);
        const max = Number(row.dataset.max);
        const step = Number(row.dataset.step);
        const value = numberSetting(this.settings, row.dataset.setting, Number(row.dataset.default));
        row.style.setProperty('--slider-pct', `${(value - min) / (max - min) * 100}%`);
        row.style.setProperty('--slider-zero', `${(0 - min) / (max - min) * 100}%`);
        row.querySelector('input').value = displayValue(value, step).replace('+', '');
        row.dataset.value = String(value);
    }

    syncHslMode() {
        const bw = boolSetting(this.settings, 'ConvertToGrayscale');
        this.host.querySelector('#develop-bw').checked = bw;
        this.host.querySelector('#develop-hsl-controls').hidden = bw;
        this.host.querySelector('#develop-gray-controls').hidden = !bw;
    }

    setSettings(settings) {
        this.settings = settings;
        for (const row of this.host.querySelectorAll('.develop-slider')) this.syncSlider(row);
        this.host.querySelector('#develop-wb').value = settings.WhiteBalance || 'As Shot';
        this.syncHslMode();
        this.curve.setSettings(settings);
    }
}
