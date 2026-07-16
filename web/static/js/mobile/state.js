// Shared mobile app state: the single current scope, the selection,
// and a tiny event bus that keeps the modules decoupled.

const listeners = new Map();
const VIEW_PREFS_KEY = 'pa-m-view-prefs';
const VIEW_PREF_DEFAULTS = {
    sort: 'date_taken',
    collapseStacks: false,
};
const VIEW_SORTS = new Set(['date_taken', 'date_taken_asc', 'elo', 'elo_asc', 'taste']);
const SMART_QUERY_FIELDS = {
    q: 'q',
    people: 'people',
    flag: 'flag',
    folder: 'folder',
    date_taken: 'dateTaken',
    file_type: 'fileType',
    camera: 'camera',
    lens: 'lens',
    tag: 'tag',
    orientation: 'orientation',
    compared: 'compared',
    min_stars: 'minStars',
};

function savedViewPrefs() {
    try {
        const saved = JSON.parse(localStorage.getItem(VIEW_PREFS_KEY) || '{}');
        return {
            sort: VIEW_SORTS.has(saved.sort) ? saved.sort : VIEW_PREF_DEFAULTS.sort,
            collapseStacks: saved.collapseStacks == null
                ? VIEW_PREF_DEFAULTS.collapseStacks
                : Boolean(saved.collapseStacks),
        };
    } catch {
        return { ...VIEW_PREF_DEFAULTS };
    }
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
            console.error(`mobile ${event} listener failed`, error);
        }
    }
}

// One current scope at a time (charter: Scope is a noun, the app has exactly one).
export const scope = {
    q: '',
    people: '',
    flag: '',
    fileType: '',
    camera: '',
    lens: '',
    tag: '',
    orientation: '',
    folder: '',
    compared: '',
    minStars: '',
    dateTaken: '',
    collectionId: '',
    collectionSmart: false,
    smartName: '',
    smartQuery: {},
    similarId: '',
    similarImages: null,
    label: '',
    peopleLabel: '',
    thumb: '',
};

export const viewPrefs = savedViewPrefs();

export function scopeActive() {
    return Boolean(
        scope.q || scope.people || scope.flag || scope.fileType || scope.camera
        || scope.lens || scope.tag || scope.orientation || scope.folder || scope.compared
        || scope.minStars || scope.dateTaken || scope.collectionId || scope.smartName || scope.similarId
    );
}

export function scopeParams(extra = {}) {
    const params = new URLSearchParams();
    if (scope.q) params.set('q', scope.q);
    if (scope.people) params.set('people', scope.people);
    if (scope.flag) params.set('flag', scope.flag);
    if (scope.fileType) params.set('file_type', scope.fileType);
    if (scope.camera) params.set('camera', scope.camera);
    if (scope.lens) params.set('lens', scope.lens);
    if (scope.tag) params.set('tag', scope.tag);
    if (scope.orientation) params.set('orientation', scope.orientation);
    if (scope.folder) params.set('folder', scope.folder);
    if (scope.compared) params.set('compared', scope.compared);
    if (scope.minStars) params.set('min_stars', scope.minStars);
    if (scope.dateTaken) params.set('date_taken', scope.dateTaken);
    if (scope.collectionId) params.set('collection_id', scope.collectionId);
    params.set('stacks', (scope.smartName || !viewPrefs.collapseStacks) ? 'expanded' : 'collapsed');
    for (const [key, value] of Object.entries(extra)) {
        if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
    }
    return params;
}

export function setScope(patch) {
    scope.q = '';
    scope.people = '';
    scope.flag = '';
    scope.fileType = '';
    scope.camera = '';
    scope.lens = '';
    scope.tag = '';
    scope.orientation = '';
    scope.folder = '';
    scope.compared = '';
    scope.minStars = '';
    scope.dateTaken = '';
    scope.collectionId = '';
    scope.collectionSmart = false;
    scope.smartName = '';
    scope.smartQuery = {};
    scope.similarId = '';
    scope.similarImages = null;
    scope.label = '';
    scope.peopleLabel = '';
    scope.thumb = '';
    Object.assign(scope, patch);
    emit('scope', scope);
}

export function patchScope(patch) {
    Object.assign(scope, patch);
    emit('scope', scope);
}

export function setViewPrefs(patch) {
    Object.assign(viewPrefs, patch);
    try {
        localStorage.setItem(VIEW_PREFS_KEY, JSON.stringify(viewPrefs));
    } catch {
        // Preferences are best-effort when storage is unavailable.
    }
    emit('view-prefs', viewPrefs);
}

export function clearScope() {
    setScope({});
}

export function scopePatchFromSmartQuery(query = {}) {
    const patch = {};
    for (const [queryKey, scopeKey] of Object.entries(SMART_QUERY_FIELDS)) {
        const value = query[queryKey];
        if (value !== undefined && value !== null) patch[scopeKey] = String(value);
    }
    return patch;
}

// Selection (corner-check model, long-press to enter).
export const selection = new Set();
export const selState = { mode: false };

export function selectionChanged() {
    if (!selection.size) selState.mode = false;
    emit('selection', selection);
}

export function clearSelection() {
    selection.clear();
    selState.mode = false;
    emit('selection', selection);
}

export const network = { online: navigator.onLine !== false };

export function isOffline() {
    return network.online === false || navigator.onLine === false;
}

export function setNetworkOnline(online) {
    const next = Boolean(online);
    if (network.online === next) return;
    network.online = next;
    emit('network', network);
}

// Registry of every image object the app has seen, by id.
// Timeline pages, collection drill-ins, and refine sets all feed it,
// so flag writes can consult previous values for undo.
export const byId = new Map();

export function rememberImages(images) {
    for (const img of images || []) {
        if (img && img.id != null) byId.set(Number(img.id), img);
    }
}

// Tab switching is owned by bootstrap; modules call through this hook.
export const nav = { setTab: () => {} };
