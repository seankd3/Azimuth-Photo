export const INITIAL_RANKINGS_PAGE_SIZE = 48;
export const RANKINGS_PAGE_SIZE = 100;
export const SEARCH_RANKINGS_PAGE_SIZE = 5000;
export const LIBRARY_NEIGHBOR_LIMIT = 24;
export const MAX_RANKINGS_PAGE_SIZE = 5000;
export const MAX_SCROLL_RESTORE_OFFSET = MAX_RANKINGS_PAGE_SIZE - RANKINGS_PAGE_SIZE;


export function currentLibraryPageSize({
    rankingsOffset = 0,
    pendingScrollRestoreOffset = 0,
    hasActiveSearch = false,
    initialPageSize = INITIAL_RANKINGS_PAGE_SIZE,
    pageSize = RANKINGS_PAGE_SIZE,
    searchPageSize = SEARCH_RANKINGS_PAGE_SIZE,
    maxPageSize = MAX_RANKINGS_PAGE_SIZE,
} = {}) {
    if (hasActiveSearch) return Math.min(maxPageSize, searchPageSize);
    const offset = Number(pendingScrollRestoreOffset || 0);
    if (rankingsOffset === 0 && Number.isFinite(offset) && offset > 0) {
        const restoreSize = Math.max(initialPageSize, Math.min(offset, MAX_SCROLL_RESTORE_OFFSET) + pageSize);
        return Math.min(maxPageSize, restoreSize);
    }
    return rankingsOffset === 0 ? initialPageSize : pageSize;
}
