import { DEFAULTS, numberSetting } from './ops_constants.js';

const PRIMARY_NAMES = ['Red', 'Green', 'Blue'];

function primaryControls(name) {
    return `<div class="develop-calibration-primary"><b>${name} Primary</b><label data-calibration-control="Calibration${name}PrimaryHue"><span>Hue</span><input type="range" min="-100" max="100" step="1" aria-label="${name} primary hue"><output></output></label><label data-calibration-control="Calibration${name}PrimarySaturation"><span>Saturation</span><input type="range" min="-100" max="100" step="1" aria-label="${name} primary saturation"><output></output></label></div>`;
}

/** Compact Adobe-style Calibration controls; values remain Adobe-native keys. */
export class CalibrationPanel {
    constructor({ host, onChange }) {
        this.host = host;
        this.onChange = onChange;
        this.settings = {};
        host.innerHTML = '<div class="develop-calibration-shadow"><label data-calibration-control="CalibrationShadowTint"><span>Shadows Tint</span><input type="range" min="-100" max="100" step="1" aria-label="Shadows tint"><output></output></label></div>'
            + PRIMARY_NAMES.map(primaryControls).join('');
        for (const row of host.querySelectorAll('[data-calibration-control]')) {
            const input = row.querySelector('input');
            input.addEventListener('input', () => {
                const value = Number(input.value);
                this.onChange(row.dataset.calibrationControl, value, row.querySelector('span').textContent);
                this.syncRow(row, value);
            });
        }
        this.setSettings(this.settings);
    }

    syncRow(row, value) {
        row.querySelector('output').value = String(value);
        row.querySelector('output').textContent = `${value > 0 ? '+' : ''}${value}`;
    }

    setSettings(settings = {}) {
        this.settings = settings;
        for (const row of this.host.querySelectorAll('[data-calibration-control]')) {
            const key = row.dataset.calibrationControl;
            const value = numberSetting(settings, key, DEFAULTS[key] ?? 0);
            row.querySelector('input').value = String(value);
            this.syncRow(row, value);
        }
    }
}
