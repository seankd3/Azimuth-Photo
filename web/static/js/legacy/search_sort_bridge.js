import {
    SORT_KEYS,
    sortValueForState,
} from '../library/sort.js';
import {
    SEARCH_DEEP_STORAGE_KEY,
    SEARCH_SORT_STORAGE_KEY,
    SEARCH_STORAGE_KEY,
    SORT_STORAGE_KEY,
    clearPersistedSearchState as clearPersistedSearchStateCore,
    restoreSearchSortState as restoreSearchSortStateCore,
    restoreSearchState as restoreSearchStateCore,
    restoreSortState as restoreSortStateCore,
    saveSearchSortState as saveSearchSortStateCore,
    saveSearchState as saveSearchStateCore,
    saveSortState as saveSortStateCore,
} from '../library/search_state.js';
import {
    syncSortControls as syncSortControlsCore,
    updateCompareSearchIndicator as updateCompareSearchIndicatorCore,
    updateSearchControls as updateSearchControlsCore,
    updateSimilaritySortOption as updateSimilaritySortOptionCore,
    updateSortDirIcon as updateSortDirIconCore,
} from '../library/search_controls.js';

export function createLegacySearchSortBridge({
    documentImpl,
    storage,
    locationImpl,
    getSearchQuery,
    setSearchQuery,
    getDeepSearchRequested,
    setDeepSearchRequested,
    getSortField,
    setSortField,
    getSortDesc,
    setSortDesc,
    setRankingsSort,
    hasActiveTextSearch,
    syncLibraryUrlState,
    afterCompareSearchIndicator,
} = {}) {
    function applySortState(field, desc, { persist = true, persistSearch = true } = {}) {
        if (!SORT_KEYS[field]) return;
        setSortField(field);
        setSortDesc(Boolean(desc));
        setRankingsSort(sortValueForState(field, Boolean(desc)));
        syncSortControls();
        if (persist && field !== 'similarity') saveSortState();
        if (persistSearch && hasActiveTextSearch(getSearchQuery())) saveSearchSortState();
    }

    function saveSortState() {
        saveSortStateCore({
            storage,
            storageKey: SORT_STORAGE_KEY,
            field: getSortField(),
            desc: getSortDesc(),
        });
        syncLibraryUrlState();
    }

    function saveSearchState() {
        saveSearchStateCore({
            storage,
            searchKey: SEARCH_STORAGE_KEY,
            deepKey: SEARCH_DEEP_STORAGE_KEY,
            searchQuery: getSearchQuery(),
            deepSearchRequested: getDeepSearchRequested(),
            hasActiveTextSearch,
        });
    }

    function saveSearchSortState() {
        saveSearchSortStateCore({
            storage,
            storageKey: SEARCH_SORT_STORAGE_KEY,
            searchQuery: getSearchQuery(),
            field: getSortField(),
            desc: getSortDesc(),
            hasActiveTextSearch,
        });
    }

    function clearPersistedSearchState() {
        clearPersistedSearchStateCore({
            storage,
            searchKey: SEARCH_STORAGE_KEY,
            searchSortKey: SEARCH_SORT_STORAGE_KEY,
            deepKey: SEARCH_DEEP_STORAGE_KEY,
        });
    }

    function restoreSortState() {
        const restored = restoreSortStateCore({
            storage,
            storageKey: SORT_STORAGE_KEY,
            locationSearch: locationImpl?.search || '',
        });
        if (restored) {
            applySortState(restored.field, restored.desc, { persist: false, persistSearch: false });
        }
    }

    function restoreSearchSortState() {
        return restoreSearchSortStateCore({
            storage,
            storageKey: SEARCH_SORT_STORAGE_KEY,
        });
    }

    function restoreSearchState() {
        const restored = restoreSearchStateCore({
            storage,
            searchKey: SEARCH_STORAGE_KEY,
            deepKey: SEARCH_DEEP_STORAGE_KEY,
        });
        setSearchQuery(restored.searchQuery);
        setDeepSearchRequested(restored.deepSearchRequested);
        updateSimilaritySortOption();
        if (hasActiveTextSearch(getSearchQuery())) {
            const restoredSort = restoreSearchSortState() || { field: 'similarity', desc: true };
            applySortState(restoredSort.field, restoredSort.desc, { persist: false });
        }
        updateSearchControls();
    }

    function updateSearchControls() {
        updateSearchControlsCore({
            documentImpl,
            searchQuery: getSearchQuery(),
            deepSearchRequested: getDeepSearchRequested(),
            sortField: getSortField(),
            sortDesc: getSortDesc(),
            hasActiveTextSearch,
            afterCompareSearchIndicator,
        });
    }

    function updateCompareSearchIndicator() {
        updateCompareSearchIndicatorCore({
            documentImpl,
            searchQuery: getSearchQuery(),
            active: hasActiveTextSearch(getSearchQuery()),
            afterUpdate: afterCompareSearchIndicator,
        });
    }

    function syncSortControls() {
        syncSortControlsCore({
            documentImpl,
            sortField: getSortField(),
            sortDesc: getSortDesc(),
        });
    }

    function updateSimilaritySortOption() {
        updateSimilaritySortOptionCore({
            documentImpl,
            active: hasActiveTextSearch(getSearchQuery()),
        });
    }

    function updateSortDirIcon() {
        updateSortDirIconCore({
            documentImpl,
            sortDesc: getSortDesc(),
        });
    }

    return {
        applySortState,
        clearPersistedSearchState,
        restoreSearchSortState,
        restoreSearchState,
        restoreSortState,
        saveSearchSortState,
        saveSearchState,
        saveSortState,
        syncSortControls,
        updateCompareSearchIndicator,
        updateSearchControls,
        updateSimilaritySortOption,
        updateSortDirIcon,
    };
}
