import { bindLibraryKeyboard } from '../library/keyboard.js';

export function createLegacyLibraryInitBridge({
    documentImpl = document,
    windowImpl = window,
    sessionStorageImpl = sessionStorage,
    intersectionObserverImpl = globalThis.IntersectionObserver,
    setTimeoutImpl = windowImpl?.setTimeout?.bind(windowImpl) || setTimeout,
    fetchStats = () => fetch('/api/stats').then(r => r.json()),
    scrollOffsetStorageKey,
    initBottomBarMeasurement,
    startAIStatusPolling,
    resetLibraryResults,
    restoreFilters,
    restoreSortState,
    restoreSearchState,
    setPendingScrollRestoreOffset,
    loadUiSettings,
    loadRankings,
    setCompareStats,
    updateCompareProgress,
    loadFolderList,
    scheduleFilterOptionsLoad,
    initStarHover,
    restoreScrollPosition,
    currentLibraryView,
    getRankingsLoading,
    getRankingsExhausted,
    libraryScrollRoot,
    updateBackToTopButton,
    getSelectedLibraryIndex,
    getLibraryImages,
    hasBatchSelection,
    selectLibraryCard,
    deselectLibraryCard,
    findCardInDirection,
    openLightbox,
    lightboxNext,
    lightboxPrev,
    closeLightbox,
    setCurrentLibraryFlag,
    trapLoupeFocus,
    batchFlag,
    clearBatchSelection,
    saveScrollPosition,
    initLoupeInteraction,
    initSearchInputControls,
    bindLibraryKeyboardImpl = bindLibraryKeyboard,
} = {}) {
    async function initLibrary() {
        initBottomBarMeasurement();
        startAIStatusPolling(750, { immediate: true });
        resetLibraryResults();
        restoreFilters();
        restoreSortState();
        restoreSearchState();
        setPendingScrollRestoreOffset(Number(sessionStorageImpl.getItem(scrollOffsetStorageKey) || 0));
        loadUiSettings();

        const rankingsPromise = loadRankings();
        const statsPromise = fetchStats().then(stats => {
            setCompareStats(stats);
            updateCompareProgress();
        }).catch(() => {});

        setTimeoutImpl(() => {
            loadFolderList();
            scheduleFilterOptionsLoad();
        }, 500);
        initStarHover();

        await rankingsPromise;
        restoreScrollPosition();
        await statsPromise;

        const sentinel = documentImpl.createElement('div');
        sentinel.style.height = '1px';
        documentImpl.querySelector('.rankings-grid')?.after(sentinel);
        const scrollObserver = new intersectionObserverImpl((entries) => {
            if (
                entries[0].isIntersecting &&
                currentLibraryView() === 'grid' &&
                !getRankingsLoading() &&
                !getRankingsExhausted()
            ) {
                loadRankings();
            }
        }, { root: libraryScrollRoot(), rootMargin: '600px 0px' });
        scrollObserver.observe(sentinel);

        const scrollRoot = libraryScrollRoot();
        if (scrollRoot) {
            scrollRoot.addEventListener('scroll', updateBackToTopButton, { passive: true });
            updateBackToTopButton();
        }

        bindLibraryKeyboardImpl({
            getSelectedLibraryIndex,
            getLibraryImages,
            hasBatchSelection,
            selectLibraryCard,
            deselectLibraryCard,
            findCardInDirection,
            openLightbox,
            lightboxNext,
            lightboxPrev,
            closeLightbox,
            setCurrentLibraryFlag,
            trapLoupeFocus,
            batchFlag,
            clearBatchSelection,
            saveScrollPosition,
        });

        initLoupeInteraction();
        initSearchInputControls();

        documentImpl.querySelectorAll('.bottom-bar a[href]').forEach((link) => {
            link.addEventListener('click', () => {
                const href = link.getAttribute('href') || '';
                if (href && href !== windowImpl.location.pathname) saveScrollPosition();
            });
        });
        windowImpl.addEventListener('beforeunload', saveScrollPosition);
    }

    function initRankings() {
        initLibrary();
    }

    return {
        initLibrary,
        initRankings,
    };
}
