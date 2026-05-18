export const INITIAL_RANKINGS_PAGE_SIZE = 48;
export const RANKINGS_PAGE_SIZE = 100;
export const LIBRARY_NEIGHBOR_LIMIT = 24;


export function currentLibraryPageSize({
    rankingsOffset = 0,
    pendingScrollRestoreOffset = 0,
    initialPageSize = INITIAL_RANKINGS_PAGE_SIZE,
    pageSize = RANKINGS_PAGE_SIZE,
} = {}) {
    if (rankingsOffset === 0 && pendingScrollRestoreOffset > 0) {
        return Math.max(initialPageSize, pendingScrollRestoreOffset + pageSize);
    }
    return rankingsOffset === 0 ? initialPageSize : pageSize;
}
