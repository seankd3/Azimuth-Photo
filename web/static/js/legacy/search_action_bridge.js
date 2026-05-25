import {
    applySearchQueryChange as applySearchQueryChangeCore,
    clearSearch as clearSearchCore,
    initSearchInputControls as initSearchInputControlsCore,
} from '../library/search_controller.js';

export function createLegacySearchActionBridge({
    documentImpl = document,
    clearTimeoutImpl = clearTimeout,
    getSearchQuery = () => '',
    setSearchQuery = () => {},
    getSortField = () => '',
    hasActiveTextSearch,
    saveSearchState,
    updateSimilaritySortOption,
    applySortState,
    saveSearchSortState,
    clearPersistedSearchState,
    restoreSortState,
    updateSearchControls,
    reloadForFilters,
    updateDateScrubber,
    initSearchInputControlsImpl = initSearchInputControlsCore,
    applySearchQueryChangeImpl = applySearchQueryChangeCore,
    clearSearchImpl = clearSearchCore,
} = {}) {
    let searchDebounce = null;

    function clearSearchDebounceTimer() {
        clearTimeoutImpl(searchDebounce);
        searchDebounce = null;
    }

    function context() {
        return {
            getSearchQuery,
            setSearchQuery,
            getSortField,
            hasActiveTextSearch,
            saveSearchState,
            updateSimilaritySortOption,
            applySortState,
            saveSearchSortState,
            clearPersistedSearchState,
            restoreSortState,
            updateSearchControls,
            reloadForFilters,
            updateDateScrubber,
        };
    }

    function initSearchInputControls() {
        const input = documentImpl.getElementById('search-input');
        if (!input || input.dataset.searchBound === '1') return false;
        input.dataset.searchBound = '1';
        return initSearchInputControlsImpl({
            documentImpl,
            getSearchDebounce: () => searchDebounce,
            setSearchDebounce: (timer) => {
                searchDebounce = timer;
            },
            hasActiveTextSearch,
            applySearchQueryChangeImpl: (value) => applySearchQueryChangeImpl(value, context()),
            clearSearchImpl: clearSearch,
        });
    }

    function clearSearch() {
        return clearSearchImpl({
            ...context(),
            documentImpl,
            clearSearchDebounce: clearSearchDebounceTimer,
        });
    }

    return {
        clearSearch,
        clearSearchDebounceTimer,
        context,
        initSearchInputControls,
    };
}
