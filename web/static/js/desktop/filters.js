import { getDateHistogram, getFilterOptions, getFolders, getPeople } from './api.js';
import { byId, on, scope, scopeParams, setScope } from './state.js';
import { loadCollectionImages } from './scope_data.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { icon } from '../icons.js';
import { personLabel } from '../people_labels.js';

let popover = null;
let loaded = false;
let loading = false;
let lastOptionsLoadedAt = 0;
let expandedYear = '';
let monthsLoadingKey = '';
let monthsLoadSeq = 0;
let activeMonthScopeSignature = '';
const monthsByScopeYear = new Map();
let options = {
    people: [],
    folders: [],
    years: [],
    fileTypes: [],
    cameras: [],
    lenses: [],
    undated: 0,
};

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const OPTIONS_MAX_AGE_MS = 60000;

function countLabel(item, fallback = '') {
    const count = item.count ?? item.image_count ?? item.face_count;
    return count == null ? fallback : fmt(count);
}

function selectValue(key, value, extra = {}) {
    setScope({
        [key]: String(value || ''),
        collectionId: '',
        collectionName: '',
        similarIds: [],
        similarLabel: '',
        ...extra,
    }, { merge: true });
    render();
}

function toggleValue(key, value) {
    selectValue(key, scope[key] === value ? '' : value);
}

function optionRows(items, key, valueOf, labelOf) {
    return (items || []).map((item) => {
        const value = String(valueOf(item) || '');
        if (!value) return '';
        const active = String(scope[key] || '') === value;
        const label = labelOf(item);
        return `<button class="filter-row ${active ? 'active' : ''}" data-key="${key}" data-value="${esc(value)}" title="${esc(label)}">`
            + `<span title="${esc(label)}">${esc(label)}</span><span class="num">${esc(countLabel(item))}</span></button>`;
    }).join('');
}

function emptyOption(copy, glyph = 'search') {
    return '<div class="filter-empty chrome-empty">'
        + `<span class="chrome-empty-glyph">${icon(glyph)}</span>`
        + `<span>${esc(copy)}</span></div>`;
}

function selectBlock(title, body, attrs = '') {
    return `<section class="filter-sec" ${attrs}><h3>${esc(title)}</h3><div class="filter-list">${body}</div></section>`;
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
    const rows = optionRows(
        options.people.filter((person) => {
            const query = (popover?.querySelector('#filter-people-search')?.value || '').trim().toLowerCase();
            return !query || personLabel(person).toLowerCase().includes(query);
        }),
        'people',
        (person) => person.id,
        personLabel,
    );
    return `<section class="filter-sec" data-filter-section="people"><h3>People</h3>`
        + '<input class="filter-search" id="filter-people-search" placeholder="Search people" autocomplete="off">'
        + `<div class="filter-list">${rows || emptyOption('No people found.', 'users')}</div></section>`;
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
        const monthRows = expanded
            ? '<div class="filter-months">'
                + (loadingMonths && !months.length ? '<div class="filter-empty">Loading months…</div>' : months.map((month) => {
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
    monthsLoadSeq += 1;
}

async function scopedMonthCounts(year) {
    if (scope.collectionId) {
        const images = await loadCollectionImages(scope.collectionId);
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
    const data = await getDateHistogram(params);
    return (data && data.months ? data.months : []).reduce((acc, item) => {
        const month = item.month || '';
        if (month.startsWith(`${year}-`)) acc.set(month, Number(item.count) || 0);
        return acc;
    }, new Map());
}

async function expandYear(year) {
    resetMonthCacheIfScopeChanged();
    expandedYear = expandedYear === year ? '' : year;
    const cacheKey = monthCacheKey(year);
    if (!expandedYear || monthsByScopeYear.has(cacheKey)) {
        render();
        return;
    }
    const token = ++monthsLoadSeq;
    monthsLoadingKey = cacheKey;
    render();
    const counts = await scopedMonthCounts(year);
    if (token !== monthsLoadSeq || cacheKey !== monthCacheKey(year) || expandedYear !== year) return;
    monthsByScopeYear.set(cacheKey, [...counts.entries()]
        .sort((a, b) => b[0].localeCompare(a[0]))
        .map(([value, count]) => ({ value, count, label: monthLabel(value) })));
    if (monthsLoadingKey === cacheKey) monthsLoadingKey = '';
    render();
}

function renderRanked() {
    return selectBlock('Ranked status', [
        ['compared', 'Ranked'],
        ['uncompared', 'Unranked'],
        ['confident', 'High confidence'],
    ].map(([value, label]) => (
        `<button class="filter-pill ${scope.compared === value ? 'active' : ''}" data-toggle-key="compared" data-value="${value}">${label}</button>`
    )).join(''), 'data-filter-section="ranked"');
}

function renderStars() {
    return selectBlock('Rating floor', [1, 2, 3, 4, 5].map((level) => {
        const active = Number(scope.min_stars || 0) >= level;
        return `<button class="filter-star ${active ? 'active' : ''}" data-star="${level}" aria-label="${level}+ rating">${icon('star')}</button>`;
    }).join(''), 'data-filter-section="stars"');
}

function render() {
    if (!popover) return;
    const keepPeopleFocus = document.activeElement?.id === 'filter-people-search';
    const peopleSearch = popover.querySelector('#filter-people-search')?.value || '';
    popover.innerHTML = `<div class="filter-pop-head"><b>Filter</b><button class="icon-btn" id="filter-close" data-tip="Close (Esc)" aria-label="Close">${icon('x')}</button></div>`
        + (loading ? '<div class="filter-loading skel"></div>' : [
            renderFlag(),
            renderPeople(),
            selectBlock('Folder', optionRows(options.folders, 'folder', (item) => item.path, (item) => item.path) || emptyOption('No folders found.', 'folder'), 'data-filter-section="folder"'),
            renderDate(),
            selectBlock('File type', optionRows(options.fileTypes, 'file_type', (item) => item.ext || item.value, (item) => String(item.ext || item.value).replace('.', '').toUpperCase()) || emptyOption('No file types found.', 'file-type'), 'data-filter-section="filetype"'),
            selectBlock('Camera', optionRows(options.cameras, 'camera', (item) => item.camera || item.value, (item) => item.camera || item.value) || emptyOption('No cameras found.', 'camera'), 'data-filter-section="camera"'),
            selectBlock('Lens', optionRows(options.lenses, 'lens', (item) => item.lens || item.value, (item) => item.lens || item.value) || emptyOption('No lenses found.', 'aperture'), 'data-filter-section="lens"'),
            selectBlock('Orientation', [
                ['landscape', 'Landscape'],
                ['portrait', 'Portrait'],
            ].map(([value, label]) => (
                `<button class="filter-pill ${scope.orientation === value ? 'active' : ''}" data-toggle-key="orientation" data-value="${value}">${label}</button>`
            )).join(''), 'data-filter-section="orientation"'),
            renderRanked(),
            renderStars(),
        ].join(''));
    const search = popover.querySelector('#filter-people-search');
    if (search) {
        search.value = peopleSearch;
        search.addEventListener('input', render);
        if (keepPeopleFocus) {
            search.focus({ preventScroll: true });
            search.setSelectionRange(search.value.length, search.value.length);
        }
    }
    bindRows();
}

function bindRows() {
    popover.querySelector('#filter-close')?.addEventListener('click', closeFilters);
    for (const row of popover.querySelectorAll('[data-key][data-value]')) {
        row.addEventListener('click', () => {
            const key = row.dataset.key;
            const value = scope[key] === row.dataset.value ? '' : row.dataset.value;
            if (row.dataset.year) expandYear(row.dataset.year);
            if (key === 'people') {
                const person = options.people.find((item) => String(item.id) === String(value));
                selectValue(key, value, {
                    personLabel: person ? personLabel(person) : '',
                    personThumb: person?.thumb_url || '',
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

async function loadOptions({ force = false } = {}) {
    if ((optionsAreFresh() && !force) || loading) return;
    loading = true;
    render();
    const [peopleData, folderData, filterData] = await Promise.all([
        getPeople(500),
        getFolders(),
        getFilterOptions(),
    ]);
    options = {
        people: (peopleData && (peopleData.people || peopleData.persons || peopleData.results)) || [],
        folders: (folderData && folderData.folders) || [],
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
}

function invalidateOptions() {
    loaded = false;
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
