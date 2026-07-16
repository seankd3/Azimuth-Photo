import { getDateHistogram, getFilterOptions, getFolders, getTags } from './api.js';
import { byId, folderValues, on, scope, scopeParams, setScope } from './state.js';
import { loadCollectionImages } from './scope_data.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { icon } from '../icons.js';
import { personLabel } from '../people_labels.js';
import { showToast } from './toast.js';

let popover = null;
let loaded = false;
let loading = false;
let foldersLoading = false;
let tagsLoading = false;
let loadSeq = 0;
let lastOptionsLoadedAt = 0;
let expandedYear = '';
let monthsLoadingKey = '';
let monthsLoadSeq = 0;
let monthsErrorKey = '';
let activeMonthScopeSignature = '';
const monthsByScopeYear = new Map();
let options = {
    people: [],
    folders: [],
    years: [],
    fileTypes: [],
    cameras: [],
    lenses: [],
    tags: [],
    undated: 0,
};

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const OPTIONS_MAX_AGE_MS = 60000;
const SEARCHABLE_SECTION_MIN = 10;

function countLabel(item, fallback = '') {
    const count = item.count ?? item.image_count ?? item.face_count;
    return count == null ? fallback : fmt(count);
}

function personThumb(person) {
    return person?.face_thumb_url || person?.thumb_url || person?.image_thumb_url || '';
}

function selectValue(key, value, extra = {}) {
    const leavingScopedResults = Boolean(value) && (scope.collectionId || scope.similarIds.length);
    const previousScope = { ...scope, folder: [...folderValues()], similarIds: [...scope.similarIds] };
    setScope({
        [key]: key === 'folder' ? folderValues(value) : String(value || ''),
        ...(leavingScopedResults ? {
            collectionId: '',
            collectionName: '',
            collectionSmart: false,
            similarIds: [],
            similarSourceId: '',
            similarLimit: 100,
            similarLabel: '',
        } : {}),
        ...extra,
    }, { merge: true });
    if (leavingScopedResults) {
        const name = previousScope.collectionName || previousScope.similarLabel || 'this view';
        showToast(`Left '${name}'`, { undo: () => setScope(previousScope) });
    }
    render();
}

function toggleValue(key, value) {
    const active = key === 'folder' ? folderValues().includes(value) : scope[key] === value;
    selectValue(key, active ? '' : value);
}

function optionRows(items, key, valueOf, labelOf) {
    return (items || []).map((item) => {
        const value = String(valueOf(item) || '');
        if (!value) return '';
        const active = key === 'folder' ? folderValues().includes(value) : String(scope[key] || '') === value;
        const label = labelOf(item);
        const unavailable = Number(item.count ?? item.image_count ?? item.face_count) === 0;
        return `<button class="filter-row ${active ? 'active' : ''} ${unavailable ? 'unavailable' : ''}" data-key="${key}" data-value="${esc(value)}" title="${esc(label)}">`
            + `<span title="${esc(label)}">${esc(label)}</span><span class="num">${esc(countLabel(item))}</span></button>`;
    }).join('');
}

function sectionSearchQuery(section) {
    return (popover?.querySelector(`#filter-${section}-search`)?.value || '').trim().toLowerCase();
}

function searchableItems(section, items, labelOf) {
    const query = sectionSearchQuery(section);
    return !query ? items : items.filter((item) => String(labelOf(item) || '').toLowerCase().includes(query));
}

function sectionSearchInput(section, count, placeholder) {
    if (count <= SEARCHABLE_SECTION_MIN) return '';
    const query = sectionSearchQuery(section);
    return `<input class="filter-search" id="filter-${section}-search" value="${esc(query)}" placeholder="${esc(placeholder)}" autocomplete="off">`;
}

function searchableOptionBlock(title, section, items, key, valueOf, labelOf, { placeholder, empty, loading: isLoading = false, glyph = 'search' } = {}) {
    const filtered = searchableItems(section, items, labelOf);
    const rows = optionRows(filtered, key, valueOf, labelOf);
    const body = rows || (isLoading ? loadingOption(`Loading ${title.toLowerCase()}…`) : emptyOption(empty, glyph));
    return `<section class="filter-sec" data-filter-section="${esc(section)}"><h3>${esc(title)}</h3>${sectionSearchInput(section, items.length, placeholder)}<div class="filter-list">${body}</div></section>`;
}

function emptyOption(copy, glyph = 'search') {
    return '<div class="filter-empty chrome-empty">'
        + `<span class="chrome-empty-glyph">${icon(glyph)}</span>`
        + `<span>${esc(copy)}</span></div>`;
}

function loadingOption(copy) {
    return `<div class="filter-empty">${esc(copy)}</div>`;
}

function selectBlock(title, body, attrs = '') {
    return `<section class="filter-sec" ${attrs}><h3>${esc(title)}</h3><div class="filter-list">${body}</div></section>`;
}

function renderReadOnlyScope() {
    const rows = [];
    if (scope.collectionId) rows.push([scope.collectionSmart ? 'sparkles' : 'folder', `Collection · ${scope.collectionName || 'Untitled'}`]);
    if (scope.similarIds.length) rows.push(['scan-search', scope.similarLabel || 'Similar photos']);
    if (scope.import_batch) rows.push(['upload', scope.importBatchLabel || `Import ${scope.import_batch}`]);
    if (!rows.length) return '';
    return `<section class="filter-sec filter-readonly-scope"><h3>Current scope</h3><div class="filter-list">${rows.map(([glyph, label]) => (
        `<div class="filter-scope-row"><span class="tk-glyph">${icon(glyph)}</span><span title="${esc(label)}">${esc(label)}</span></div>`
    )).join('')}</div></section>`;
}

function renderFlag() {
    return selectBlock('Flag', [
        ['picked', 'Picked'],
        ['unflagged', 'Unflagged'],
        ['rejected', 'Rejected'],
    ].map(([value, label]) => (
        `<button class="filter-pill ${scope.flag === value ? 'active' : ''}" data-toggle-key="flag" data-value="${value}">${label}</button>`
    )).join(''), 'data-filter-section="flag"');
}

function renderPeople() {
    return searchableOptionBlock('People', 'people', options.people, 'people', (person) => person.id, personLabel, {
        placeholder: 'Search people', empty: 'No people found.', loading, glyph: 'users',
    });
}

function folderName(path) {
    return String(path || '').split('/').filter(Boolean).pop() || path || '';
}

function folderRows(items) {
    return (items || []).map((item) => {
        const value = String(item.path || '');
        if (!value) return '';
        const active = folderValues().includes(value);
        const label = folderName(value);
        const unavailable = Number(item.count) === 0;
        return `<button class="filter-row filter-folder-row ${active ? 'active' : ''} ${unavailable ? 'unavailable' : ''}" data-key="folder" data-value="${esc(value)}" title="${esc(value)}">`
            + `<span class="filter-folder-copy"><span title="${esc(label)}">${esc(label)}</span><small title="${esc(value)}">${esc(value)}</small></span><span class="num">${fmt(item.count)}</span></button>`;
    }).join('');
}

function renderFolders() {
    const filtered = searchableItems('folder', options.folders, (item) => item.path);
    const rows = folderRows(filtered);
    const body = rows || (foldersLoading ? loadingOption('Loading folders…') : emptyOption('No folders found.', 'folder'));
    return `<section class="filter-sec" data-filter-section="folder"><h3>Folder</h3>${sectionSearchInput('folder', options.folders.length, 'Search folders')}<div class="filter-list">${body}</div></section>`;
}

function renderDate() {
    const yearRows = (options.years || []).map((item) => {
        const year = String(item.year || item.value || '');
        if (!year) return '';
        const active = String(scope.date_taken || '') === year;
        const expanded = expandedYear === year;
        const cacheKey = monthCacheKey(year);
        const months = monthsByScopeYear.get(cacheKey) || [];
        const loadingMonths = monthsLoadingKey === cacheKey;
        const monthsFailed = monthsErrorKey === cacheKey;
        const monthRows = expanded
            ? '<div class="filter-months">'
                + (loadingMonths && !months.length
                    ? '<div class="filter-empty">Loading months…</div>'
                    : monthsFailed && !months.length
                        ? '<div class="filter-empty">Couldn\'t load months. <button type="button" class="btn" data-months-retry="' + esc(year) + '">Try again</button></div>'
                        : months.map((month) => {
                            const activeMonth = scope.date_taken === month.value;
                            return `<button class="filter-row filter-month ${activeMonth ? 'active' : ''}" data-key="date_taken" data-value="${month.value}" title="${esc(month.label)}">`
                                + `<span title="${esc(month.label)}">${esc(month.label)}</span><span class="num">${fmt(month.count)}</span></button>`;
                        }).join('') || emptyOption('No months in this year.', 'calendar'))
                + '</div>'
            : '';
        return `<button class="filter-row ${active ? 'active' : ''}" data-key="date_taken" data-value="${esc(year)}" data-year="${esc(year)}" title="${esc(year)}">`
            + `<span title="${esc(year)}">${esc(year)}</span><span class="num">${esc(countLabel(item))}</span></button>${monthRows}`;
    }).join('');
    const undated = Number(options.undated || 0) > 0
        ? `<button class="filter-row ${scope.date_taken === 'undated' ? 'active' : ''}" data-key="date_taken" data-value="undated" title="Undated"><span title="Undated">Undated</span><span class="num">${fmt(options.undated)}</span></button>`
        : '';
    return selectBlock('Date', yearRows + undated || emptyOption('No dates found.', 'calendar'), 'data-filter-section="date"');
}

function monthLabel(value) {
    const [year, month] = String(value || '').split('-');
    const index = Number(month) - 1;
    return index >= 0 && index < MONTHS.length ? `${MONTHS[index]} ${year}` : value;
}

function monthFromDate(value) {
    if (!value) return '';
    const match = String(value).match(/^(\d{4})-(\d{2})/);
    return match ? `${match[1]}-${match[2]}` : '';
}

function monthScopeSignature() {
    const params = scopeParams();
    params.delete('sort');
    params.delete('date_taken');
    if (scope.collectionId) params.set('collection_id', String(scope.collectionId));
    if (scope.similarIds.length) {
        params.set('similar_ids', scope.similarIds.map(Number).filter((id) => id > 0).join(','));
    }
    return params.toString();
}

function monthCacheKey(year) {
    return `${activeMonthScopeSignature}|${year}`;
}

function resetMonthCacheIfScopeChanged() {
    const signature = monthScopeSignature();
    if (signature === activeMonthScopeSignature) return;
    activeMonthScopeSignature = signature;
    monthsByScopeYear.clear();
    monthsLoadingKey = '';
    monthsErrorKey = '';
    monthsLoadSeq += 1;
}

async function scopedMonthCounts(year) {
    if (scope.collectionId) {
        let images = [];
        try {
            images = await loadCollectionImages(scope.collectionId);
        } catch {
            throw new Error('collection-months');
        }
        return images.reduce((acc, img) => {
            const month = monthFromDate(img.date_taken);
            if (month.startsWith(`${year}-`)) acc.set(month, (acc.get(month) || 0) + 1);
            return acc;
        }, new Map());
    }
    if (scope.similarIds.length) {
        return scope.similarIds.map(Number).reduce((acc, id) => {
            const img = byId.get(id);
            const month = monthFromDate(img && img.date_taken);
            if (month.startsWith(`${year}-`)) acc.set(month, (acc.get(month) || 0) + 1);
            return acc;
        }, new Map());
    }
    const params = scopeParams();
    params.delete('sort');
    params.delete('date_taken');
    let data = null;
    try {
        data = await getDateHistogram(params);
    } catch {
        return new Map();
    }
    return (data && data.months ? data.months : []).reduce((acc, item) => {
        const month = item.month || '';
        if (month.startsWith(`${year}-`)) acc.set(month, Number(item.count) || 0);
        return acc;
    }, new Map());
}

async function expandYear(year, { force = false } = {}) {
    resetMonthCacheIfScopeChanged();
    if (!force) expandedYear = expandedYear === year ? '' : year;
    else expandedYear = year;
    const cacheKey = monthCacheKey(year);
    if (!expandedYear || (monthsByScopeYear.has(cacheKey) && !force)) {
        render();
        return;
    }
    if (force) monthsByScopeYear.delete(cacheKey);
    const token = ++monthsLoadSeq;
    monthsLoadingKey = cacheKey;
    monthsErrorKey = '';
    render();
    let counts;
    try {
        counts = await scopedMonthCounts(year);
    } catch {
        if (token !== monthsLoadSeq || cacheKey !== monthCacheKey(year) || expandedYear !== year) return;
        if (monthsLoadingKey === cacheKey) monthsLoadingKey = '';
        monthsErrorKey = cacheKey;
        render();
        return;
    }
    if (token !== monthsLoadSeq || cacheKey !== monthCacheKey(year) || expandedYear !== year) return;
    monthsByScopeYear.set(cacheKey, [...counts.entries()]
        .sort((a, b) => b[0].localeCompare(a[0]))
        .map(([value, count]) => ({ value, count, label: monthLabel(value) })));
    if (monthsLoadingKey === cacheKey) monthsLoadingKey = '';
    if (monthsErrorKey === cacheKey) monthsErrorKey = '';
    render();
}

function renderRanked() {
    return selectBlock('Ranked status', [
        ['compared', 'Ranked'],
        ['uncompared', 'Unranked'],
        ['direct_uncompared', 'Not compared yet'],
        ['confident', 'High confidence'],
    ].map(([value, label]) => {
        const tip = value === 'direct_uncompared' ? ' data-tip="Photos you haven’t compared yet in Refine."' : '';
        return `<button class="filter-pill ${scope.compared === value ? 'active' : ''}" data-toggle-key="compared" data-value="${value}"${tip}>${label}</button>`;
    }).join(''), 'data-filter-section="ranked"');
}

function renderStars() {
    return selectBlock('Elo floor', [1, 2, 3, 4, 5].map((level) => {
        const active = Number(scope.min_stars || 0) >= level;
        return `<button class="filter-star ${active ? 'active' : ''}" data-star="${level}" aria-label="Elo ${level}+">${icon('star')}</button>`;
    }).join(''), 'data-filter-section="stars"');
}

function render() {
    if (!popover) return;
    const focusedSearch = document.activeElement?.classList.contains('filter-search') ? document.activeElement.id : '';
    popover.innerHTML = `<div class="filter-pop-head"><b>Filter</b><button class="icon-btn" id="filter-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
        + [
            renderReadOnlyScope(),
            renderFlag(),
            renderPeople(),
            renderFolders(),
            renderDate(),
            selectBlock('File type', optionRows(options.fileTypes, 'file_type', (item) => item.ext || item.value, (item) => String(item.ext || item.value).replace('.', '').toUpperCase())
                || (loading ? loadingOption('Loading file types…') : emptyOption('No file types found.', 'file-type')), 'data-filter-section="filetype"'),
            searchableOptionBlock('Camera', 'camera', options.cameras, 'camera', (item) => item.camera || item.value, (item) => item.camera || item.value, {
                placeholder: 'Search cameras', empty: 'No cameras found.', loading, glyph: 'camera',
            }),
            searchableOptionBlock('Lens', 'lens', options.lenses, 'lens', (item) => item.lens || item.value, (item) => item.lens || item.value, {
                placeholder: 'Search lenses', empty: 'No lenses found.', loading, glyph: 'aperture',
            }),
            searchableOptionBlock('Tags', 'tags', options.tags, 'tag', (item) => item.tag || item.value, (item) => item.tag || item.value, {
                placeholder: 'Search tags', empty: 'No caption tags yet.', loading: tagsLoading, glyph: 'tag',
            }),
            selectBlock('Orientation', [
                ['landscape', 'Landscape'],
                ['portrait', 'Portrait'],
            ].map(([value, label]) => (
                `<button class="filter-pill ${scope.orientation === value ? 'active' : ''}" data-toggle-key="orientation" data-value="${value}">${label}</button>`
            )).join(''), 'data-filter-section="orientation"'),
            renderRanked(),
            renderStars(),
        ].join('');
    for (const search of popover.querySelectorAll('.filter-search')) {
        search.addEventListener('input', render);
        if (search.id === focusedSearch) {
            search.focus({ preventScroll: true });
            search.setSelectionRange(search.value.length, search.value.length);
        }
    }
    bindRows();
}

function bindRows() {
    popover.querySelector('#filter-close')?.addEventListener('click', closeFilters);
    for (const retry of popover.querySelectorAll('[data-months-retry]')) {
        retry.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            expandYear(retry.dataset.monthsRetry, { force: true });
        });
    }
    for (const row of popover.querySelectorAll('[data-key][data-value]')) {
        row.addEventListener('click', () => {
            const key = row.dataset.key;
            const active = key === 'folder' ? folderValues().includes(row.dataset.value) : scope[key] === row.dataset.value;
            if (row.classList.contains('unavailable') && !active) return;
            const value = active ? '' : row.dataset.value;
            if (row.dataset.year) expandYear(row.dataset.year);
            if (key === 'people') {
                const person = options.people.find((item) => String(item.id) === String(value));
                selectValue(key, value, {
                    personLabel: person ? personLabel(person) : '',
                    personThumb: personThumb(person),
                });
            } else {
                selectValue(key, value);
            }
        });
    }
    for (const button of popover.querySelectorAll('[data-toggle-key][data-value]')) {
        button.addEventListener('click', () => toggleValue(button.dataset.toggleKey, button.dataset.value));
    }
    for (const star of popover.querySelectorAll('[data-star]')) {
        star.addEventListener('click', () => {
            const level = Number(star.dataset.star);
            selectValue('min_stars', Number(scope.min_stars || 0) === level ? '' : level);
        });
    }
}

function optionsAreFresh() {
    return loaded && Date.now() - lastOptionsLoadedAt < OPTIONS_MAX_AGE_MS;
}

let optionsScopeKey = '';

async function loadOptions({ force = false } = {}) {
    const scopeKey = scopeParams().toString();
    if (scopeKey !== optionsScopeKey) {
        optionsScopeKey = scopeKey;
        force = true;
    }
    if ((optionsAreFresh() && !force) || loading) return;
    const seq = ++loadSeq;
    loading = true;
    foldersLoading = true;
    tagsLoading = true;
    render();
    const core = getFilterOptions(scopeParams()).then((filterData) => {
        if (seq !== loadSeq) return;
        options = {
            ...options,
            people: (filterData && filterData.people) || [],
            years: (filterData && filterData.years) || [],
            fileTypes: (filterData && filterData.file_types) || [],
            cameras: (filterData && filterData.cameras) || [],
            lenses: (filterData && filterData.lenses) || [],
            undated: Number(filterData && filterData.undated) || 0,
        };
        loaded = true;
        lastOptionsLoadedAt = Date.now();
        loading = false;
        render();
    }).catch(() => {
        if (seq !== loadSeq) return;
        loading = false;
        render();
    });
    const folders = getFolders().then((folderData) => {
        if (seq !== loadSeq) return;
        options.folders = (folderData && folderData.folders) || [];
    }).catch(() => {}).finally(() => {
        if (seq !== loadSeq) return;
        foldersLoading = false;
        render();
    });
    const tags = getTags({ limit: 100 }).then((tagData) => {
        if (seq !== loadSeq) return;
        options.tags = (tagData && tagData.tags) || [];
    }).catch(() => {}).finally(() => {
        if (seq !== loadSeq) return;
        tagsLoading = false;
        render();
    });
    await Promise.allSettled([core, folders, tags]);
}

function invalidateOptions() {
    loadSeq += 1;
    loaded = false;
    loading = false;
    foldersLoading = false;
    tagsLoading = false;
    lastOptionsLoadedAt = 0;
    resetMonthCacheIfScopeChanged();
    if (filtersOpen()) loadOptions({ force: true });
}

function ensurePopover() {
    if (popover) return popover;
    popover = document.createElement('div');
    popover.id = 'filter-popover';
    popover.className = 'filter-popover';
    popover.hidden = true;
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', 'Filter photos');
    popover.tabIndex = -1;
    document.body.appendChild(popover);
    popover.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeFilters();
        }
    });
    return popover;
}

function positionPopover() {
    const anchor = document.getElementById('btn-filter');
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(420, window.innerWidth - 24);
    const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left - width / 2 + rect.width / 2));
    popover.style.width = `${width}px`;
    popover.style.left = `${left}px`;
    popover.style.top = `${Math.min(window.innerHeight - 120, rect.bottom + 8)}px`;
}

export function openFilters() {
    ensurePopover();
    if (!popover.hidden) return;
    resetMonthCacheIfScopeChanged();
    popover.hidden = false;
    positionPopover();
    render();
    trapFocus(popover, popover.querySelector('button, input'));
    loadOptions();
}

export function closeFilters() {
    if (!popover || popover.hidden) return;
    popover.hidden = true;
    releaseFocus(popover);
}

export function filtersOpen() {
    return Boolean(popover && !popover.hidden);
}

function outsideClose(event) {
    if (!popover || popover.hidden) return;
    if (popover.contains(event.target) || document.getElementById('btn-filter').contains(event.target)) return;
    closeFilters();
}

export function initFilters() {
    ensurePopover();
    on('filters:open', openFilters);
    on('filters:toggle', () => (filtersOpen() ? closeFilters() : openFilters()));
    on('scope', () => {
        resetMonthCacheIfScopeChanged();
        if (!popover.hidden) render();
    });
    on('flags', invalidateOptions);
    on('trash:changed', invalidateOptions);
    on('import:changed', invalidateOptions);
    on('collections:changed', invalidateOptions);
    document.addEventListener('pointerdown', outsideClose);
    window.addEventListener('resize', () => {
        if (filtersOpen()) positionPopover();
    });
}
