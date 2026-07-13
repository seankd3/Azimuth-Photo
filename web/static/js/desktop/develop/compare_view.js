import { DevelopRenderer } from './gl.js';

// The comparison surface owns only its second renderer and interaction state.
// Develop supplies cached/loaded preview data, keeping image/history ownership in develop.js.
export class DevelopCompareView {
    constructor({ stage, canvas, loadPreview, getCurrent, getImages }) {
        this.stage = stage;
        this.canvas = canvas;
        this.loadPreview = loadPreview;
        this.getCurrent = getCurrent;
        this.getImages = getImages;
        this.mode = 'off';
        this.orientation = 'vertical';
        this.reference = null;
        this.proof = { profile: 'off', warning: false };
        this.altCanvas = document.createElement('canvas');
        this.altCanvas.className = 'develop-compare-canvas';
        this.altCanvas.hidden = true;
        this.altCanvas.setAttribute('aria-label', 'Before or reference photo');
        this.divider = document.createElement('button');
        this.divider.className = 'develop-compare-divider';
        this.divider.type = 'button';
        this.divider.hidden = true;
        this.divider.dataset.tip = 'Drag comparison divider';
        this.divider.setAttribute('aria-label', 'Drag comparison divider');
        this.divider.innerHTML = '<i></i>';
        this.label = document.createElement('span');
        this.label.className = 'develop-compare-label';
        this.label.hidden = true;
        stage.append(this.altCanvas, this.divider, this.label);
        this.position = .5;
        this.bindDivider();
    }

    bindDivider() {
        this.divider.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            this.divider.setPointerCapture(event.pointerId);
            const move = (next) => this.moveDivider(next);
            const up = () => {
                this.divider.removeEventListener('pointermove', move);
                this.divider.removeEventListener('pointerup', up);
                this.divider.removeEventListener('pointercancel', up);
            };
            this.divider.addEventListener('pointermove', move);
            this.divider.addEventListener('pointerup', up);
            this.divider.addEventListener('pointercancel', up);
            this.moveDivider(event);
        });
    }

    moveDivider(event) {
        const rect = this.stage.getBoundingClientRect();
        this.position = this.orientation === 'vertical'
            ? Math.max(.05, Math.min(.95, (event.clientX - rect.left) / rect.width))
            : Math.max(.05, Math.min(.95, (event.clientY - rect.top) / rect.height));
        this.paint();
    }

    paint() {
        const visible = this.mode !== 'off';
        this.stage.classList.toggle('compare-active', visible);
        this.stage.classList.toggle('compare-horizontal', this.orientation === 'horizontal');
        this.altCanvas.hidden = !visible;
        this.divider.hidden = !visible;
        this.label.hidden = !visible;
        if (!visible) {
            this.canvas.style.clipPath = '';
            return;
        }
        const pct = `${this.position * 100}%`;
        this.canvas.style.clipPath = this.orientation === 'vertical'
            ? `inset(0 0 0 ${pct})` : `inset(${pct} 0 0 0)`;
        this.divider.style.setProperty('--compare-position', pct);
        this.label.textContent = this.mode === 'reference' ? 'Reference' : 'Before';
    }

    async loadPane(image, kind) {
        if (!image) return;
        const preview = await this.loadPreview(image);
        if (!preview) return;
        if (!this.altRenderer) this.altRenderer = new DevelopRenderer(this.altCanvas);
        this.altRenderer.uploadSource(preview.base.rgba, preview.base.width, preview.base.height);
        this.altRenderer.setSettings(kind === 'before' ? preview.entry.origin : preview.entry.settings, preview.entry.meta);
        this.altRenderer.setSoftProof(this.proof);
    }

    async showBefore(orientation = 'vertical') {
        this.orientation = orientation;
        this.mode = 'before';
        this.paint();
        await this.loadPane(this.getCurrent(), 'before');
    }

    async holdReference(held) {
        if (!held) {
            this.mode = 'off';
            this.paint();
            return;
        }
        this.mode = 'reference';
        this.reference ||= this.getImages().find((image) => Number(image.id) !== Number(this.getCurrent()?.id)) || this.getCurrent();
        this.paint();
        await this.loadPane(this.reference, 'after');
    }

    async pickReference(image) {
        this.reference = image;
        if (this.mode === 'reference') await this.loadPane(image, 'after');
    }

    async toggle(orientation = 'vertical') {
        if (this.mode === 'before' && this.orientation === orientation) {
            this.mode = 'off';
            this.paint();
            return;
        }
        await this.showBefore(orientation);
    }

    updateCurrent(entry) {
        if (this.mode !== 'before' || !this.altRenderer || !entry) return;
        this.altRenderer.setSettings(entry.origin, entry.meta);
    }

    setProof(proof) {
        this.proof = { profile: proof?.profile || 'off', warning: Boolean(proof?.warning) };
        this.altRenderer?.setSoftProof(this.proof);
    }

    destroy() {
        this.altRenderer?.destroy();
        this.altCanvas.remove();
        this.divider.remove();
        this.label.remove();
    }
}

export class SoftProofPopover {
    constructor({ toolbar, onChange }) {
        this.toolbar = toolbar;
        this.onChange = onChange;
        this.state = { profile: 'off', warning: false };
        this.button = document.createElement('button');
        this.button.dataset.action = 'proof';
        this.button.dataset.tip = 'Soft proof: target gamut and warning';
        this.button.textContent = 'Proof';
        toolbar.querySelector('.develop-toolbar-spacer')?.before(this.button);
        this.button.addEventListener('click', () => this.toggle());
    }

    toggle() {
        this.popover?.remove();
        if (this.popover) { this.popover = null; return; }
        const popover = document.createElement('div');
        popover.className = 'develop-proof-popover';
        popover.innerHTML = '<strong>Soft proof</strong><label>Target <select><option value="off">Off</option><option value="srgb">sRGB</option><option value="adobe-rgb">Adobe RGB</option><option value="display-p3">Display P3</option><option value="paper">Matte paper</option></select></label><label><input type="checkbox"> Gamut warning</label><p>Matrix/clip preview; Matte paper simulates paper white and black, not an ICC profile.</p>';
        const rect = this.button.getBoundingClientRect();
        popover.style.left = `${rect.left}px`;
        popover.style.top = `${rect.bottom + 6}px`;
        document.body.append(popover);
        const select = popover.querySelector('select');
        const warning = popover.querySelector('input');
        select.value = this.state.profile;
        warning.checked = this.state.warning;
        const change = () => {
            this.state = { profile: select.value, warning: warning.checked };
            this.button.classList.toggle('active', this.state.profile !== 'off');
            this.onChange(this.state);
        };
        select.addEventListener('change', change);
        warning.addEventListener('change', change);
        this.popover = popover;
    }
}
