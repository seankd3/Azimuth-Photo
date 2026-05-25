import {
    SCROLL_OFFSET_STORAGE_KEY,
    SCROLL_POS_STORAGE_KEY,
    hideLibraryEmptyState as hideLibraryEmptyStateCore,
    libraryScrollRoot as libraryScrollRootCore,
    restoreScrollPosition as restoreScrollPositionCore,
    saveScrollPosition as saveScrollPositionCore,
    scrollLibraryContainerToElement as scrollLibraryContainerToElementCore,
    scrollToTop as scrollToTopCore,
    updateBackToTopButton as updateBackToTopButtonCore,
    updateLibraryEmptyState as updateLibraryEmptyStateCore,
} from '../library/shell.js';
import {
    bindLibraryLoadMoreButton as bindLibraryLoadMoreButtonCore,
    updateLibraryLoadMore as updateLibraryLoadMoreCore,
} from '../library/load_more.js';
import {
    deselectLibraryCard as deselectLibraryCardCore,
    findCardInDirection as findCardInDirectionCore,
    selectLibraryCard as selectLibraryCardCore,
} from '../library/navigation.js';

export {
    SCROLL_OFFSET_STORAGE_KEY,
    SCROLL_POS_STORAGE_KEY,
};

export function createLegacyLibraryShellBridge({
    hideLibraryEmptyStateImpl = hideLibraryEmptyStateCore,
    updateLibraryEmptyStateImpl = updateLibraryEmptyStateCore,
    libraryScrollRootImpl = libraryScrollRootCore,
    saveScrollPositionImpl = saveScrollPositionCore,
    restoreScrollPositionImpl = restoreScrollPositionCore,
    updateBackToTopButtonImpl = updateBackToTopButtonCore,
    updateLibraryLoadMoreImpl = updateLibraryLoadMoreCore,
    bindLibraryLoadMoreButtonImpl = bindLibraryLoadMoreButtonCore,
    scrollToTopImpl = scrollToTopCore,
    scrollLibraryContainerToElementImpl = scrollLibraryContainerToElementCore,
    selectLibraryCardImpl = selectLibraryCardCore,
    deselectLibraryCardImpl = deselectLibraryCardCore,
    findCardInDirectionImpl = findCardInDirectionCore,
    getImages = () => [],
    getRankingsOffset = () => 0,
    setRankingsOffset,
    setPendingScrollRestoreOffset,
    setSelectedLibraryIndex,
    getSearchQuery = () => '',
    hasActiveTextSearch,
    hasActiveLibraryFilters,
    clearSearch,
    clearLibraryFilters,
    getFilteredPoolVisible = () => 0,
    getRankingsLoading = () => false,
    getRankingsExhausted = () => false,
    getCurrentLibraryView = () => 'grid',
    onLoadMoreRankings = () => {},
} = {}) {
    return {
        hideLibraryEmptyState: () => hideLibraryEmptyStateImpl(),
        updateLibraryEmptyState: (options = {}) => updateLibraryEmptyStateImpl({
            hasImages: getImages().length > 0,
            searchQuery: getSearchQuery(),
            hasActiveTextSearch,
            hasActiveLibraryFilters,
            clearSearch,
            clearLibraryFilters,
            ...options,
        }),
        libraryScrollRoot: () => libraryScrollRootImpl(),
        saveScrollPosition: () => saveScrollPositionImpl({
            rankingsOffset: getRankingsOffset(),
            scrollPosStorageKey: SCROLL_POS_STORAGE_KEY,
            scrollOffsetStorageKey: SCROLL_OFFSET_STORAGE_KEY,
        }),
        restoreScrollPosition: () => restoreScrollPositionImpl({
            getRankingsOffset,
            setRankingsOffset,
            setPendingScrollRestoreOffset,
            scrollPosStorageKey: SCROLL_POS_STORAGE_KEY,
            scrollOffsetStorageKey: SCROLL_OFFSET_STORAGE_KEY,
        }),
        updateBackToTopButton: () => updateBackToTopButtonImpl(),
        updateLibraryLoadMore: (options = {}) => updateLibraryLoadMoreImpl({
            shownCount: getImages().length,
            totalCount: getFilteredPoolVisible(),
            loading: getRankingsLoading(),
            exhausted: getRankingsExhausted(),
            hasActiveSearch: hasActiveTextSearch?.(getSearchQuery()),
            viewMode: getCurrentLibraryView(),
            ...options,
        }),
        bindLibraryLoadMoreButton: () => bindLibraryLoadMoreButtonImpl({
            onLoadMore: onLoadMoreRankings,
        }),
        scrollToTop: () => scrollToTopImpl(),
        scrollLibraryContainerToElement: (el, behavior = 'smooth') => scrollLibraryContainerToElementImpl(el, behavior),
        selectLibraryCard: (index, cards) => {
            const selected = selectLibraryCardImpl(index, cards, { scrollRoot: libraryScrollRootImpl() });
            if (selected !== null) setSelectedLibraryIndex?.(selected);
            return selected;
        },
        deselectLibraryCard: (cards) => {
            const selected = deselectLibraryCardImpl(cards);
            setSelectedLibraryIndex?.(selected);
            return selected;
        },
        findCardInDirection: (cards, currentIdx, direction) => findCardInDirectionImpl(cards, currentIdx, direction),
    };
}
