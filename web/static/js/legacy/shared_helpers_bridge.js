import {
    COMPARE_NEIGHBOR_PAIRS,
    MOSAIC_NEIGHBOR_LIMIT,
} from '../compare/query.js';
import { showCompareEmpty as showCompareEmptyCore } from '../compare/view.js';
import { DEFAULT_CROSS_VIEW_WARM_DELAY_MS } from '../warmup_neighbors.js';
import {
    INITIAL_RANKINGS_PAGE_SIZE,
    LIBRARY_NEIGHBOR_LIMIT,
    RANKINGS_PAGE_SIZE,
    currentLibraryPageSize,
} from '../library/pagination.js';
import { formatDateTime } from '../media_metadata.js';
import { hasActiveTextSearch } from '../search/query.js';

export function createLegacySharedHelpersBridge({
    compareNeighborPairs = COMPARE_NEIGHBOR_PAIRS,
    mosaicNeighborLimit = MOSAIC_NEIGHBOR_LIMIT,
    initialRankingsPageSize = INITIAL_RANKINGS_PAGE_SIZE,
    libraryNeighborLimit = LIBRARY_NEIGHBOR_LIMIT,
    rankingsPageSize = RANKINGS_PAGE_SIZE,
    crossViewWarmDelayMs = DEFAULT_CROSS_VIEW_WARM_DELAY_MS,
    currentLibraryPageSizeImpl = currentLibraryPageSize,
    formatDateTimeImpl = formatDateTime,
    hasActiveTextSearchImpl = hasActiveTextSearch,
    showCompareEmptyImpl = showCompareEmptyCore,
} = {}) {
    return {
        compareNeighborPairs,
        crossViewWarmDelayMs,
        formatDateTime: (...args) => formatDateTimeImpl(...args),
        hasActiveTextSearch: (...args) => hasActiveTextSearchImpl(...args),
        initialRankingsPageSize,
        libraryNeighborLimit,
        mosaicNeighborLimit,
        rankingsPageSize,
        currentLibraryPageSize: (...args) => currentLibraryPageSizeImpl(...args),
        showCompareEmpty: (...args) => showCompareEmptyImpl(...args),
    };
}
