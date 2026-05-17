import {
    SORT_KEYS,
    sortStateFromValue,
} from './sort.js';


export function createLibrarySortController({
    getSortField,
    getSortDesc,
    setRankingsSortValue,
    applySortState,
    resetLibraryResults,
    clearDateGroups,
    loadRankings,
    updateDateScrubber,
    sortKeys = SORT_KEYS,
    sortStateFromValueImpl = sortStateFromValue,
} = {}) {
    const reloadSortedResults = () => {
        resetLibraryResults({ clearBatch: true });
        clearDateGroups();
        loadRankings(true);
        updateDateScrubber();
    };

    function setRankingsSort(sort, { persist = true } = {}) {
        setRankingsSortValue(sort);
        const state = sortStateFromValueImpl(sort);
        if (state) {
            applySortState(state.field, state.desc, { persist });
        }
        reloadSortedResults();
    }

    function setSortField(field) {
        if (!sortKeys[field]) return false;
        const key = sortKeys[field];
        applySortState(field, key?.defaultDesc !== false, { persist: field !== 'similarity' });
        reloadSortedResults();
        return true;
    }

    function toggleSortDir() {
        const sortField = getSortField();
        if (sortField === 'similarity') return false;
        applySortState(sortField, !getSortDesc());
        reloadSortedResults();
        return true;
    }

    return {
        setRankingsSort,
        setSortField,
        toggleSortDir,
    };
}
