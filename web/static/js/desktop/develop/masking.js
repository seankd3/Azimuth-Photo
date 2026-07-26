import { showToast } from '../toast.js';

const LOCAL_SLIDERS = [
    ['LocalExposure2012', 'Exposure', .01], ['LocalContrast2012', 'Contrast'],
    ['LocalHighlights2012', 'Highlights'], ['LocalShadows2012', 'Shadows'],
    ['LocalWhites2012', 'Whites'], ['LocalBlacks2012', 'Blacks'],
    ['LocalTemperature', 'Temp', .1], ['LocalTint', 'Tint'],
    ['LocalSaturation', 'Saturation'], ['LocalHue', 'Hue'],
    ['LocalClarity2012', 'Clarity'], ['LocalTexture', 'Texture'],
    ['LocalDehaze', 'Dehaze'], ['LocalSharpness', 'Sharpness'],
];
const GROUPS = [
    ['Light', LOCAL_SLIDERS.slice(0, 6)], ['Color', LOCAL_SLIDERS.slice(6, 10)], ['Effects', LOCAL_SLIDERS.slice(10)],
];
const ICONS = { Gradient: '╱', CircularGradient: '◯', Paint: '◌', Luminance: '◐', Color: '●', Image: '✦' };
const ADD_ITEMS = [
    ['subject', 'Subject'], ['sky', 'Sky'], ['brush', 'Brush'], ['linear', 'Linear Gradient'],
    ['radial', 'Radial Gradient'], ['luminance', 'Luminance Range'], ['color', 'Color Range'],
];

const clone = (value) => JSON.parse(JSON.stringify(value || {}));
const safeText = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const sliderValue = (number) => `${number > 0 ? '+' : ''}${number.toFixed(2)}`;
const percentValue = (value, fallback = 1) => Math.round(clamp(number(value, fallback)) * 100);

function correctionId() {
    return globalThis.crypto?.randomUUID?.() || `azimuth-mask-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function kindOf(mask) {
    const what = String(mask?.What || mask?.MaskType || '');
    if (/Circular|Radial/i.test(what)) return 'CircularGradient';
    if (/Gradient/i.test(what)) return 'Gradient';
    if (/Paint|Brush/i.test(what)) return 'Paint';
    if (/Image/i.test(what)) return 'Image';
    if (Number(mask?.CorrectionRangeMask?.Type) === 2 || /Color/i.test(what)) return 'Color';
    if (Number(mask?.CorrectionRangeMask?.Type) === 1 || /Luminance/i.test(what)) return 'Luminance';
    return 'Paint';
}

function newCorrection(index) {
    return { CorrectionID: correctionId(), CorrectionName: `Mask ${index}`, CorrectionActive: true, CorrectionAmount: 1, CorrectionMasks: [] };
}

function newMask(kind, blend = 0) {
    const base = { MaskBlendMode: blend, MaskInverted: false, MaskValue: 1 };
    if (kind === 'linear') return { ...base, What: 'Mask/Gradient', ZeroX: .35, ZeroY: .5, FullX: .65, FullY: .5 };
    if (kind === 'radial') return { ...base, What: 'Mask/CircularGradient', Top: .25, Left: .25, Bottom: .75, Right: .75, Angle: 0, Feather: .5, Flipped: false };
    if (kind === 'brush') return { ...base, What: 'Mask/Paint', Dabs: [], Flow: 1, CenterWeight: .5 };
    if (kind === 'luminance') return { ...base, What: 'Mask/Range', CorrectionRangeMask: { Type: 1, LumRange: '0 0 1 1' } };
    if (kind === 'color') return { ...base, What: 'Mask/Range', CorrectionRangeMask: { Type: 2, ColorAmount: .5, PointModels: [] } };
    return base;
}

const clamp = (value, low = 0, high = 1) => Math.min(Math.max(value, low), high);
const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const rangeMaskOf = (mask) => mask?.CorrectionRangeMask || null;
const rangeQuad = (rangeMask) => {
    const values = (String(rangeMask?.LumRange || '').match(/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?/gi) || []).slice(0, 4).map(Number);
    return values.length === 4 && values.every(Number.isFinite) ? values.map((value) => clamp(value)) : [0, 0, 1, 1];
};
const serializeQuad = (values) => values.map((value) => clamp(value).toFixed(4)).join(' ');
const anchorValues = (rangeMask) => {
    let raw = ['PointModels', 'SampledColors', 'ColorSamples', 'Colors'].map((key) => rangeMask?.[key]).find((value) => value != null) || [];
    if (!Array.isArray(raw)) raw = raw?.PointModel || [raw];
    return raw.map((item) => {
        if (item && typeof item === 'object') item = item.Color || item.RGB || item.Value || item;
        const values = (String(item).match(/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?/gi) || []).slice(0, 3).map(Number);
        if (values.length !== 3 || values.some((value) => !Number.isFinite(value))) return null;
        if (Math.max(...values.map(Math.abs)) > 1) return values.map((value) => clamp(value / 255));
        return values.map((value) => clamp(value));
    }).filter(Boolean);
};
const colorCss = (rgb) => `rgb(${rgb.map((value) => Math.round(clamp(value) * 255)).join(' ')})`;

export class MaskingController {
    constructor({ host, toolbar, stage, canvas, onChange, getImageId, getRenderer }) {
        this.host = host;
        this.toolbar = toolbar;
        this.stage = stage;
        this.canvas = canvas;
        this.onChange = onChange;
        this.getImageId = getImageId;
        this.getRenderer = getRenderer;
        this.settings = {};
        this.selected = null;
        this.mode = null;
        this.brushSize = .08;
        this.overlayShown = false;
        this.rasterModule = null;
        this.rasterLoad = null;
        this.renderToken = 0;
        this.makeStageLayer();
        this.mountToolbarButton();
        this.bindCanvas();
        this.loadRasterModule();
    }

    makeStageLayer() {
        this.layer = document.createElement('div');
        this.layer.className = 'develop-mask-layer';
        this.layer.hidden = true;
        this.layer.innerHTML = '<div class="develop-mask-preview" hidden></div><div class="develop-mask-cursor" hidden></div>';
        this.stage.append(this.layer);
        this.preview = this.layer.querySelector('.develop-mask-preview');
        this.cursor = this.layer.querySelector('.develop-mask-cursor');
    }

    mountToolbarButton() {
        this.toolbarButton = document.createElement('button');
        this.toolbarButton.type = 'button';
        this.toolbarButton.dataset.action = 'masking';
        this.toolbarButton.dataset.tip = 'Masking tools';
        this.toolbarButton.setAttribute('aria-pressed', 'false');
        this.toolbarButton.textContent = 'Masking';
        const spacer = this.toolbar.querySelector('.develop-toolbar-spacer');
        this.toolbar.insertBefore(this.toolbarButton, spacer);
        this.toolbarButton.addEventListener('click', () => this.togglePanel());
    }

    async loadRasterModule() {
        try {
            this.rasterModule = await import('./mask_raster.js');
            this.rebuildRasters();
        } catch {
            // MASKCORE may land after the panel. Controls remain fully editable meanwhile.
        }
    }

    corrections() {
        if (!Array.isArray(this.settings.MaskGroupBasedCorrections)) this.settings.MaskGroupBasedCorrections = [];
        return this.settings.MaskGroupBasedCorrections;
    }

    correction(index = this.selected) {
        return Number.isInteger(index) ? this.corrections()[index] : null;
    }

    setSettings(settings) {
        this.settings = settings || {};
        this.historySnapshot = clone(this.settings);
        const corrections = this.corrections();
        if (this.selected == null || !corrections[this.selected]) this.selected = corrections.length ? 0 : null;
        this.render();
        this.rebuildRasters();
    }

    emit(label, { history = true } = {}) {
        this.onChange('MaskGroupBasedCorrections', this.corrections(), label, { history, previousSettings: this.historySnapshot });
        this.historySnapshot = clone(this.settings);
        this.rebuildRasters();
    }

    render() {
        const corrections = this.corrections();
        this.host.innerHTML = `<div class="develop-masking-head"><button class="develop-mask-add" data-mask-add data-tip="Add a new mask correction">+ Add Mask</button>${corrections.length ? '<span>Local adjustments</span>' : ''}</div>`
            + (corrections.length ? corrections.map((correction, index) => this.correctionHtml(correction, index)).join('') : '<p class="develop-mask-empty">Add a mask to adjust only part of this photo.</p>');
        this.bindPanel();
    }

    correctionHtml(correction, index) {
        const active = correction.CorrectionActive !== false;
        const open = index === this.selected;
        const masks = Array.isArray(correction.CorrectionMasks) ? correction.CorrectionMasks : [];
        return `<article class="develop-mask-card ${open ? 'selected' : ''}" data-correction="${index}">
            <header><button class="develop-mask-eye" data-mask-eye data-tip="${active ? 'Hide mask adjustment' : 'Show mask adjustment'}" aria-pressed="${active}">${active ? '◉' : '○'}</button>
            <button class="develop-mask-name" data-mask-select data-tip="Select mask; double-click to rename"><span>${safeText(correction.CorrectionName || correction.Name || `Mask ${index + 1}`)}</span></button>
            <button data-mask-duplicate data-tip="Duplicate mask">⧉</button><button data-mask-delete data-tip="Delete mask">×</button>${this.correctionAmountHtml(correction)}</header>
            ${open ? `<div class="develop-mask-card-body"><div class="develop-mask-chips">${masks.map((mask, maskIndex) => this.maskRowHtml(mask, maskIndex)).join('')}<button data-mask-add-part data-tip="Add to or subtract from this mask" aria-label="Add or subtract a mask part">+</button></div>
            <div class="develop-mask-tools"><button data-mask-invert data-tip="Invert all masks in this adjustment">Invert</button><button data-mask-overlay data-tip="Toggle red mask overlay (O)" aria-pressed="${this.overlayShown}">Overlay</button></div>
            ${masks.map((mask, maskIndex) => this.rangeHtml(mask, maskIndex)).join('')}
            ${GROUPS.map(([name, sliders]) => `<div class="develop-mask-slider-group"><b>${name}</b>${sliders.map(([key, label, step]) => this.sliderHtml(correction, key, label, step)).join('')}</div>`).join('')}</div>` : ''}
        </article>`;
    }

    correctionAmountHtml(correction) {
        const current = percentValue(correction.CorrectionAmount);
        return `<label class="develop-mask-slider" data-correction-amount data-min="0" data-max="100" data-step="1" style="grid-column:2 / -1;padding-right:4px" data-tip="Drag to adjust correction amount; double-click to reset"><span>Amount</span><input type="range" min="0" max="100" step="1" value="${current}" style="--mask-slider-pct:${current}%" aria-label="Correction amount"><output>${current}</output></label>`;
    }

    maskRowHtml(mask, maskIndex) {
        const kind = kindOf(mask);
        const current = percentValue(mask.MaskValue);
        return `<div style="display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:6px;width:100%"><button class="develop-mask-chip" data-mask-chip="${maskIndex}" data-tip="${kind} mask; click to invert"><i>${ICONS[kind]}</i>${safeText(kind.replace('CircularGradient', 'Radial').replace('Gradient', 'Linear'))}${Number(mask.MaskBlendMode) === 1 ? ' −' : ''}</button><label class="develop-mask-slider" data-mask-value="${maskIndex}" data-min="0" data-max="100" data-step="1" style="grid-template-columns:42px minmax(0,1fr) 26px" data-tip="Drag to adjust mask density; double-click to reset"><span>Density</span><input type="range" min="0" max="100" step="1" value="${current}" style="--mask-slider-pct:${current}%" aria-label="${kind} mask density"><output>${current}</output></label></div>`;
    }

    sliderHtml(correction, key, label, step = 1) {
        const scale = this.sliderScale(key);
        const current = this.localToSlider(correction, key);
        const min = -scale;
        const max = scale;
        const percent = (current - min) / (max - min) * 100;
        return `<label class="develop-mask-slider" data-local-setting="${key}" data-min="${min}" data-max="${max}" data-step="${step}" data-tip="Drag to adjust local ${label}; double-click to reset"><span>${label}</span><input type="range" min="${min}" max="${max}" step="${step}" value="${current}" style="--mask-slider-pct:${percent}%" aria-label="Local ${label}"><output>${sliderValue(current)}</output></label>`;
    }

    rangeHtml(mask, maskIndex) {
        const range = rangeMaskOf(mask);
        if (!range || ![1, 2].includes(number(range.Type))) return '';
        const inverted = mask.MaskInverted === true || String(mask.MaskInverted).toLowerCase() === 'true';
        if (number(range.Type) === 1) {
            const [lowSoft, low, high, highSoft] = rangeQuad(range);
            const feather = Math.min(low - lowSoft, highSoft - high);
            return `<section data-range-section="${maskIndex}" style="margin-top:10px;padding-top:8px;border-top:1px solid var(--line-soft)">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px"><b style="color:var(--text-3);font-size:9px;letter-spacing:.05em;text-transform:uppercase">Range · Luminance</b><button data-range-invert="${maskIndex}" aria-pressed="${inverted}" style="min-height:20px;padding:0 6px;border:1px solid var(--line);border-radius:999px;color:var(--text-2);font-size:9px;background:#15171b">Invert</button></div>
                <canvas data-range-histogram="${maskIndex}" width="188" height="28" aria-label="Rendered luminance histogram" style="display:block;width:100%;height:28px;margin:0 0 5px;background:#101216;border-radius:3px"></canvas>
                ${this.rangeSliderHtml(maskIndex, 'low', 'Lo', low)}${this.rangeSliderHtml(maskIndex, 'high', 'Hi', high)}${this.rangeSliderHtml(maskIndex, 'feather', 'Feather', feather)}
            </section>`;
        }
        const anchors = anchorValues(range);
        return `<section data-range-section="${maskIndex}" style="margin-top:10px;padding-top:8px;border-top:1px solid var(--line-soft)">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px"><b style="color:var(--text-3);font-size:9px;letter-spacing:.05em;text-transform:uppercase">Range · Color</b><button data-range-invert="${maskIndex}" aria-pressed="${inverted}" style="min-height:20px;padding:0 6px;border:1px solid var(--line);border-radius:999px;color:var(--text-2);font-size:9px;background:#15171b">Invert</button></div>
            ${this.rangeSliderHtml(maskIndex, 'tolerance', 'Tolerance', number(range.ColorAmount, .5))}
            <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-top:5px"><button data-range-eyedropper="${maskIndex}" style="min-height:23px;padding:0 6px;border:1px solid var(--accent-lin);border-radius:3px;color:#dbeaf7;font-size:9px;background:var(--accent-dim)">Eyedropper</button>${anchors.map((rgb, anchorIndex) => `<button data-range-anchor="${maskIndex}:${anchorIndex}" aria-label="Remove sampled color ${anchorIndex + 1}" data-tip="Remove sampled color" style="width:22px;height:22px;padding:0;border:2px solid ${colorCss(rgb)};border-radius:50%;background:${colorCss(rgb)};box-shadow:inset 0 0 0 1px #111">×</button>`).join('')}<span style="color:var(--text-3);font-size:9px">${anchors.length}/4 · canvas sRGB</span></div>
            <p style="margin:5px 0 0;color:var(--text-3);font-size:9px;line-height:1.35">Canvas samples are normalized sRGB anchors; mask rasterization decodes them into its linear OKLab working space.</p>
        </section>`;
    }

    rangeSliderHtml(maskIndex, setting, label, value) {
        const percent = clamp(value) * 100;
        return `<label class="develop-mask-slider" data-range-setting="${setting}" data-range-mask="${maskIndex}"><span>${label}</span><input type="range" min="0" max="1" step=".01" value="${clamp(value)}" style="--mask-slider-pct:${percent}%" aria-label="Range ${label}"><output>${Math.round(clamp(value) * 100)}</output></label>`;
    }

    bindPanel() {
        this.host.querySelector('[data-mask-add]')?.addEventListener('click', (event) => this.openMenu(event.currentTarget, null));
        this.host.querySelectorAll('[data-correction]').forEach((card) => {
            const index = Number(card.dataset.correction);
            card.querySelector('[data-mask-select]')?.addEventListener('click', () => { this.selected = index; this.render(); });
            card.querySelector('[data-mask-name]')?.addEventListener('dblclick', (event) => this.rename(index, event.currentTarget));
            card.querySelector('[data-mask-eye]')?.addEventListener('click', () => {
                const correction = this.correction(index); correction.CorrectionActive = correction.CorrectionActive === false;
                this.emit('Toggle Mask'); this.render();
            });
            card.querySelector('[data-mask-delete]')?.addEventListener('click', () => {
                this.corrections().splice(index, 1); this.selected = Math.min(index, this.corrections().length - 1);
                this.emit('Delete Mask'); this.render(); this.exitMode();
            });
            card.querySelector('[data-mask-duplicate]')?.addEventListener('click', () => {
                const copy = clone(this.correction(index)); copy.CorrectionID = correctionId(); copy.CorrectionName = `${copy.CorrectionName || `Mask ${index + 1}`} Copy`;
                this.corrections().splice(index + 1, 0, copy); this.selected = index + 1; this.emit('Duplicate Mask'); this.render();
            });
            card.querySelector('[data-mask-add-part]')?.addEventListener('click', (event) => this.openMenu(event.currentTarget, index));
            card.querySelector('[data-mask-invert]')?.addEventListener('click', () => {
                for (const mask of this.correction(index).CorrectionMasks || []) mask.MaskInverted = !mask.MaskInverted;
                this.emit('Invert Mask'); this.render();
            });
            card.querySelector('[data-mask-overlay]')?.addEventListener('click', () => this.toggleOverlay());
            card.querySelectorAll('[data-mask-chip]').forEach((chip) => chip.addEventListener('click', () => {
                const mask = this.correction(index).CorrectionMasks[Number(chip.dataset.maskChip)]; mask.MaskInverted = !mask.MaskInverted;
                this.emit('Invert Mask'); this.render();
            }));
            card.querySelector('[data-correction-amount]') && this.bindPercentSlider(card.querySelector('[data-correction-amount]'), {
                read: () => percentValue(this.correction(index)?.CorrectionAmount),
                write: (value) => { this.correction(index).CorrectionAmount = value / 100; },
                label: 'Correction Amount',
            });
            card.querySelectorAll('[data-mask-value]').forEach((row) => this.bindPercentSlider(row, {
                read: () => percentValue(this.rangePart(index, Number(row.dataset.maskValue))?.MaskValue),
                write: (value) => { this.rangePart(index, Number(row.dataset.maskValue)).MaskValue = value / 100; },
                label: 'Mask Density',
            }));
            card.querySelectorAll('[data-local-setting]').forEach((row) => this.bindSlider(row, index));
            card.querySelectorAll('[data-range-setting]').forEach((row) => this.bindRangeSlider(row, index));
            card.querySelectorAll('[data-range-invert]').forEach((button) => button.addEventListener('click', () => {
                const mask = this.rangePart(index, Number(button.dataset.rangeInvert));
                if (!mask) return;
                mask.MaskInverted = !mask.MaskInverted;
                this.emit('Invert Range'); this.render();
            }));
            card.querySelectorAll('[data-range-eyedropper]').forEach((button) => button.addEventListener('click', () => {
                const mask = this.rangePart(index, Number(button.dataset.rangeEyedropper));
                if (mask) this.enterEyedropper(index, mask);
            }));
            card.querySelectorAll('[data-range-anchor]').forEach((button) => button.addEventListener('click', () => {
                const [maskIndex, anchorIndex] = button.dataset.rangeAnchor.split(':').map(Number);
                this.removeRangeAnchor(index, maskIndex, anchorIndex);
            }));
        });
        requestAnimationFrame(() => this.drawRangeHistograms());
    }

    bindSlider(row, index) {
        const input = row.querySelector('input');
        const commit = (history) => {
            const correction = this.correction(index); if (!correction) return;
            correction[row.dataset.localSetting] = Number(input.value) / this.sliderScale(row.dataset.localSetting);
            row.querySelector('output').textContent = sliderValue(Number(input.value));
            this.emit(`Local ${row.querySelector('span').textContent}`, { history });
        };
        let began = false;
        input.addEventListener('pointerdown', () => { began = false; });
        input.addEventListener('input', () => { commit(!began); began = true; });
        input.addEventListener('change', () => commit(!began));
        row.addEventListener('dblclick', () => { input.value = '0'; commit(true); });
    }

    bindPercentSlider(row, { read, write, label }) {
        const input = row.querySelector('input');
        const min = Number(row.dataset.min);
        const max = Number(row.dataset.max);
        const step = Number(row.dataset.step) || 1;
        const setValue = (value, history = true) => {
            const next = Math.max(min, Math.min(max, Math.round(value / step) * step));
            input.value = String(next);
            write(next);
            row.querySelector('output').textContent = String(next);
            this.emit(label, { history });
        };
        let began = false;
        input.addEventListener('pointerdown', () => { began = false; });
        input.addEventListener('input', () => { setValue(Number(input.value), !began); began = true; });
        input.addEventListener('change', () => setValue(Number(input.value), !began));
        row.addEventListener('pointerdown', (event) => {
            if (event.target === input || event.button !== 0) return;
            event.preventDefault();
            const startX = event.clientX;
            const startValue = read();
            began = false;
            row.setPointerCapture(event.pointerId);
            const move = (next) => {
                const sensitivity = next.shiftKey ? .1 : 1;
                const delta = (next.clientX - startX) / Math.max(120, row.clientWidth) * (max - min) * sensitivity;
                setValue(startValue + delta, !began); began = true;
            };
            const end = () => {
                row.removeEventListener('pointermove', move);
                row.removeEventListener('pointerup', end);
                row.removeEventListener('pointercancel', end);
            };
            row.addEventListener('pointermove', move);
            row.addEventListener('pointerup', end);
            row.addEventListener('pointercancel', end);
        });
        row.addEventListener('dblclick', () => setValue(max));
        input.addEventListener('keydown', (event) => {
            const direction = event.key === 'ArrowUp' || event.key === 'ArrowRight' ? 1 : (event.key === 'ArrowDown' || event.key === 'ArrowLeft' ? -1 : 0);
            if (!direction) return;
            event.preventDefault();
            setValue(Number(input.value) + direction * step * (event.shiftKey ? 10 : 1));
        });
        row.addEventListener('wheel', (event) => {
            event.preventDefault();
            setValue(Number(input.value) + (event.deltaY < 0 ? 1 : -1) * step * (event.shiftKey ? 10 : 1));
        }, { passive: false });
    }

    rangePart(correctionIndex, maskIndex) {
        const correction = this.correction(correctionIndex);
        return correction?.CorrectionMasks?.[maskIndex] || null;
    }

    bindRangeSlider(row, correctionIndex) {
        const input = row.querySelector('input');
        const maskIndex = Number(row.dataset.rangeMask);
        const commit = (history) => {
            const mask = this.rangePart(correctionIndex, maskIndex), range = rangeMaskOf(mask);
            if (!range) return;
            const value = clamp(number(input.value));
            if (row.dataset.rangeSetting === 'tolerance') range.ColorAmount = value;
            else {
                let [lowSoft, low, high, highSoft] = rangeQuad(range);
                if (row.dataset.rangeSetting === 'low') low = Math.min(value, high);
                if (row.dataset.rangeSetting === 'high') high = Math.max(value, low);
                if (row.dataset.rangeSetting === 'feather') {
                    lowSoft = Math.max(0, low - value); highSoft = Math.min(1, high + value);
                } else {
                    const feather = Math.min(low - lowSoft, highSoft - high);
                    lowSoft = Math.max(0, low - feather); highSoft = Math.min(1, high + feather);
                }
                range.LumRange = serializeQuad([lowSoft, low, high, highSoft]);
            }
            row.querySelector('output').textContent = String(Math.round(value * 100));
            this.emit('Adjust Range', { history });
        };
        let began = false;
        input.addEventListener('pointerdown', () => { began = false; });
        input.addEventListener('input', () => { commit(!began); began = true; });
        input.addEventListener('change', () => commit(!began));
    }

    sliderScale(key) {
        const helper = this.rasterModule?.localToSlider;
        const scale = typeof helper === 'function' ? Math.abs(Number(helper({ [key]: 1 }, key))) : 1;
        return Number.isFinite(scale) && scale > 0 ? scale : 1;
    }

    localToSlider(correction, key) {
        const helper = this.rasterModule?.localToSlider;
        if (typeof helper === 'function') return Number(helper(correction, key)) || 0;
        return Number(correction?.[key]) || 0;
    }

    rename(index, button) {
        const correction = this.correction(index);
        if (!correction) return;
        const previous = correction.CorrectionName || correction.Name || `Mask ${index + 1}`;
        button.innerHTML = `<input aria-label="Mask name" value="${safeText(previous)}" data-tip="Rename mask">`;
        const input = button.querySelector('input');
        input.focus(); input.select();
        let done = false;
        const commit = () => {
            if (done) return; done = true;
            const next = input.value.trim();
            if (next && next !== previous) { correction.CorrectionName = next; this.emit('Rename Mask'); }
            this.render();
        };
        input.addEventListener('blur', commit);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') { event.preventDefault(); commit(); }
            if (event.key === 'Escape') { done = true; this.render(); }
        });
    }

    openMenu(button, correctionIndex) {
        this.host.querySelector('.develop-mask-menu')?.remove();
        const menu = document.createElement('div');
        menu.className = 'develop-mask-menu';
        const heading = correctionIndex == null ? 'New mask' : 'Add to mask';
        menu.innerHTML = `<b>${heading}</b>` + ADD_ITEMS.map(([kind, label]) => `<button data-kind="${kind}" data-blend="0" data-tip="Add ${label} mask">+ ${label}</button>${correctionIndex == null ? '' : `<button data-kind="${kind}" data-blend="1" data-tip="Subtract ${label} mask">− ${label}</button>`}`).join('');
        button.after(menu);
        menu.querySelectorAll('[data-kind]').forEach((item) => item.addEventListener('click', () => {
            const kind = item.dataset.kind;
            this.addMask(kind, correctionIndex, Number(item.dataset.blend));
            menu.remove();
        }));
    }

    addMask(kind, correctionIndex = null, blend = 0) {
        let index = correctionIndex;
        if (index == null) {
            const correction = newCorrection(this.corrections().length + 1);
            this.corrections().push(correction); index = this.corrections().length - 1; this.selected = index;
        }
        const correction = this.correction(index);
        correction.CorrectionMasks ||= [];
        if (kind === 'subject' || kind === 'sky') {
            this.addAiMask(kind, correction, index);
            return;
        }
        const mask = newMask(kind, blend);
        correction.CorrectionMasks.push(mask);
        this.emit('Add Mask'); this.render();
        if (kind === 'linear' || kind === 'radial' || kind === 'brush') this.enterMode(kind, index, mask);
    }

    async addAiMask(kind, correction, index) {
        const imageId = this.getImageId?.();
        if (!imageId) return;
        this.host.querySelector('.develop-masking-head')?.classList.add('generating');
        showToast('Generating mask…');
        try {
            const response = await fetch(`/api/develop/${imageId}/ai-mask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }) });
            if (!response.ok) throw new Error(response.status === 404 ? 'AI masking is not available yet.' : 'Mask generation failed.');
            const result = await response.json();
            correction.CorrectionMasks ||= [];
            correction.CorrectionMasks.push({ What: 'Mask/Image', MaskSubType: kind === 'subject' ? '1' : '2', ReferencePoint: '0.5, 0.5', pa_cache_key: result.cache_key, MaskValue: 1, MaskBlendMode: 0, MaskInverted: false });
            this.selected = index; this.emit(`Add ${kind === 'subject' ? 'Subject' : 'Sky'} Mask`); this.render();
        } catch (error) {
            showToast(error.message || 'Could not generate mask');
            if (!correction.CorrectionMasks?.length) {
                const at = this.corrections().indexOf(correction); if (at >= 0) this.corrections().splice(at, 1);
            }
        } finally {
            this.host.querySelector('.develop-masking-head')?.classList.remove('generating');
        }
    }

    enterMode(mode, index, mask) {
        this.selected = index; this.mode = { kind: mode, index, mask, drawing: false };
        this.layer.hidden = false; this.stage.classList.add('masking-active'); this.toolbarButton.setAttribute('aria-pressed', 'true');
        this.render();
    }

    enterEyedropper(index, mask) {
        this.selected = index; this.mode = { kind: 'eyedropper', index, mask, anchorStarted: false };
        this.layer.hidden = false; this.stage.classList.add('masking-active'); this.toolbarButton.setAttribute('aria-pressed', 'true');
        showToast('Click the rendered photo to sample up to four colors');
    }

    rangeAnchorStore(range) {
        const key = range.PointModels != null ? 'PointModels' : (range.SampledColors != null ? 'SampledColors' : 'PointModels');
        range[key] = anchorValues(range).map((rgb) => rgb.map((value) => value.toFixed(6)).join(' '));
        return range[key];
    }

    addRangeAnchor(mask, rgb, history) {
        const range = rangeMaskOf(mask);
        if (!range) return false;
        const anchors = this.rangeAnchorStore(range);
        if (anchors.length >= 4) { showToast('Color ranges can use up to four samples'); return false; }
        anchors.push(rgb.map((value) => clamp(value).toFixed(6)).join(' '));
        this.emit('Sample Color Range', { history });
        return true;
    }

    removeRangeAnchor(correctionIndex, maskIndex, anchorIndex) {
        const range = rangeMaskOf(this.rangePart(correctionIndex, maskIndex));
        if (!range) return;
        const anchors = this.rangeAnchorStore(range);
        anchors.splice(anchorIndex, 1);
        this.emit('Remove Color Sample'); this.render();
    }

    exitMode() {
        this.mode = null; this.layer.hidden = true; this.preview.hidden = true; this.cursor.hidden = true;
        this.stage.classList.remove('masking-active'); this.toolbarButton.setAttribute('aria-pressed', 'false');
        this.setRendererOverlay(this.overlayShown);
    }

    togglePanel() {
        const section = this.host.closest('.develop-section');
        if (section) section.open = true;
        if (this.mode) this.exitMode();
        else this.host.querySelector('[data-mask-add]')?.focus();
    }

    toggleOverlay(force) {
        this.overlayShown = force ?? !this.overlayShown;
        this.setRendererOverlay(this.overlayShown);
        this.render();
    }

    setRendererOverlay(show) {
        const renderer = this.getRenderer?.();
        if (typeof renderer?.setMaskOverlay === 'function') renderer.setMaskOverlay(show ? this.selected : null);
    }

    renderedPixels() {
        const renderer = this.getRenderer?.();
        if (!renderer || typeof renderer.readPixels !== 'function') return null;
        try {
            return { data: renderer.readPixels(), width: this.canvas.width, height: this.canvas.height };
        } catch {
            return null;
        }
    }

    drawRangeHistograms() {
        const canvases = this.host.querySelectorAll('[data-range-histogram]');
        if (!canvases.length) return;
        const rendered = this.renderedPixels();
        for (const canvas of canvases) {
            const context = canvas.getContext('2d'); if (!context) continue;
            const bins = new Uint32Array(64);
            if (rendered) {
                const stride = Math.max(1, Math.floor(rendered.width * rendered.height / 8192));
                for (let pixel = 0; pixel < rendered.width * rendered.height; pixel += stride) {
                    const at = pixel * 4;
                    const luma = (rendered.data[at] * .2126 + rendered.data[at + 1] * .7152 + rendered.data[at + 2] * .0722) / 255;
                    bins[Math.min(bins.length - 1, Math.floor(luma * bins.length))] += 1;
                }
            }
            const maximum = Math.max(1, ...bins);
            context.clearRect(0, 0, canvas.width, canvas.height);
            context.fillStyle = '#101216'; context.fillRect(0, 0, canvas.width, canvas.height);
            context.fillStyle = '#6e849b';
            for (let index = 0; index < bins.length; index += 1) {
                const height = Math.round((bins[index] / maximum) * (canvas.height - 3));
                context.fillRect(index * canvas.width / bins.length, canvas.height - height, Math.ceil(canvas.width / bins.length), height);
            }
            const mask = this.rangePart(this.selected, Number(canvas.dataset.rangeHistogram));
            const [, low, high] = rangeQuad(rangeMaskOf(mask));
            context.fillStyle = 'rgba(105, 184, 255, .25)'; context.fillRect(low * canvas.width, 0, (high - low) * canvas.width, canvas.height);
            context.strokeStyle = '#83c5ff'; context.beginPath(); context.moveTo(low * canvas.width + .5, 0); context.lineTo(low * canvas.width + .5, canvas.height); context.moveTo(high * canvas.width + .5, 0); context.lineTo(high * canvas.width + .5, canvas.height); context.stroke();
        }
    }

    canvasBox() {
        const stage = this.stage.getBoundingClientRect();
        const canvas = this.canvas.getBoundingClientRect();
        return { left: canvas.left - stage.left, top: canvas.top - stage.top, width: canvas.width, height: canvas.height };
    }

    point(event) {
        const box = this.canvas.getBoundingClientRect();
        return { x: Math.max(0, Math.min(1, (event.clientX - box.left) / Math.max(1, box.width))), y: Math.max(0, Math.min(1, (event.clientY - box.top) / Math.max(1, box.height))) };
    }

    bindCanvas() {
        const surface = this.layer;
        surface.addEventListener('pointermove', (event) => {
            if (!this.mode) return;
            const point = this.point(event);
            this.drawCursor(event);
            if (this.mode.drawing) this.drawTo(point, false);
        });
        surface.addEventListener('pointerdown', (event) => {
            if (!this.mode || event.button !== 0 || event.target.closest('button')) return;
            event.preventDefault();
            const point = this.point(event);
            if (this.mode.kind === 'eyedropper') {
                const rendered = this.renderedPixels();
                if (!rendered) { showToast('The rendered canvas is not ready to sample'); return; }
                const x = Math.min(rendered.width - 1, Math.max(0, Math.round(point.x * (rendered.width - 1))));
                const y = Math.min(rendered.height - 1, Math.max(0, Math.round((1 - point.y) * (rendered.height - 1))));
                const at = (y * rendered.width + x) * 4;
                // readPixels is bottom-left based; values are final rendered sRGB.
                const added = this.addRangeAnchor(this.mode.mask, [rendered.data[at] / 255, rendered.data[at + 1] / 255, rendered.data[at + 2] / 255], !this.mode.anchorStarted);
                if (!added) { this.exitMode(); this.render(); return; }
                this.mode.anchorStarted = true;
                if (anchorValues(rangeMaskOf(this.mode.mask)).length >= 4) this.exitMode();
                this.render();
                return;
            }
            this.mode.start = point; this.mode.drawing = true;
            this.overlayShown = true; this.setRendererOverlay(true); this.drawTo(point, true);
            surface.setPointerCapture?.(event.pointerId);
        });
        const finish = () => {
            if (!this.mode?.drawing) return;
            this.mode.drawing = false; this.preview.hidden = true; this.toggleOverlay(false); this.render();
        };
        surface.addEventListener('pointerup', finish);
        surface.addEventListener('pointercancel', finish);
        surface.addEventListener('pointerleave', () => { if (this.mode) this.cursor.hidden = true; });
    }

    drawCursor(event) {
        if (this.mode?.kind !== 'brush') return;
        const stage = this.stage.getBoundingClientRect(); const box = this.canvasBox();
        const diameter = Math.max(10, this.brushSize * Math.max(box.width, box.height));
        this.cursor.hidden = false;
        this.cursor.style.width = `${diameter}px`; this.cursor.style.height = `${diameter}px`;
        this.cursor.style.left = `${event.clientX - stage.left}px`; this.cursor.style.top = `${event.clientY - stage.top}px`;
    }

    drawTo(point, first) {
        const mode = this.mode; if (!mode) return;
        if (mode.kind === 'brush') {
            mode.mask ||= newMask('brush');
            mode.mask.Dabs ||= [];
            const previous = mode.last;
            if (!previous || Math.hypot(point.x - previous.x, point.y - previous.y) > this.brushSize * .2) {
                mode.mask.Dabs.push(`d ${point.x.toFixed(5)} ${point.y.toFixed(5)}`, `r ${this.brushSize.toFixed(5)}`);
                mode.last = point; this.emit('Brush Mask', { history: first });
            }
        } else {
            const start = mode.start;
            if (mode.kind === 'linear') Object.assign(mode.mask, { ZeroX: start.x, ZeroY: start.y, FullX: point.x, FullY: point.y });
            else {
                Object.assign(mode.mask, { Left: Math.min(start.x, point.x), Right: Math.max(start.x, point.x), Top: Math.min(start.y, point.y), Bottom: Math.max(start.y, point.y) });
            }
            this.previewGradient(start, point, mode.kind);
            this.emit('Create Mask', { history: first });
        }
    }

    previewGradient(start, point, kind) {
        const box = this.canvasBox();
        this.preview.hidden = false;
        this.preview.className = `develop-mask-preview ${kind}`;
        if (kind === 'linear') {
            const angle = Math.atan2(point.y - start.y, point.x - start.x) * 180 / Math.PI;
            this.preview.style.cssText = `left:${box.left}px;top:${box.top}px;width:${box.width}px;height:${box.height}px;--mask-angle:${angle}deg`;
        } else {
            this.preview.style.cssText = `left:${box.left + Math.min(start.x, point.x) * box.width}px;top:${box.top + Math.min(start.y, point.y) * box.height}px;width:${Math.max(1, Math.abs(point.x - start.x) * box.width)}px;height:${Math.max(1, Math.abs(point.y - start.y) * box.height)}px`;
        }
    }

    keydown(event) {
        if (!this.mode) return false;
        if (event.key === '[' || event.key === ']') {
            event.preventDefault(); event.stopPropagation();
            this.brushSize = Math.max(.01, Math.min(.5, this.brushSize + (event.key === ']' ? .01 : -.01)));
            showToast(`Brush ${Math.round(this.brushSize * 100)}%`); return true;
        }
        if (event.key.toLowerCase() === 'o') { event.preventDefault(); event.stopPropagation(); this.toggleOverlay(); return true; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.exitMode(); return true; }
        return false;
    }

    rebuildRasters() {
        const renderer = this.getRenderer?.();
        if (!renderer || typeof renderer.setMaskRasters !== 'function' || !this.rasterModule) return;
        const builder = this.rasterModule.buildMaskRasters || this.rasterModule.buildRasters || this.rasterModule.default;
        if (typeof builder !== 'function') return;
        const token = ++this.renderToken;
        Promise.resolve(builder({ imageId: this.getImageId?.(), corrections: this.corrections(), width: renderer.width, height: renderer.height }))
            .then((rasters) => { if (token === this.renderToken) renderer.setMaskRasters(rasters); })
            .catch(() => { /* A partial renderer must never block mask editing. */ });
    }
}
