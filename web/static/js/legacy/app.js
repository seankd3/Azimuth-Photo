import {
    formatBytes,
    hideConfirmModal as hideConfirmModalUi,
    hideShortcuts as hideShortcutsUi,
    initShortcutOverlay,
    showConfirmModal as showConfirmModalUi,
    showShortcuts as showShortcutsUi,
    showToast as showToastUi,
} from '../ui.js';
import { fetchJson } from '../api.js';
import { toggleAIPanel } from '../ai/status.js';
import { createHomeScanController } from '../catalog/home_scan.js';
import { createScanEntrypoint } from '../catalog/scan_entrypoint.js';
import {
    COMPARE_NEIGHBOR_PAIRS,
    MOSAIC_NEIGHBOR_LIMIT,
} from '../compare/query.js';
import { createLibraryFilterController } from '../library/filter_controller.js';
import {
    createImagePreloader,
    createWarmupManager,
    loadImageProbe,
    withTimeout,
} from '../warmup.js';
import {
    DEFAULT_CROSS_VIEW_WARM_DELAY_MS,
    createNeighborWarmupController,
} from '../warmup_neighbors.js';
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
    showCompareEmpty as showCompareEmptyCore,
} from '../compare/view.js';
import { createCompareModeController } from '../compare/mode_controller.js';
import {
    INITIAL_RANKINGS_PAGE_SIZE,
    LIBRARY_NEIGHBOR_LIMIT,
    RANKINGS_PAGE_SIZE,
    currentLibraryPageSize as currentLibraryPageSizeCore,
} from '../library/pagination.js';
import { createLibrarySortController } from '../library/sort_controller.js';
import { eloToStars } from '../library/display.js';
import { appendLibraryRankCards } from '../library/rank_cards.js';
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
import { createLegacyBatchBridge } from './batch_bridge.js';
import { createLegacyCompareDisplayBridge } from './compare_display_bridge.js';
import { createLegacyCompareFlowBridge } from './compare_flow_bridge.js';
import { createLegacyDateScrubberBridge } from './date_scrubber_bridge.js';
import { createLegacyExportBridge } from './export_bridge.js';
import { createLegacyFilterQueryBridge } from './filter_query_bridge.js';
import { createLegacyFlagBridge } from './flag_bridge.js';
import {
    SCROLL_OFFSET_STORAGE_KEY,
    SCROLL_POS_STORAGE_KEY,
    createLegacyLibraryShellBridge,
} from './library_shell_bridge.js';
import { createLegacyLibraryInitBridge } from './library_init_bridge.js';
import { createLegacyLoupeBridge } from './loupe_bridge.js';
import { createLegacyPublicApi } from './public_api.js';
import { createLegacySearchActionBridge } from './search_action_bridge.js';
import { createLegacySearchSortBridge } from './search_sort_bridge.js';
import { createLegacyUiRuntimeBridge } from './ui_runtime_bridge.js';

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
    let selectedLibraryIndex = -1;
    let selectedMosaicIndex = -1;
    const loupeBridge = createLegacyLoupeBridge({
        getLibraryImages: () => libraryImages,
        getSearchQuery: () => searchQuery,
        getRankingsExhausted: () => rankingsExhausted,
        loadRankings,
        clearWarmups: () => clearWarmups(),
        currentWarmupGeneration,
        enqueueWarmup: (...args) => enqueueWarmup(...args),
        warmImageTiers: (...args) => warmImageTiers(...args),
        preloadImage: (...args) => preloadImage(...args),
        withTimeoutImpl: withTimeout,
        getUiSettings: () => uiRuntimeBridge.getUiSettings(),
        imageAspectRatio,
        eloToStars,
    });
    const {
        closeLightbox,
        ensureLibraryImageIndex,
        focusLoupe,
        getMediaStatus,
        handleWarmTiersApplied,
        initLoupeInteraction,
        lightboxNext,
        lightboxPrev,
        loupeFocusableElements,
        loupeTierUrl,
        openLightbox,
        openStandaloneLightbox,
        preloadImageWithTimeout,
        primeMediaStatuses,
        renderLoupeStatusLine,
        trapLoupeFocus,
        updateLoupeFlagDisplay,
    } = loupeBridge;
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

    const uiRuntimeBridge = createLegacyUiRuntimeBridge({
        documentImpl: document,
        fetchJsonImpl: fetchJson,
        onBottomBarMeasured: () => {
            if (document.getElementById('mosaic-grid')) scheduleMosaicRender();
        },
        refreshSettingsMetaIfActive: () => refreshSettingsMetaIfActive(),
        onUiSettingsLoaded: () => renderLoupeStatusLine(),
    });
    const {
        initBottomBarMeasurement,
        initVisibilityRefresh,
        loadUiSettings,
        startAIStatusPolling,
        updateBottomBarHeightVar,
    } = uiRuntimeBridge;

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

    const compareDisplayBridge = createLegacyCompareDisplayBridge({
        displayedTiers: compareDisplayedTier,
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
        getMediaStatus,
        loadImageProbe,
        loupeTierUrl,
    });

    function showComparePair() {
        return compareDisplayBridge.showComparePair();
    }

    function isCurrentCompareImage(token) {
        return compareDisplayBridge.isCurrentCompareImage(token);
    }

    function renderCompareImage(img, imgEl, side, token) {
        return compareDisplayBridge.renderCompareImage(img, imgEl, side, token);
    }

    async function upgradeCompareImage(img, imgEl, side, token) {
        return compareDisplayBridge.upgradeCompareImage(img, imgEl, side, token);
    }

    async function adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs) {
        return compareDisplayBridge.adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs);
    }

    async function fetchComparePairs() {
        return compareDisplayBridge.fetchComparePairs();
    }

    const compareFlowBridge = createLegacyCompareFlowBridge({
        fetchImpl: fetch,
        getCompareStats: () => compareStats,
        getMosaicImages: () => mosaicImages,
        setMosaicPropagationCounts: (counts) => {
            mosaicPropagationCounts = counts;
        },
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
    });

    function bumpRankingSignals(signalDelta, directDelta = 0) {
        return compareFlowBridge.bumpRankingSignals(signalDelta, directDelta);
    }

    function updateCompareProgress() {
        return compareFlowBridge.updateCompareProgress();
    }

    function renderCoverageBar(stats = compareStats) {
        return compareFlowBridge.renderCoverageBar(stats);
    }

    function mergeCoverageStats(stats) {
        return compareFlowBridge.mergeCoverageStats(stats);
    }

    function updateCoverageBar() {
        return compareFlowBridge.updateCoverageBar();
    }

    function rollUpCounter(el, from, to) {
        return compareFlowBridge.rollUpCounter(el, from, to);
    }

    function precomputePropagation() {
        return compareFlowBridge.precomputePropagation();
    }

    function fetchPropagationCount(directCount = 0) {
        return compareFlowBridge.fetchPropagationCount(directCount);
    }

    function showPropagationBadge(count) {
        return compareFlowBridge.showPropagationBadge(count);
    }

    function submitComparison(side) {
        return compareFlowBridge.submitComparison(side);
    }

    async function undoComparison() {
        return compareFlowBridge.undoComparison();
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
    let rankingsLoading = false;
    let rankingsLoadPromise = null;
    let rankingsExhausted = false;
    let libraryRequestGeneration = 0;
    let pendingScrollRestoreOffset = 0;
    let thumbHeight = 220;
    let libraryImages = [];
    const filterQueryBridge = createLegacyFilterQueryBridge({
        documentImpl: document,
        storage: sessionStorage,
        locationImpl: window.location,
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
    const {
        EMPTY_FILTERS,
        activeMetadataFilterCount,
        applyFilterUiState,
        buildFilterNeighborStates,
        buildRankingsUrl,
        currentFilterState,
        currentQueryState,
        currentSearchMode,
        filterParams,
        filterQueryString,
        getFilters,
        hasActiveLibraryFilters,
        saveFilters,
        setFilters,
        syncLibraryUrlState,
        updateMetadataFilterButton,
    } = filterQueryBridge;

    function buildCompareUrl(...args) {
        return filterQueryBridge.buildCompareUrl(...args);
    }

    function buildMosaicUrl(...args) {
        return filterQueryBridge.buildMosaicUrl(...args);
    }

    function restoreFilters() {
        return filterQueryBridge.restoreFilters();
    }

    const libraryShellBridge = createLegacyLibraryShellBridge({
        getImages: () => libraryImages,
        getRankingsOffset: () => rankingsOffset,
        setRankingsOffset: (value) => { rankingsOffset = value; },
        setPendingScrollRestoreOffset: (value) => { pendingScrollRestoreOffset = value; },
        setSelectedLibraryIndex: (value) => { selectedLibraryIndex = value; },
        getSearchQuery: () => searchQuery,
        hasActiveTextSearch,
        hasActiveLibraryFilters,
        clearSearch,
        clearLibraryFilters,
    });
    const {
        deselectLibraryCard,
        findCardInDirection,
        hideLibraryEmptyState,
        libraryScrollRoot,
        restoreScrollPosition,
        saveScrollPosition,
        scrollLibraryContainerToElement,
        scrollToTop,
        selectLibraryCard,
        updateBackToTopButton,
        updateLibraryEmptyState,
    } = libraryShellBridge;

    function currentLibraryPageSize() {
        return currentLibraryPageSizeCore({ rankingsOffset, pendingScrollRestoreOffset });
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

    const dateScrubberBridge = createLegacyDateScrubberBridge({
        documentImpl: document,
        windowImpl: window,
        fetchImpl: fetch,
        currentLibraryView,
        currentSearchMode,
        currentQueryString: () => filterQueryString(currentQueryState()),
        getRankingsSort: () => rankingsSort,
        getDateGroups: () => dateGroupsData,
        setGroups: (groups) => { dateGroupsData = groups; },
        nextDateScrubberGeneration: () => ++dateScrubberGeneration,
        isCurrentDateScrubberGeneration: (gen) => gen === dateScrubberGeneration,
        libraryScrollRoot,
        scrollLibraryContainerToElement,
        resetForDateOffset: (offset) => {
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
        isCurrentDateJump: (gen) => gen === dateJumpGeneration,
        loadRankings,
    });
    const {
        dateGroupOffset,
        findDateGroupHeader,
        isDateScrubberActive,
        isDateSortActive,
        jumpToDateGroup,
        renderDateScrubber,
        setActiveDateScrubberGroup,
        setupDateScrubberScrollTracking,
        setupScrubberScrollObserver,
        syncDateScrubberVisibility,
        teardownDateScrubberScrollTracking,
        updateDateScrubber,
    } = dateScrubberBridge;

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
        crossViewWarmDelayMs: DEFAULT_CROSS_VIEW_WARM_DELAY_MS,
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

    const searchSortBridge = createLegacySearchSortBridge({
        documentImpl: document,
        storage: sessionStorage,
        locationImpl: window.location,
        getSearchQuery: () => searchQuery,
        setSearchQuery: (value) => {
            searchQuery = value;
        },
        getDeepSearchRequested: () => deepSearchRequested,
        setDeepSearchRequested: (value) => {
            deepSearchRequested = value;
        },
        getSortField: () => sortField,
        setSortField: (value) => {
            sortField = value;
        },
        getSortDesc: () => sortDesc,
        setSortDesc: (value) => {
            sortDesc = value;
        },
        setRankingsSort: (value) => {
            rankingsSort = value;
        },
        hasActiveTextSearch,
        syncLibraryUrlState,
        afterCompareSearchIndicator: updateBottomBarHeightVar,
    });
    const {
        applySortState,
        clearPersistedSearchState,
        restoreSearchSortState,
        restoreSortState,
        saveSearchSortState,
        saveSearchState,
        saveSortState,
        syncSortControls,
        updateCompareSearchIndicator,
        updateSearchControls,
        updateSimilaritySortOption,
        updateSortDirIcon,
    } = searchSortBridge;

    function restoreSearchState() {
        return searchSortBridge.restoreSearchState();
    }

    function rankingQueryString({
        queryState = currentQueryState(),
        limit = LIBRARY_NEIGHBOR_LIMIT,
        offset = 0,
        sort = queryState.sort,
    } = {}) {
        return filterQueryBridge.rankingQueryString({ queryState, limit, offset, sort });
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
    const {
        setRankingsSort,
        setSortField,
        toggleSortDir,
    } = librarySortController;

    const searchActionBridge = createLegacySearchActionBridge({
        documentImpl: document,
        getSearchQuery: () => searchQuery,
        setSearchQuery: (value) => {
            searchQuery = value;
        },
        getDeepSearchRequested: () => deepSearchRequested,
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
    });

    function initSearchInputControls() {
        return searchActionBridge.initSearchInputControls();
    }

    function clearSearch() {
        return searchActionBridge.clearSearch();
    }

    function runDeepSearch() {
        return searchActionBridge.runDeepSearch();
    }

    const flagBridge = createLegacyFlagBridge({
        getImages: () => libraryImages,
        getLightboxIndex: () => loupeBridge.getLightboxIndex(),
        getSelectedLibraryIndex: () => selectedLibraryIndex,
        getStandaloneImage: () => loupeBridge.getStandaloneImage(),
        getCurrentImage: () => loupeBridge.getCurrentImage(),
        showToast,
        updateLoupeFlagDisplay,
    });
    const {
        setCurrentLibraryFlag,
        updateImageFlagLocal,
    } = flagBridge;

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
                isBatchMode: () => batchBridge.isBatchMode(),
                isSelected: (imageId) => batchBridge.isSelected(imageId),
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

    loupeBridge.initController();

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
        getFilters,
        setFilters,
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
        getLightboxIndex: () => loupeBridge.getLightboxIndex(),
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

    const batchBridge = createLegacyBatchBridge({
        getImages: () => libraryImages,
        openImage: openLightbox,
        showToast,
        updateImageFlagLocal,
    });

    function toggleBatchMode() {
        batchBridge.toggleBatchMode();
    }

    function clearBatchSelection() {
        batchBridge.clearBatchSelection();
    }

    function handleCardClick(e, img, card, index) {
        batchBridge.handleCardClick(e, img, card, index);
    }

    async function batchFlag(flag) {
        await batchBridge.batchFlag(flag);
    }

    function batchExport(format) {
        batchBridge.batchExport(format);
    }

    const exportBridge = createLegacyExportBridge({
        getQueryState: () => currentQueryState({ sort: rankingsSort }),
        getSort: () => rankingsSort,
    });
    const { exportRankings } = exportBridge;

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

    const libraryInitBridge = createLegacyLibraryInitBridge({
        documentImpl: document,
        windowImpl: window,
        sessionStorageImpl: sessionStorage,
        scrollOffsetStorageKey: SCROLL_OFFSET_STORAGE_KEY,
        initBottomBarMeasurement,
        startAIStatusPolling,
        resetLibraryResults,
        restoreFilters,
        restoreSortState,
        restoreSearchState,
        setPendingScrollRestoreOffset: (value) => { pendingScrollRestoreOffset = value; },
        loadUiSettings,
        loadRankings,
        setCompareStats: (stats) => { compareStats = stats; },
        updateCompareProgress,
        loadFolderList,
        scheduleFilterOptionsLoad,
        initStarHover,
        restoreScrollPosition,
        currentLibraryView,
        getRankingsLoading: () => rankingsLoading,
        getRankingsExhausted: () => rankingsExhausted,
        libraryScrollRoot,
        updateBackToTopButton,
        getSelectedLibraryIndex: () => selectedLibraryIndex,
        getLibraryImages: () => libraryImages,
        hasBatchSelection: () => batchBridge.hasSelection(),
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
    });
    const { initLibrary, initRankings } = libraryInitBridge;

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

    return createLegacyPublicApi({
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
    });
})();

export default legacyPhotoArchive;
