import { numberSetting } from './ops_constants.js';

const HANDLES = [
    ['nw', 'Top left crop handle'], ['n', 'Top crop edge'], ['ne', 'Top right crop handle'],
    ['e', 'Right crop edge'], ['se', 'Bottom right crop handle'], ['s', 'Bottom crop edge'],
    ['sw', 'Bottom left crop handle'], ['w', 'Left crop edge'],
];

const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, value));
const ASPECTS = {
    Original: null,
    '1:1': 1,
    '4:5': 4 / 5,
    '5:7': 5 / 7,
    '2:3': 2 / 3,
    '16:9': 16 / 9,
    Free: null,
};
const OVERLAYS = ['thirds', 'golden', 'diagonal'];

export class CropController {
    constructor({ stage, canvas, overlay, controls, onChange }) {
        this.stage = stage;
        this.canvas = canvas;
        this.overlay = overlay;
        this.controls = controls;
        this.onChange = onChange;
        this.settings = {};
        this.active = false;
        this.straightening = false;
        this.overlayMode = 0;
        this.overlay.innerHTML = '<div class="develop-crop-shade"></div><div class="develop-crop-frame">'
            + '<i class="crop-grid-v one"></i><i class="crop-grid-v two"></i><i class="crop-grid-h one"></i><i class="crop-grid-h two"></i>'
            + HANDLES.map(([name, tip]) => `<button class="crop-handle ${name}" data-handle="${name}" data-tip="${tip}" aria-label="${tip}"></button>`).join('')
            + '</div><div class="develop-straight-line" hidden></div>';
        this.controls.innerHTML = '<button class="develop-crop-toggle" data-tip="Open crop overlay" aria-pressed="false">Crop & Straighten</button>'
            + '<label class="develop-crop-control"><span>Aspect</span><select data-crop-aspect data-tip="Crop aspect ratio; hold Shift while dragging for freeform" aria-label="Crop aspect ratio"><option>Original</option><option>1:1</option><option>4:5</option><option>5:7</option><option>2:3</option><option>16:9</option><option>Free</option></select></label>'
            + '<label class="develop-crop-control"><span>Angle</span><input data-crop-angle type="range" min="-45" max="45" step="0.1" value="0" data-tip="Crop angle" aria-label="Crop angle"><output>0.0°</output></label>'
            + '<button data-straighten data-tip="Drag a horizon line on the photo" aria-pressed="false">Straighten line</button>'
            + '<button data-crop-reset data-tip="Reset crop and angle">Reset Crop</button>';
        this.bind();
        this.canvas.addEventListener("develop:rendered", () => this.syncOverlay());
        window.addEventListener("resize", () => this.syncOverlay());
    }

    bind() {
        this.controls.querySelector('.develop-crop-toggle').addEventListener('click', () => this.setActive(!this.active));
        this.controls.querySelector('[data-crop-aspect]').addEventListener('change', (event) => this.applyAspect(event.target.value));
        const angle = this.controls.querySelector('[data-crop-angle]');
        angle.addEventListener('input', () => {
            const value = Number(angle.value);
            this.settings.CropAngle = value;
            angle.nextElementSibling.value = `${value.toFixed(1)}°`;
            this.onChange('CropAngle', value, 'Crop Angle');
            this.syncOverlay();
        });
        this.controls.querySelector('[data-straighten]').addEventListener('click', (event) => {
            this.straightening = !this.straightening;
            event.currentTarget.setAttribute('aria-pressed', String(this.straightening));
            this.overlay.classList.toggle('straightening', this.straightening);
        });
        this.controls.querySelector('[data-crop-reset]').addEventListener('click', () => {
            Object.assign(this.settings, { CropLeft: 0, CropTop: 0, CropRight: 1, CropBottom: 1, CropAngle: 0 });
            for (const key of ['CropLeft', 'CropTop', 'CropRight', 'CropBottom', 'CropAngle']) this.onChange(key, this.settings[key], 'Reset Crop');
            this.setSettings(this.settings);
        });
        for (const handle of this.overlay.querySelectorAll('[data-handle]')) {
            handle.addEventListener('pointerdown', (event) => this.beginHandle(event, handle.dataset.handle));
        }
        this.overlay.addEventListener('pointerdown', (event) => {
            if (this.straightening && !event.target.closest('[data-handle]')) this.beginStraighten(event);
        });
        new ResizeObserver(() => this.syncOverlay()).observe(this.stage);
    }

    setActive(active) {
        this.active = Boolean(active);
        this.overlay.hidden = !this.active;
        this.stage.classList.toggle('crop-active', this.active);
        this.controls.querySelector('.develop-crop-toggle').setAttribute('aria-pressed', String(this.active));
        if (this.active) requestAnimationFrame(() => { this.updateOverlayGrid(); this.syncOverlay(); });
    }

    setSettings(settings) {
        this.settings = settings;
        const angle = numberSetting(settings, 'CropAngle');
        const input = this.controls.querySelector('[data-crop-angle]');
        input.value = String(angle);
        input.nextElementSibling.value = `${angle.toFixed(1)}°`;
        this.syncOverlay();
    }

    canvasBox() {
        const stage = this.stage.getBoundingClientRect();
        const canvas = this.canvas.getBoundingClientRect();
        return { left: canvas.left - stage.left, top: canvas.top - stage.top, width: canvas.width, height: canvas.height };
    }

    crop() {
        return {
            left: numberSetting(this.settings, 'CropLeft'), top: numberSetting(this.settings, 'CropTop'),
            right: numberSetting(this.settings, 'CropRight', 1), bottom: numberSetting(this.settings, 'CropBottom', 1),
        };
    }

    syncOverlay() {
        if (!this.active) return;
        const box = this.canvasBox();
        const crop = this.crop();
        const frame = this.overlay.querySelector('.develop-crop-frame');
        frame.style.left = `${box.left + crop.left * box.width}px`;
        frame.style.top = `${box.top + crop.top * box.height}px`;
        frame.style.width = `${Math.max(1, (crop.right - crop.left) * box.width)}px`;
        frame.style.height = `${Math.max(1, (crop.bottom - crop.top) * box.height)}px`;
        frame.style.transform = `rotate(${numberSetting(this.settings, 'CropAngle')}deg)`;
    }

    cycleOverlay() {
        this.overlayMode = (this.overlayMode + 1) % OVERLAYS.length;
        this.updateOverlayGrid();
    }

    updateOverlayGrid() {
        const frame = this.overlay.querySelector('.develop-crop-frame');
        const mode = OVERLAYS[this.overlayMode];
        frame.classList.remove(...OVERLAYS.map((name) => `crop-overlay-${name}`));
        frame.classList.add(`crop-overlay-${mode}`);
        frame.style.backgroundImage = '';
        const lines = [...frame.querySelectorAll('.crop-grid-v, .crop-grid-h')];
        for (const line of lines) {
            line.hidden = false;
            line.style.cssText = '';
        }
        if (mode === 'golden') {
            frame.querySelector('.crop-grid-v.one').style.left = '38.2%';
            frame.querySelector('.crop-grid-v.two').style.left = '61.8%';
            frame.querySelector('.crop-grid-h.one').style.top = '38.2%';
            frame.querySelector('.crop-grid-h.two').style.top = '61.8%';
        } else if (mode === 'diagonal') {
            for (const line of lines) line.hidden = true;
            frame.style.backgroundImage = 'linear-gradient(to bottom right, transparent calc(50% - .5px), rgba(255,255,255,.48) 50%, transparent calc(50% + .5px)), linear-gradient(to bottom left, transparent calc(50% - .5px), rgba(255,255,255,.48) 50%, transparent calc(50% + .5px))';
        }
    }

    beginHandle(event, handle) {
        event.preventDefault();
        event.stopPropagation();
        const startX = event.clientX;
        const startY = event.clientY;
        const start = this.crop();
        const box = this.canvasBox();
        event.currentTarget.setPointerCapture(event.pointerId);
        const move = (next) => {
            const dx = (next.clientX - startX) / Math.max(1, box.width);
            const dy = (next.clientY - startY) / Math.max(1, box.height);
            const result = { ...start };
            if (handle.includes('w')) result.left = clamp(start.left + dx, 0, result.right - .02);
            if (handle.includes('e')) result.right = clamp(start.right + dx, result.left + .02, 1);
            if (handle.includes('n')) result.top = clamp(start.top + dy, 0, result.bottom - .02);
            if (handle.includes('s')) result.bottom = clamp(start.bottom + dy, result.top + .02, 1);
            this.commitCrop(next.shiftKey ? result : this.lockAspect(result, handle, dx, dy), 'Crop');
        };
        const up = () => {
            event.currentTarget.removeEventListener('pointermove', move);
            event.currentTarget.removeEventListener('pointerup', up);
            event.currentTarget.removeEventListener('pointercancel', up);
        };
        event.currentTarget.addEventListener('pointermove', move);
        event.currentTarget.addEventListener('pointerup', up);
        event.currentTarget.addEventListener('pointercancel', up);
    }

    commitCrop(crop, label) {
        const patch = { CropLeft: crop.left, CropTop: crop.top, CropRight: crop.right, CropBottom: crop.bottom };
        Object.assign(this.settings, patch);
        for (const [key, value] of Object.entries(patch)) this.onChange(key, value, label);
        this.syncOverlay();
    }

    aspectRatio() {
        const preset = this.controls.querySelector('[data-crop-aspect]').value;
        if (preset === 'Free') return null;
        return ASPECTS[preset] || this.canvasBox().width / Math.max(1, this.canvasBox().height);
    }

    lockAspect(crop, handle, dx, dy) {
        const target = this.aspectRatio();
        if (!target) return crop;
        const box = this.canvasBox();
        const ratio = target / (box.width / Math.max(1, box.height));
        const rawWidth = crop.right - crop.left;
        const rawHeight = crop.bottom - crop.top;
        const horizontal = handle === 'e' || handle === 'w'
            || (handle.length === 2 && Math.abs(dx) >= Math.abs(dy * ratio));
        let width = horizontal ? rawWidth : rawHeight * ratio;
        let height = horizontal ? width / ratio : rawHeight;
        const xAnchor = handle.includes('w') ? 'end' : (handle.includes('e') ? 'start' : 'center');
        const yAnchor = handle.includes('n') ? 'end' : (handle.includes('s') ? 'start' : 'center');
        const maxWidth = this.maxDimension(crop.left, crop.right, xAnchor);
        const maxHeight = this.maxDimension(crop.top, crop.bottom, yAnchor);
        const scale = Math.min(1, maxWidth / width, maxHeight / height);
        width *= scale; height *= scale;
        const [left, right] = this.placeDimension(crop.left, crop.right, width, xAnchor);
        const [top, bottom] = this.placeDimension(crop.top, crop.bottom, height, yAnchor);
        return { left, right, top, bottom };
    }

    maxDimension(start, end, anchor) {
        if (anchor === 'start') return 1 - start;
        if (anchor === 'end') return end;
        return 2 * Math.min((start + end) / 2, 1 - (start + end) / 2);
    }

    placeDimension(start, end, size, anchor) {
        if (anchor === 'start') return [start, start + size];
        if (anchor === 'end') return [end - size, end];
        const center = (start + end) / 2;
        return [center - size / 2, center + size / 2];
    }

    applyAspect(preset) {
        if (preset === 'Free') return;
        const box = this.canvasBox();
        const sourceRatio = box.width / Math.max(1, box.height);
        const target = ASPECTS[preset] || sourceRatio;
        const current = this.crop();
        const cx = (current.left + current.right) / 2;
        const cy = (current.top + current.bottom) / 2;
        let width = current.right - current.left;
        let height = width * sourceRatio / target;
        if (height > current.bottom - current.top) {
            height = current.bottom - current.top;
            width = height * target / sourceRatio;
        }
        this.commitCrop({ left: clamp(cx - width / 2), right: clamp(cx + width / 2), top: clamp(cy - height / 2), bottom: clamp(cy + height / 2) }, 'Crop Aspect');
    }

    beginStraighten(event) {
        event.preventDefault();
        const line = this.overlay.querySelector('.develop-straight-line');
        const stage = this.stage.getBoundingClientRect();
        const x = event.clientX - stage.left;
        const y = event.clientY - stage.top;
        line.hidden = false;
        line.style.left = `${x}px`;
        line.style.top = `${y}px`;
        line.style.width = '0px';
        line.style.transform = 'rotate(0deg)';
        this.overlay.setPointerCapture(event.pointerId);
        const move = (next) => {
            const dx = next.clientX - event.clientX;
            const dy = next.clientY - event.clientY;
            line.style.width = `${Math.hypot(dx, dy)}px`;
            line.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;
        };
        const up = (next) => {
            const dx = next.clientX - event.clientX;
            const dy = next.clientY - event.clientY;
            const angle = clamp(-Math.atan2(dy, dx) * 180 / Math.PI, -45, 45);
            this.settings.CropAngle = angle;
            this.onChange('CropAngle', angle, 'Straighten');
            line.hidden = true;
            this.straightening = false;
            this.controls.querySelector('[data-straighten]').setAttribute('aria-pressed', 'false');
            this.overlay.classList.remove('straightening');
            this.overlay.removeEventListener('pointermove', move);
            this.overlay.removeEventListener('pointerup', up);
            this.setSettings(this.settings);
        };
        this.overlay.addEventListener('pointermove', move);
        this.overlay.addEventListener('pointerup', up);
    }
}
