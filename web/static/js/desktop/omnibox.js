import {
    getFilterOptions, getFolders, getPeople, getRankings, getTags, listCollections, thumbUrl,
} from './api.js';
import {
    emit, folderLabel, folderValues, on, patchScope, scope, scopeActive, setScope, setSort, smartQueryActive, smartQuerySummary, toggleBestOf,
} from './state.js';
import { currentFocusedImage } from './grid.js';
import { openLoupe, toggleLoupeLights } from './loupe.js';
import { scopeTokenHtml } from './contextbar.js';
import {
    exportCurrentScope, requestDeleteCurrentCollection, requestNewCollection,
    requestRenameCurrentCollection, requestSaveSmartCollection, requestShareCurrentCollection, toggleLeftPanel,
} from './panel.js';
import { switchLens } from './lenses.js';
import { openSuggestionsReview } from './suggestions.js';
import { icon } from '../icons.js';
import { isUnnamedPersonLabel, personLabel } from '../people_labels.js';

const RECENT_KEY = 'pa_d_recent_scopes';
const LIVE_DELAY_MS = 250;
const LIVE_MIN_CHARS = 2;
const LIVE_LIMIT = 6;
const MAX_SECTION_ROWS = 6;
const DEEP_SEARCH_TIP = 'Uses the active embedding model instead of the fast search model when those model indexes differ.';
const FLAG_VALUES = [
    { value: 'picked', label: 'Picked', icon: 'star' },
    { value: 'rejected', label: 'Rejected', icon: 'x' },
    { value: 'unflagged', label: 'Unflagged', icon: 'circle' },
];
const MONTHS = [
    ['january', 'jan', 1],
    ['february', 'feb', 2],
    ['march', 'mar', 3],
    ['april', 'apr', 4],
    ['may', 'may', 5],
    ['june', 'jun', 6],
    ['july', 'jul', 7],
    ['august', 'aug', 8],
    ['september', 'sep', 9],
    ['october', 'oct', 10],
    ['november', 'nov', 11],
    ['december', 'dec', 12],
];
const OPERATORS = [
    { name: 'camera', key: 'camera', icon: 'camera', label: 'Camera' },
    { name: 'lens', key: 'lens', icon: 'aperture', label: 'Lens' },
    { name: 'tag', key: 'tag', icon: 'tag', label: 'Caption tag' },
    { name: 'type', key: 'file_type', icon: 'file-type', label: 'File type' },
    { name: 'flag', key: 'flag', icon: 'flag', label: 'Flag' },
    { name: 'folder', key: 'folder', icon: 'folder', label: 'Folder' },
];
const COMMANDS = [
    { icon: 'zap', label: 'Open Refine', kbd: 'R', run: () => emit('refine:open') },
    { icon: 'image', label: 'Open Loupe', kbd: 'E', run: () => {
        const img = currentFocusedImage();
        if (img) openLoupe({ id: img.id });
    } },
    { icon: 'eye', label: 'Cycle Loupe lights', kbd: 'L', run: toggleLoupeLights },
    { icon: 'layers', label: 'Stacks', run: () => emit('duplicates:open') },
    { icon: 'sparkles', label: 'Review suggested collections', run: openSuggestionsReview },
    { icon: 'funnel', label: 'Filter…', run: () => emit('filters:open') },
    { icon: 'upload', label: 'Import', run: () => emit('import:open') },
    { icon: 'star', label: 'Toggle Best of', kbd: 'B', run: toggleBestOf },
    { icon: 'download', label: 'Export CSV', run: () => exportCurrentScope('csv') },
    { icon: 'download', label: 'Export JSON', run: () => exportCurrentScope('json') },
    { icon: 'download', label: 'Download files (zip)', run: () => exportCurrentScope('zip', 'original') },
    { icon: 'plus', label: 'New collection', run: requestNewCollection },
    { icon: 'sparkles', label: 'Save as Smart Collection', meta: () => smartQuerySummary(), when: smartQueryActive, run: requestSaveSmartCollection },
    { icon: 'share-2', label: 'Share this collection', when: () => Boolean(scope.collectionId), run: requestShareCurrentCollection },
    { icon: 'pencil', label: 'Rename this collection', when: () => Boolean(scope.collectionId), run: requestRenameCurrentCollection },
    { icon: 'trash-2', label: 'Delete this collection', when: () => Boolean(scope.collectionId), run: requestDeleteCurrentCollection },
    { icon: 'layout-grid', label: 'Switch lens: grid', kbd: 'G', run: () => switchLens('grid') },
    { icon: 'rows-3', label: 'Switch lens: events', run: () => switchLens('events') },
    { icon: 'users', label: 'Switch lens: people', kbd: 'O', run: () => switchLens('people') },
    { icon: 'map-pin', label: 'Switch lens: map', kbd: 'M', run: () => switchLens('map') },
    { icon: 'panel-left', label: 'Toggle left panel', kbd: '[', run: toggleLeftPanel },
    { icon: 'keyboard', label: 'Keyboard shortcuts', kbd: '?', run: () => emit('help:open') },
    { icon: 'house', label: 'Clear scope / All photos', run: () => setScope({}) },
    { icon: 'arrow-down-wide-narrow', label: 'Sort by Elo', run: () => setSort('elo') },
    { icon: 'calendar', label: 'Sort by date', run: () => setSort('date_taken') },
    { icon: 'file-type', label: 'Sort by filename', run: () => setSort('filename') },
];

let people = [];
let collections = [];
let folders = [];
let tags = [];
let filterOptions = null;
let dataPromise = null;
let rows = [];
let hot = -1;
let liveTimer = null;
let liveAbort = null;
let liveSeq = 0;
let live = { q: '', loading: false, data: null };
let tokenSelected = false;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function fuzzy(haystack, needle) {
    const hay = String(haystack || '').toLowerCase();
    const term = String(needle || '').toLowerCase().trim();
    if (!term) return true;
    let i = 0;
    for (const ch of hay) {
        if (ch === term[i]) i += 1;
        if (i === term.length) return true;
    }
    return false;
}

function includesText(haystack, needle) {
    return String(haystack || '').toLowerCase().includes(String(needle || '').toLowerCase());
}

function highlight(value, term) {
    const text = String(value == null ? '' : value);
    const cleanTerm = String(term || '').trim();
    if (!cleanTerm) return esc(text);
    const index = text.toLowerCase().indexOf(cleanTerm.toLowerCase());
    if (index < 0) return esc(text);
    return `${esc(text.slice(0, index))}<mark>${esc(text.slice(index, index + cleanTerm.length))}</mark>${esc(text.slice(index + cleanTerm.length))}`;
}

function aspect(img) {
    const ar = Number(img?.aspect_ratio) || (Number(img?.width) && Number(img?.height) ? Number(img.width) / Number(img.height) : 1.5);
    return Math.max(.65, Math.min(2.1, ar));
}

function activeToken(input) {
    const raw = String(input || '');
    const match = raw.match(/(?:^|\s)(\S*)$/);
    const token = match ? match[1] : raw;
    return {
        token,
        start: raw.length - token.length,
        end: raw.length,
    };
}

function remainingQuery(input, start, end) {
    return `${input.slice(0, start)} ${input.slice(end)}`.replace(/\s+/g, ' ').trim();
}

function recentScopes() {
    try {
        const values = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
        return Array.isArray(values) ? values : [];
    } catch {
        return [];
    }
}

function storeRecents(values) {
    localStorage.setItem(RECENT_KEY, JSON.stringify(values.slice(0, 6)));
}

function scopeLabel(value) {
    if (value.collectionName) return value.collectionName;
    if (value.personLabel) return personLabel({ label: value.personLabel });
    if (value.people) return 'Add name';
    const folders = folderValues(value.folder);
    if (folders.length) return folders.length === 1 ? folderLabel(folders[0]) : `${folderLabel(folders[0])} + ${folders.length - 1} more`;
    if (value.date_taken) return dateLabel(value.date_taken);
    if (value.camera) return `Camera · ${value.camera}`;
    if (value.lens) return `Lens · ${value.lens}`;
    if (value.file_type) return String(value.file_type).toUpperCase();
    if (value.flag) return value.flag === 'picked' ? 'Picked' : value.flag === 'rejected' ? 'Rejected' : 'Unflagged';
    if (value.q) return `“${value.q}”`;
    return 'All photos';
}

function scopeIcon(value) {
    if (value.personLabel || value.people) return 'users';
    if (value.collectionSmart) return 'sparkles';
    if (value.collectionName || value.collectionId || folderValues(value.folder).length) return 'folder';
    if (value.date_taken) return 'calendar';
    if (value.camera) return 'camera';
    if (value.lens) return 'aperture';
    if (value.file_type) return 'file-type';
    if (value.flag) return 'flag';
    if (value.q) return 'search';
    return 'house';
}

function remember(value) {
    const storedScope = { ...value };
    const values = recentScopes().filter((item) => JSON.stringify(item.scope) !== JSON.stringify(storedScope));
    values.unshift({ label: scopeLabel(storedScope), scope: storedScope });
    storeRecents(values);
}

async function ensureSuggestionData() {
    if (dataPromise) return dataPromise;
    dataPromise = Promise.all([
        getPeople(500),
        listCollections(),
        getFolders(null),
        getFilterOptions(),
        getTags({ limit: 100 }),
    ]).then(([peopleData, collectionData, folderData, optionsData, tagData]) => {
        people = flattenPeople(peopleData);
        collections = (collectionData && collectionData.collections) || [];
        folders = (folderData && folderData.folders) || [];
        filterOptions = optionsData || {};
        tags = (tagData && tagData.tags) || [];
        render();
    }).catch(() => {});
    return dataPromise;
}

function invalidateSuggestionData() {
    dataPromise = null;
    if (document.getElementById('scopebox')?.classList.contains('open')) ensureSuggestionData();
}

function open() {
    document.getElementById('scopebox').classList.add('open');
}

function close() {
    document.getElementById('scopebox').classList.remove('open');
    hot = -1;
    document.getElementById('scope-input')?.removeAttribute('aria-activedescendant');
}

function sectionHead(label, action = '') {
    return `<div class="sd-head"><span>${esc(label)}</span>${action}</div>`;
}

function rowHtml(row, index) {
    const face = row.thumb
        ? `<img class="sd-face" src="${esc(row.thumb)}" alt="">`
        : `<span class="sd-glyph">${row.icon ? icon(row.icon) : esc(row.glyph || '')}</span>`;
    const rawMeta = typeof row.meta === 'function' ? row.meta() : row.meta;
    const meta = row.kbd ? `<kbd>${esc(row.kbd)}</kbd>` : esc(rawMeta || '');
    const label = row.labelHtml || esc(row.label);
    const recentRemove = row.recentIndex == null
        ? ''
        : `<button class="sd-recent-x" data-recent-index="${row.recentIndex}" aria-label="Remove recent">${icon('x')}</button>`;
    const cls = ['sd-item', index === hot ? 'hot' : '', row.muted ? 'muted' : ''].filter(Boolean).join(' ');
    const title = row.label || '';
    return `<div id="scope-option-${index}" class="${cls}" role="option" aria-selected="${index === hot ? 'true' : 'false'}" data-index="${index}" title="${esc(title)}">${face}<span class="sd-label" title="${esc(title)}">${label}</span><span class="sd-meta">${meta}</span>${recentRemove}</div>`;
}

function photoStripHtml(items) {
    return '<div class="sd-photo-strip" role="group" aria-label="Photo search results">'
        + items.map((row) => {
            const index = row.runIndex;
            const img = row.photo;
            const cls = `sd-photo ${index === hot ? 'hot' : ''}`;
            return `<button id="scope-option-${index}" class="${cls}" role="option" aria-selected="${index === hot ? 'true' : 'false'}" data-index="${index}" aria-label="${esc(img.filename || `Photo ${img.id}`)}" title="${esc(img.filename || `Photo ${img.id}`)}" style="--ar:${aspect(img)}">`
                + `<img src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" alt="">`
                + '</button>';
        }).join('')
        + '</div>';
}

function personThumb(person) {
    return person?.face_thumb_url || person?.thumb_url || person?.image_thumb_url || '';
}

function personCount(person) {
    return Number(person?.image_count || person?.photo_count || person?.face_count || 0);
}

function flattenPeople(peopleData) {
    const sections = (peopleData && peopleData.sections) || {};
    const seen = new Map();
    for (const list of [
        peopleData?.people,
        peopleData?.persons,
        peopleData?.results,
        sections.named_people,
        sections.most_seen,
        sections.other_faces,
    ]) {
        for (const person of list || []) {
            if (person?.id != null && !seen.has(String(person.id))) seen.set(String(person.id), person);
        }
    }
    return [...seen.values()];
}

function idQuery(term) {
    const text = String(term || '').trim().toLowerCase();
    return text.match(/^(?:#|id:?)?\d+$/) ? text.replace(/\D/g, '') : '';
}

function skeletonPhotosHtml() {
    return '<div class="sd-photo-strip" aria-label="Loading photo search results">'
        + Array.from({ length: LIVE_LIMIT }, (_, i) => `<span class="sd-photo sd-photo-skel skel" style="--ar:${[1.45, .8, 1.2, 1.7, 1, 1.55][i]}"></span>`).join('')
        + '</div>';
}

function searchRow(term) {
    const count = live.q === term && live.data ? Number(live.data.visible_images) : null;
    const meta = count == null ? 'Enter' : `${fmt(count)} results ⏎`;
    return {
        icon: live.q === term && live.data && !live.data.ai_unavailable ? 'sparkles' : 'search',
        label: `Search “${term}”`,
        labelHtml: `Search “${highlight(term, term)}”`,
        meta,
        navRow: 1,
        run: () => applySearch(term),
    };
}

function buildPhotoRows(term) {
    if (!term) return [];
    const section = [{ head: 'Photos' }, { deepToggle: true, term }, searchRow(term)];
    if (term.length < LIVE_MIN_CHARS) return section;
    if (live.q === term && live.loading) {
        section.push({ photoSkeleton: true });
        return section;
    }
    if (live.q !== term || !live.data) return section;
    if (live.data.ai_unavailable) {
        section.push({ note: 'filename search only — AI model not ready', icon: 'info' });
    }
    const images = (live.data.images || []).slice(0, LIVE_LIMIT);
    if (images.length) {
        section.push(...images.map((photo) => ({
            photo,
            navRow: 2,
            run: () => openPhotoResult(term, photo, images),
        })));
        const contextTags = [...new Set(images.flatMap((img) => img.caption_tags || []))].slice(0, 5);
        if (contextTags.length) {
            section.push({
                note: contextTags.map((tag) => `#${tag}`).join('  '),
                icon: 'tag',
            });
        }
    }
    return section;
}

function buildPeopleRows(term) {
    const idTerm = idQuery(term);
    const matches = people
        .map((person) => {
            const label = personLabel(person);
            const named = !isUnnamedPersonLabel(label);
            const idMatch = idTerm && String(person.id).includes(idTerm);
            return { person, label: named ? label : 'Add name', named, idMatch };
        })
        .filter((item) => (item.named && includesText(item.label, term)) || (!item.named && item.idMatch))
        .sort((a, b) => Number(b.named) - Number(a.named) || personCount(b.person) - personCount(a.person))
        .slice(0, MAX_SECTION_ROWS);
    if (!matches.length) return [];
    return [
        { head: 'People' },
        ...matches.map(({ person, label, named }, i) => ({
            icon: 'users',
            thumb: personThumb(person),
            label,
            labelHtml: named ? highlight(label, term) : esc(label),
            meta: named ? fmt(personCount(person)) : `ID ${person.id}`,
            navRow: 10 + i,
            run: () => applyScope({
                people: person.id,
                personLabel: named ? label : '',
                personThumb: personThumb(person),
            }),
        })),
    ];
}

function buildCollectionRows(term) {
    const matches = collections
        .filter((collection) => includesText(collection.name, term))
        .slice(0, MAX_SECTION_ROWS);
    if (!matches.length) return [];
    return [
        { head: 'Collections' },
        ...matches.map((collection, i) => ({
            icon: collection.smart ? 'sparkles' : 'folder',
            thumb: collection.cover_image_id ? thumbUrl('sm', collection.cover_image_id) : '',
            label: collection.name,
            labelHtml: highlight(collection.name, term),
            meta: fmt(collection.image_count || 0),
            navRow: 30 + i,
            run: () => applyScope({
                collectionId: collection.id,
                collectionName: collection.name || 'Collection',
                collectionSmart: Boolean(collection.smart),
            }),
        })),
    ];
}

function folderName(path) {
    return String(path || '').split('/').filter(Boolean).pop() || path || '';
}

function buildFolderRows(term) {
    const matches = folders
        .filter((folder) => includesText(folder.path, term))
        .slice(0, MAX_SECTION_ROWS);
    if (!matches.length) return [];
    return [
        { head: 'Folders' },
        ...matches.map((folder, i) => ({
            icon: 'folder',
            label: folderName(folder.path),
            labelHtml: highlight(folderName(folder.path), term),
            meta: `${fmt(folder.count || 0)} · ${folder.path}`,
            navRow: 50 + i,
            run: () => applyFacet({ key: 'folder', value: folder.path, remove: { start: 0, end: term.length }, closeAfter: true }),
        })),
    ];
}

function latestYear() {
    const years = (filterOptions?.years || []).map((item) => Number(item.year || item.value)).filter(Boolean).sort((a, b) => b - a);
    return years[0] || new Date().getFullYear();
}

function monthShort(month) {
    const found = MONTHS.find((item) => item[2] === Number(month));
    return found ? found[1][0].toUpperCase() + found[1].slice(1) : String(month).padStart(2, '0');
}

function dateLabel(value) {
    if (value === 'undated') return 'Undated';
    const month = String(value || '').match(/^(\d{4})-(\d{2})$/);
    if (month) return `${monthShort(Number(month[2]))} ${month[1]}`;
    return value;
}

function findDateSuggestion(input) {
    const text = String(input || '');
    const lower = text.toLowerCase();
    const yearOnly = lower.match(/(?:^|\s)(\d{4})(?=\s|$)/);
    let best = null;
    for (const [full, short, month] of MONTHS) {
        const monthPattern = `(?:${full}|${short})`;
        const monthYear = new RegExp(`(?:^|\\s)(${monthPattern})\\s+(\\d{4})(?=\\s|$)`, 'i').exec(text);
        const yearMonth = new RegExp(`(?:^|\\s)(\\d{4})\\s+(${monthPattern})(?=\\s|$)`, 'i').exec(text);
        const monthOnly = new RegExp(`(?:^|\\s)(${monthPattern})(?=\\s|$)`, 'i').exec(text);
        const match = monthYear || yearMonth || monthOnly;
        if (!match) continue;
        const year = monthYear ? Number(monthYear[2]) : yearMonth ? Number(yearMonth[1]) : latestYear();
        const value = `${year}-${String(month).padStart(2, '0')}`;
        const start = match.index + (match[0].startsWith(' ') ? 1 : 0);
        best = {
            value,
            label: dateLabel(value),
            remove: { start, end: match.index + match[0].length },
        };
        break;
    }
    if (!best && yearOnly) {
        const start = yearOnly.index + (yearOnly[0].startsWith(' ') ? 1 : 0);
        best = {
            value: yearOnly[1],
            label: yearOnly[1],
            remove: { start, end: yearOnly.index + yearOnly[0].length },
        };
    }
    return best;
}

function valuesForOperator(operator, query) {
    const q = String(query || '').toLowerCase();
    if (operator.name === 'camera') {
        return (filterOptions?.cameras || [])
            .filter((item) => includesText(item.camera || item.value, q))
            .slice(0, MAX_SECTION_ROWS)
            .map((item) => ({ value: item.camera || item.value, label: item.camera || item.value, count: item.count }));
    }
    if (operator.name === 'lens') {
        return (filterOptions?.lenses || [])
            .filter((item) => includesText(item.lens || item.value, q))
            .slice(0, MAX_SECTION_ROWS)
            .map((item) => ({ value: item.lens || item.value, label: item.lens || item.value, count: item.count }));
    }
    if (operator.name === 'tag') {
        return (tags || [])
            .filter((item) => includesText(item.tag || item.value, q))
            .slice(0, MAX_SECTION_ROWS)
            .map((item) => ({ value: item.tag || item.value, label: item.tag || item.value, count: item.count }));
    }
    if (operator.name === 'type') {
        return (filterOptions?.file_types || [])
            .filter((item) => includesText(item.ext || item.value, q))
            .slice(0, MAX_SECTION_ROWS)
            .map((item) => {
                const ext = String(item.ext || item.value || '').replace('.', '').toLowerCase();
                return { value: ext, label: ext.toUpperCase(), count: item.count };
            });
    }
    if (operator.name === 'flag') {
        return FLAG_VALUES.filter((item) => includesText(item.label, q) || includesText(item.value, q));
    }
    if (operator.name === 'folder') {
        return folders
            .filter((item) => includesText(item.path, q))
            .slice(0, MAX_SECTION_ROWS)
            .map((item) => ({ value: item.path, label: folderName(item.path), count: item.count, meta: item.path }));
    }
    return [];
}

function buildFacetRows(input) {
    const tokenInfo = activeToken(input);
    const token = tokenInfo.token;
    const lower = token.toLowerCase();
    const rowsOut = [];
    const date = findDateSuggestion(input);
    if (date) {
        rowsOut.push({
            icon: 'calendar',
            label: `Date · ${date.label}`,
            labelHtml: `Date · ${highlight(date.label, token)}`,
            meta: 'facet',
            navRow: 70,
            run: () => applyFacet({ key: 'date_taken', value: date.value, remove: date.remove }),
        });
    }
    const operatorMatch = lower.match(/^([a-z]*):?(.*)$/);
    if (!operatorMatch) return rowsOut.length ? [{ head: 'Facet completions' }, ...rowsOut] : [];
    const typedName = operatorMatch[1] || '';
    const hasColon = token.includes(':');
    const typedValue = hasColon ? token.slice(token.indexOf(':') + 1) : '';
    if (!typedName) return rowsOut.length ? [{ head: 'Facet completions' }, ...rowsOut] : [];
    const operators = OPERATORS.filter((op) => op.name.startsWith(typedName) || typedName.startsWith(op.name));
    for (const operator of operators) {
        if (!hasColon || operator.name !== typedName) {
            rowsOut.push({
                icon: operator.icon,
                label: `${operator.name}:`,
                labelHtml: `${highlight(operator.name, typedName)}:`,
                meta: operator.label,
                navRow: 80 + rowsOut.length,
                run: () => completeOperator(operator.name, tokenInfo),
            });
            continue;
        }
        for (const value of valuesForOperator(operator, typedValue)) {
            rowsOut.push({
                icon: value.icon || operator.icon,
                label: `${operator.name}:${value.label}`,
                labelHtml: `${operator.name}:${highlight(value.label, typedValue)}`,
                meta: value.count == null ? (value.meta || 'facet') : fmt(value.count),
                navRow: 80 + rowsOut.length,
                run: () => applyFacet({
                    key: operator.key,
                    value: value.value,
                    remove: tokenInfo,
                }),
            });
        }
    }
    return rowsOut.length ? [{ head: 'Facet completions' }, ...rowsOut.slice(0, MAX_SECTION_ROWS + 1)] : [];
}

function operatorIntent(input) {
    const token = activeToken(input).token.toLowerCase();
    if (!token) return false;
    const name = token.includes(':') ? token.slice(0, token.indexOf(':')) : token;
    return OPERATORS.some((op) => op.name.startsWith(name) || name.startsWith(op.name));
}

function dateOnlyIntent(input) {
    const suggestion = findDateSuggestion(input);
    if (!suggestion) return false;
    return !remainingQuery(input, suggestion.remove.start, suggestion.remove.end);
}

function facetOnlyIntent(input) {
    return operatorIntent(input) || dateOnlyIntent(input);
}

function buildEmptyRows() {
    const recents = recentScopes();
    const result = [
        { hint: true },
    ];
    if (recents.length) {
        result.push({
            head: 'Recents',
            action: '<button class="sd-head-action" data-clear-recents="1">Clear</button>',
        });
        result.push(...recents.map((item, i) => ({
            icon: scopeIcon(item.scope || {}),
            label: item.label || scopeLabel(item.scope || {}),
            meta: '',
            recentIndex: i,
            navRow: 100 + i,
            run: () => applyScope(item.scope || {}),
        })));
    }
    return result;
}

function buildCommandRows(input) {
    const query = input.slice(1);
    const matches = COMMANDS.filter((item) => (!item.when || item.when()) && fuzzy(item.label, query));
    return [
        { head: 'Commands' },
        ...(matches.length ? matches.map((command, i) => ({ ...command, navRow: i })) : [{ empty: 'No matching command.' }]),
    ];
}

function buildRows() {
    const input = document.getElementById('scope-input');
    const raw = input.value;
    const term = raw.trim();
    if (raw.startsWith('>')) return buildCommandRows(raw);
    if (!term) return buildEmptyRows();
    if (facetOnlyIntent(raw)) return buildFacetRows(raw);
    return [
        ...buildPhotoRows(term),
        ...buildPeopleRows(term),
        ...buildCollectionRows(term),
        ...buildFolderRows(term),
        ...buildFacetRows(raw),
    ];
}

function assignRunIndexes() {
    let index = 0;
    for (const row of rows) {
        if (row.run) {
            row.runIndex = index;
            index += 1;
        }
    }
}

function render() {
    const drop = document.getElementById('scope-drop');
    if (!drop) return;
    rows = buildRows();
    assignRunIndexes();
    const runnableCount = runnableRows().length;
    hot = runnableCount ? Math.min(Math.max(hot, -1), runnableCount - 1) : -1;
    let html = '';
    for (let i = 0; i < rows.length; i += 1) {
        const row = rows[i];
        if (row.head) {
            html += sectionHead(row.head, row.action || '');
        } else if (row.empty) {
            html += `<div class="sd-empty">${esc(row.empty)}</div>`;
        } else if (row.note) {
            html += `<div class="sd-note"><span class="sd-glyph">${icon(row.icon || 'info')}</span><span>${esc(row.note)}</span></div>`;
        } else if (row.hint) {
            html += '<div class="sd-hint">'
                + OPERATORS.map((op) => `<button data-op="${op.name}"><span>${icon(op.icon)}</span>${op.name}:</button>`).join('')
                + '</div>';
        } else if (row.deepToggle) {
            html += '<div class="sd-tools">'
                + `<button class="sd-chip ${scope.deep ? 'active' : ''}" data-deep-toggle="1" data-tip="${esc(DEEP_SEARCH_TIP)}" aria-pressed="${scope.deep ? 'true' : 'false'}">${icon('sparkles')} Deep</button>`
                + '</div>';
        } else if (row.photoSkeleton) {
            html += skeletonPhotosHtml();
        } else if (row.photo) {
            const photoRows = [];
            while (rows[i]?.photo) {
                photoRows.push(rows[i]);
                i += 1;
            }
            i -= 1;
            html += photoStripHtml(photoRows);
        } else {
            html += rowHtml(row, row.runIndex);
        }
    }
    drop.innerHTML = html || '<div class="sd-empty">Type to search this archive</div>';
    const input = document.getElementById('scope-input');
    if (hot >= 0 && drop.querySelector(`#scope-option-${CSS.escape(String(hot))}`)) {
        input.setAttribute('aria-activedescendant', `scope-option-${hot}`);
    } else {
        input.removeAttribute('aria-activedescendant');
    }
    bindDropdown(drop);
}

function bindDropdown(drop) {
    for (const item of drop.querySelectorAll('[data-index]')) {
        item.addEventListener('click', () => run(Number(item.dataset.index)));
    }
    for (const button of drop.querySelectorAll('[data-op]')) {
        button.addEventListener('click', () => {
            const input = document.getElementById('scope-input');
            input.value = `${button.dataset.op}:`;
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
            render();
        });
    }
    drop.querySelector('[data-deep-toggle]')?.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggleDeepSearch();
    });
    drop.querySelector('[data-clear-recents]')?.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        storeRecents([]);
        hot = -1;
        render();
    });
    for (const button of drop.querySelectorAll('.sd-recent-x')) {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const index = Number(button.dataset.recentIndex);
            const next = recentScopes().filter((_, i) => i !== index);
            storeRecents(next);
            hot = -1;
            render();
        });
    }
}

function runnableRows() {
    return rows.filter((row) => row.run);
}

function run(index) {
    const row = runnableRows()[index];
    if (!row) return;
    const input = document.getElementById('scope-input');
    const commandMode = input.value.startsWith('>');
    if (commandMode) {
        input.value = '';
        close();
        input.blur();
    }
    row.run();
    if (!commandMode && !document.getElementById('scopebox')?.classList.contains('open')) input.blur();
}

function applyScope(patch, { keepFocus = false } = {}) {
    const clean = { ...patch };
    remember(clean);
    setScope(clean);
    document.getElementById('scope-input').value = '';
    if (keepFocus) {
        open();
        render();
        document.getElementById('scope-input').focus();
    } else {
        close();
        input.blur();
    }
}

function applySearch(term) {
    applyScope({ q: term, deep: scope.deep });
    switchLens('grid');
}

function toggleDeepSearch() {
    const input = document.getElementById('scope-input');
    const term = input.value.trim() || scope.q || '';
    const next = !scope.deep;
    if (term) patchScope({ q: term, deep: next });
    else patchScope({ deep: false });
    scheduleLiveSearch();
    open();
}

function openPhotoResult(term, photo, images) {
    remember({ q: term });
    setScope({ q: term });
    switchLens('grid');
    document.getElementById('scope-input').value = '';
    close();
    requestAnimationFrame(() => {
        emit('loupe:open', {
            id: Number(photo.id),
            index: images.findIndex((img) => Number(img.id) === Number(photo.id)),
            images,
        });
    });
}

function applyFacet({ key, value, remove, closeAfter = false }) {
    const input = document.getElementById('scope-input');
    const nextQ = remainingQuery(input.value, remove.start, remove.end);
    const patch = {
        [key]: String(value || ''),
        q: nextQ || scope.q || '',
        collectionId: '',
        collectionName: '',
        collectionSmart: false,
        similarIds: [],
        similarSourceId: '',
        similarLimit: 100,
        similarLabel: '',
    };
    if (key === 'people') {
        patch.personLabel = '';
        patch.personThumb = '';
    }
    remember({ ...scope, ...patch });
    setScope(patch, { merge: true });
    input.value = '';
    input.focus();
    if (closeAfter) close();
    else {
        hot = -1;
        open();
        render();
    }
}

function completeOperator(name, tokenInfo) {
    const input = document.getElementById('scope-input');
    input.value = `${input.value.slice(0, tokenInfo.start)}${name}:${input.value.slice(tokenInfo.end)}`;
    input.focus();
    input.setSelectionRange(tokenInfo.start + name.length + 1, tokenInfo.start + name.length + 1);
    hot = -1;
    render();
}

function renderToken() {
    const slot = document.getElementById('scope-token-slot');
    slot.innerHTML = scopeTokenHtml();
    slot.querySelector('.scope-token:last-child')?.classList.toggle('selected', tokenSelected && scopeActive());
}

function setTokenSelected(selected) {
    tokenSelected = Boolean(selected) && scopeActive();
    renderToken();
}

function inputAtTokenBoundary(input) {
    const start = Number(input.selectionStart ?? 0);
    const end = Number(input.selectionEnd ?? start);
    return input.value.length === 0 || (start === 0 && end === 0);
}

function clearScopeTokenSelection() {
    if (tokenSelected) setTokenSelected(false);
}

function scheduleLiveSearch() {
    const term = document.getElementById('scope-input').value.trim();
    if (term.startsWith('>') || term.length < LIVE_MIN_CHARS || facetOnlyIntent(term)) {
        if (liveAbort) liveAbort.abort();
        window.clearTimeout(liveTimer);
        live = { q: term, loading: false, data: null };
        render();
        return;
    }
    window.clearTimeout(liveTimer);
    if (liveAbort) liveAbort.abort();
    live = { q: term, loading: false, data: null };
    const seq = ++liveSeq;
    liveTimer = window.setTimeout(async () => {
        live = { q: term, loading: true, data: null };
        render();
        const controller = new AbortController();
        liveAbort = controller;
        const params = new URLSearchParams({ q: term, limit: String(LIVE_LIMIT), offset: '0', sort: 'similarity' });
        if (scope.deep) params.set('deep', '1');
        const data = await getRankings(params, { fetchOptions: { signal: controller.signal } });
        if (seq !== liveSeq || controller.signal.aborted) return;
        live = { q: term, loading: false, data };
        render();
    }, LIVE_DELAY_MS);
}

function moveHot(delta) {
    const runnables = runnableRows();
    if (!runnables.length) return;
    if (hot < 0) {
        hot = delta > 0 ? 0 : runnables.length - 1;
        render();
        return;
    }
    const current = runnables[hot];
    const currentRow = current.navRow ?? hot;
    const candidates = runnables
        .map((row, index) => ({ row, index }))
        .filter((item) => delta > 0 ? (item.row.navRow ?? item.index) > currentRow : (item.row.navRow ?? item.index) < currentRow);
    hot = candidates.length ? candidates[delta > 0 ? 0 : candidates.length - 1].index : hot;
    render();
}

function movePhotoHot(delta) {
    const runnables = runnableRows();
    const current = runnables[hot];
    if (!current?.photo) return false;
    const photoIndexes = runnables
        .map((row, index) => ({ row, index }))
        .filter((item) => item.row.photo && item.row.navRow === current.navRow);
    const pos = photoIndexes.findIndex((item) => item.index === hot);
    const next = photoIndexes[Math.max(0, Math.min(photoIndexes.length - 1, pos + delta))];
    if (!next || next.index === hot) return true;
    hot = next.index;
    render();
    return true;
}

export function focusOmnibox(seed = null) {
    const input = document.getElementById('scope-input');
    clearScopeTokenSelection();
    if (seed != null) input.value = seed;
    input.focus();
    if (seed == null) input.select();
    else input.setSelectionRange(input.value.length, input.value.length);
    ensureSuggestionData();
    scheduleLiveSearch();
    open();
}

export function openCommandPalette() {
    focusOmnibox('>');
}

export function initOmnibox() {
    const box = document.getElementById('scopebox');
    const field = document.getElementById('scope-field');
    const input = document.getElementById('scope-input');
    field.addEventListener('click', (event) => {
        if (event.target.closest('button, [data-clear-all], .chip-x')) return;
        if (document.activeElement !== input) input.focus();
    });
    input.addEventListener('focus', () => {
        ensureSuggestionData();
        scheduleLiveSearch();
        open();
    });
    input.addEventListener('input', () => {
        hot = -1;
        clearScopeTokenSelection();
        ensureSuggestionData();
        scheduleLiveSearch();
    });
    input.addEventListener('keydown', (event) => {
        const count = runnableRows().length;
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            moveHot(1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            moveHot(-1);
        } else if (event.key === 'ArrowRight' && movePhotoHot(1)) {
            event.preventDefault();
        } else if (event.key === 'ArrowLeft' && movePhotoHot(-1)) {
            event.preventDefault();
        } else if (event.key === 'Enter') {
            event.preventDefault();
            run(hot >= 0 ? hot : 0);
        } else if (event.key === 'Backspace' && scopeActive() && inputAtTokenBoundary(input)) {
            event.preventDefault();
            if (tokenSelected) {
                setScope({});
                setTokenSelected(false);
                input.focus();
                hot = -1;
                render();
            } else {
                setTokenSelected(true);
            }
        } else if (event.key === 'Escape') {
            if (tokenSelected) {
                event.preventDefault();
                event.stopPropagation();
                setTokenSelected(false);
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            input.value = '';
            input.blur();
            close();
        } else if (event.key.length === 1 || event.key === 'Delete') {
            clearScopeTokenSelection();
        } else if (!count && event.key.length === 1) {
            render();
        }
    });
    document.addEventListener('pointerdown', (event) => {
        if (!box.contains(event.target)) close();
    });
    on('scope', () => {
        tokenSelected = false;
        renderToken();
    });
    on('meta', renderToken);
    on('collections:changed', invalidateSuggestionData);
    on('import:changed', invalidateSuggestionData);
    on('trash:changed', invalidateSuggestionData);
    on('flags', invalidateSuggestionData);
    renderToken();
    ensureSuggestionData();
}
