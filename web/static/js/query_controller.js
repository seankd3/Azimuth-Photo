import {
    buildFilterNeighborStates,
    filterParams,
    filterQueryString,
    normalizeFilterState,
} from './query_state.js';
import {
    buildCompareUrl as buildCompareApiUrl,
    buildMosaicUrl as buildMosaicApiUrl,
} from './compare/query.js';
import { rankingQueryString as rankingQueryStringCore } from './library/query.js';
import { sortValueForState } from './library/sort.js';
import { searchModeForQuery } from './search/query.js';


export function createQueryController({
    getFilters,
    getSearchQuery,
    getDeepSearchRequested,
    getSortField,
    getSortDesc,
    getRankingsSort,
    getMosaicStrategy,
    getMosaicGridElo,
    libraryNeighborLimit,
    mosaicNeighborLimit,
    compareNeighborPairs,
} = {}) {
    function currentSearchMode() {
        return searchModeForQuery(getSearchQuery?.() || '');
    }

    function currentQueryState(overrides = {}) {
        const field = overrides.sortField || getSortField();
        const desc = overrides.sortDesc ?? getSortDesc();
        const mode = overrides.searchMode || currentSearchMode();
        const query = overrides.searchQuery ?? (mode === 'search' ? getSearchQuery() : '');
        return {
            filters: normalizeFilterState(overrides.filters || overrides.filterState || getFilters()),
            sortField: field,
            sortDesc: Boolean(desc),
            sort: overrides.sort || sortValueForState(field, desc),
            searchMode: mode,
            searchQuery: query,
            deepSearch: Boolean(overrides.deepSearch ?? (mode === 'search' && getDeepSearchRequested())),
        };
    }

    function currentFilterParams(state = currentQueryState()) {
        return filterParams(state);
    }

    function currentFilterQueryString(state = currentQueryState()) {
        return filterQueryString(state);
    }

    function currentFilterNeighborStates(baseState = getFilters()) {
        return buildFilterNeighborStates(normalizeFilterState(baseState));
    }

    function rankingQueryString({
        queryState = currentQueryState(),
        limit = libraryNeighborLimit,
        offset = 0,
        sort = queryState.sort,
    } = {}) {
        const state = currentQueryState({ ...queryState, sort });
        return rankingQueryStringCore({ queryState: state, limit, offset, sort });
    }

    function buildRankingsUrl({
        queryState = currentQueryState(),
        sort = queryState.sort || getRankingsSort(),
        filterState = null,
        limit = libraryNeighborLimit,
        offset = 0,
    } = {}) {
        const state = currentQueryState({
            ...queryState,
            filters: filterState || queryState.filters,
            sort,
        });
        return `/api/rankings?${rankingQueryString({ queryState: state, sort, limit, offset })}`;
    }

    function buildMosaicUrl({
        strategy = getMosaicStrategy(),
        queryState = currentQueryState(),
        filterState = null,
        gridElo = getMosaicGridElo(),
        n = mosaicNeighborLimit,
        exclude = '',
    } = {}) {
        const state = currentQueryState({
            ...queryState,
            filters: filterState || queryState.filters,
        });
        return buildMosaicApiUrl({ strategy, queryState: state, gridElo, n, exclude });
    }

    function buildCompareUrl(mode, n = compareNeighborPairs, queryState = currentQueryState()) {
        const state = currentQueryState(queryState);
        return buildCompareApiUrl({ mode, n, queryState: state });
    }

    return {
        buildCompareUrl,
        buildFilterNeighborStates: currentFilterNeighborStates,
        buildMosaicUrl,
        buildRankingsUrl,
        currentQueryState,
        currentSearchMode,
        filterParams: currentFilterParams,
        filterQueryString: currentFilterQueryString,
        rankingQueryString,
    };
}
