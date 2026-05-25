import { bindLibraryKeyboard } from '../library/keyboard.js';
import { bindSharedBottomBarControls } from '../bottom_bar_controls.js';


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
    scrollToTop,
    bindLibraryLoadMoreButton,
    initLoupeInteraction,
    initSearchInputControls,
    clearSearch,
    setFilter,
    toggleMetadataFilters,
    toggleFilter,
    toggleStar,
    setThumbSize,
    toggleBackgroundWorkPanel,
    setSortField,
    toggleSortDir,
    toggleBatchMode,
    exportRankings,
    setLibraryView,
    bindLibraryKeyboardImpl = bindLibraryKeyboard,
    bindSharedBottomBarControlsImpl = bindSharedBottomBarControls,
} = {}) {
    function bindLibraryToolbarControls() {
        const bar = documentImpl.getElementById('library-bar');
        if (bar && bar.dataset.paLibraryToolbarBound !== '1') {
            bar.dataset.paLibraryToolbarBound = '1';
            bar.addEventListener('change', (event) => {
                const control = event.target?.closest?.('[data-action="set-sort-field"]');
                if (!control || !bar.contains(control)) return;
                setSortField?.(control.value);
            });
            bar.addEventListener('click', (event) => {
                const control = event.target?.closest?.('[data-action]');
                if (!control || !bar.contains(control)) return;
                const action = control.dataset.action;
                if (![
                    'toggle-sort-dir',
                    'toggle-batch-mode',
                    'export-rankings',
                    'set-library-view',
                ].includes(action)) return;
                event.preventDefault();
                if (action === 'toggle-sort-dir') {
                    toggleSortDir?.();
                } else if (action === 'toggle-batch-mode') {
                    toggleBatchMode?.();
                } else if (action === 'export-rankings') {
                    exportRankings?.(control.dataset.format || 'json');
                } else if (action === 'set-library-view') {
                    setLibraryView?.(control.dataset.view || 'grid');
                }
            });
        }

        const backToTop = documentImpl.getElementById('back-to-top');
        if (backToTop && backToTop.dataset.paScrollTopBound !== '1') {
            backToTop.dataset.paScrollTopBound = '1';
            backToTop.addEventListener('click', (event) => {
                event.preventDefault();
                scrollToTop?.();
            });
        }
    }

    async function initLibrary() {
        initBottomBarMeasurement();
        startAIStatusPolling(750, { immediate: true });
        resetLibraryResults();
        restoreFilters();
        restoreSortState();
        restoreSearchState();
        setPendingScrollRestoreOffset(Number(sessionStorageImpl.getItem(scrollOffsetStorageKey) || 0));
        loadUiSettings();
        initLoupeInteraction();
        initSearchInputControls();
        bindSharedBottomBarControlsImpl({
            documentImpl,
            clearSearch,
            setFilter,
            toggleMetadataFilters,
            toggleFilter,
            toggleStar,
            setThumbSize,
            toggleBackgroundWorkPanel,
        });
        bindLibraryToolbarControls();

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

        bindLibraryLoadMoreButton?.();

        const sentinel = documentImpl.createElement('div');
        sentinel.style.height = '1px';
        const scrollAnchor = documentImpl.getElementById('library-load-more')
            || documentImpl.querySelector('.rankings-grid');
        scrollAnchor?.after(sentinel);
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
