import { appendSearchParams, searchModeForQuery } from './search/query.js';

export const EMPTY_FILTERS = {
    orientation: '',
    compared: '',
    rating: '',
    folder: '',
    flag: '',
    taken: '',
    fileType: '',
    camera: '',
    lens: '',
    people: '',
};

export const FILTER_QUERY_KEYS = [
    'orientation',
    'compared',
    'min_stars',
    'folder',
    'flag',
    'date_taken',
    'file_type',
    'camera',
    'lens',
    'people',
];

export const SCOPE_QUERY_KEYS = [
    'import_batch',
];

export function normalizeFilterState(state = {}) {
    return {
        orientation: state.orientation || '',
        compared: state.compared || '',
        rating: state.rating || '',
        folder: state.folder || '',
        flag: state.flag || '',
        taken: state.taken || '',
        fileType: state.fileType || '',
        camera: state.camera || '',
        lens: state.lens || '',
        people: state.people || '',
    };
}

export function appendFilterParams(params, state = EMPTY_FILTERS) {
    const normalized = normalizeFilterState(state);
    if (normalized.orientation) params.set('orientation', normalized.orientation);
    if (normalized.compared) params.set('compared', normalized.compared);
    if (normalized.rating) params.set('min_stars', normalized.rating);
    if (normalized.folder) params.set('folder', normalized.folder);
    if (normalized.flag) params.set('flag', normalized.flag);
    if (normalized.taken) params.set('date_taken', normalized.taken);
    if (normalized.fileType) params.set('file_type', normalized.fileType);
    if (normalized.camera) params.set('camera', normalized.camera);
    if (normalized.lens) params.set('lens', normalized.lens);
    if (normalized.people) params.set('people', normalized.people);
    return params;
}


export function appendScopeParams(params, state = {}) {
    const importBatch = state.importBatch || state.import_batch || '';
    if (importBatch) params.set('import_batch', importBatch);
    return params;
}


export function filterParams(state = EMPTY_FILTERS) {
    const filtersOnly = state.filters ? state.filters : state;
    const params = appendFilterParams(new URLSearchParams(), filtersOnly);
    const query = params.toString();
    return query ? `&${query}` : '';
}


export function filterQueryString(state = EMPTY_FILTERS) {
    const filtersOnly = state.filters ? state.filters : state;
    return appendFilterParams(new URLSearchParams(), filtersOnly).toString();
}


export function buildFilterNeighborStates(baseState = EMPTY_FILTERS) {
    const stateBase = normalizeFilterState(baseState);
    const states = [];
    const seen = new Set();
    const currentKey = JSON.stringify(stateBase);

    function pushState(patch) {
        const state = { ...stateBase, ...patch };
        const key = JSON.stringify(state);
        if (key === currentKey || seen.has(key)) return;
        seen.add(key);
        states.push(state);
    }

    if (!stateBase.orientation) {
        pushState({ orientation: 'landscape' });
        pushState({ orientation: 'portrait' });
    } else {
        pushState({ orientation: '' });
    }

    if (!stateBase.compared) {
        pushState({ compared: 'compared' });
        pushState({ compared: 'uncompared' });
    } else {
        pushState({ compared: '' });
    }

    if (stateBase.folder) {
        pushState({ folder: '' });
    }

    if (stateBase.taken) {
        pushState({ taken: '' });
    }

    if (stateBase.fileType) {
        pushState({ fileType: '' });
    }

    if (stateBase.camera) {
        pushState({ camera: '' });
    }

    if (stateBase.lens) {
        pushState({ lens: '' });
    }

    if (stateBase.people) {
        pushState({ people: '' });
    }

    if (!stateBase.flag) {
        pushState({ flag: 'picked' });
        pushState({ flag: 'rejected' });
    } else {
        pushState({ flag: '' });
    }

    const rating = Number(stateBase.rating || 0);
    if (rating > 0) {
        pushState({ rating: rating > 1 ? String(rating - 1) : '' });
        if (rating < 5) pushState({ rating: String(rating + 1) });
    }

    return states;
}


export function syncLibraryUrlState({
    filters = EMPTY_FILTERS,
    sortField = 'elo',
    sortDesc = true,
    searchQuery = '',
    searchMode = searchModeForQuery(searchQuery),
    historyImpl = globalThis.window?.history || globalThis.history,
    locationImpl = globalThis.window?.location || globalThis.location,
} = {}) {
    if (!historyImpl?.replaceState || !locationImpl) return;
    const params = new URLSearchParams(locationImpl.search);
    FILTER_QUERY_KEYS.forEach(key => params.delete(key));
    params.delete('q');
    params.delete('deep');
    params.delete('sort');
    params.delete('dir');
    appendFilterParams(params, filters);
    appendSearchParams(params, { searchMode, searchQuery });
    if (sortField !== 'similarity') {
        params.set('sort', sortField);
        params.set('dir', sortDesc ? 'desc' : 'asc');
    }
    const query = params.toString();
    const newUrl = query ? `${locationImpl.pathname}?${query}` : locationImpl.pathname;
    historyImpl.replaceState(null, '', newUrl);
}
