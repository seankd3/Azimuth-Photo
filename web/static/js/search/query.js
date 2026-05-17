export const SIMILAR_SEARCH_SENTINEL = '__similar__';


export function hasActiveTextSearch(value = '') {
    return Boolean(value && value !== SIMILAR_SEARCH_SENTINEL);
}


export function searchModeForQuery(searchQuery = '') {
    if (searchQuery === SIMILAR_SEARCH_SENTINEL) return 'similar';
    return searchQuery ? 'search' : 'library';
}


export function appendSearchParams(params, state = {}) {
    if (state.searchMode === 'search' && state.searchQuery) {
        params.set('q', state.searchQuery);
        if (state.deepSearch) params.set('deep', '1');
    }
    return params;
}
