import { createLibrarySortController } from '../library/sort_controller.js';

export function createLegacyLibrarySortBridge({
    getSortField,
    getSortDesc,
    setRankingsSortValue,
    applySortState,
    resetLibraryResults,
    clearDateGroups,
    loadRankings,
    updateDateScrubber,
    createLibrarySortControllerImpl = createLibrarySortController,
} = {}) {
    const controller = createLibrarySortControllerImpl({
        getSortField,
        getSortDesc,
        setRankingsSortValue,
        applySortState,
        resetLibraryResults,
        clearDateGroups,
        loadRankings,
        updateDateScrubber,
    });

    return {
        setRankingsSort: (...args) => controller.setRankingsSort(...args),
        setSortField: (...args) => controller.setSortField(...args),
        toggleSortDir: (...args) => controller.toggleSortDir(...args),
    };
}
