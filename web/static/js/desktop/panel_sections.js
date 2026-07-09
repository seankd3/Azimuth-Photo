import { icon } from '../icons.js';
import { patchPrefs, viewState } from './state.js';

function sectionId(section, index) {
    if (section.id) return section.id;
    const title = section.querySelector('.psec-head h3')?.textContent || `section-${index}`;
    return `psec-${title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

function readCollapsed(id) {
    const saved = viewState.prefs.panelSections || {};
    return Boolean(saved[id]);
}

function saveCollapsed(id, collapsed) {
    const panelSections = { ...(viewState.prefs.panelSections || {}), [id]: collapsed };
    patchPrefs({ panelSections });
}

function applyCollapsed(section, collapsed) {
    section.classList.toggle('collapsed', collapsed);
    section.querySelector('.psec-toggle')?.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
}

function enhanceSection(section, index) {
    const head = section.querySelector(':scope > .psec-head');
    const title = head?.querySelector('h3');
    if (!head || !title || head.dataset.collapseReady === '1') return;
    const id = sectionId(section, index);
    section.id = id;
    head.dataset.collapseReady = '1';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'psec-toggle';
    toggle.setAttribute('aria-label', `Toggle ${title.textContent.trim()}`);
    toggle.innerHTML = icon('chevron-down');
    head.prepend(toggle);
    head.setAttribute('role', 'button');
    head.setAttribute('tabindex', '0');
    const doToggle = () => {
        const collapsed = !section.classList.contains('collapsed');
        applyCollapsed(section, collapsed);
        saveCollapsed(id, collapsed);
    };
    head.addEventListener('click', (event) => {
        if (event.target.closest('.psec-act')) return;
        doToggle();
    });
    head.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        doToggle();
    });
    applyCollapsed(section, readCollapsed(id));
}

export function initPanelSections() {
    document.querySelectorAll('#panel-left .psec, #panel-right .psec').forEach(enhanceSection);
}
