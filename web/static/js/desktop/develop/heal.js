// Circular clone/heal spot interaction for Develop §23.  Rendering is owned
// by gl.js; this module owns persisted pa_RetouchSpots and canvas affordances.

export const RETOUCH_SETTINGS_KEY = 'pa_RetouchSpots';
const clone = (value) => JSON.parse(JSON.stringify(value || {}));
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const spotId = () => globalThis.crypto?.randomUUID?.() || `azimuth-heal-${Date.now()}-${Math.random().toString(16).slice(2)}`;

function newSpot(dst) {
    return { id: spotId(), src_x: dst.x, src_y: dst.y, dst_x: dst.x, dst_y: dst.y, radius: .035, feather: .5, opacity: 1, mode: 'heal' };
}

export class HealController {
    constructor({ host, toolbar, stage, canvas, onChange, getRenderer }) {
        Object.assign(this, { host, toolbar, stage, canvas, onChange, getRenderer, settings: {}, selected: null, active: false, overlay: false });
        this.makeLayer(); this.mountToolbarButton(); this.bindCanvas();
    }

    makeLayer() {
        this.layer = document.createElement('div');
        this.layer.className = 'develop-heal-layer'; this.layer.hidden = true;
        this.stage.append(this.layer);
    }

    mountToolbarButton() {
        this.toolbarButton = document.createElement('button');
        this.toolbarButton.type = 'button'; this.toolbarButton.dataset.action = 'heal';
        this.toolbarButton.dataset.tip = 'Heal: click destination, then drag to choose source';
        this.toolbarButton.textContent = 'Heal'; this.toolbarButton.setAttribute('aria-pressed', 'false');
        this.toolbar.insertBefore(this.toolbarButton, this.toolbar.querySelector('.develop-toolbar-spacer'));
        this.toolbarButton.addEventListener('click', () => this.toggle());
    }

    spots() {
        if (!Array.isArray(this.settings[RETOUCH_SETTINGS_KEY])) this.settings[RETOUCH_SETTINGS_KEY] = [];
        return this.settings[RETOUCH_SETTINGS_KEY];
    }

    setSettings(settings) { this.settings = settings || {}; this.historySnapshot = clone(this.settings); this.selected = this.spots().length ? Math.min(this.selected ?? 0, this.spots().length - 1) : null; this.render(); }
    emit(label, history = true) { this.onChange(RETOUCH_SETTINGS_KEY, this.spots(), label, { history, previousSettings: this.historySnapshot }); if (history) this.historySnapshot = clone(this.settings); this.getRenderer?.()?.setSettings?.(this.settings); }

    render() {
        const spots = this.spots();
        this.host.innerHTML = `<div class="develop-heal-head"><button data-heal-overlay aria-pressed="${this.overlay}" data-tip="Show spot circles (O)">Overlay</button><span>${spots.length ? `${spots.length} spot${spots.length === 1 ? '' : 's'}` : 'Click the image to remove a spot'}</span></div>${spots.map((spot, index) => `<div class="develop-heal-spot ${index === this.selected ? 'selected' : ''}" data-heal-spot="${index}"><button data-heal-select>${spot.mode === 'heal' ? 'Heal' : 'Clone'} ${index + 1}</button><select data-heal-mode><option value="heal" ${spot.mode === 'heal' ? 'selected' : ''}>Heal</option><option value="clone" ${spot.mode === 'clone' ? 'selected' : ''}>Clone</option></select><button data-heal-delete data-tip="Delete spot">×</button>${this.controls(spot)}</div>`).join('')}`;
        this.host.querySelector('[data-heal-overlay]')?.addEventListener('click', () => { this.overlay = !this.overlay; this.syncOverlay(); this.render(); });
        this.host.querySelectorAll('[data-heal-spot]').forEach((row) => this.bindRow(row, Number(row.dataset.healSpot)));
        this.drawHandles(); this.syncOverlay();
    }

    controls(spot) { return [['radius', 'Radius', .005], ['feather', 'Feather', .01], ['opacity', 'Opacity', .01]].map(([key, label, step]) => `<label>${label}<input data-heal-control="${key}" type="range" min="${key === 'radius' ? .005 : 0}" max="${key === 'radius' ? .5 : 1}" step="${step}" value="${spot[key]}"></label>`).join(''); }
    bindRow(row, index) {
        row.querySelector('[data-heal-select]')?.addEventListener('click', () => { this.selected = index; this.render(); });
        row.querySelector('[data-heal-mode]')?.addEventListener('change', (event) => { this.spots()[index].mode = event.target.value; this.emit('Change Heal Mode'); });
        row.querySelector('[data-heal-delete]')?.addEventListener('click', () => { this.spots().splice(index, 1); this.selected = this.spots().length ? Math.min(index, this.spots().length - 1) : null; this.emit('Delete Heal Spot'); this.render(); });
        row.querySelectorAll('[data-heal-control]').forEach((input) => input.addEventListener('input', () => { this.spots()[index][input.dataset.healControl] = Number(input.value); this.emit('Adjust Heal Spot', false); this.drawHandles(); }));
    }

    toggle(force) { this.active = force ?? !this.active; this.layer.hidden = !this.active; this.stage.classList.toggle('heal-active', this.active); this.toolbarButton.setAttribute('aria-pressed', String(this.active)); this.drawHandles(); }
    point(event) { const box = this.canvas.getBoundingClientRect(); return { x: clamp((event.clientX - box.left) / Math.max(box.width, 1), 0, 1), y: clamp((event.clientY - box.top) / Math.max(box.height, 1), 0, 1) }; }
    bindCanvas() {
        this.layer.addEventListener('pointerdown', (event) => { if (event.button !== 0) return; event.preventDefault(); const point = this.point(event); const spot = newSpot(point); this.spots().push(spot); this.selected = this.spots().length - 1; this.dragging = { spot, kind: 'source', created: true }; this.layer.setPointerCapture?.(event.pointerId); this.emit('Create Heal Spot'); this.drawHandles(); });
        this.layer.addEventListener('pointermove', (event) => { if (!this.dragging) return; const point = this.point(event); const prefix = this.dragging.kind === 'destination' ? 'dst' : 'src'; this.dragging.spot[`${prefix}_x`] = point.x; this.dragging.spot[`${prefix}_y`] = point.y; this.emit(`Move Heal ${prefix === 'dst' ? 'Destination' : 'Source'}`, false); this.drawHandles(); });
        const finish = () => { if (!this.dragging) return; const label = this.dragging.kind === 'destination' ? 'Move Heal Destination' : 'Move Heal Source'; const created = this.dragging.created; this.dragging = null; if (!created) this.emit(label); this.render(); };
        this.layer.addEventListener('pointerup', finish); this.layer.addEventListener('pointercancel', finish);
    }

    drawHandles() {
        this.layer.replaceChildren(); if (!this.active) return;
        const box = this.canvas.getBoundingClientRect(); const stage = this.stage.getBoundingClientRect();
        this.spots().forEach((spot, index) => {
            const add = (kind, x, y) => { const handle = document.createElement('button'); handle.type = 'button'; handle.className = `develop-heal-handle ${kind}`; handle.dataset.tip = kind === 'source' ? 'Source: drag to move' : 'Destination: drag to move'; handle.style.left = `${box.left - stage.left + x * box.width}px`; handle.style.top = `${box.top - stage.top + y * box.height}px`; handle.addEventListener('pointerdown', (event) => { event.stopPropagation(); this.selected = index; this.historySnapshot = clone(this.settings); this.dragging = { spot, kind }; this.layer.setPointerCapture?.(event.pointerId); }); this.layer.append(handle); };
            add('source', spot.src_x, spot.src_y); add('destination', spot.dst_x, spot.dst_y);
            if (this.overlay) { const circle = document.createElement('i'); circle.className = 'develop-heal-circle'; circle.style.left = `${box.left - stage.left + spot.dst_x * box.width}px`; circle.style.top = `${box.top - stage.top + spot.dst_y * box.height}px`; circle.style.width = circle.style.height = `${spot.radius * Math.max(box.width, box.height) * 2}px`; this.layer.append(circle); }
        });
    }
    syncOverlay() { this.getRenderer?.()?.setHealOverlay?.(this.overlay); }
    keydown(event) { if (!this.active) return false; if (event.key.toLowerCase() === 'o') { this.overlay = !this.overlay; this.render(); return true; } if (event.key === 'Escape') { this.toggle(false); return true; } return false; }
}
