import {
    clearFacet, describeScope, emit, folderChip, nonSearchFacetCount, on, scope, setBestOf, setScope, setSortBase,
    setThumbSize, sortAscending, sortBase, toggleBestOf, toggleSortDirection, viewState, folderValues,
} from './state.js';
import { toggleLeftPanel } from './panel.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { personLabel as cleanPersonLabel } from '../people_labels.js';
import { getRankings } from './api.js';
import { escapeHtml as esc, formatCount as fmt, MONTH_NAMES } from './dom.js';
import { effectiveExcludeSources, quietSourceIds } from './quiet_sources.js';

let thumbInputTimer = 0;
let tasteAvailable = false;

function searchSourceLabel(source) {
    return ({ embedding: 'vision', caption: 'captions', captions: 'captions', metadata: 'metadata' })[source]
        || String(source || '').replaceAll('_', ' ');
}

function browsingQuietSource() {
    const quiet = quietSourceIds();
    if (!quiet.length) return false;
    const folders = folderValues();
    if (!folders.length) return false;
    const excluded = effectiveExcludeSources(undefined, folders, { q: scope.q });
    return excluded.length < quiet.length;
}

function renderSearchModeChip() {
    if (!scope.q || !viewState.searchMode || !viewState.searchSources.length) return '';
    const mode = viewState.searchMode.charAt(0).toUpperCase() + viewState.searchMode.slice(1);
    const sources = viewState.searchSources.map(searchSourceLabel);
    const label = `${mode} · ${sources.join(' + ')}`;
    return `<span class="chip search-mode-chip" title="Search sources: ${esc(viewState.searchSources.join(', '))}">${esc(label)}</span>`;
}

function syncSimilarityOption() {
    const select = document.getElementById('sort-select');
    let option = select.querySelector('option[value="similarity"]');
    if (scope.q && !option) {
        option = document.createElement('option');
        option.value = 'similarity';
        option.textContent = 'Similarity';
        select.prepend(option);
    } else if (!scope.q && option) {
        option.remove();
    }
}

async function loadTasteStatus() {
    const data = await getRankings(new URLSearchParams({ sort: 'taste', limit: '0' })).catch(() => null);
    tasteAvailable = Boolean(data?.taste_available);
    const option = document.querySelector('#sort-select option[value="taste"]');
    if (!option) return;
    option.disabled = !tasteAvailable;
    option.textContent = tasteAvailable ? 'Taste' : 'Taste — Refine a few duels to teach it';
    option.title = tasteAvailable ? 'your eye, learned from Refine' : 'Refine a few duels to teach it';
    render();
}

function dateLabel(value) {
    if (value === 'undated') return 'Undated';
    const match = String(value || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return value;
    return `${MONTH_NAMES.short[Number(match[2]) - 1] || match[2]} ${match[1]}`;
}

function chipHtml(key, label, extra = '', className = '', title = label) {
    const cls = ['chip', className].filter(Boolean).join(' ');
    return `<span class="${cls}" data-facet="${key}" title="${esc(title)}">${extra}<span title="${esc(title)}">${esc(label)}</span><button class="chip-x" aria-label="Remove ${esc(label)}">${icon('x')}</button></span>`;
}

function renderChips() {
    const chips = [];
    if (scope.similarIds.length) chips.push(chipHtml('similarIds', scope.similarLabel || 'Similar photos'));
    if (scope.similarIds.length && scope.similarSourceId) {
        chips.push('<label class="similar-depth" data-tip="Re-run Similar from the source photo with this many results.">'
            + '<span>Depth</span><select id="similar-depth-select" aria-label="Similar depth">'
            + [100, 250, 500].map((value) => `<option value="${value}"${Number(scope.similarLimit || 100) === value ? ' selected' : ''}>${value}</option>`).join('')
            + '</select></label>');
    }
    if (scope.q) chips.push(chipHtml('q', `“${scope.q}”`, `<span class="tk-glyph tk-spark">${icon('sparkles')}</span>`));
    const searchMode = renderSearchModeChip();
    if (searchMode) chips.push(searchMode);
    if (scope.q && scope.deep) chips.push(chipHtml('deep', 'Deep search', `<span class="tk-glyph tk-spark">${icon('sparkles')}</span>`, 'smart-chip', 'Slower, more thorough visual search'));
    if (scope.collectionId) {
        const glyph = scope.collectionSmart ? `<span class="tk-glyph tk-spark">${icon('sparkles')}</span>` : '';
        chips.push(chipHtml('collectionId', `Collection · ${scope.collectionName || 'Untitled'}`, glyph, scope.collectionSmart ? 'smart-chip' : ''));
    }
    if (scope.import_batch) chips.push(chipHtml('import_batch', scope.importBatchLabel || `Import ${scope.import_batch}`));
    if (scope.people) {
        const label = cleanPersonLabel({ label: scope.personLabel });
        const display = label === 'Unnamed' ? 'Unnamed person' : label;
        const face = scope.personThumb ? `<img src="${esc(scope.personThumb)}" alt="">` : `<span class="tk-glyph">${icon('users')}</span>`;
        chips.push(chipHtml('people', display, face, 'person-chip'));
    }
    if (scope.flag) chips.push(chipHtml('flag', scope.flag === 'picked' ? 'Picked' : scope.flag === 'rejected' ? 'Rejected' : 'Unflagged'));
    const folderScope = folderChip();
    if (folderScope) chips.push(chipHtml('folder', folderScope.label, '', '', folderScope.title));
    if (browsingQuietSource()) {
        chips.push('<span class="chip quiet-source-note" title="This source stays managed; it is only hidden from default library views.">Hidden from library views</span>');
    }
    if (scope.date_taken) chips.push(chipHtml('date_taken', dateLabel(scope.date_taken)));
    if (scope.file_type) chips.push(chipHtml('file_type', String(scope.file_type).toUpperCase()));
    if (scope.camera) chips.push(chipHtml('camera', `Camera · ${scope.camera}`));
    if (scope.lens) chips.push(chipHtml('lens', `Lens · ${scope.lens}`));
    if (scope.tag) chips.push(chipHtml('tag', `Tag · ${scope.tag}`));
    if (scope.orientation) chips.push(chipHtml('orientation', scope.orientation === 'landscape' ? 'Landscape' : scope.orientation === 'portrait' ? 'Portrait' : scope.orientation));
    if (scope.compared) {
        const labels = { compared: 'Ranked', uncompared: 'Unranked', direct_uncompared: 'Not compared yet', confident: 'High confidence' };
        chips.push(chipHtml('compared', labels[scope.compared] || scope.compared));
    }
    if (scope.min_stars) chips.push(chipHtml('min_stars', `Elo ${scope.min_stars}+`));
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
    document.getElementById('similar-depth-select')?.addEventListener('change', (event) => {
        emit('similar:find', { imageId: Number(scope.similarSourceId || 0), limit: Number(event.target.value || 100) });
    });
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
    syncSimilarityOption();
    renderChips();
    renderQuality();
    const sharpening = viewState.hiddenPendingThumbnails;
    const sharpeningSuffix = sharpening
        ? `<span class="ctx-count-sharpening">· ${fmt(sharpening)} sharpening</span>`
        : '';
    if (viewState.bestOf) {
        const shown = viewState.bestOfLimit == null ? viewState.images.length : viewState.bestOfLimit;
        const total = viewState.bestOfTotal || viewState.visibleImages;
        document.getElementById('ctx-count').innerHTML = `Top <b>${fmt(shown)}</b> of ${fmt(total)}${sharpeningSuffix}`;
    } else {
        const visible = fmt(viewState.visibleImages);
        document.getElementById('ctx-count').innerHTML = `<b>${visible}</b> photos${sharpeningSuffix}`;
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
    document.getElementById('sort-select').value = sortBase();
    const sortDir = document.getElementById('sort-dir');
    const ascending = sortAscending();
    sortDir.innerHTML = icon(ascending ? 'sort-asc' : 'arrow-down-wide-narrow');
    sortDir.classList.toggle('active', ascending);
    sortDir.setAttribute('aria-pressed', ascending ? 'true' : 'false');
    sortDir.setAttribute('aria-label', ascending ? 'Sort ascending' : 'Sort descending');
    sortDir.disabled = sortBase() === 'taste' || sortBase() === 'similarity';
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
        setSortBase(event.target.value || 'elo');
    });
    document.getElementById('sort-dir').addEventListener('click', toggleSortDirection);
    document.getElementById('thumb-size').addEventListener('input', (event) => {
        window.clearTimeout(thumbInputTimer);
        const value = event.target.value;
        thumbInputTimer = window.setTimeout(() => setThumbSize(value), 70);
    });
    document.getElementById('canvas').addEventListener('wheel', adjustThumbWithWheel, { passive: false });
    on('scope', render);
    on('meta', render);
    on('bestof', render);
    on('bestof:unsupported', () => showToast('Best of isn’t available in collections yet.'));
    on('thumbsize', render);
    on('lens', (lens) => {
        if (lens === 'grid') loadTasteStatus();
    });
    render();
    loadTasteStatus();
}

export function scopeTokenHtml() {
    const count = viewState.visibleImages ? `<span class="tk-count">- ${fmt(viewState.visibleImages)}</span>` : '';
    if (scope.people) {
        const label = cleanPersonLabel({ label: scope.personLabel });
        const display = label === 'Unnamed' ? 'Unnamed person' : label;
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
        const label = scope.deep ? `Deep · “${scope.q}”` : `“${scope.q}”`;
        return `<span class="scope-token semantic" title="${esc(label)}"><span class="tk-glyph tk-spark">${icon('sparkles')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
    }
    if (scope.tag) {
        const label = `Tag · ${scope.tag}`;
        return `<span class="scope-token" title="${esc(label)}"><span class="tk-glyph">${icon('tag')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
    }
    const label = describeScope();
    return `<span class="scope-token" title="${esc(label)}"><span class="tk-glyph">${icon('house')}</span><b title="${esc(label)}">${esc(label)}</b>${count}</span>`;
}

export function scopeTokenFacetKey() {
    if (scope.people) return 'people';
    if (scope.collectionId) return 'collectionId';
    if (scope.import_batch) return 'import_batch';
    if (scope.similarIds.length) return 'similarIds';
    if (scope.q) return 'q';
    if (scope.tag) return 'tag';
    if (scope.flag) return 'flag';
    if (folderChip()) return 'folder';
    if (scope.date_taken) return 'date_taken';
    if (scope.file_type) return 'file_type';
    if (scope.camera) return 'camera';
    if (scope.lens) return 'lens';
    if (scope.orientation) return 'orientation';
    if (scope.compared) return 'compared';
    if (scope.min_stars) return 'min_stars';
    return '';
}
