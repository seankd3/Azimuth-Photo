import {
    SORT_KEYS,
    sortStateFromValue,
} from './sort.js';
import { FILTER_QUERY_KEYS } from '../query_state.js';


export const SORT_STORAGE_KEY = 'pa_sort';
export const SEARCH_STORAGE_KEY = 'pa_search_query';
export const SEARCH_SORT_STORAGE_KEY = 'pa_search_sort';

const EXPLICIT_LIBRARY_QUERY_KEYS = [
    ...FILTER_QUERY_KEYS,
    'sort',
    'dir',
    'deep',
];


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
    searchQuery = '',
    hasActiveTextSearch,
} = {}) {
    safeStorageOp(() => {
        if (hasActiveTextSearch?.(searchQuery)) {
            storage.setItem(searchKey, searchQuery);
        } else {
            storage.removeItem(searchKey);
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
} = {}) {
    safeStorageOp(() => {
        storage.removeItem(searchKey);
        storage.removeItem(searchSortKey);
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
    locationSearch = globalThis.window?.location?.search || globalThis.location?.search || '',
} = {}) {
    return safeStorageOp(() => {
        const urlParams = new URLSearchParams(locationSearch);
        let restoredSearch = '';
        if (urlParams.has('q')) {
            restoredSearch = urlParams.get('q') || '';
        } else if (!EXPLICIT_LIBRARY_QUERY_KEYS.some(key => urlParams.has(key))) {
            restoredSearch = storage.getItem(searchKey) || '';
        }
        const searchQuery = restoredSearch.trim();
        storage.removeItem('pa_search_deep');
        return { searchQuery };
    }, { searchQuery: '' });
}
