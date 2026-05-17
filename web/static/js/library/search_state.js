import {
    SORT_KEYS,
    sortStateFromValue,
} from './sort.js';


function safeStorageOp(callback, fallback = null) {
    try {
        return callback();
    } catch {
        return fallback;
    }
}


export function saveSortState({
    storage = sessionStorage,
    storageKey,
    field,
    desc,
} = {}) {
    safeStorageOp(() => {
        storage.setItem(storageKey, JSON.stringify({ field, desc }));
    });
}


export function saveSearchState({
    storage = sessionStorage,
    searchKey,
    deepKey,
    searchQuery = '',
    deepSearchRequested = false,
    hasActiveTextSearch,
} = {}) {
    safeStorageOp(() => {
        if (hasActiveTextSearch?.(searchQuery)) {
            storage.setItem(searchKey, searchQuery);
            if (deepSearchRequested) {
                storage.setItem(deepKey, '1');
            } else {
                storage.removeItem(deepKey);
            }
        } else {
            storage.removeItem(searchKey);
            storage.removeItem(deepKey);
        }
    });
}


export function saveSearchSortState({
    storage = sessionStorage,
    storageKey,
    searchQuery = '',
    field,
    desc,
    hasActiveTextSearch,
} = {}) {
    safeStorageOp(() => {
        if (hasActiveTextSearch?.(searchQuery)) {
            storage.setItem(storageKey, JSON.stringify({ field, desc }));
        } else {
            storage.removeItem(storageKey);
        }
    });
}


export function clearPersistedSearchState({
    storage = sessionStorage,
    searchKey,
    searchSortKey,
    deepKey,
} = {}) {
    safeStorageOp(() => {
        storage.removeItem(searchKey);
        storage.removeItem(searchSortKey);
        storage.removeItem(deepKey);
    });
}


export function restoreSortState({
    storage = sessionStorage,
    storageKey,
    locationSearch = globalThis.window?.location?.search || globalThis.location?.search || '',
} = {}) {
    return safeStorageOp(() => {
        const urlParams = new URLSearchParams(locationSearch);
        const urlSort = urlParams.get('sort');
        if (urlSort) {
            const urlState = SORT_KEYS[urlSort]
                ? { field: urlSort, desc: urlParams.get('dir') !== 'asc' }
                : sortStateFromValue(urlSort);
            if (urlState?.field && SORT_KEYS[urlState.field] && urlState.field !== 'similarity') {
                const desc = urlParams.has('dir') ? urlParams.get('dir') !== 'asc' : urlState.desc !== false;
                return { field: urlState.field, desc };
            }
        }
        const saved = storage.getItem(storageKey);
        if (!saved) return null;
        const parsed = JSON.parse(saved);
        const field = SORT_KEYS[parsed?.field] && parsed.field !== 'similarity' ? parsed.field : 'elo';
        return { field, desc: parsed?.desc !== false };
    }, null);
}


export function restoreSearchSortState({
    storage = sessionStorage,
    storageKey,
} = {}) {
    return safeStorageOp(() => {
        const saved = storage.getItem(storageKey);
        if (!saved) return null;
        const parsed = JSON.parse(saved);
        const field = SORT_KEYS[parsed?.field] ? parsed.field : '';
        if (!field) return null;
        return { field, desc: parsed?.desc !== false };
    }, null);
}


export function restoreSearchState({
    storage = sessionStorage,
    searchKey,
    deepKey,
} = {}) {
    return safeStorageOp(() => {
        const searchQuery = (storage.getItem(searchKey) || '').trim();
        return {
            searchQuery,
            deepSearchRequested: Boolean(searchQuery && storage.getItem(deepKey) === '1'),
        };
    }, { searchQuery: '', deepSearchRequested: false });
}
