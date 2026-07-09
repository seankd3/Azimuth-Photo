import {
    clearFacet, describeScope, emit, nonSearchFacetCount, on, scope, setBestOf, setScope, setSort, setThumbSize, toggleBestOf, viewState,
} from './state.js';
import { toggleLeftPanel } from './panel.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { personLabel as cleanPersonLabel } from '../people_labels.js';

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
let thumbInputTimer = 0;

function dateLabel(value) {
    if (value === 'undated') return 'Undated';
    const match = String(value || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return value;
    return `${MONTHS[Number(match[2]) - 1] || match[2]} ${match[1]}`;
}

function chipHtml(key, label, extra = '', className = '') {
    const cls = ['chip', className].filter(Boolean).join(' ');
    return `<span class="${cls}" data-facet="${key}" title="${esc(label)}">${extra}<span title="${esc(label)}">${esc(label)}</span><button class="chip-x" aria-label="Remove ${esc(label)}">${icon('x')}</button></span>`;
}

function renderChips() {
    const chips = [];
    if (scope.similarIds.length) chips.push(chipHtml('similarIds', scope.similarLabel || 'Similar photos'));
    if (scope.q) chips.push(chipHtml('q', `“${scope.q}”`, `<span class="tk-glyph tk-spark">${icon('sparkles')}</span>`));
    if (scope.collectionId) {
        const glyph = scope.collectionSmart ? `<span class="tk-glyph tk-spark">${icon('sparkles')}</span>` : '';
        chips.push(chipHtml('collectionId', `Collection · ${scope.collectionName || 'Untitled'}`, glyph, scope.collectionSmart ? 'smart-chip' : ''));
    }
    if (scope.import_batch) chips.push(chipHtml('import_batch', scope.importBatchLabel || `Import ${scope.import_batch}`));
    if (scope.people) {
        const label = cleanPersonLabel({ label: scope.personLabel });
        const display = label === 'Unnamed' ? 'Add name' : label;
        const face = scope.personThumb ? `<img src="${esc(scope.personThumb)}" alt="">` : `<span class="tk-glyph">${icon('users')}</span>`;
        chips.push(chipHtml('people', display, face, 'person-chip'));
    }
    if (scope.flag) chips.push(chipHtml('flag', scope.flag === 'picked' ? 'Picked' : scope.flag === 'rejected' ? 'Rejected' : 'Unflagged'));
    if (scope.folder) chips.push(chipHtml('folder', scope.folder.split('/').filter(Boolean).pop() || scope.folder));
    if (scope.date_taken) chips.push(chipHtml('date_taken', dateLabel(scope.date_taken)));
    if (scope.file_type) chips.push(chipHtml('file_type', String(scope.file_type).toUpperCase()));
    if (scope.camera) chips.push(chipHtml('camera', `Camera · ${scope.camera}`));
    if (scope.lens) chips.push(chipHtml('lens', `Lens · ${scope.lens}`));
    if (scope.orientation) chips.push(chipHtml('orientation', scope.orientation === 'landscape' ? 'Landscape' : scope.orientation === 'portrait' ? 'Portrait' : scope.orientation));
    if (scope.compared) {
        const labels = { compared: 'Ranked', uncompared: 'Unranked', confident: 'High confidence' };
        chips.push(chipHtml('compared', labels[scope.compared] || scope.compared));
    }
    if (scope.min_stars) chips.push(chipHtml('min_stars', `${scope.min_stars}+ rating`));
    if (viewState.bestOf) chips.push(chipHtml('bestOf', 'Best of'));
    if (chips.length > 1) chips.push(`<button class="chip ghost" data-clear-all="1">${icon('x')}<span>Clear all</span></button>`);
    document.getElementById('ctx-crumbs').innerHTML = chips.join('');
    for (const chip of document.querySelectorAll('.chip[data-facet]')) {
        chip.querySelector('.chip-x').addEventListener('click', () => {
            if (chip.dataset.facet === 'bestOf') {
                setBestOf(false);
            } else {
                clearFacet(chip.dataset.facet);
            }
        });
    }
    document.querySelector('.chip[data-clear-all="1"]')?.addEventListener('click', () => setScope({}));
}

function renderQuality() {
    const wrap = document.getElementById('quality-wrap');
    const label = document.getElementById('quality-label');
    const fill = document.querySelector('#quality-bar i');
    const quality = viewState.sortQuality;
    const pct = quality && quality.percent != null ? Number(quality.percent) : null;
    wrap.classList.toggle('on', pct != null);
    if (pct == null) return;
    label.textContent = `${Math.round(pct)}% sorted`;
    fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function render() {
    renderChips();
    renderQuality();
    if (viewState.bestOf) {
        const shown = viewState.bestOfLimit == null ? viewState.images.length : viewState.bestOfLimit;
        const total = viewState.bestOfTotal || viewState.visibleImages;
        document.getElementById('ctx-count').innerHTML = `Top <b>${fmt(shown)}</b> of ${fmt(total)}`;
    } else {
        document.getElementById('ctx-count').innerHTML = `<b>${fmt(viewState.visibleImages)}</b> photos`;
    }
    document.getElementById('btn-bestof').classList.toggle('active', viewState.bestOf);
    const filterCount = nonSearchFacetCount();
    const filterButton = document.getElementById('btn-filter');
    const filterBadge = document.getElementById('filter-count');
    filterButton.classList.toggle('active', filterCount > 0);
    filterButton.setAttribute('aria-pressed', filterCount > 0 ? 'true' : 'false');
    if (filterBadge) {
        filterBadge.hidden = filterCount === 0;
        filterBadge.textContent = filterCount > 9 ? '9+' : String(filterCount);
    }
    document.getElementById('sort-select').value = scope.sort || 'elo';
    document.getElementById('thumb-size').value = String(viewState.thumbSize);
}

function adjustThumbWithWheel(event) {
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    const before = document.elementFromPoint(event.clientX, event.clientY);
    const cell = before && before.closest ? before.closest('.cell[data-id]') : null;
    const top = cell ? cell.getBoundingClientRect().top : null;
    const next = viewState.thumbSize + (event.deltaY > 0 ? -10 : 10);
    setThumbSize(next);
    requestAnimationFrame(() => {
        if (!cell || top == null) return;
        const after = cell.getBoundingClientRect().top;
        document.getElementById('canvas').scrollTop += after - top;
    });
}

export function initContextbar() {
    document.getElementById('btn-left-drawer').addEventListener('click', toggleLeftPanel);
    document.getElementById('btn-refine').addEventListener('click', () => emit('refine:open'));
    document.getElementById('btn-filter').addEventListener('click', () => emit('filters:toggle'));
    document.getElementById('btn-bestof').addEventListener('click', toggleBestOf);
    document.getElementById('sort-select').addEventListener('change', (event) => {
        setSort(event.target.value || 'elo');
    });
    document.getElementById('thumb-size').addEventListener('input', (event) => {
        window.clearTimeout(thumbInputTimer);
        const value = event.target.value;
        thumbInputTimer = window.setTimeout(() => setThumbSize(value), 70);
    });
    document.getElementById('canvas').addEventListener('wheel', adjustThumbWithWheel, { passive: false });
    on('scope', render);
    on('meta', render);
    on('bestof', render);
    on('bestof:unsupported', () => showToast('Collection Best of needs backend collection filtering first.'));
    on('thumbsize', render);
    render();
}

export function scopeTokenHtml() {
    const count = viewState.visibleImages ? `<span class="tk-count">- ${fmt(viewState.visibleImages)}</span>` : '';
    if (scope.people) {
        const label = cleanPersonLabel({ label: scope.personLabel });
        const display = label === 'Unnamed' ? 'Add name' : label;
        const img = scope.personThumb ? `<img src="${esc(scope.personThumb)}" alt="">` : `<span class="tk-glyph">${icon('users')}</span>`;
        return `<span class="scope-token person-token" title="${esc(display)}">${img}<b title="${esc(display)}">${esc(display)}</b>${count}</span>`;
    }
    if (scope.collectionId) {
        const label = scope.collectionName || 'Collection';
        const glyph = scope.collectionSmart ? 'sparkles' : 'folder';
        const cls = scope.collectionSmart ? ' smart-token' : '';
        return `<span class="scope-token${cls}" title="${esc(label)}"><span class="tk-glyph ${scope.collectionSmart ? 'tk-spark' : ''}">${icon(glyph)}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
    }
    if (scope.import_batch) {
        const label = scope.importBatchLabel || `Import ${scope.import_batch}`;
        return `<span class="scope-token" title="${esc(label)}"><span class="tk-glyph">${icon('upload')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
    }
    if (scope.similarIds.length) {
        const label = scope.similarLabel || 'Similar photos';
        return `<span class="scope-token" title="${esc(label)}"><span class="tk-glyph">${icon('scan-search')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
    }
    if (scope.q) {
        const label = `“${scope.q}”`;
        return `<span class="scope-token semantic" title="${esc(label)}"><span class="tk-glyph tk-spark">${icon('sparkles')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
    }
    const label = describeScope();
    return `<span class="scope-token" title="${esc(label)}"><span class="tk-glyph">${icon('house')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
}
