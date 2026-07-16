import { setActiveLens } from './state.js';
import {
    bindSystemSurface, mountSystemSurface, refreshSystemSurface, renderSystemSections, unmountSystemSurface,
} from './drawer.js';

const SECTION_KEY = 'pa_d_system_section';
const SECTIONS = [
    ['library', 'Library'], ['processing', 'Processing'], ['performance', 'Performance'],
    ['import', 'Import'], ['publishing', 'Publishing'], ['connectivity', 'Connectivity'], ['preferences', 'Preferences'],
];

let root = null;
let activeSection = localStorage.getItem(SECTION_KEY) || 'library';

function render() {
    if (!root) return;
    const content = renderSystemSections();
    root.innerHTML = `<aside class="system-nav" aria-label="System sections">${SECTIONS.map(([id, label]) => (
        `<button type="button" data-system-section="${id}" class="${id === activeSection ? 'active' : ''}">${label}</button>`
    )).join('')}</aside><main class="system-canvas"><header class="system-canvas-head"><div><p>System</p><h1>${SECTIONS.find(([id]) => id === activeSection)?.[1] || 'Library'}</h1></div><button class="mini-btn" type="button" data-system-close>Back to photos</button></header><div id="system-lens-content">${content[activeSection] || content.library}</div></main>`;
    root.querySelectorAll('[data-system-section]').forEach((button) => button.addEventListener('click', () => {
        activeSection = button.dataset.systemSection;
        localStorage.setItem(SECTION_KEY, activeSection);
        render();
    }));
    root.querySelector('[data-system-close]')?.addEventListener('click', () => setActiveLens('grid'));
    bindSystemSurface(root.querySelector('#system-lens-content'));
}

export function openSystemLens(section = null) {
    if (section && SECTIONS.some(([id]) => id === section)) {
        activeSection = section;
        localStorage.setItem(SECTION_KEY, activeSection);
    }
    setActiveLens('system');
}

export function mountSystemLens() {
    root = document.getElementById('view-system');
    mountSystemSurface(render);
    render();
    refreshSystemSurface();
}

export function unmountSystemLens() {
    unmountSystemSurface();
    root = null;
}
