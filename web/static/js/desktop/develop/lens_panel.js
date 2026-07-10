import { boolSetting, DEFAULTS, numberSetting } from './ops_constants.js';

const PROFILE_CONTROLS = [
    ['LensProfileDistortionScale', 'Distortion', 0, 200, 100],
    ['LensProfileVignettingScale', 'Vignetting', 0, 200, 100],
];
const MANUAL_CONTROLS = [
    ['LensManualDistortionAmount', 'Distortion', -100, 100, 0],
    ['LensManualVignetteAmount', 'Vignette', -100, 100, 0],
    ['LensManualVignetteMidpoint', 'Midpoint', 0, 100, 50],
];

function slider([key, label, min, max, fallback]) {
    return `<label class="develop-compact-slider" data-lens-control="${key}"><span>${label}</span><input type="range" min="${min}" max="${max}" step="1" value="${fallback}" aria-label="${label}"><output></output></label>`;
}

/** Lensfun profile and manual correction controls; renderer owns the math. */
export class LensPanel {
    constructor({ host, onChange }) {
        this.host = host;
        this.onChange = onChange;
        this.settings = {};
        this.meta = {};
        host.innerHTML = '<div class="develop-tabs develop-lens-tabs" role="tablist"><button class="active" type="button" data-lens-tab="profile">Profile</button><button type="button" data-lens-tab="manual">Manual</button></div>'
            + '<div data-lens-panel="profile"><label class="develop-toggle"><span>Enable Profile Corrections</span><input type="checkbox" data-lens-enable><i></i></label><div class="develop-lens-resolved" data-lens-resolved></div><div class="develop-compact-sliders">'
            + PROFILE_CONTROLS.map(slider).join('') + '</div></div>'
            + '<div data-lens-panel="manual" hidden><div class="develop-compact-sliders">' + MANUAL_CONTROLS.map(slider).join('') + '</div></div>';
        this.bind();
        this.setMeta(this.meta);
        this.setSettings(this.settings);
    }

    bind() {
        for (const button of this.host.querySelectorAll('[data-lens-tab]')) {
            button.addEventListener('click', () => {
                this.host.querySelectorAll('[data-lens-tab]').forEach((tab) => tab.classList.toggle('active', tab === button));
                this.host.querySelectorAll('[data-lens-panel]').forEach((panel) => { panel.hidden = panel.dataset.lensPanel !== button.dataset.lensTab; });
            });
        }
        this.host.querySelector('[data-lens-enable]').addEventListener('change', (event) => {
            this.onChange('LensProfileEnable', event.target.checked, 'Lens Profile Corrections');
        });
        for (const row of this.host.querySelectorAll('[data-lens-control]')) {
            const input = row.querySelector('input');
            input.addEventListener('input', () => {
                const value = Number(input.value);
                this.onChange(row.dataset.lensControl, value, row.querySelector('span').textContent);
                row.querySelector('output').value = String(value);
                row.querySelector('output').textContent = `${value > 0 && Number(input.min) < 0 ? '+' : ''}${value}`;
            });
        }
    }

    setMeta(meta = {}) {
        this.meta = meta || {};
        const correction = this.meta.lens_correction || this.meta.color?.lens_correction;
        const resolved = this.host.querySelector('[data-lens-resolved]');
        const camera = correction?.camera || this.meta.camera_model || this.meta.camera || 'Camera unknown';
        const lens = correction?.lens || this.meta.lens_model || this.meta.lens || 'Lens unknown';
        resolved.innerHTML = correction
            ? `<span>Camera</span><b>${camera}</b><span>Lens</span><b>${lens}</b>`
            : `<span>Profile</span><b>No matching Lensfun profile</b><small>${camera} · ${lens}</small>`;
        const available = Boolean(correction);
        this.host.querySelector('[data-lens-enable]').disabled = !available;
        this.host.querySelectorAll('[data-lens-control^="LensProfile"]').forEach((row) => {
            row.classList.toggle('is-unavailable', !available);
            row.querySelector('input').disabled = !available;
        });
    }

    setSettings(settings = {}) {
        this.settings = settings;
        this.host.querySelector('[data-lens-enable]').checked = boolSetting(settings, 'LensProfileEnable');
        for (const row of this.host.querySelectorAll('[data-lens-control]')) {
            const input = row.querySelector('input');
            const key = row.dataset.lensControl;
            const value = numberSetting(settings, key, Number(input.defaultValue || DEFAULTS[key] || 0));
            input.value = String(value);
            row.querySelector('output').value = String(value);
            row.querySelector('output').textContent = `${value > 0 && Number(input.min) < 0 ? '+' : ''}${value}`;
        }
    }
}
