import { MONTH_NAMES } from './dom.js';
import { applyExcludeSources, clearQuietReveal } from './quiet_sources.js';

const listeners = new Map();
const PANEL_KEY = 'pa_d_left_collapsed';
const RIGHT_PANEL_KEY = 'pa_d_right_collapsed';
const THUMB_KEY = 'pa_d_thumb_size';
const PREFS_KEY = 'pa_d_prefs';
const LENS_KEY = 'pa_d_lens';
const PERSISTENT_LENSES = new Set(['grid', 'events', 'timeline', 'people', 'map']);
const VALID_LENSES = new Set([...PERSISTENT_LENSES, 'refine', 'suggestions', 'loupe', 'duplicates', 'trash', 'shared', 'system']);
const DEFAULT_PREFS = {
    density: 'comfortable',
    badgeCheck: true,
    badgeFlag: true,
    badgeElo: true,
    badgeIndex: true,
    autoAdvanceFlags: true,
    collapseStacks: true,
    reduceMotion: false,
    panelSections: {},
};
const DENSITIES = ['comfortable', 'cozy', 'compact'];
const SMART_QUERY_KEYS = [
    'q', 'people', 'folder', 'camera', 'lens', 'flag', 'date_taken', 'file_type',
    'tag', 'orientation', 'compared', 'min_stars', 'sort',
];
const SMART_ACTIVE_KEYS = SMART_QUERY_KEYS.filter((key) => key !== 'sort');
const SORT_VARIANTS = {
    similarity: { desc: 'similarity', asc: 'similarity' },
    elo: { desc: 'elo', asc: 'elo_asc' },
    taste: { desc: 'taste', asc: 'taste' },
    date_taken: { desc: 'date_taken', asc: 'date_taken_asc' },
    camera: { desc: 'camera_desc', asc: 'camera' },
    filename: { desc: 'filename_desc', asc: 'filename' },
    file_size: { desc: 'file_size', asc: 'file_size_asc' },
    date_modified: { desc: 'date_modified', asc: 'date_modified_asc' },
    resolution: { desc: 'resolution', asc: 'resolution_asc' },
    newest: { desc: 'newest', asc: 'oldest' },
    comparisons: { desc: 'comparisons', asc: 'least_compared' },
};
const SORT_ALIASES = {
    camera_desc: 'camera',
    filename_desc: 'filename',
};

function sortVariant(sort) {
    const clean = normalizeSort(sort);
    for (const [base, variants] of Object.entries(SORT_VARIANTS)) {
        if (clean === variants.desc || clean === variants.asc) return { base, ...variants };
    }
    return { base: clean || 'elo', desc: clean || 'elo', asc: `${clean || 'elo'}_asc` };
}

function normalizeSort(sort) {
    return SORT_ALIASES[String(sort || '')] || String(sort || 'elo');
}

export function sortBase(sort = scope.sort) {
    return sortVariant(sort).base;
}

export function sortAscending(sort = scope.sort) {
    return sortVariant(sort).asc === String(sort || '');
}

export function sortWithDirection(sort = scope.sort, ascending = false) {
    const variant = sortVariant(sort);
    return ascending ? variant.asc : variant.desc;
}

export const scope = {
    q: '',
    deep: false,
    people: '',
    personLabel: '',
    personThumb: '',
    flag: '',
    folder: [],
    date_taken: '',
    file_type: '',
    camera: '',
    lens: '',
    tag: '',
    orientation: '',
    compared: '',
    min_stars: '',
    import_batch: '',
    importBatchLabel: '',
    similarIds: [],
    similarSourceId: '',
    similarLimit: 100,
    similarLabel: '',
    collectionId: '',
    collectionName: '',
    collectionSmart: false,
    sort: 'elo',
};

export const viewState = {
    visibleImages: 0,
    hiddenPendingThumbnails: 0,
    sortQuality: null,
    images: [],
    generation: 0,
    bestOf: false,
    bestOfLimit: null,
    bestOfTotal: 0,
    bestOfPreviousSort: null,
    searchPreviousSort: null,
    searchMode: '',
    searchSources: [],
    hiddenInQuietSources: 0,
    thumbSize: Number(localStorage.getItem(THUMB_KEY) || 176),
    leftCollapsed: localStorage.getItem(PANEL_KEY) === '1',
    rightCollapsed: localStorage.getItem(RIGHT_PANEL_KEY) === '1',
    prefs: readPrefs(),
    focusIndex: 0,
    activeLens: readLens(),
};

export const byId = new Map();
export const selection = new Set();
export const selState = { mode: false, lastIndex: null };
let developIsOpen = false;

export function developOpen() {
    return developIsOpen;
}

export function setDevelopOpen(open) {
    developIsOpen = Boolean(open);
}

export function folderValues(value = scope.folder) {
    if (Array.isArray(value)) return value.map((item) => String(item || '')).filter(Boolean);
    const clean = String(value || '');
    return clean ? [clean] : [];
}

function normalizeFolderValue(value) {
    return [...new Set(folderValues(value))];
}

export function folderActive(path) {
    return folderValues().includes(path);
}

export function folderLabel(path) {
    return String(path || '').split('/').filter(Boolean).pop() || path || '';
}

export function folderChip() {
    const folders = folderValues();
    if (!folders.length) return null;
    const labels = folders.map(folderLabel);
    return {
        label: labels.length === 1 ? labels[0] : `${labels[0]} + ${labels.length - 1} more`,
        title: labels.join('\n'),
    };
}

function isPlainObject(value) {
    return Boolean(value && Object.prototype.toString.call(value) === '[object Object]');
}

function normalizePrefs(value) {
    const saved = isPlainObject(value) ? value : {};
    const panelSections = {};
    if (isPlainObject(saved.panelSections)) {
        for (const [key, collapsed] of Object.entries(saved.panelSections)) {
            panelSections[key] = Boolean(collapsed);
        }
    }
    return {
        ...DEFAULT_PREFS,
        density: DENSITIES.includes(saved.density) ? saved.density : DEFAULT_PREFS.density,
        badgeCheck: saved.badgeCheck == null ? DEFAULT_PREFS.badgeCheck : Boolean(saved.badgeCheck),
        badgeFlag: saved.badgeFlag == null ? DEFAULT_PREFS.badgeFlag : Boolean(saved.badgeFlag),
        badgeElo: saved.badgeElo == null ? DEFAULT_PREFS.badgeElo : Boolean(saved.badgeElo),
        badgeIndex: saved.badgeIndex == null ? DEFAULT_PREFS.badgeIndex : Boolean(saved.badgeIndex),
        autoAdvanceFlags: saved.autoAdvanceFlags == null ? DEFAULT_PREFS.autoAdvanceFlags : Boolean(saved.autoAdvanceFlags),
        collapseStacks: saved.collapseStacks == null ? DEFAULT_PREFS.collapseStacks : Boolean(saved.collapseStacks),
        reduceMotion: saved.reduceMotion == null ? DEFAULT_PREFS.reduceMotion : Boolean(saved.reduceMotion),
        panelSections,
    };
}

function readPrefs() {
    try {
        const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
        return normalizePrefs(saved);
    } catch {
        return { ...DEFAULT_PREFS };
    }
}

function readLens() {
    const saved = localStorage.getItem(LENS_KEY) || 'grid';
    return PERSISTENT_LENSES.has(saved) ? saved : 'grid';
}

function applyPrefs() {
    const html = document.documentElement;
    const prefs = viewState.prefs;
    html.dataset.density = DENSITIES.includes(prefs.density) ? prefs.density : DEFAULT_PREFS.density;
    html.dataset.badgesCheck = prefs.badgeCheck ? '1' : '0';
    html.dataset.badgesFlag = prefs.badgeFlag ? '1' : '0';
    html.dataset.badgesElo = prefs.badgeElo ? '1' : '0';
    html.dataset.badgesIndex = prefs.badgeIndex ? '1' : '0';
    html.dataset.motion = prefs.reduceMotion ? 'reduced' : 'full';
    const autoAdvance = document.getElementById('auto-advance-flags');
    if (autoAdvance) autoAdvance.checked = Boolean(prefs.autoAdvanceFlags);
}

export function on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, []);
    listeners.get(event).push(fn);
}

export function emit(event, payload) {
    for (const fn of listeners.get(event) || []) {
        try {
            fn(payload);
        } catch (error) {
            console.error(`desktop ${event} listener failed`, error);
        }
    }
}

export function rememberImages(images) {
    for (const img of images || []) {
        if (img && img.id != null) byId.set(Number(img.id), img);
    }
}

export function scopeActive() {
    return Boolean(
        scope.q || scope.deep || scope.people || scope.flag || folderValues().length || scope.date_taken || scope.file_type
        || scope.camera || scope.lens || scope.tag || scope.orientation || scope.compared || scope.min_stars
        || scope.import_batch || scope.similarIds.length || scope.collectionId,
    );
}

export function nonSearchFacetCount() {
    return [
        scope.people, scope.flag, folderValues().length, scope.date_taken, scope.file_type, scope.camera,
        scope.lens, scope.tag, scope.orientation, scope.compared, scope.min_stars,
    ].filter(Boolean).length;
}

export function scopeParams(extra = {}) {
    const params = new URLSearchParams();
    if (scope.q) params.set('q', scope.q);
    if (scope.q && scope.deep) params.set('deep', '1');
    if (scope.people) params.set('people', scope.people);
    if (scope.flag) params.set('flag', scope.flag);
    for (const folder of folderValues()) params.append('folder', folder);
    if (scope.date_taken) params.set('date_taken', scope.date_taken);
    if (scope.file_type) params.set('file_type', scope.file_type);
    if (scope.camera) params.set('camera', scope.camera);
    if (scope.lens) params.set('lens', scope.lens);
    if (scope.tag) params.set('tag', scope.tag);
    if (scope.orientation) params.set('orientation', scope.orientation);
    if (scope.compared) params.set('compared', scope.compared);
    if (scope.min_stars) params.set('min_stars', scope.min_stars);
    if (scope.import_batch) params.set('import_batch', scope.import_batch);
    if (scope.collectionId) params.set('collection_id', scope.collectionId);
    if (scope.sort) params.set('sort', scope.sort);
    params.set('stacks', viewState.prefs.collapseStacks ? 'collapsed' : 'expanded');
    applyExcludeSources(params, undefined, folderValues(), { q: scope.q });
    for (const [key, value] of Object.entries(extra)) {
        if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
    }
    return params;
}

export function refineParams(extra = {}) {
    const params = scopeParams(extra);
    params.delete('sort');
    params.delete('stacks');
    return params;
}

export function setScope(patch = {}, { merge = false, pushHash = true } = {}) {
    const next = merge ? { ...scope, ...patch } : {
        q: '', deep: false, people: '', personLabel: '', personThumb: '', flag: '',
        folder: [], date_taken: '', file_type: '', camera: '', lens: '',
        tag: '', orientation: '', compared: '', min_stars: '', import_batch: '', importBatchLabel: '',
        similarIds: [], similarSourceId: '', similarLimit: 100, similarLabel: '', collectionId: '', collectionName: '', collectionSmart: false,
        sort: scope.sort || 'elo', ...patch,
    };
    preserveSearchSort(next);
    if (!Array.isArray(next.similarIds)) next.similarIds = [];
    next.similarLimit = [100, 250, 500].includes(Number(next.similarLimit)) ? Number(next.similarLimit) : 100;
    next.deep = Boolean(next.deep && next.q);
    next.folder = normalizeFolderValue(next.folder);
    if (String(next.q || '') !== String(scope.q || '')) clearQuietReveal();
    Object.assign(scope, next);
    clearBestOfState();
    viewState.focusIndex = 0;
    emit('scope', scope);
    if (pushHash) writeHash();
}

export function navigateToScope(patch = {}, options = {}) {
    setActiveLens('grid');
    setScope(patch, options);
}

export function patchScope(patch, { pushHash = true } = {}) {
    if (patch.similarIds && !Array.isArray(patch.similarIds)) patch.similarIds = [];
    if (Object.prototype.hasOwnProperty.call(patch, 'similarLimit')) {
        patch.similarLimit = [100, 250, 500].includes(Number(patch.similarLimit)) ? Number(patch.similarLimit) : 100;
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'folder')) patch.folder = normalizeFolderValue(patch.folder);
    if (Object.prototype.hasOwnProperty.call(patch, 'deep')) patch.deep = Boolean(patch.deep);
    if (Object.prototype.hasOwnProperty.call(patch, 'q') && !patch.q) patch.deep = false;
    if (Object.prototype.hasOwnProperty.call(patch, 'q') && String(patch.q || '') !== String(scope.q || '')) {
        clearQuietReveal();
    }
    preserveSearchSort(patch, { merge: true });
    Object.assign(scope, patch);
    emit('scope', scope);
    if (pushHash) writeHash();
}

function preserveSearchSort(next, { merge = false } = {}) {
    const nextQ = merge && !Object.prototype.hasOwnProperty.call(next, 'q') ? scope.q : next.q;
    const nextSort = merge && !Object.prototype.hasOwnProperty.call(next, 'sort') ? scope.sort : next.sort;
    if (!scope.q && nextQ && nextSort === 'similarity') {
        viewState.searchPreviousSort = scope.sort === 'similarity' ? 'elo' : (scope.sort || 'elo');
    } else if (scope.q && !nextQ && viewState.searchPreviousSort) {
        next.sort = viewState.searchPreviousSort;
        viewState.searchPreviousSort = null;
    }
    viewState.searchMode = '';
    viewState.searchSources = [];
}

function clearBestOfState() {
    viewState.bestOf = false;
    viewState.bestOfLimit = null;
    viewState.bestOfTotal = 0;
    viewState.bestOfPreviousSort = null;
}

export function bestOfCap(total) {
    const n = Number(total) || 0;
    return n > 0 ? Math.ceil(n * .2) : 0;
}

export function setBestOfTotal(total) {
    viewState.bestOfTotal = Number(total) || 0;
    viewState.bestOfLimit = bestOfCap(viewState.bestOfTotal);
}

export function setBestOf(enabled) {
    if (enabled && scope.collectionId) {
        emit('bestof:unsupported', { reason: 'collection' });
        return;
    }
    if (enabled && !viewState.bestOf) {
        viewState.bestOf = true;
        viewState.bestOfLimit = null;
        viewState.bestOfTotal = 0;
        viewState.bestOfPreviousSort = scope.sort || 'elo';
        emit('bestof', true);
        patchScope({ sort: 'elo' });
        return;
    }
    if (!enabled && viewState.bestOf) {
        const restoreSort = viewState.bestOfPreviousSort || 'elo';
        clearBestOfState();
        emit('bestof', false);
        patchScope({ sort: restoreSort });
    }
}

export function toggleBestOf() {
    setBestOf(!viewState.bestOf);
}

export function setSort(sort) {
    const nextSort = normalizeSort(sort);
    if (viewState.bestOf) {
        clearBestOfState();
        emit('bestof', false);
    }
    patchScope({ sort: nextSort });
}

export function setSortBase(sort) {
    setSort(sortWithDirection(sort || 'elo', sortAscending()));
}

export function setSortDirection(ascending) {
    setSort(sortWithDirection(scope.sort || 'elo', Boolean(ascending)));
}

export function toggleSortDirection() {
    setSortDirection(!sortAscending());
}

export function setActiveLens(lens) {
    const next = VALID_LENSES.has(lens) ? lens : 'grid';
    if (viewState.activeLens === next) return;
    viewState.activeLens = next;
    if (PERSISTENT_LENSES.has(next)) {
        localStorage.setItem(LENS_KEY, next);
    }
    emit('lens', next);
}

export function clearFacet(key) {
    const patch = { [key]: '' };
    if (key === 'people') {
        patch.personLabel = '';
        patch.personThumb = '';
    }
    if (key === 'collectionId') {
        patch.collectionName = '';
        patch.collectionSmart = false;
    }
    if (key === 'import_batch') {
        patch.importBatchLabel = '';
    }
    if (key === 'similarIds') {
        patch.similarLabel = '';
        patch.similarIds = [];
        patch.similarSourceId = '';
        patch.similarLimit = 100;
    }
    patchScope(patch);
}

export function setRankingsMeta({
    visibleImages,
    hiddenPendingThumbnails = 0,
    sortQuality,
    searchMode = '',
    searchSources = [],
    hiddenInQuietSources = 0,
}) {
    viewState.visibleImages = Number(visibleImages) || 0;
    viewState.hiddenPendingThumbnails = Math.max(0, Number(hiddenPendingThumbnails) || 0);
    viewState.sortQuality = sortQuality || null;
    viewState.searchMode = String(searchMode || '');
    viewState.searchSources = Array.isArray(searchSources) ? searchSources.filter(Boolean).map(String) : [];
    viewState.hiddenInQuietSources = Math.max(0, Number(hiddenInQuietSources) || 0);
    emit('meta', viewState);
}

export function setImages(images) {
    viewState.images = images || [];
    rememberImages(viewState.images);
    emit('images', viewState.images);
}

export function setThumbSize(value) {
    viewState.thumbSize = Math.max(120, Math.min(320, Number(value) || 176));
    localStorage.setItem(THUMB_KEY, String(viewState.thumbSize));
    document.documentElement.style.setProperty('--thumb-h', `${viewState.thumbSize}px`);
    emit('thumbsize', viewState.thumbSize);
}

export function setLeftCollapsed(collapsed) {
    viewState.leftCollapsed = Boolean(collapsed);
    localStorage.setItem(PANEL_KEY, viewState.leftCollapsed ? '1' : '0');
    emit('panel', viewState.leftCollapsed);
}

export function setRightCollapsed(collapsed) {
    viewState.rightCollapsed = Boolean(collapsed);
    localStorage.setItem(RIGHT_PANEL_KEY, viewState.rightCollapsed ? '1' : '0');
    emit('rightpanel', viewState.rightCollapsed);
}

export function patchPrefs(patch = {}) {
    const beforeCollapseStacks = viewState.prefs.collapseStacks;
    viewState.prefs = normalizePrefs({ ...viewState.prefs, ...(isPlainObject(patch) ? patch : {}) });
    localStorage.setItem(PREFS_KEY, JSON.stringify(viewState.prefs));
    applyPrefs();
    emit('prefs', viewState.prefs);
    if (beforeCollapseStacks !== viewState.prefs.collapseStacks) emit('scope', scope);
}

export function cycleDensity() {
    const current = DENSITIES.includes(viewState.prefs.density) ? viewState.prefs.density : DEFAULT_PREFS.density;
    const next = DENSITIES[(DENSITIES.indexOf(current) + 1) % DENSITIES.length];
    patchPrefs({ density: next });
    return next;
}

export function selectionChanged(imageIds = null) {
    if (!selection.size) {
        selState.mode = false;
        selState.lastIndex = null;
    }
    emit('selection', { selection, imageIds });
}

export function clearSelection() {
    const imageIds = [...selection];
    selection.clear();
    selState.mode = false;
    selState.lastIndex = null;
    emit('selection', { selection, imageIds });
}

function writeHash() {
    const params = new URLSearchParams();
    for (const key of [
        'q', 'people', 'personLabel', 'personThumb', 'flag', 'date_taken', 'file_type',
        'camera', 'lens', 'tag', 'orientation', 'compared', 'min_stars', 'import_batch', 'importBatchLabel',
        'collectionId', 'collectionName', 'collectionSmart', 'sort',
    ]) {
        if (scope[key]) params.set(key, scope[key]);
    }
    if (scope.q && scope.deep) params.set('deep', '1');
    for (const folder of folderValues()) params.append('folder', folder);
    const next = params.toString();
    if (location.hash.slice(1) !== next) {
        history.pushState(null, '', next ? `#${next}` : location.pathname);
    }
}

function loadHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const patch = {};
    for (const key of [
        'q', 'people', 'personLabel', 'personThumb', 'flag', 'date_taken', 'file_type',
        'camera', 'lens', 'tag', 'orientation', 'compared', 'min_stars', 'import_batch', 'importBatchLabel',
        'collectionId', 'collectionName', 'collectionSmart', 'sort',
    ]) {
        patch[key] = params.get(key) || '';
    }
    patch.folder = params.getAll('folder').filter(Boolean);
    patch.deep = ['1', 'true', 'yes', 'on'].includes(String(params.get('deep') || '').toLowerCase()) && Boolean(patch.q);
    patch.collectionSmart = patch.collectionSmart === '1' || patch.collectionSmart === 'true';
    patch.similarIds = [];
    patch.similarLabel = '';
    if (!patch.sort) patch.sort = 'elo';
    else patch.sort = normalizeSort(patch.sort);
    setScope(patch, { pushHash: false });
}

export function initState() {
    applyPrefs();
    setThumbSize(viewState.thumbSize);
    loadHash();
    window.addEventListener('hashchange', loadHash);
}

export function describeScope() {
    if (scope.similarIds.length) return scope.similarLabel || 'Similar photos';
    if (scope.collectionId) return scope.collectionName || 'Collection';
    if (scope.import_batch) return scope.importBatchLabel || `Import ${scope.import_batch}`;
    if (scope.people) return scope.personLabel || 'Unnamed person';
    if (scope.q) return `“${scope.q}”`;
    if (scope.flag === 'picked') return 'Picked';
    if (scope.flag === 'unflagged') return 'Unflagged';
    if (scope.flag === 'rejected') return 'Rejected';
    const chip = folderChip();
    if (chip) return chip.label;
    if (scope.date_taken) {
        const match = String(scope.date_taken).match(/^(\d{4})-(\d{2})$/);
        if (match) {
            const month = new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString(undefined, { month: 'short' });
            return `${month} ${match[1]}`;
        }
        return scope.date_taken === 'undated' ? 'Undated' : scope.date_taken;
    }
    if (scope.file_type) return String(scope.file_type).toUpperCase();
    if (scope.camera) return `Camera · ${scope.camera}`;
    if (scope.lens) return `Lens · ${scope.lens}`;
    if (scope.tag) return `Tag · ${scope.tag}`;
    if (scope.orientation) return scope.orientation === 'landscape' ? 'Landscape' : scope.orientation === 'portrait' ? 'Portrait' : scope.orientation;
    if (scope.compared) return { compared: 'Ranked', uncompared: 'Unranked', direct_uncompared: 'Not compared yet', confident: 'High confidence' }[scope.compared] || scope.compared;
    if (scope.min_stars) return `${scope.min_stars}+ rating`;
    return 'All photos';
}

function smartDateLabel(value) {
    if (value === 'undated') return 'Undated';
    const match = String(value || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return value;
    return `${MONTH_NAMES.short[Number(match[2]) - 1] || match[2]} ${match[1]}`;
}

export function smartQueryFromScope() {
    const query = {};
    for (const key of SMART_QUERY_KEYS) {
        if (key === 'folder') {
            const folders = folderValues();
            if (folders.length === 1) query.folder = folders[0];
            continue;
        }
        if (scope[key] !== undefined && scope[key] !== null && scope[key] !== '') query[key] = scope[key];
    }
    if (!query.sort) query.sort = scope.sort || 'elo';
    return query;
}

export function smartQueryActive(query = smartQueryFromScope()) {
    return SMART_ACTIVE_KEYS.some((key) => Boolean(query && query[key] !== undefined && query[key] !== null && query[key] !== ''));
}

export function smartQuerySummary(query = {}, { valuesOnly = false, fallback = 'All photos' } = {}) {
    const parts = [];
    const add = (key, value) => {
        if (value === undefined || value === null || value === '') return;
        parts.push(valuesOnly ? String(value) : `${key} ${value}`);
    };
    add('search', query.q ? `“${query.q}”` : '');
    add('person', query.people);
    add('folder', folderLabel(query.folder));
    add('flag', query.flag);
    add('date', smartDateLabel(query.date_taken));
    add('type', query.file_type ? String(query.file_type).toUpperCase() : '');
    add('camera', query.camera);
    add('lens', query.lens);
    add('tag', query.tag);
    add('orientation', query.orientation);
    add('rank', { compared: 'ranked', uncompared: 'unranked', direct_uncompared: 'never dueled', confident: 'high confidence' }[query.compared] || query.compared);
    add('rating', query.min_stars ? `${query.min_stars}+` : '');
    if (!parts.length && query.sort) add('sort', query.sort === 'date_taken' ? 'date' : query.sort);
    return parts.length ? parts.join(' · ') : fallback;
}

export function smartQueryName(query = smartQueryFromScope()) {
    return smartQuerySummary(query, { valuesOnly: true, fallback: 'Smart collection' });
}

export function scopePatchFromSmartQuery(query = {}) {
    const patch = {
        q: '', deep: false, people: '', personLabel: '', personThumb: '', flag: '',
        folder: [], date_taken: '', file_type: '', camera: '', lens: '',
        tag: '', orientation: '', compared: '', min_stars: '', import_batch: '', importBatchLabel: '',
        similarIds: [], similarSourceId: '', similarLimit: 100, similarLabel: '', collectionId: '', collectionName: '', collectionSmart: false,
        sort: query.sort || 'elo',
    };
    for (const key of SMART_ACTIVE_KEYS) {
        if (query[key] !== undefined && query[key] !== null) patch[key] = key === 'folder' ? [String(query[key])] : String(query[key]);
    }
    return patch;
}
