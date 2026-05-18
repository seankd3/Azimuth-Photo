import {
    formatBytes,
    hideConfirmModal as hideConfirmModalUi,
    hideShortcuts as hideShortcutsUi,
    initBottomBarMeasurement as initBottomBarMeasurementUi,
    initShortcutOverlay,
    initVisibilityRefresh as initVisibilityRefreshUi,
    showConfirmModal as showConfirmModalUi,
    showShortcuts as showShortcutsUi,
    showToast as showToastUi,
    updateBottomBarHeightVar as updateBottomBarHeightVarUi,
} from '../ui.js';
import { fetchJson } from '../api.js';
import {
    createAIStatusPoller,
} from '../ai/poller.js';
import { toggleAIPanel } from '../ai/status.js';
import { createHomeScanController } from '../catalog/home_scan.js';
import { createScanEntrypoint } from '../catalog/scan_entrypoint.js';
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
import { createLibraryFilterController } from '../library/filter_controller.js';
import {
    createMediaStatusClient,
    createMediaStatusController,
} from '../media_status.js';
import {
    createImagePreloader,
    createWarmupManager,
    loadImageProbe,
    withTimeout,
} from '../warmup.js';
import { createNeighborWarmupController } from '../warmup_neighbors.js';
import { createThumbnailSizeHandler } from '../thumbnail_size.js';
import {
    mosaicSizeFromThumbHeight,
} from '../compare/mosaic.js';
import { createComparePageController } from '../compare/page_controller.js';
import { createMosaicActionController } from '../compare/mosaic_action_controller.js';
import { createMosaicRenderController } from '../compare/mosaic_render_controller.js';
import {
    createMosaicReplacementBuffer,
    MOSAIC_REPLACEMENT_LOW_WATER,
} from '../compare/mosaic_replacements.js';
import {
    deselectMosaicCell as deselectMosaicCellCore,
    findMosaicCellInDirection as findMosaicCellInDirectionCore,
    selectMosaicCell as selectMosaicCellCore,
} from '../compare/navigation.js';
import { createCompareKeyboardHandler } from '../compare/keyboard.js';
import {
    createCompareImageController,
} from '../compare/image_controller.js';
import { createComparePairController } from '../compare/pair_controller.js';
import {
    showCompareEmpty as showCompareEmptyCore,
} from '../compare/view.js';
import { createCompareModeController } from '../compare/mode_controller.js';
import {
    createCompareActionController,
} from '../compare/action_controller.js';
import {
    precomputePropagationCounts,
} from '../compare/propagation.js';
import { createCompareStatusController } from '../compare/status_controller.js';
import {
    loupeTierUrl as loupeTierUrlCore,
} from '../loupe/tiers.js';
import { createLoupeController } from '../loupe/controller.js';
import {
    SORT_KEYS,
    sortValueForState,
} from '../library/sort.js';
import { createLibrarySortController } from '../library/sort_controller.js';
import {
    clearPersistedSearchState as clearPersistedSearchStateCore,
    restoreSearchSortState as restoreSearchSortStateCore,
    restoreSearchState as restoreSearchStateCore,
    restoreSortState as restoreSortStateCore,
    saveSearchSortState as saveSearchSortStateCore,
    saveSearchState as saveSearchStateCore,
    saveSortState as saveSortStateCore,
} from '../library/search_state.js';
import {
    syncSortControls as syncSortControlsCore,
    updateCompareSearchIndicator as updateCompareSearchIndicatorCore,
    updateSearchControls as updateSearchControlsCore,
    updateSimilaritySortOption as updateSimilaritySortOptionCore,
    updateSortDirIcon as updateSortDirIconCore,
} from '../library/search_controls.js';
import {
    applySearchQueryChange as applySearchQueryChangeCore,
    clearSearch as clearSearchCore,
    initSearchInputControls as initSearchInputControlsCore,
    runDeepSearch as runDeepSearchCore,
} from '../library/search_controller.js';
import {
    setCurrentLibraryFlag as setCurrentLibraryFlagCore,
    setImageFlag as setImageFlagCore,
    updateImageFlagLocal as updateImageFlagLocalCore,
} from '../library/flags.js';
import { createBatchSelectionController } from '../library/batch_controller.js';
import { exportRankings as exportRankingsCore } from '../export/actions.js';
import { eloToStars } from '../library/display.js';
import { appendLibraryRankCards } from '../library/rank_cards.js';
import {
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
    dateGroupOffset as dateGroupOffsetCore,
    findDateGroupHeader as findDateGroupHeaderCore,
    isDateSortValue,
    jumpToDateGroup as jumpToDateGroupCore,
    renderDateScrubber as renderDateScrubberCore,
    setActiveDateScrubberGroup as setActiveDateScrubberGroupCore,
    setupDateScrubberScrollTracking as setupDateScrubberScrollTrackingCore,
    syncDateScrubberVisibility as syncDateScrubberVisibilityCore,
    teardownDateScrubberScrollTracking as teardownDateScrubberScrollTrackingCore,
    createDateScrubberController,
} from '../library/date_scrubber.js';
import {
    deselectLibraryCard as deselectLibraryCardCore,
    findCardInDirection as findCardInDirectionCore,
    selectLibraryCard as selectLibraryCardCore,
} from '../library/navigation.js';
import { bindLibraryKeyboard } from '../library/keyboard.js';
import { createLibraryMapController } from '../library/map_controller.js';
import {
    formatDateTime,
    imageAspectRatio,
} from '../media_metadata.js';
import {
    hasActiveTextSearch as hasActiveTextSearchCore,
} from '../search/query.js';
import { createFindSimilarAction } from '../library/similar.js';
import { createPeopleApi } from '../people/controller.js';
import { createSettingsPageController } from '../settings/page.js';
import { createUiSettingsLoader } from '../settings/ui_settings.js';

const legacyPhotoArchive = (() => {
    // --- Compare Mode State ---
    let comparePairs = [];
    let compareIndex = 0;
    let compareMode = 'swiss';
    let compareModeTransitionToken = 0;
    let compareBusy = false;
    let compareActionSeq = 0;
    let undoCount = 0;
    let compareStats = {};
    let compareImageToken = 0;
    const compareDisplayedTier = { left: -1, right: -1 };

    // --- Rankings State ---
    let rankingsOffset = 0;
    const INITIAL_RANKINGS_PAGE_SIZE = 48;
    const RANKINGS_PAGE_SIZE = 100;
    const CROSS_VIEW_WARM_DELAY_MS = 1000;
    const LIBRARY_NEIGHBOR_LIMIT = 24;
    const MOSAIC_NEIGHBOR_LIMIT = 8;
    const COMPARE_NEIGHBOR_PAIRS = 4;
    let loupeController = null;
    const mediaStatusController = createMediaStatusController({
        client: createMediaStatusClient({ maxAgeMs: 15000 }),
        getCurrentLoupeImage: () => loupeController?.getCurrentImage() || null,
        getLoupeImageToken: () => loupeController?.getImageToken() || 0,
        refreshLoupeMediaStatus: (...args) => loupeController?.refreshLoupeMediaStatus(...args),
    });
    const { handleWarmTiersApplied } = mediaStatusController;
    let selectedLibraryIndex = -1;
    let selectedMosaicIndex = -1;
    const aiStatusPoller = createAIStatusPoller({
        initVisibilityRefresh,
    });
    const warmups = createWarmupManager({
        fetchJsonImpl: fetchJson,
        preloadImageWithTimeout,
        onWarmTiersApplied: handleWarmTiersApplied,
    });
    const {
        clearWarmups,
        enqueueWarmup,
        fetchWarmJson,
        scheduleBackgroundWarm,
        takeWarmCache,
        warmImageTiers,
        warmRequests,
    } = warmups;

    function currentWarmupGeneration() {
        return warmups.currentGeneration();
    }

    function updateBottomBarHeightVar() {
        updateBottomBarHeightVarUi({
            onMeasured: () => {
                if (document.getElementById('mosaic-grid')) scheduleMosaicRender();
            },
        });
    }

    function initBottomBarMeasurement() {
        initBottomBarMeasurementUi({
            onMeasured: () => {
                if (document.getElementById('mosaic-grid')) scheduleMosaicRender();
            },
        });
    }

    function startAIStatusPolling(initialDelayMs = 0, { immediate = false } = {}) {
        aiStatusPoller.start(initialDelayMs);
        if (immediate && !document.hidden) {
            aiStatusPoller.poll();
        }
    }

    function initVisibilityRefresh() {
        initVisibilityRefreshUi({
            onVisible: () => {
                aiStatusPoller.handleVisible();
                refreshSettingsMetaIfActive().catch(() => {});
            },
        });
    }

    const uiSettingsLoader = createUiSettingsLoader({
        fetchJsonImpl: fetchJson,
        onLoaded: () => renderLoupeStatusLine(),
    });

    async function loadUiSettings() {
        return uiSettingsLoader.loadUiSettings();
    }

    // ==================== MOSAIC RANKING MODE ====================

    let mosaicSize = 12;
    let mosaicImages = []; // currently visible images [{id, filename, elo, thumb_url}, ...]
    let mosaicAge = []; // how many clicks each image has survived on the board
    let mosaicPickCount = 0;
    let mosaicStrategy = 'diverse';
    let mosaicPropagationCounts = {}; // precomputed: {imageId: predictedCount}
    let mosaicRenderToken = 0;
    let mosaicResizeRaf = null;

    const mosaicRenderController = createMosaicRenderController({
        getMosaicImages: () => mosaicImages,
        getCompareMode: () => compareMode,
        getMosaicResizeRaf: () => mosaicResizeRaf,
        setMosaicResizeRaf: (raf) => { mosaicResizeRaf = raf; },
        incrementMosaicRenderToken: () => ++mosaicRenderToken,
        getMosaicRenderToken: () => mosaicRenderToken,
        setSelectedMosaicIndex: (index) => { selectedMosaicIndex = index; },
        getMediaStatus,
        loadImageProbe,
        loupeTierUrl,
        mosaicClick,
        preloadImage: (...args) => preloadImage(...args),
    });

    function mosaicGridElo() {
        return mosaicRenderController.mosaicGridElo();
    }

    async function loadMosaicBatch() {
        const url = buildMosaicUrl({ n: mosaicSize });
        // Never use warm cache for diverse strategy — each load should be fresh
        const data = (mosaicStrategy !== 'diverse' ? takeWarmCache(`compare:${url}`) : null) || await fetchWarmJson(url);
        if (!data) return;
        compareStats = data.stats || {};
        updateCompareProgress();

        if (data.images.length < 2) {
            showCompareEmpty();
            return;
        }

        mosaicImages = data.images;
        mosaicAge = new Array(data.images.length).fill(0);
        mosaicPickCount = 0;
        mosaicReplacements = [];
        mosaicFilling = false;
        mosaicBusy = false;
        primeMediaStatuses(mosaicImages.map((img) => img.id));
        renderMosaic();
        warmImageTiers({
            md: mosaicImages.map((img) => img.id),
            lg: mosaicImages.map((img) => img.id),
        });
        mosaicFillReplacements();
        precomputePropagation();
        scheduleCompareNeighborWarmup('mosaic');
        scheduleCrossViewWarmup('compare');
    }

    function renderMosaic() {
        return mosaicRenderController.renderMosaic();
    }

    function scheduleMosaicRender() {
        return mosaicRenderController.scheduleMosaicRender();
    }

    function scheduleMosaicImageUpgrade(cell, img, rowH, token, index = 0) {
        return mosaicRenderController.scheduleMosaicImageUpgrade(cell, img, rowH, token, index);
    }

    async function upgradeMosaicCellImage(cell, img, rowH, token) {
        return mosaicRenderController.upgradeMosaicCellImage(cell, img, rowH, token);
    }

    async function adoptMosaicTier(cell, img, tier, cachedOnly, token, timeoutMs) {
        return mosaicRenderController.adoptMosaicTier(cell, img, tier, cachedOnly, token, timeoutMs);
    }

    // Pre-fetched replacement images ready to swap in instantly
    let mosaicReplacements = [];
    let mosaicFilling = false;

    const mosaicReplacementBuffer = createMosaicReplacementBuffer({
        getMosaicFilling: () => mosaicFilling,
        setMosaicFilling: (filling) => { mosaicFilling = filling; },
        getMosaicImages: () => mosaicImages,
        getMosaicReplacements: () => mosaicReplacements,
        getMosaicRenderToken: () => mosaicRenderToken,
        getWarmupGeneration: currentWarmupGeneration,
        enqueueWarmup,
        buildMosaicUrl,
        loadImageProbe,
        setCompareStats: (stats) => { compareStats = stats; },
        updateCompareProgress,
    });

    function mosaicFillReplacements() {
        return mosaicReplacementBuffer.fillReplacements();
    }

    let mosaicBusy = false;
    let mosaicActionSeq = 0;

    const mosaicActionController = createMosaicActionController({
        getMosaicBusy: () => mosaicBusy,
        setMosaicBusy: (busy) => { mosaicBusy = busy; },
        setUndoCount: (count) => { undoCount = count; },
        incrementMosaicActionSeq: () => ++mosaicActionSeq,
        getMosaicActionSeq: () => mosaicActionSeq,
        getMosaicImages: () => mosaicImages,
        setMosaicImages: (images) => { mosaicImages = images; },
        getMosaicAge: () => mosaicAge,
        setMosaicAge: (age) => { mosaicAge = age; },
        getMosaicReplacements: () => mosaicReplacements,
        setMosaicReplacements: (replacements) => { mosaicReplacements = replacements; },
        getMosaicRenderToken: () => mosaicRenderToken,
        getMosaicPropagationCounts: () => mosaicPropagationCounts,
        setMosaicPropagationCounts: (counts) => { mosaicPropagationCounts = counts; },
        getCompareStats: () => compareStats,
        setCompareStats: (stats) => { compareStats = stats; },
        queryMosaicCells: () => document.querySelectorAll('.mosaic-cell'),
        scheduleMosaicImageUpgrade,
        mosaicFillReplacements,
        precomputePropagation,
        bumpRankingSignals,
        updateCompareProgress,
        fetchPropagationCount,
        showPropagationBadge,
        renderMosaic,
        showToast,
        showCompareEmpty,
        replacementLowWater: MOSAIC_REPLACEMENT_LOW_WATER,
    });

    function mosaicClick(id) {
        return mosaicActionController.mosaicClick(id);
    }

    function showToast(msg) {
        showToastUi(msg, { beforeShow: updateBottomBarHeightVar });
    }

    function showConfirmModal(title, text, onConfirm) {
        showConfirmModalUi(title, text, onConfirm);
    }

    function hideConfirmModal() {
        hideConfirmModalUi();
    }

    const compareModeController = createCompareModeController({
        clearWarmups,
        setMosaicStrategyValue: (strategy) => { mosaicStrategy = strategy; },
        setCompareModeValue: (mode) => { compareMode = mode; },
        incrementTransitionToken: () => ++compareModeTransitionToken,
        isCurrentTransition: (token) => token === compareModeTransitionToken,
        incrementCompareImageToken: () => { compareImageToken++; },
        loadMosaicBatch,
        resetComparePairs: () => {
            comparePairs = [];
            compareIndex = 0;
        },
        fetchComparePairs,
        showComparePair,
    });

    function setMosaicStrategy(strategy) {
        return compareModeController.setMosaicStrategy(strategy);
    }

    function mosaicShuffle() {
        return compareModeController.mosaicShuffle();
    }

    // ==================== COMPARE MODE ====================

    const comparePairController = createComparePairController({
        getCompareMode: () => compareMode,
        getCompareIndex: () => compareIndex,
        setCompareIndex: (index) => { compareIndex = index; },
        getComparePairs: () => comparePairs,
        setComparePairs: (pairs) => { comparePairs = pairs; },
        setCompareStats: (stats) => { compareStats = stats; },
        incrementCompareImageToken: () => ++compareImageToken,
        getCompareImageToken: () => compareImageToken,
        buildCompareUrl,
        takeWarmCache,
        fetchWarmJson,
        preloadImage: (...args) => preloadImage(...args),
        primeMediaStatuses,
        renderCompareImage,
        warmImageTiers,
        updateCompareProgress,
        scheduleCompareNeighborWarmup,
        scheduleCrossViewWarmup,
        showCompareEmpty,
    });

    async function fetchComparePairs() {
        return comparePairController.fetchComparePairs();
    }

    function showComparePair() {
        return comparePairController.showComparePair();
    }

    function isCurrentCompareImage(token) {
        return comparePairController.isCurrentCompareImage(token);
    }

    const compareImageController = createCompareImageController({
        displayedTiers: compareDisplayedTier,
        getMediaStatus,
        isCurrentCompareImage,
        loadImageProbe,
        loupeTierUrl,
    });

    function renderCompareImage(img, imgEl, side, token) {
        return compareImageController.renderCompareImage(img, imgEl, side, token);
    }

    async function upgradeCompareImage(img, imgEl, side, token) {
        return compareImageController.upgradeCompareImage(img, imgEl, side, token);
    }

    async function adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs) {
        return compareImageController.adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs);
    }

    const compareStatusController = createCompareStatusController({
        getCompareStats: () => compareStats,
        fetchImpl: fetch,
    });

    function bumpRankingSignals(signalDelta, directDelta = 0) {
        return compareStatusController.bumpRankingSignals(signalDelta, directDelta);
    }

    function updateCompareProgress() {
        return compareStatusController.updateCompareProgress();
    }

    function renderCoverageBar(stats = compareStats) {
        return compareStatusController.renderCoverageBar(stats);
    }

    function mergeCoverageStats(stats) {
        return compareStatusController.mergeCoverageStats(stats);
    }

    function updateCoverageBar() {
        return compareStatusController.updateCoverageBar();
    }

    function rollUpCounter(el, from, to) {
        return compareStatusController.rollUpCounter(el, from, to);
    }

    function precomputePropagation() {
        precomputePropagationCounts(mosaicImages, {
            onCounts: (counts) => {
                mosaicPropagationCounts = counts;
            },
        });
    }

    function fetchPropagationCount(directCount = 0) {
        return compareStatusController.fetchPropagationCount(directCount);
    }

    function showPropagationBadge(count) {
        return compareStatusController.showPropagationBadge(count);
    }

    const compareActionController = createCompareActionController({
        getCompareBusy: () => compareBusy,
        setCompareBusy: (busy) => { compareBusy = busy; },
        setUndoCount: (value) => { undoCount = value; },
        incrementUndoCount: () => ++undoCount,
        incrementCompareActionSeq: () => ++compareActionSeq,
        getCompareActionSeq: () => compareActionSeq,
        getCompareIndex: () => compareIndex,
        setCompareIndex: (index) => { compareIndex = index; },
        getComparePairs: () => comparePairs,
        getCompareMode: () => compareMode,
        showComparePair,
        showToast,
        fetchPropagationCount,
        bumpRankingSignals,
        updateCompareProgress,
    });

    function submitComparison(side) {
        return compareActionController.submitComparison(side);
    }

    async function undoComparison() {
        return compareActionController.undoComparison();
    }

    function selectMosaicCell(index, cells) {
        const selected = selectMosaicCellCore(index, cells);
        if (selected !== null) selectedMosaicIndex = selected;
    }

    function deselectMosaicCell(cells) {
        selectedMosaicIndex = deselectMosaicCellCore(cells);
    }

    function findMosaicCellInDirection(cells, currentIdx, direction) {
        return findMosaicCellInDirectionCore(cells, currentIdx, direction);
    }

    const handleCompareKey = createCompareKeyboardHandler({
        getCompareMode: () => compareMode,
        getSelectedMosaicIndex: () => selectedMosaicIndex,
        getMosaicImages: () => mosaicImages,
        selectMosaicCell,
        deselectMosaicCell,
        findMosaicCellInDirection,
        mosaicClick,
        undoComparison,
        submitComparison,
    });

    function setCompareMode(mode) {
        return compareModeController.setCompareMode(mode);
    }

    const comparePageController = createComparePageController({
        getMosaicSize: () => mosaicSize,
        initBottomBarMeasurement,
        startAIStatusPolling,
        handleCompareKey,
        scheduleMosaicRender,
        submitComparison,
        restoreFilters,
        restoreSearchState,
        initSearchInputControls,
        setCompareMode,
        loadFolderList,
        scheduleFilterOptionsLoad,
        initStarHover,
    });

    async function initCompare() {
        return comparePageController.initCompare();
    }

    function showCompareEmpty() {
        showCompareEmptyCore();
    }

    // ==================== LIBRARY ====================

    let rankingsSort = 'elo';
    let sortField = 'elo';
    let sortDesc = true;
    let lastDateGroup = null;
    let dateGroupsData = [];
    let dateScrubberGeneration = 0;
    let dateJumpGeneration = 0;
    let searchQuery = '';
    let deepSearchRequested = false;
    let lastDeepSearchNoticeQuery = '';
    let searchDebounce = null;
    let rankingsLoading = false;
    let rankingsLoadPromise = null;
    let rankingsExhausted = false;
    let libraryRequestGeneration = 0;
    let pendingScrollRestoreOffset = 0;
    let thumbHeight = 220;
    let libraryImages = [];
    const SORT_STORAGE_KEY = 'pa_sort';
    const SEARCH_STORAGE_KEY = 'pa_search_query';
    const SEARCH_SORT_STORAGE_KEY = 'pa_search_sort';
    const SEARCH_DEEP_STORAGE_KEY = 'pa_search_deep';
    const SCROLL_POS_STORAGE_KEY = 'pa_scroll_pos';
    const SCROLL_OFFSET_STORAGE_KEY = 'pa_scroll_offset';
    const EMPTY_FILTERS = { ...EMPTY_FILTERS_CORE };
    let filters = { ...EMPTY_FILTERS };

    function setCurrentFilters(nextFilters) {
        filters = normalizeFilterState(nextFilters);
        return filters;
    }

    function saveFilters() {
        return saveFiltersCore({
            getFilters: currentFilterState,
            storage: sessionStorage,
            storageKey: FILTER_STORAGE_KEY,
            syncLibraryUrlState,
        });
    }

    function restoreFilters() {
        return restoreFiltersCore({
            storage: sessionStorage,
            storageKey: FILTER_STORAGE_KEY,
            location: window.location,
            setFilters: setCurrentFilters,
            applyFilterUiState,
        });
    }

    function applyFilterUiState(state = filters) {
        return applyFilterUiStateCore({
            filters: state,
            document,
            updateMetadataFilterButton,
        });
    }

    function activeMetadataFilterCount() {
        return activeMetadataFilterCountCore(filters);
    }

    function updateMetadataFilterButton() {
        updateMetadataFilterButtonCore({ filters });
    }

    function normalizeFilterState(state = {}) {
        return normalizeFilterStateCore(state);
    }

    function currentFilterState() {
        return normalizeFilterState(filters);
    }

    const queryController = createQueryController({
        getFilters: currentFilterState,
        getSearchQuery: () => searchQuery,
        getDeepSearchRequested: () => deepSearchRequested,
        getSortField: () => sortField,
        getSortDesc: () => sortDesc,
        getRankingsSort: () => rankingsSort,
        getMosaicStrategy: () => mosaicStrategy,
        getMosaicGridElo: mosaicGridElo,
        libraryNeighborLimit: LIBRARY_NEIGHBOR_LIMIT,
        mosaicNeighborLimit: MOSAIC_NEIGHBOR_LIMIT,
        compareNeighborPairs: COMPARE_NEIGHBOR_PAIRS,
    });

    function syncLibraryUrlState() {
        syncLibraryUrlStateCore({ filters: currentFilterState(), sortField, sortDesc });
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

    function buildCompareUrl(mode, n = COMPARE_NEIGHBOR_PAIRS, queryState = currentQueryState()) {
        return queryController.buildCompareUrl(mode, n, queryState);
    }

    function currentLibraryPageSize() {
        if (rankingsOffset === 0 && pendingScrollRestoreOffset > 0) {
            return Math.max(INITIAL_RANKINGS_PAGE_SIZE, pendingScrollRestoreOffset + RANKINGS_PAGE_SIZE);
        }
        return rankingsOffset === 0 ? INITIAL_RANKINGS_PAGE_SIZE : RANKINGS_PAGE_SIZE;
    }

    function resetLibraryResults({ clearBatch = false } = {}) {
        clearWarmups();
        libraryRequestGeneration++;
        rankingsOffset = 0;
        rankingsExhausted = false;
        libraryImages = [];
        lastDateGroup = null;
        rankingsLoading = false;
        rankingsLoadPromise = null;
        selectedLibraryIndex = -1;
        hideLibraryEmptyState();
        if (clearBatch) clearBatchSelection();
    }

    function hasActiveLibraryFilters() {
        return hasActiveFilters(currentFilterState());
    }

    function hideLibraryEmptyState() {
        hideLibraryEmptyStateCore();
    }

    function updateLibraryEmptyState() {
        updateLibraryEmptyStateCore({
            hasImages: libraryImages.length > 0,
            searchQuery,
            hasActiveTextSearch,
            hasActiveLibraryFilters,
            clearSearch,
            clearLibraryFilters,
        });
    }

    function libraryScrollRoot() {
        return libraryScrollRootCore();
    }

    function saveScrollPosition() {
        saveScrollPositionCore({
            rankingsOffset,
            scrollPosStorageKey: SCROLL_POS_STORAGE_KEY,
            scrollOffsetStorageKey: SCROLL_OFFSET_STORAGE_KEY,
        });
    }

    function restoreScrollPosition() {
        restoreScrollPositionCore({
            getRankingsOffset: () => rankingsOffset,
            setRankingsOffset: (value) => { rankingsOffset = value; },
            setPendingScrollRestoreOffset: (value) => { pendingScrollRestoreOffset = value; },
            scrollPosStorageKey: SCROLL_POS_STORAGE_KEY,
            scrollOffsetStorageKey: SCROLL_OFFSET_STORAGE_KEY,
        });
    }

    function updateBackToTopButton() {
        updateBackToTopButtonCore();
    }

    function scrollToTop() {
        scrollToTopCore();
    }

    function scrollLibraryContainerToElement(el, behavior = 'smooth') {
        scrollLibraryContainerToElementCore(el, behavior);
    }

    function syncDateScrubberVisibility() {
        return syncDateScrubberVisibilityCore({
            documentImpl: document,
            currentLibraryView,
            isDateScrubberActive,
        });
    }

    const dateScrubberController = createDateScrubberController({
        documentImpl: document,
        windowImpl: window,
        fetchImpl: fetch,
        isActive: isDateScrubberActive,
        getQueryString: () => filterQueryString(currentQueryState()),
        getSortValue: () => rankingsSort,
        getGroups: () => dateGroupsData,
        setGroups: (groups) => { dateGroupsData = groups; },
        nextGeneration: () => ++dateScrubberGeneration,
        isCurrentGeneration: (gen) => gen === dateScrubberGeneration,
        onJump: jumpToDateGroup,
        setupScrollObserver: () => setupDateScrubberScrollTracking(),
        syncVisibility: syncDateScrubberVisibility,
        teardownScrollTracking: teardownDateScrubberScrollTracking,
        renderDateScrubberImpl: renderDateScrubberCore,
    });

    const neighborWarmups = createNeighborWarmupController({
        getMosaicStrategy: () => mosaicStrategy,
        getMosaicSize: () => mosaicSize,
        getSearchQuery: () => searchQuery,
        getRankingsExhausted: () => rankingsExhausted,
        getRankingsSort: () => rankingsSort,
        getRankingsOffset: () => rankingsOffset,
        buildRankingsUrl,
        buildMosaicUrl,
        buildCompareUrl,
        currentQueryState,
        scheduleBackgroundWarm,
        warmRequests,
        initialRankingsPageSize: INITIAL_RANKINGS_PAGE_SIZE,
        rankingsPageSize: RANKINGS_PAGE_SIZE,
        compareNeighborPairs: COMPARE_NEIGHBOR_PAIRS,
        crossViewWarmDelayMs: CROSS_VIEW_WARM_DELAY_MS,
    });

    function scheduleCrossViewWarmup(fromView) {
        return neighborWarmups.scheduleCrossViewWarmup(fromView);
    }

    function scheduleLibraryNeighborWarmup() {
        return neighborWarmups.scheduleLibraryNeighborWarmup();
    }

    function scheduleCompareNeighborWarmup(mode = compareMode) {
        return neighborWarmups.scheduleCompareNeighborWarmup(mode);
    }

    function hasActiveTextSearch(value = searchQuery) {
        return hasActiveTextSearchCore(value);
    }

    function currentSearchMode() {
        return queryController.currentSearchMode();
    }

    function currentQueryState(overrides = {}) {
        return queryController.currentQueryState(overrides);
    }

    function applySortState(field, desc, { persist = true, persistSearch = true } = {}) {
        if (!SORT_KEYS[field]) return;
        sortField = field;
        sortDesc = Boolean(desc);
        rankingsSort = sortValueForState(sortField, sortDesc);
        syncSortControls();
        if (persist && sortField !== 'similarity') saveSortState();
        if (persistSearch && hasActiveTextSearch()) saveSearchSortState();
    }

    function saveSortState() {
        saveSortStateCore({
            storage: sessionStorage,
            storageKey: SORT_STORAGE_KEY,
            field: sortField,
            desc: sortDesc,
        });
        syncLibraryUrlState();
    }

    function saveSearchState() {
        saveSearchStateCore({
            storage: sessionStorage,
            searchKey: SEARCH_STORAGE_KEY,
            deepKey: SEARCH_DEEP_STORAGE_KEY,
            searchQuery,
            deepSearchRequested,
            hasActiveTextSearch,
        });
    }

    function saveSearchSortState() {
        saveSearchSortStateCore({
            storage: sessionStorage,
            storageKey: SEARCH_SORT_STORAGE_KEY,
            searchQuery,
            field: sortField,
            desc: sortDesc,
            hasActiveTextSearch,
        });
    }

    function clearPersistedSearchState() {
        clearPersistedSearchStateCore({
            storage: sessionStorage,
            searchKey: SEARCH_STORAGE_KEY,
            searchSortKey: SEARCH_SORT_STORAGE_KEY,
            deepKey: SEARCH_DEEP_STORAGE_KEY,
        });
    }

    function restoreSortState() {
        const restored = restoreSortStateCore({
            storage: sessionStorage,
            storageKey: SORT_STORAGE_KEY,
            locationSearch: window.location.search,
        });
        if (restored) {
            applySortState(restored.field, restored.desc, { persist: false, persistSearch: false });
        }
    }

    function restoreSearchSortState() {
        return restoreSearchSortStateCore({
            storage: sessionStorage,
            storageKey: SEARCH_SORT_STORAGE_KEY,
        });
    }

    function restoreSearchState() {
        const restored = restoreSearchStateCore({
            storage: sessionStorage,
            searchKey: SEARCH_STORAGE_KEY,
            deepKey: SEARCH_DEEP_STORAGE_KEY,
        });
        searchQuery = restored.searchQuery;
        deepSearchRequested = restored.deepSearchRequested;
        updateSimilaritySortOption();
        if (hasActiveTextSearch()) {
            const restoredSort = restoreSearchSortState() || { field: 'similarity', desc: true };
            applySortState(restoredSort.field, restoredSort.desc, { persist: false });
        }
        updateSearchControls();
    }

    function updateSearchControls() {
        updateSearchControlsCore({
            searchQuery,
            deepSearchRequested,
            sortField,
            sortDesc,
            hasActiveTextSearch,
            afterCompareSearchIndicator: updateBottomBarHeightVar,
        });
    }

    function updateCompareSearchIndicator() {
        updateCompareSearchIndicatorCore({
            searchQuery,
            active: hasActiveTextSearch(),
            afterUpdate: updateBottomBarHeightVar,
        });
    }

    function syncSortControls() {
        syncSortControlsCore({ sortField, sortDesc });
    }

    function rankingQueryString({
        queryState = currentQueryState(),
        limit = LIBRARY_NEIGHBOR_LIMIT,
        offset = 0,
        sort = queryState.sort,
    } = {}) {
        return queryController.rankingQueryString({ queryState, limit, offset, sort });
    }

    function initSearchInputControls() {
        const input = document.getElementById('search-input');
        if (!input || input.dataset.searchBound === '1') return;
        input.dataset.searchBound = '1';
        initSearchInputControlsCore({
            getSearchDebounce: () => searchDebounce,
            setSearchDebounce: (timer) => {
                searchDebounce = timer;
            },
            hasActiveTextSearch,
            applySearchQueryChangeImpl: (value) => applySearchQueryChangeCore(value, searchControllerContext()),
            clearSearchImpl: clearSearch,
        });
    }

    async function initLibrary() {
        initBottomBarMeasurement();
        startAIStatusPolling(750, { immediate: true });
        resetLibraryResults();
        restoreFilters();
        restoreSortState();
        restoreSearchState();
        pendingScrollRestoreOffset = Number(sessionStorage.getItem(SCROLL_OFFSET_STORAGE_KEY) || 0);
        loadUiSettings();

        // Fire all init requests in parallel — don't block on rankings
        const rankingsPromise = loadRankings();
        const statsPromise = fetch('/api/stats').then(r => r.json()).then(stats => {
            compareStats = stats;
            updateCompareProgress();
        }).catch(() => {});

        setTimeout(() => {
            loadFolderList();
            scheduleFilterOptionsLoad();
        }, 500);
        initStarHover();

        await rankingsPromise;
        restoreScrollPosition();
        await statsPromise;

        // Infinite scroll via IntersectionObserver (avoids continuous scroll events)
        const sentinel = document.createElement('div');
        sentinel.style.height = '1px';
        document.querySelector('.rankings-grid')?.after(sentinel);
        const scrollObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && currentLibraryView() === 'grid' && !rankingsLoading && !rankingsExhausted) {
                loadRankings();
            }
        }, { root: libraryScrollRoot(), rootMargin: '600px 0px' });
        scrollObserver.observe(sentinel);
        const scrollRoot = libraryScrollRoot();
        if (scrollRoot) {
            scrollRoot.addEventListener('scroll', updateBackToTopButton, { passive: true });
            updateBackToTopButton();
        }

        bindLibraryKeyboard({
            getSelectedLibraryIndex: () => selectedLibraryIndex,
            getLibraryImages: () => libraryImages,
            hasBatchSelection: () => batchController.hasSelection(),
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

        // Loupe zoom/pan interaction
        initLoupeInteraction();

        initSearchInputControls();

        document.querySelectorAll('.bottom-bar a[href]').forEach((link) => {
            link.addEventListener('click', () => {
                const href = link.getAttribute('href') || '';
                if (href && href !== window.location.pathname) saveScrollPosition();
            });
        });
        window.addEventListener('beforeunload', saveScrollPosition);
    }

    function initRankings() { initLibrary(); }

    function updateSimilaritySortOption() {
        updateSimilaritySortOptionCore({ active: hasActiveTextSearch() });
    }

    function clearSearch() {
        clearSearchCore({
            ...searchControllerContext(),
            clearSearchDebounce: clearSearchDebounceTimer,
        });
    }

    function runDeepSearch() {
        runDeepSearchCore({
            ...searchControllerContext(),
            clearSearchDebounce: clearSearchDebounceTimer,
        });
    }

    const librarySortController = createLibrarySortController({
        getSortField: () => sortField,
        getSortDesc: () => sortDesc,
        setRankingsSortValue: (value) => { rankingsSort = value; },
        applySortState,
        resetLibraryResults,
        clearDateGroups: () => { dateGroupsData = []; },
        loadRankings,
        updateDateScrubber,
    });

    function setRankingsSort(sort, options) {
        return librarySortController.setRankingsSort(sort, options);
    }

    function setSortField(field) {
        return librarySortController.setSortField(field);
    }

    function toggleSortDir() {
        return librarySortController.toggleSortDir();
    }

    function updateSortDirIcon() {
        updateSortDirIconCore({ sortDesc });
    }

    function clearSearchDebounceTimer() {
        clearTimeout(searchDebounce);
        searchDebounce = null;
    }

    function searchControllerContext() {
        return {
            getSearchQuery: () => searchQuery,
            setSearchQuery: (value) => {
                searchQuery = value;
            },
            setDeepSearchRequested: (value) => {
                deepSearchRequested = value;
            },
            getSortField: () => sortField,
            hasActiveTextSearch,
            saveSearchState,
            updateSimilaritySortOption,
            applySortState,
            saveSearchSortState,
            clearPersistedSearchState,
            restoreSortState,
            updateSearchControls,
            reloadForFilters,
            updateDateScrubber,
        };
    }

    function updateImageFlagLocal(imageId, flag) {
        updateImageFlagLocalCore(imageId, flag, {
            images: libraryImages,
            loupeStandaloneImage: loupeController?.getStandaloneImage() || null,
            loupeCurrentImage: loupeController?.getCurrentImage() || null,
            lightboxIndex: loupeController?.getLightboxIndex() ?? -1,
            updateLoupeFlagDisplay,
        });
    }

    async function setImageFlag(imageId, flag) {
        await setImageFlagCore(imageId, flag, {
            images: libraryImages,
            showToast,
            updateImageFlagLocalImpl: updateImageFlagLocal,
        });
    }

    function setCurrentLibraryFlag(flag) {
        setCurrentLibraryFlagCore(flag, {
            images: libraryImages,
            lightboxIndex: loupeController?.getLightboxIndex() ?? -1,
            loupeStandaloneImage: loupeController?.getStandaloneImage() || null,
            selectedLibraryIndex,
            setImageFlagImpl: setImageFlag,
        });
    }

    async function loadRankings(clearFirst = false) {
        if (rankingsLoading) return rankingsLoadPromise || 0;
        const promise = loadRankingsBatch(clearFirst);
        rankingsLoadPromise = promise;
        try {
            return await promise;
        } finally {
            if (rankingsLoadPromise === promise) rankingsLoadPromise = null;
        }
    }

    async function loadRankingsBatch(clearFirst = false) {
        rankingsLoading = true;
        const requestGeneration = libraryRequestGeneration;
        const requestOffset = rankingsOffset;
        const limit = currentLibraryPageSize();
        const url = buildRankingsUrl({
            queryState: currentQueryState({ sort: rankingsSort }),
            limit,
            offset: requestOffset,
            sort: rankingsSort,
        });
        try {
            const data = (requestOffset === 0 ? takeWarmCache(`library:${url}`) : null) || await fetchWarmJson(url);
            if (!data) return 0;
            if (requestGeneration !== libraryRequestGeneration) return 0;
            if (
                requestOffset === 0 &&
                data.deep_requested &&
                !data.deep_search_cached &&
                data.fallback_reason === 'deep_search_not_cached' &&
                searchQuery &&
                searchQuery !== lastDeepSearchNoticeQuery
            ) {
                lastDeepSearchNoticeQuery = searchQuery;
                showToast('Deep Search queued. Showing quick results until the 8B cache is ready.');
            }
            if (requestOffset === 0 && typeof data.total_images === 'number') {
                const visible = Number(data.visible_images ?? data.total_images ?? 0);
                const total = Number(data.total_images ?? data.total_kept ?? visible);
                compareStats = {
                    ...compareStats,
                    filtered_pool: visible,
                    filtered_pool_visible: visible,
                    filtered_pool_total: total,
                };
                updateCompareProgress();
            }
            const grid = document.getElementById('rankings-grid');
            if (clearFirst) { grid.innerHTML = ''; selectedLibraryIndex = -1; lastDateGroup = null; }
            const rowH = thumbHeight;

            // Batch DOM writes with DocumentFragment to avoid per-card reflows
            const frag = document.createDocumentFragment();
            const baseIndex = libraryImages.length;
            const appendResult = appendLibraryRankCards({
                fragment: frag,
                images: data.images,
                rankingsOffset,
                baseIndex,
                rankingsSort,
                thumbHeight: rowH,
                lastDateGroup,
                isBatchMode: () => batchController.isBatchMode(),
                isSelected: (imageId) => batchController.isSelected(imageId),
                onCardClick: handleCardClick,
            });
            lastDateGroup = appendResult.lastDateGroup;
            libraryImages.push(...data.images);
            grid.appendChild(frag);

            rankingsOffset += data.images.length;
            if (data.images.length < limit) {
                rankingsExhausted = true;
            }
            if (data.images.length > 0) {
                // Always warm neighbors — not just on first load
                scheduleLibraryNeighborWarmup();
                if (requestOffset === 0) scheduleCrossViewWarmup('library');
                if (isDateScrubberActive()) setupScrubberScrollObserver();
            }
            updateLibraryEmptyState();
            return data.images.length;
        } finally {
            if (requestGeneration === libraryRequestGeneration) rankingsLoading = false;
        }
    }

    function isDateSortActive() {
        return isDateSortValue(rankingsSort);
    }

    function isDateScrubberActive() {
        return isDateSortActive() && currentSearchMode() === 'library';
    }

    function findDateGroupHeader(group) {
        return findDateGroupHeaderCore(group);
    }

    function dateGroupOffset(group) {
        return dateGroupOffsetCore(dateGroupsData, group);
    }

    function setActiveDateScrubberGroup(group) {
        setActiveDateScrubberGroupCore(group);
    }

    function teardownDateScrubberScrollTracking() {
        teardownDateScrubberScrollTrackingCore();
    }

    function setupDateScrubberScrollTracking() {
        setupDateScrubberScrollTrackingCore({ scrollRoot: libraryScrollRoot() });
    }

    async function jumpToDateGroup(group) {
        return jumpToDateGroupCore(group, {
            isActive: isDateScrubberActive,
            findHeader: findDateGroupHeader,
            setActiveGroup: setActiveDateScrubberGroup,
            scrollToElement: scrollLibraryContainerToElement,
            getOffset: dateGroupOffset,
            resetForOffset: (offset) => {
                const gen = ++dateJumpGeneration;
                libraryRequestGeneration++;
                rankingsOffset = offset;
                rankingsExhausted = false;
                libraryImages = [];
                lastDateGroup = null;
                rankingsLoading = false;
                rankingsLoadPromise = null;
                selectedLibraryIndex = -1;
                return gen;
            },
            isCurrentJump: (gen) => gen === dateJumpGeneration,
            loadRankings,
            scrollRoot: libraryScrollRoot,
        });
    }

    async function updateDateScrubber() {
        return dateScrubberController.updateDateScrubber();
    }

    function renderDateScrubber() {
        return dateScrubberController.renderDateScrubber();
    }

    function setupScrubberScrollObserver() {
        return dateScrubberController.setupScrubberScrollObserver();
    }

    function selectLibraryCard(index, cards) {
        const selected = selectLibraryCardCore(index, cards, { scrollRoot: libraryScrollRoot() });
        if (selected !== null) selectedLibraryIndex = selected;
    }

    function deselectLibraryCard(cards) {
        selectedLibraryIndex = deselectLibraryCardCore(cards);
    }

    function findCardInDirection(cards, currentIdx, direction) {
        return findCardInDirectionCore(cards, currentIdx, direction);
    }

    loupeController = createLoupeController({
        getLibraryImages: () => libraryImages,
        getSearchQuery: () => searchQuery,
        getRankingsExhausted: () => rankingsExhausted,
        loadRankings,
        clearWarmups,
        currentWarmupGeneration,
        enqueueWarmup,
        warmImageTiers,
        preloadImageWithTimeout,
        getMediaStatus,
        getUiSettings: () => uiSettingsLoader.getSettings(),
        imageAspectRatio,
        eloToStars,
    });

    function openLightbox(img) {
        return loupeController.openLightbox(img);
    }

    function openStandaloneLightbox(img) {
        return loupeController.openStandaloneLightbox(img);
    }

    async function getMediaStatus(imageId, { force = false } = {}) {
        return mediaStatusController.getMediaStatus(imageId, { force });
    }

    function primeMediaStatuses(imageIds) {
        mediaStatusController.primeMediaStatuses(imageIds);
    }

    function loupeTierUrl(tier, imageId, cachedOnly = false) {
        return loupeTierUrlCore(tier, imageId, cachedOnly);
    }

    function updateLoupeFlagDisplay(flag) {
        return loupeController.updateLoupeFlagDisplay(flag);
    }

    function renderLoupeStatusLine(flag) {
        return loupeController?.renderLoupeStatusLine(flag);
    }

    function focusLoupe() {
        return loupeController.focusLoupe();
    }

    function loupeFocusableElements(loupe) {
        return loupeController.loupeFocusableElements(loupe);
    }

    function trapLoupeFocus(e) {
        return loupeController.trapLoupeFocus(e);
    }

    function preloadImageWithTimeout(url, priority, timeoutMs) {
        return withTimeout(preloadImage(url, priority), timeoutMs);
    }

    function initLoupeInteraction() {
        return loupeController.initLoupeInteraction();
    }

    async function ensureLibraryImageIndex(index) {
        return loupeController.ensureLibraryImageIndex(index);
    }

    function lightboxNext() {
        return loupeController.lightboxNext();
    }

    function lightboxPrev() {
        return loupeController.lightboxPrev();
    }

    function closeLightbox() {
        return loupeController.closeLightbox();
    }

    const setThumbSize = createThumbnailSizeHandler({
        getMosaicSize: () => mosaicSize,
        setMosaicSize: (value) => { mosaicSize = value; },
        mosaicSizeFromThumbHeight,
        clearWarmups,
        loadMosaicBatch,
        setThumbHeight: (value) => { thumbHeight = value; },
    });

    const libraryFilterController = createLibraryFilterController({
        emptyFilters: EMPTY_FILTERS,
        getFilters: () => filters,
        setFilters: (nextFilters) => { filters = nextFilters; },
        clearWarmups,
        resetLibraryResults,
        loadRankings,
        isDateSortActive,
        updateDateScrubber,
        currentLibraryView,
        loadMap,
        getCompareMode: () => compareMode,
        loadMosaicBatch,
        resetComparePairs: () => {
            comparePairs = [];
            compareIndex = 0;
        },
        fetchComparePairs,
        showComparePair,
        updateMetadataFilterButton,
        saveFilters,
        activeMetadataFilterCount,
    });

    function reloadForFilters() {
        return libraryFilterController.reloadForFilters();
    }

    function setFilter(key, value) {
        return libraryFilterController.setFilter(key, value);
    }

    function clearLibraryFilters() {
        return libraryFilterController.clearLibraryFilters();
    }

    function toggleFilter(key, value, btn) {
        return libraryFilterController.toggleFilter(key, value, btn);
    }

    function toggleStar(level) {
        return libraryFilterController.toggleStar(level);
    }

    function loadFolderList() {
        return libraryFilterController.loadFolderList();
    }

    function loadFilterOptions() {
        return libraryFilterController.loadFilterOptions();
    }

    function scheduleFilterOptionsLoad() {
        return libraryFilterController.scheduleFilterOptionsLoad();
    }

    function toggleMetadataFilters() {
        return libraryFilterController.toggleMetadataFilters();
    }

    function initStarHover() {
        return libraryFilterController.initStarHover();
    }

    const findSimilar = createFindSimilarAction({
        getLightboxIndex: () => loupeController?.getLightboxIndex() ?? -1,
        getLibraryImages: () => libraryImages,
        setLibraryImages: (images) => { libraryImages = images; },
        setRankingsOffset: (offset) => { rankingsOffset = offset; },
        setRankingsExhausted: (exhausted) => { rankingsExhausted = exhausted; },
        getThumbHeight: () => thumbHeight,
        getCompareStats: () => compareStats,
        setCompareStats: (stats) => { compareStats = stats; },
        setSearchQuery: (query) => { searchQuery = query; },
        setDeepSearchRequested: (requested) => { deepSearchRequested = requested; },
        bumpLibraryRequestGeneration: () => ++libraryRequestGeneration,
        getLibraryRequestGeneration: () => libraryRequestGeneration,
        closeLightbox,
        clearWarmups,
        clearPersistedSearchState,
        updateDateScrubber,
        clearBatchSelection,
        updateCompareProgress,
        openLightbox,
    });


    // ==================== BATCH SELECTION ====================

    const batchController = createBatchSelectionController({
        getImages: () => libraryImages,
        openImage: openLightbox,
        showToast,
        updateImageFlagLocal,
    });

    function toggleBatchMode() {
        batchController.toggleBatchMode();
    }

    function clearBatchSelection() {
        batchController.clearBatchSelection();
    }

    function handleCardClick(e, img, card, index) {
        batchController.handleCardClick(e, img, card, index);
    }

    async function batchFlag(flag) {
        await batchController.batchFlag(flag);
    }

    function batchExport(format) {
        batchController.batchExport(format);
    }

    function exportRankings(format) {
        exportRankingsCore(format, {
            queryState: currentQueryState({ sort: rankingsSort }),
            sort: rankingsSort,
        });
    }

    // ==================== MAP VIEW ====================

    const mapController = createLibraryMapController({
        clearWarmups,
        clearBatchSelection,
        currentFilterState,
        currentQueryState,
        getLibraryImages: () => libraryImages,
        libraryScrollRoot,
        openLightbox,
        openStandaloneLightbox,
        showToast,
        syncDateScrubberVisibility,
    });

    function currentLibraryView() {
        return mapController.getLibraryView();
    }

    function setLibraryView(mode) {
        return mapController.setLibraryView(mode);
    }

    function loadMap() {
        return mapController.loadMap();
    }

    function openLightboxById(id) {
        return mapController.openLightboxById(id);
    }

    // ==================== PEOPLE ====================

    const peopleApi = createPeopleApi({ showToast });
    const {
        initPeople,
        labelPerson,
        mergePeople,
        rejectPeopleMerge,
        ignorePerson,
        filterLibraryByPerson,
        useFallbackThumb,
        rememberPeopleLabelDraft,
    } = peopleApi;

    // ==================== SETTINGS ====================

    const settingsPage = createSettingsPageController({
        showToast,
        showConfirmModal,
        formatBytes,
        initVisibilityRefresh,
        initBottomBarMeasurement,
    });
    const {
        initSettings,
        refreshSettingsMetaIfActive,
        saveSettings,
        applyRecommendedCache,
        resetSettings,
        clearThumbnailCache,
        startCachePregeneration,
        stopCachePregeneration,
        installAIModel,
        startScan: startSettingsScan,
        addCatalogSource,
        rescanCatalogSource,
        openRemoveSourceDialog,
        closeRemoveSourceDialog,
        removeCatalogSource,
        chooseCatalogFolder,
        browseDirectory,
        toggleDirectoryBrowser,
        browseDirectoryParent,
        selectBrowsedDirectory,
        useBrowsedDirectory,
        pauseEmbeddings,
        resumeEmbeddings,
        pauseAllWork,
        resumeAllWork,
    } = settingsPage;
    const homeScan = createHomeScanController();
    const scanEntrypoint = createScanEntrypoint({
        documentImpl: document,
        homeScan,
        settingsScan: startSettingsScan,
    });

    function startScan() {
        return scanEntrypoint.startScan();
    }

    // ==================== UTILITIES ====================

    const preloadImage = createImagePreloader({ limit: 240, concurrency: 8 });

    function showShortcuts() {
        showShortcutsUi();
    }

    function hideShortcuts() {
        hideShortcutsUi();
    }

    initShortcutOverlay();

    // ==================== PUBLIC API ====================

    return {
        initCompare,
        initLibrary,
        initRankings,
        initPeople,
        initSettings,
        showShortcuts,
        hideShortcuts,
        showConfirmModal,
        hideConfirmModal,
        scrollToTop,
        clearSearch,
        runDeepSearch,
        setCompareMode,
        setRankingsSort,
        setSortField,
        toggleSortDir,
        exportRankings,
        openLightbox,
        openStandaloneLightbox,
        closeLightbox,
        lightboxNext,
        lightboxPrev,
        setThumbSize,
        setFilter,
        toggleMetadataFilters,
        toggleFilter,
        toggleStar,
        findSimilar,
        toggleBatchMode,
        clearBatchSelection,
        batchExport,
        batchFlag,
        mosaicShuffle,
        setMosaicStrategy,
        toggleAIPanel,
        saveSettings,
        applyRecommendedCache,
        resetSettings,
        clearThumbnailCache,
        startCachePregeneration,
        stopCachePregeneration,
        installAIModel,
        startScan,
        addCatalogSource,
        rescanCatalogSource,
        openRemoveSourceDialog,
        closeRemoveSourceDialog,
        removeCatalogSource,
        chooseCatalogFolder,
        toggleDirectoryBrowser,
        browseDirectory,
        browseDirectoryParent,
        selectBrowsedDirectory,
        useBrowsedDirectory,
        pauseEmbeddings,
        resumeEmbeddings,
        pauseAllWork,
        resumeAllWork,
        setLibraryView,
        openLightboxById,
        labelPerson,
        mergePeople,
        rejectPeopleMerge,
        ignorePerson,
        filterLibraryByPerson,
        useFallbackThumb,
        rememberPeopleLabelDraft,
    };
})();

export default legacyPhotoArchive;
