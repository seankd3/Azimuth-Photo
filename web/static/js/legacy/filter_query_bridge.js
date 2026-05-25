import {
    EMPTY_FILTERS as EMPTY_FILTERS_CORE,
    normalizeFilterState as normalizeFilterStateCore,
    syncLibraryUrlState as syncLibraryUrlStateCore,
} from '../query_state.js';
import { createQueryController } from '../query_controller.js';
import {
    activeMetadataFilterCount as activeMetadataFilterCountCore,
    hasActiveFilters,
    updateMetadataFilterButton as updateMetadataFilterButtonCore,
} from '../filters.js';
import {
    FILTER_STORAGE_KEY,
    applyFilterUiState as applyFilterUiStateCore,
    restoreFilters as restoreFiltersCore,
    saveFilters as saveFiltersCore,
} from '../library/filters.js';

export function createLegacyFilterQueryBridge({
    documentImpl,
    storage,
    locationImpl,
    getSearchQuery,
    getSortField,
    getSortDesc,
    getRankingsSort,
    getMosaicStrategy,
    getMosaicGridElo,
    libraryNeighborLimit,
    mosaicNeighborLimit,
    compareNeighborPairs,
} = {}) {
    const EMPTY_FILTERS = { ...EMPTY_FILTERS_CORE };
    let filters = { ...EMPTY_FILTERS };

    function normalizeFilterState(state = {}) {
        return normalizeFilterStateCore(state);
    }

    function setCurrentFilters(nextFilters) {
        filters = normalizeFilterState(nextFilters);
        return filters;
    }

    function currentFilterState() {
        return normalizeFilterState(filters);
    }

    function saveFilters() {
        return saveFiltersCore({
            getFilters: currentFilterState,
            storage,
            storageKey: FILTER_STORAGE_KEY,
            syncLibraryUrlState,
        });
    }

    function restoreFilters() {
        return restoreFiltersCore({
            storage,
            storageKey: FILTER_STORAGE_KEY,
            location: locationImpl,
            setFilters: setCurrentFilters,
            applyFilterUiState,
        });
    }

    function applyFilterUiState(state = filters) {
        return applyFilterUiStateCore({
            filters: state,
            document: documentImpl,
            updateMetadataFilterButton,
        });
    }

    function activeMetadataFilterCount() {
        return activeMetadataFilterCountCore(filters);
    }

    function updateMetadataFilterButton() {
        updateMetadataFilterButtonCore({ filters, documentImpl });
    }

    const queryController = createQueryController({
        getFilters: currentFilterState,
        getSearchQuery,
        getSortField,
        getSortDesc,
        getRankingsSort,
        getMosaicStrategy,
        getMosaicGridElo,
        libraryNeighborLimit,
        mosaicNeighborLimit,
        compareNeighborPairs,
    });

    function syncLibraryUrlState() {
        syncLibraryUrlStateCore({
            filters: currentFilterState(),
            sortField: getSortField(),
            sortDesc: getSortDesc(),
            searchQuery: getSearchQuery(),
        });
    }

    function filterParams(state = currentQueryState()) {
        return queryController.filterParams(state);
    }

    function filterQueryString(state = currentQueryState()) {
        return queryController.filterQueryString(state);
    }

    function buildFilterNeighborStates(baseState = currentFilterState()) {
        return queryController.buildFilterNeighborStates(baseState);
    }

    function buildRankingsUrl(options = {}) {
        return queryController.buildRankingsUrl(options);
    }

    function buildMosaicUrl(options = {}) {
        return queryController.buildMosaicUrl(options);
    }

    function buildCompareUrl(mode, n = compareNeighborPairs, queryState = currentQueryState()) {
        return queryController.buildCompareUrl(mode, n, queryState);
    }

    function hasActiveLibraryFilters() {
        return hasActiveFilters(currentFilterState());
    }

    function currentSearchMode() {
        return queryController.currentSearchMode();
    }

    function currentQueryState(overrides = {}) {
        return queryController.currentQueryState(overrides);
    }

    function rankingQueryString(options = {}) {
        return queryController.rankingQueryString(options);
    }

    return {
        EMPTY_FILTERS,
        activeMetadataFilterCount,
        applyFilterUiState,
        buildCompareUrl,
        buildFilterNeighborStates,
        buildMosaicUrl,
        buildRankingsUrl,
        currentFilterState,
        currentQueryState,
        currentSearchMode,
        filterParams,
        filterQueryString,
        getFilters: () => filters,
        hasActiveLibraryFilters,
        normalizeFilterState,
        rankingQueryString,
        restoreFilters,
        saveFilters,
        setFilters: setCurrentFilters,
        setCurrentFilters,
        syncLibraryUrlState,
        updateMetadataFilterButton,
    };
}
