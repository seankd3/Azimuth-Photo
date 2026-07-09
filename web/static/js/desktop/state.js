const listeners = new Map();
const PANEL_KEY = 'pa_d_left_collapsed';
const RIGHT_PANEL_KEY = 'pa_d_right_collapsed';
const THUMB_KEY = 'pa_d_thumb_size';
const PREFS_KEY = 'pa_d_prefs';
const LENS_KEY = 'pa_d_lens';
const PERSISTENT_LENSES = new Set(['grid', 'events', 'people', 'map']);
const VALID_LENSES = new Set([...PERSISTENT_LENSES, 'refine', 'suggestions', 'loupe', 'duplicates', 'trash']);
const DEFAULT_PREFS = {
    density: 'comfortable',
    badgeCheck: true,
    badgeFlag: true,
    badgeElo: true,
    badgeIndex: true,
    collapseStacks: true,
    reduceMotion: false,
    panelSections: {},
};
const DENSITIES = ['comfortable', 'cozy', 'compact'];

export const scope = {
    q: '',
    people: '',
    personLabel: '',
    personThumb: '',
    flag: '',
    folder: '',
    date_taken: '',
    file_type: '',
    camera: '',
    lens: '',
    orientation: '',
    compared: '',
    min_stars: '',
    import_batch: '',
    importBatchLabel: '',
    similarIds: [],
    similarLabel: '',
    collectionId: '',
    collectionName: '',
    sort: 'elo',
};

export const viewState = {
    visibleImages: 0,
    sortQuality: null,
    images: [],
    generation: 0,
    bestOf: false,
    bestOfLimit: null,
    bestOfTotal: 0,
    bestOfPreviousSort: null,
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
        scope.q || scope.people || scope.flag || scope.folder || scope.date_taken || scope.file_type
        || scope.camera || scope.lens || scope.orientation || scope.compared || scope.min_stars
        || scope.import_batch || scope.similarIds.length || scope.collectionId,
    );
}

export function nonSearchFacetCount() {
    return [
        scope.people, scope.flag, scope.folder, scope.date_taken, scope.file_type, scope.camera,
        scope.lens, scope.orientation, scope.compared, scope.min_stars, scope.import_batch,
        scope.collectionId, scope.similarIds.length,
    ].filter(Boolean).length;
}

export function scopeParams(extra = {}) {
    const params = new URLSearchParams();
    if (scope.q) params.set('q', scope.q);
    if (scope.people) params.set('people', scope.people);
    if (scope.flag) params.set('flag', scope.flag);
    if (scope.folder) params.set('folder', scope.folder);
    if (scope.date_taken) params.set('date_taken', scope.date_taken);
    if (scope.file_type) params.set('file_type', scope.file_type);
    if (scope.camera) params.set('camera', scope.camera);
    if (scope.lens) params.set('lens', scope.lens);
    if (scope.orientation) params.set('orientation', scope.orientation);
    if (scope.compared) params.set('compared', scope.compared);
    if (scope.min_stars) params.set('min_stars', scope.min_stars);
    if (scope.import_batch) params.set('import_batch', scope.import_batch);
    if (scope.sort) params.set('sort', scope.sort);
    params.set('stacks', viewState.prefs.collapseStacks ? 'collapsed' : 'expanded');
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
        q: '', people: '', personLabel: '', personThumb: '', flag: '',
        folder: '', date_taken: '', file_type: '', camera: '', lens: '',
        orientation: '', compared: '', min_stars: '', import_batch: '', importBatchLabel: '',
        similarIds: [], similarLabel: '', collectionId: '', collectionName: '',
        sort: scope.sort || 'elo', ...patch,
    };
    if (!Array.isArray(next.similarIds)) next.similarIds = [];
    Object.assign(scope, next);
    clearBestOfState();
    viewState.focusIndex = 0;
    emit('scope', scope);
    if (pushHash) writeHash();
}

export function patchScope(patch, { pushHash = true } = {}) {
    if (patch.similarIds && !Array.isArray(patch.similarIds)) patch.similarIds = [];
    Object.assign(scope, patch);
    emit('scope', scope);
    if (pushHash) writeHash();
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
    const nextSort = sort || 'elo';
    if (viewState.bestOf) {
        clearBestOfState();
        emit('bestof', false);
    }
    patchScope({ sort: nextSort });
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
    }
    if (key === 'import_batch') {
        patch.importBatchLabel = '';
    }
    if (key === 'similarIds') {
        patch.similarLabel = '';
        patch.similarIds = [];
    }
    patchScope(patch);
}

export function setRankingsMeta({ visibleImages, sortQuality }) {
    viewState.visibleImages = Number(visibleImages) || 0;
    viewState.sortQuality = sortQuality || null;
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
        'q', 'people', 'personLabel', 'personThumb', 'flag', 'folder', 'date_taken', 'file_type',
        'camera', 'lens', 'orientation', 'compared', 'min_stars', 'import_batch', 'importBatchLabel',
        'collectionId', 'collectionName', 'sort',
    ]) {
        if (scope[key]) params.set(key, scope[key]);
    }
    const next = params.toString();
    if (location.hash.slice(1) !== next) {
        history.pushState(null, '', next ? `#${next}` : location.pathname);
    }
}

function loadHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const patch = {};
    for (const key of [
        'q', 'people', 'personLabel', 'personThumb', 'flag', 'folder', 'date_taken', 'file_type',
        'camera', 'lens', 'orientation', 'compared', 'min_stars', 'import_batch', 'importBatchLabel',
        'collectionId', 'collectionName', 'sort',
    ]) {
        patch[key] = params.get(key) || '';
    }
    patch.similarIds = [];
    patch.similarLabel = '';
    if (!patch.sort) patch.sort = 'elo';
    Object.assign(scope, patch);
    emit('scope', scope);
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
    if (scope.people) return scope.personLabel || 'Person';
    if (scope.q) return `“${scope.q}”`;
    if (scope.flag === 'picked') return 'Picked';
    if (scope.flag === 'unflagged') return 'Unflagged';
    if (scope.flag === 'rejected') return 'Rejected';
    if (scope.folder) return scope.folder.split('/').filter(Boolean).pop() || scope.folder;
    if (scope.date_taken) {
        const match = String(scope.date_taken).match(/^(\d{4})-(\d{2})$/);
        if (match) {
            const month = new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString(undefined, { month: 'short' });
            return `${month} ${match[1]}`;
        }
        return scope.date_taken === 'undated' ? 'Undated' : scope.date_taken;
    }
    if (scope.file_type) return String(scope.file_type).toUpperCase();
    if (scope.camera) return `camera:${scope.camera}`;
    if (scope.lens) return `lens:${scope.lens}`;
    if (scope.orientation) return scope.orientation;
    if (scope.compared) return { compared: 'Ranked', uncompared: 'Unranked', confident: 'High confidence' }[scope.compared] || scope.compared;
    if (scope.min_stars) return `${scope.min_stars}+ rating`;
    return 'All Photos';
}
