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
} = {}) {
    return {
        hideLibraryEmptyState: () => hideLibraryEmptyStateImpl(),
        updateLibraryEmptyState: () => updateLibraryEmptyStateImpl({
            hasImages: getImages().length > 0,
            searchQuery: getSearchQuery(),
            hasActiveTextSearch,
            hasActiveLibraryFilters,
            clearSearch,
            clearLibraryFilters,
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
