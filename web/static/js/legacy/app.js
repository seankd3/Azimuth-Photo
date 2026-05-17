import {
    escapeHtml,
    formatBytes,
    hideConfirmModal as hideConfirmModalUi,
    hideShortcuts as hideShortcutsUi,
    initBottomBarMeasurement as initBottomBarMeasurementUi,
    initShortcutOverlay,
    initVisibilityRefresh as initVisibilityRefreshUi,
    jsString,
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
import {
    buildFilterNeighborStates as buildFilterNeighborStatesCore,
    EMPTY_FILTERS as EMPTY_FILTERS_CORE,
    filterParams as filterParamsCore,
    filterQueryString as filterQueryStringCore,
    normalizeFilterState as normalizeFilterStateCore,
    syncLibraryUrlState as syncLibraryUrlStateCore,
} from '../query_state.js';
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
import { createMediaStatusClient } from '../media_status.js';
import {
    createImagePreloader,
    createWarmupManager,
    loadImageProbe,
    withTimeout,
} from '../warmup.js';
import { createNeighborWarmupController } from '../warmup_neighbors.js';
import { createThumbnailSizeHandler } from '../thumbnail_size.js';
import {
    buildCompareUrl as buildCompareUrlCore,
    buildMosaicUrl as buildMosaicUrlCore,
} from '../compare/query.js';
import {
    mosaicSizeFromThumbHeight,
    mosaicThumbHeightForSize,
} from '../compare/mosaic.js';
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
    LOUPE_BLANK_SRC,
    LOUPE_TIER_RANKS,
    LOUPE_TIER_TIMEOUTS,
    loupeTierUrl as loupeTierUrlCore,
} from '../loupe/tiers.js';
import {
    cancelLoupeProbes as cancelLoupeProbesCore,
    loadLoupeTier as loadLoupeTierCore,
    runLoupeProgressiveLoad as runLoupeProgressiveLoadCore,
} from '../loupe/loading.js';
import {
    clearLoupeTierLoading as clearLoupeTierLoadingCore,
    renderLoupeStatusLine as renderLoupeStatusLineCore,
    setLoupeTierLoading as setLoupeTierLoadingCore,
} from '../loupe/status.js';
import {
    renderLoupeMetadata as renderLoupeMetadataCore,
    renderLoupeMetadataOverlay,
} from '../loupe/metadata.js';
import {
    loupeHotSetTierIds,
    loupeNeighborOffsets,
} from '../loupe/navigation.js';
import {
    buildFilmstrip as buildFilmstripCore,
    clearFilmstrip as clearFilmstripCore,
    updateFilmstripActive as updateFilmstripActiveCore,
    updateFilmstripCounter as updateFilmstripCounterCore,
} from '../loupe/filmstrip.js';
import {
    focusLoupe as focusLoupeCore,
    loupeFocusableElements as loupeFocusableElementsCore,
    trapLoupeFocus as trapLoupeFocusCore,
} from '../loupe/focus.js';
import {
    applyLoupeImageSize as applyLoupeImageSizeCore,
    applyLoupeTransform as applyLoupeTransformCore,
    clampLoupePan as clampLoupePanCore,
    loupeComputeFitScale as loupeComputeFitScaleCore,
    updateLoupeZoomIndicator as updateLoupeZoomIndicatorCore,
} from '../loupe/zoom.js';
import { initLoupeInteraction as initLoupeInteractionCore } from '../loupe/interaction.js';
import { rankingQueryString as rankingQueryStringCore } from '../library/query.js';
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
import {
    eloToStars,
    flagBadge,
    flagClass,
    formatDateGroup,
    getConfidenceClass,
    getTierClass,
    libraryCardInfoLine as libraryCardInfoLineCore,
} from '../library/display.js';
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
    teardownDateScrubberScrollTracking as teardownDateScrubberScrollTrackingCore,
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
    imageMetadataTitle,
} from '../media_metadata.js';
import {
    hasActiveTextSearch as hasActiveTextSearchCore,
    searchModeForQuery,
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
    const FILMSTRIP_WINDOW_RADIUS = 55;
    const mediaStatusClient = createMediaStatusClient({ maxAgeMs: 15000 });
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

    function invalidateMediaStatusesForPayload(payload) {
        mediaStatusClient.invalidateForPayload(payload);
    }

    function handleWarmTiersApplied(payload) {
        invalidateMediaStatusesForPayload(payload);
        const currentId = Number(loupeCurrentImage?.id || 0);
        if (!currentId) return;
        const includesCurrent = Object.values(payload).some((ids) => (ids || []).includes(currentId));
        if (includesCurrent) refreshLoupeMediaStatus(loupeCurrentImage, loupeImageToken, { force: true });
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

    async function initCompare() {
        initBottomBarMeasurement();
        startAIStatusPolling(750, { immediate: true });
        document.addEventListener('keydown', handleCompareKey);
        window.addEventListener('resize', scheduleMosaicRender);
        document.getElementById('compare-left').addEventListener('click', () => submitComparison('left'));
        document.getElementById('compare-right').addEventListener('click', () => submitComparison('right'));
        // Set slider to match default mosaic size (12 images → slider ~168)
        const slider = document.getElementById('thumb-size');
        if (slider) {
            slider.value = mosaicThumbHeightForSize(mosaicSize);
        }
        restoreFilters();
        restoreSearchState();
        initSearchInputControls();
        setCompareMode('mosaic');
        setTimeout(() => {
            loadFolderList();
            scheduleFilterOptionsLoad();
        }, 500);
        initStarHover();
    }

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
    let lightboxIndex = -1;
    let loupeStandaloneImage = null;
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

    function syncLibraryUrlState() {
        syncLibraryUrlStateCore({ filters: currentFilterState(), sortField, sortDesc });
    }

    function filterParams(state = currentQueryState()) {
        return filterParamsCore(state);
    }

    function filterQueryString(state = currentQueryState()) {
        return filterQueryStringCore(state);
    }

    function buildFilterNeighborStates(baseState = currentFilterState()) {
        return buildFilterNeighborStatesCore(baseState);
    }

    function buildRankingsUrl({
        queryState = currentQueryState(),
        sort = queryState.sort || rankingsSort,
        filterState = null,
        limit = LIBRARY_NEIGHBOR_LIMIT,
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
        strategy = mosaicStrategy,
        queryState = currentQueryState(),
        filterState = null,
        gridElo = mosaicGridElo(),
        n = MOSAIC_NEIGHBOR_LIMIT,
        exclude = '',
    } = {}) {
        const state = currentQueryState({
            ...queryState,
            filters: filterState || queryState.filters,
        });
        return buildMosaicUrlCore({ strategy, queryState: state, gridElo, n, exclude });
    }

    function buildCompareUrl(mode, n = COMPARE_NEIGHBOR_PAIRS, queryState = currentQueryState()) {
        const state = currentQueryState(queryState);
        return buildCompareUrlCore({ mode, n, queryState: state });
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
        const scrubber = document.getElementById('date-scrubber');
        const active = Boolean(scrubber) && currentLibraryView() !== 'map' && isDateScrubberActive();
        document.body.classList.toggle('date-scrubber-active', active);
        if (scrubber) scrubber.classList.toggle('hidden', !active);
    }

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
        return searchModeForQuery(searchQuery);
    }

    function currentQueryState(overrides = {}) {
        const field = overrides.sortField || sortField;
        const desc = overrides.sortDesc ?? sortDesc;
        const mode = overrides.searchMode || currentSearchMode();
        const query = overrides.searchQuery ?? (mode === 'search' ? searchQuery : '');
        return {
            filters: normalizeFilterState(overrides.filters || overrides.filterState || filters),
            sortField: field,
            sortDesc: Boolean(desc),
            sort: overrides.sort || sortValueForState(field, desc),
            searchMode: mode,
            searchQuery: query,
            deepSearch: Boolean(overrides.deepSearch ?? (mode === 'search' && deepSearchRequested)),
        };
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
        const state = currentQueryState({ ...queryState, sort });
        return rankingQueryStringCore({ queryState: state, limit, offset, sort });
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
            loupeStandaloneImage,
            loupeCurrentImage,
            lightboxIndex,
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
            lightboxIndex,
            loupeStandaloneImage,
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
            const showRank = (rankingsSort === 'elo' || rankingsSort === 'elo_asc');
            const isDateSort = (rankingsSort === 'date_taken' || rankingsSort === 'date_taken_asc');
            const rowH = thumbHeight;

            // Batch DOM writes with DocumentFragment to avoid per-card reflows
            const frag = document.createDocumentFragment();
            const baseIndex = libraryImages.length;
            for (let i = 0; i < data.images.length; i++) {
                const img = data.images[i];
                const rank = rankingsOffset + i + 1;
                const ar = img.aspect_ratio || 1.5;
                const tier = getTierClass(img.elo, img.comparisons);
                const conf = img.comparisons > 0 ? getConfidenceClass(img.comparisons) : '';

                // Insert date group header when group changes
                if (isDateSort) {
                    const group = img.date_group || '';
                    if (group !== lastDateGroup) {
                        lastDateGroup = group;
                        const header = document.createElement('div');
                        header.className = 'date-group-header';
                        header.dataset.dateGroup = group;
                        header.textContent = group ? formatDateGroup(group) : 'No Date';
                        frag.appendChild(header);
                    }
                }

                const card = document.createElement('div');
                card.className = 'rank-card skeleton-cell' + (tier ? ' ' + tier : '') + (flagClass(img.flag) ? ' ' + flagClass(img.flag) : '');
                if (batchController.isBatchMode()) card.classList.add('selectable');
                if (batchController.isSelected(img.id)) card.classList.add('selected');
                card.dataset.imageId = img.id;
                card.dataset.ar = ar;
                card.style.height = rowH + 'px';
                card.style.flexGrow = ar;
                card.style.flexBasis = (rowH * ar) + 'px';
                card.title = imageMetadataTitle(img);
                card.onclick = (e) => handleCardClick(e, img, card, baseIndex + i);

                const confDot = conf ? `<div class="rank-confidence ${conf}"></div>` : '';
                const infoLine = libraryCardInfoLine(img, rank, showRank);
                const eagerThumb = requestOffset === 0 && i < 12;
                const loadingAttrs = eagerThumb ? 'loading="eager" fetchpriority="high"' : 'loading="lazy"';

                card.innerHTML = `
                    <img src="${escapeHtml(img.thumb_url)}" alt="${escapeHtml(img.filename)}" ${loadingAttrs} onload="this.classList.add('loaded'); this.parentElement.classList.remove('skeleton-cell')">
                    <div class="select-check">✓</div>
                    ${confDot}
                    ${flagBadge(img.flag)}
                    <div class="rank-card-info">${infoLine}</div>
                `;
                frag.appendChild(card);
                libraryImages.push(img);
            }
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

    function libraryCardInfoLine(img, rank, showRank) {
        return libraryCardInfoLineCore(img, rank, showRank, rankingsSort);
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
        const existing = document.getElementById('date-scrubber');
        if (!isDateScrubberActive()) {
            if (existing) existing.remove();
            if (window._scrubberObserver) window._scrubberObserver.disconnect();
            teardownDateScrubberScrollTracking();
            syncDateScrubberVisibility();
            return;
        }
        const gen = ++dateScrubberGeneration;
        const query = filterQueryString(currentQueryState());
        const url = `/api/date-groups${query ? `?${query}` : ''}`;
        try {
            const data = await fetch(url).then(r => r.json());
            if (gen !== dateScrubberGeneration) return;
            dateGroupsData = data.groups || [];
            if (rankingsSort === 'date_taken_asc') dateGroupsData.reverse();
            renderDateScrubber();
        } catch {
            // silently fail
        }
    }

    function renderDateScrubber() {
        renderDateScrubberCore(dateGroupsData, {
            onJump: jumpToDateGroup,
            setupScrollObserver: setupScrubberScrollObserver,
            syncVisibility: syncDateScrubberVisibility,
            teardownScrollTracking: teardownDateScrubberScrollTracking,
        });
    }

    function setupScrubberScrollObserver() {
        if (window._scrubberObserver) window._scrubberObserver.disconnect();
        setupDateScrubberScrollTracking();
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

    function openLightbox(img) {
        lightboxIndex = libraryImages.findIndex(i => i.id === img.id);
        if (lightboxIndex < 0) {
            openStandaloneLightbox(img);
            return;
        }
        loupeStandaloneImage = null;
        updateFilmstripCounter();
        buildFilmstrip();
        showLoupeImage(libraryImages[lightboxIndex], 0);
    }

    function openStandaloneLightbox(img) {
        loupeStandaloneImage = img;
        lightboxIndex = -1;
        clearFilmstrip();
        showLoupeImage(img, 0);
    }

    function clearFilmstrip() {
        clearFilmstripCore({ images: libraryImages, lightboxIndex });
    }

    function updateFilmstripCounter() {
        updateFilmstripCounterCore({ images: libraryImages, lightboxIndex });
    }

    function buildFilmstrip() {
        buildFilmstripCore({
            images: libraryImages,
            lightboxIndex,
            windowRadius: FILMSTRIP_WINDOW_RADIUS,
            onSelect: (index, direction) => {
                lightboxIndex = index;
                updateFilmstripCounter();
                showLoupeImage(libraryImages[index], direction);
            },
        });
    }

    function updateFilmstripActive() {
        updateFilmstripActiveCore({
            images: libraryImages,
            lightboxIndex,
            windowRadius: FILMSTRIP_WINDOW_RADIUS,
            onSelect: (index, direction) => {
                lightboxIndex = index;
                updateFilmstripCounter();
                showLoupeImage(libraryImages[index], direction);
            },
        });
    }

    // Loupe zoom/pan state
    let loupeScale = 1;
    let loupeFitScale = 1;
    let loupePanX = 0;
    let loupePanY = 0;
    let loupeNatW = 0;
    let loupeNatH = 0;
    let loupeIsFit = true;
    let loupeZoomMode = 'fit';
    let loupeDisplayedTierRank = -1;
    let loupeImageToken = 0;
    let loupeCurrentImage = null;
    let loupeFullLoadTimer = null;
    let loupeFullLoadToken = 0;
    let loupeHideTimer = null;
    let loupeCurrentMediaStatus = null;
    let loupeLoadingTierRank = -1;
    let loupePreviousFocus = null;
    const loupeTierProbes = new Set();
    const loupeRefLong = 3840; // lg thumbnail long side used before original dimensions are known
    const LOUPE_PRELOAD_RADIUS = 3;

    function cancelLoupeProbes() {
        cancelLoupeProbesCore(loupeTierProbes, {
            clearLoupeTierLoadingImpl: clearLoupeTierLoading,
        });
    }

    function focusLoupe() {
        focusLoupeCore();
    }

    function loupeFocusableElements(loupe) {
        return loupeFocusableElementsCore(loupe);
    }

    function trapLoupeFocus(e) {
        trapLoupeFocusCore(e, { focusLoupeImpl: focusLoupeCore });
    }

    function showLoupeImage(img, direction = 0) {
        const loupe = document.getElementById('loupe');
        const loupeImg = document.getElementById('loupe-img');
        if (!loupe || !loupeImg) return;
        if (loupeHideTimer) {
            clearTimeout(loupeHideTimer);
            loupeHideTimer = null;
        }

        // Pre-calculate fit dimensions from aspect ratio so all progressive
        // loads (sm/md/lg) display at the same screen size — no size jumps
        const token = ++loupeImageToken;
        clearWarmups();
        loupeCurrentImage = img;
        loupeFullLoadToken = 0;
        cancelLoupeProbes();
        if (loupeFullLoadTimer) {
            clearTimeout(loupeFullLoadTimer);
            loupeFullLoadTimer = null;
        }
        loupeDisplayedTierRank = -1;
        loupeLoadingTierRank = -1;
        loupeCurrentMediaStatus = null;
        loupeIsFit = true;
        loupeZoomMode = 'fit';
        loupeImg.onload = null;
        loupeImg.onerror = null;
        loupeImg.style.opacity = '0';
        loupeImg.src = LOUPE_BLANK_SRC;
        const ar = imageAspectRatio(img);
        loupeNatW = ar >= 1 ? loupeRefLong : Math.round(loupeRefLong * ar);
        loupeNatH = ar >= 1 ? Math.round(loupeRefLong / ar) : loupeRefLong;
        loupeImg.style.transition = 'opacity 0.15s';
        loupeApplyImageSize();

        document.body.classList.add('loupe-open');
        const loupeWasHidden = loupe.classList.contains('hidden');
        if (loupeWasHidden) {
            loupePreviousFocus = document.activeElement;
            loupe.classList.remove('hidden');
            requestAnimationFrame(() => {
                loupe.classList.add('loupe-visible');
                focusLoupe();
            });
        } else if (!loupe.contains(document.activeElement)) {
            focusLoupe();
        }
        loupeCenterFit({ animate: false });

        // Progressive loading: sm -> md -> lg -> original. Slow tiers time out
        // so the next tier still gets a chance, while late arrivals can still
        // upgrade the image if they are sharper than the current display.
        loupeImg.alt = img.filename || '';
        runLoupeProgressiveLoad(img, token);

        const { exifEl } = renderLoupeMetadataOverlay({
            image: img,
            eloToStarsImpl: eloToStars,
        });
        updateZoomIndicator();
        renderLoupeStatusLine(img.flag || 'unflagged');

        updateFilmstripActive();

        preloadLoupeNeighbors(direction);
        warmLoupeHotSet(direction);

        // Load EXIF
        fetch(`/api/image/${img.id}/exif`).then(r => r.json()).then(data => {
            if (!data.exif || !isCurrentLoupeImage(img, token)) return;
            renderLoupeMetadata({ ...img, ...data.exif }, exifEl);
        }).catch(() => {});
    }

    function renderLoupeMetadata(metadata, exifEl = document.getElementById('loupe-overlay-exif')) {
        renderLoupeMetadataCore(metadata, exifEl);
    }

    function isCurrentLoupeImage(img, token) {
        const current = lightboxIndex >= 0 ? libraryImages[lightboxIndex] : loupeStandaloneImage;
        return token === loupeImageToken && current?.id === img.id;
    }

    async function getMediaStatus(imageId, { force = false } = {}) {
        return mediaStatusClient.getStatus(imageId, { force });
    }

    function primeMediaStatuses(imageIds) {
        mediaStatusClient.primeStatuses(imageIds);
    }

    function loupeTierUrl(tier, imageId, cachedOnly = false) {
        return loupeTierUrlCore(tier, imageId, cachedOnly);
    }

    function updateLoupeFlagDisplay(flag) {
        renderLoupeStatusLine(flag);
    }

    function renderLoupeStatusLine(flag = loupeCurrentImage?.flag || 'unflagged') {
        renderLoupeStatusLineCore({
            displayedTierRank: loupeDisplayedTierRank,
            flag,
            loadingTierRank: loupeLoadingTierRank,
            mediaStatus: loupeCurrentMediaStatus,
            showCacheStatus: uiSettingsLoader.getSettings().show_loupe_cache_status,
        });
    }

    function setLoupeTierLoading(rank) {
        const updated = setLoupeTierLoadingCore({
            displayedTierRank: loupeDisplayedTierRank,
            rank,
        });
        if (updated) loupeLoadingTierRank = rank;
    }

    function clearLoupeTierLoading(rank = loupeLoadingTierRank) {
        const cleared = clearLoupeTierLoadingCore({
            loadingTierRank: loupeLoadingTierRank,
            rank,
        });
        if (cleared) loupeLoadingTierRank = -1;
    }

    function applyLoupeMediaStatus(status, img = loupeCurrentImage, token = loupeImageToken) {
        if (!status || !img || !isCurrentLoupeImage(img, token)) return false;
        loupeCurrentMediaStatus = status;
        renderLoupeStatusLine(img.flag || 'unflagged');
        return true;
    }

    async function refreshLoupeMediaStatus(img = loupeCurrentImage, token = loupeImageToken, { force = false } = {}) {
        if (!img || !isCurrentLoupeImage(img, token)) return null;
        const status = await getMediaStatus(img.id, { force });
        applyLoupeMediaStatus(status, img, token);
        return status;
    }

    function loupeApplyImageSize() {
        applyLoupeImageSizeCore({
            naturalWidth: loupeNatW,
            naturalHeight: loupeNatH,
        });
    }

    async function runLoupeProgressiveLoad(img, token) {
        return runLoupeProgressiveLoadCore(img, token, {
            loadLoupeTierImpl: loadLoupeTier,
            getMediaStatus,
            applyLoupeMediaStatus,
            isCurrentLoupeImage,
            loupeTierUrlImpl: loupeTierUrl,
            getDisplayedTierRank: () => loupeDisplayedTierRank,
            setFullLoadToken: (nextToken) => { loupeFullLoadToken = nextToken; },
        });
    }

    function loadLoupeTier(img, url, rank, token, { adoptDimensions = false, timeoutMs = 0 } = {}) {
        return loadLoupeTierCore(img, url, rank, token, {
            adoptDimensions,
            timeoutMs,
            probes: loupeTierProbes,
            isCurrentLoupeImage,
            getDisplayedTierRank: () => loupeDisplayedTierRank,
            setDisplayedTierRank: (nextRank) => { loupeDisplayedTierRank = nextRank; },
            setFullLoadToken: (nextToken) => { loupeFullLoadToken = nextToken; },
            setLoupeTierLoading,
            clearLoupeTierLoading,
            renderLoupeStatusLine: () => renderLoupeStatusLine(img.flag || 'unflagged'),
            refreshLoupeMediaStatus,
            adoptSourceDimensions: loupeAdoptSourceDimensions,
        });
    }

    function requestLoupeFullImage(img = loupeCurrentImage, token = loupeImageToken) {
        if (!img || !isCurrentLoupeImage(img, token) || loupeFullLoadToken === token) return;
        loupeFullLoadToken = token;
        if (loupeFullLoadTimer) {
            clearTimeout(loupeFullLoadTimer);
            loupeFullLoadTimer = null;
        }
        loadLoupeTier(img, loupeTierUrl('full', img.id), 3, token, {
            adoptDimensions: true,
            timeoutMs: LOUPE_TIER_TIMEOUTS.full,
        }).then((loaded) => {
            if (!loaded && isCurrentLoupeImage(img, token) && loupeDisplayedTierRank < LOUPE_TIER_RANKS.full) {
                loupeFullLoadToken = 0;
            }
        });
    }

    function loupeAdoptSourceDimensions(width, height) {
        const wrap = document.getElementById('loupe-image-wrap');
        const oldW = loupeNatW;
        const oldH = loupeNatH;
        if (!wrap || !oldW || !oldH || width <= 0 || height <= 0) {
            loupeNatW = width;
            loupeNatH = height;
            loupeApplyImageSize();
            return;
        }

        if (Math.abs(oldW - width) < 1 && Math.abs(oldH - height) < 1) return;

        const focusX = Math.max(0, Math.min(1, ((wrap.clientWidth / 2) - loupePanX) / loupeScale / oldW));
        const focusY = Math.max(0, Math.min(1, ((wrap.clientHeight / 2) - loupePanY) / loupeScale / oldH));
        const oldScale = loupeScale;
        const wasFit = loupeZoomMode === 'fit' || loupeIsFit;
        const wasOneToOne = loupeZoomMode === 'one-to-one';

        loupeNatW = width;
        loupeNatH = height;
        loupeApplyImageSize();
        loupeFitScale = loupeComputeFitScale();

        if (wasFit) {
            loupeScale = loupeFitScale;
            loupePanX = (wrap.clientWidth - loupeNatW * loupeScale) / 2;
            loupePanY = (wrap.clientHeight - loupeNatH * loupeScale) / 2;
            loupeIsFit = true;
            loupeZoomMode = 'fit';
        } else {
            loupeScale = wasOneToOne ? 1 : oldScale * (oldW / loupeNatW);
            loupePanX = (wrap.clientWidth / 2) - (focusX * loupeNatW * loupeScale);
            loupePanY = (wrap.clientHeight / 2) - (focusY * loupeNatH * loupeScale);
            loupeIsFit = false;
            loupeZoomMode = wasOneToOne ? 'one-to-one' : 'custom';
            loupeClampPan();
        }

        loupeApplyTransform();
        updateZoomIndicator();
        wrap.style.cursor = loupeIsFit ? 'zoom-in' : 'grab';
    }

    function preloadLoupeNeighbors(direction = 0) {
        if (lightboxIndex < 0) return;
        const token = loupeImageToken;
        const generation = currentWarmupGeneration();
        for (const offset of loupeNeighborOffsets(LOUPE_PRELOAD_RADIUS, direction)) {
            const ni = lightboxIndex + offset;
            if (ni < 0 || ni >= libraryImages.length) continue;
            const neighbor = libraryImages[ni];
            const distance = Math.abs(offset);
            enqueueWarmup(async () => {
                if (!loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
                await preloadLoupeNeighbor(neighbor, distance, token, generation);
            }, { generation });
        }
    }

    function warmLoupeHotSet(direction = 0) {
        const tiers = loupeHotSetTierIds(libraryImages, lightboxIndex, direction);
        if (tiers) warmImageTiers(tiers);
    }

    async function preloadLoupeNeighbor(img, distance = 1, token = loupeImageToken, generation = currentWarmupGeneration()) {
        if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
        await preloadImageWithTimeout(img.thumb_url, 'low', 1200);
        if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
        const status = await getMediaStatus(img.id);
        if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
        const tiers = status?.tiers || {};
        if (tiers.md?.cached) {
            await preloadImageWithTimeout(tiers.md.cached_url, 'low', LOUPE_TIER_TIMEOUTS.md);
        }
        if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
        if (tiers.lg?.cached) {
            await preloadImageWithTimeout(tiers.lg.cached_url, 'low', LOUPE_TIER_TIMEOUTS.lg);
        }
        if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
        if (distance === 1) {
            if (!tiers.md?.cached) {
                await preloadImageWithTimeout(loupeTierUrl('md', img.id), 'low', LOUPE_TIER_TIMEOUTS.md);
            }
            if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
            if (!tiers.lg?.cached) {
                await preloadImageWithTimeout(loupeTierUrl('lg', img.id), 'low', LOUPE_TIER_TIMEOUTS.lg);
            }
            if (generation !== currentWarmupGeneration() || !loupeCurrentImage || !isCurrentLoupeImage(loupeCurrentImage, token)) return;
            await preloadImageWithTimeout(
                tiers.full?.cached ? tiers.full.cached_url : loupeTierUrl('full', img.id),
                'low',
                LOUPE_TIER_TIMEOUTS.full,
            );
        }
    }

    function preloadImageWithTimeout(url, priority, timeoutMs) {
        return withTimeout(preloadImage(url, priority), timeoutMs);
    }

    // ==================== LOUPE ZOOM/PAN ====================

    function loupeComputeFitScale() {
        return loupeComputeFitScaleCore({
            naturalWidth: loupeNatW,
            naturalHeight: loupeNatH,
        });
    }

    function loupeApplyTransform() {
        applyLoupeTransformCore({
            panX: loupePanX,
            panY: loupePanY,
            scale: loupeScale,
        });
    }

    function updateZoomIndicator() {
        updateLoupeZoomIndicatorCore({
            currentImage: loupeCurrentImage,
            naturalWidth: loupeNatW,
            naturalHeight: loupeNatH,
            zoomMode: loupeZoomMode,
            scale: loupeScale,
        });
    }

    function loupeCenterFit({ animate = true } = {}) {
        const wrap = document.getElementById('loupe-image-wrap');
        const img = document.getElementById('loupe-img');
        if (!wrap || !img) return;
        loupeApplyImageSize();
        loupeFitScale = loupeComputeFitScale();
        loupeScale = loupeFitScale;
        loupePanX = (wrap.clientWidth - loupeNatW * loupeScale) / 2;
        loupePanY = (wrap.clientHeight - loupeNatH * loupeScale) / 2;
        loupeIsFit = true;
        loupeZoomMode = 'fit';
        if (animate) img.style.transition = 'transform 0.2s ease-out, opacity 0.15s';
        loupeApplyTransform();
        updateZoomIndicator();
        wrap.style.cursor = 'zoom-in';
        if (animate) setTimeout(() => { if (img) img.style.transition = 'opacity 0.15s'; }, 200);
    }

    function loupeZoomTo(newScale, pivotX, pivotY, mode = 'custom') {
        const wrap = document.getElementById('loupe-image-wrap');
        if (!wrap) return;

        const rect = wrap.getBoundingClientRect();
        const imgX = (pivotX - rect.left - loupePanX) / loupeScale;
        const imgY = (pivotY - rect.top - loupePanY) / loupeScale;

        const minScale = Math.max(0.01, Math.min(loupeFitScale, 1) * 0.5);
        const maxScale = Math.max(4, loupeFitScale * 4);
        loupeScale = Math.max(minScale, Math.min(maxScale, newScale));

        loupePanX = pivotX - rect.left - imgX * loupeScale;
        loupePanY = pivotY - rect.top - imgY * loupeScale;

        loupeIsFit = Math.abs(loupeScale - loupeFitScale) < 0.001;
        loupeZoomMode = loupeIsFit ? 'fit' : mode;
        loupeClampPan();
        loupeApplyTransform();
        updateZoomIndicator();
        wrap.style.cursor = loupeIsFit ? 'zoom-in' : 'grab';
    }

    function loupeClampPan() {
        const wrap = document.getElementById('loupe-image-wrap');
        const nextPan = clampLoupePanCore({
            wrap,
            naturalWidth: loupeNatW,
            naturalHeight: loupeNatH,
            scale: loupeScale,
            panX: loupePanX,
            panY: loupePanY,
        });
        loupePanX = nextPan.panX;
        loupePanY = nextPan.panY;
    }

    function initLoupeInteraction() {
        initLoupeInteractionCore({
            getPan: () => ({ x: loupePanX, y: loupePanY }),
            setPan: ({ x, y }) => {
                loupePanX = x;
                loupePanY = y;
            },
            getScale: () => loupeScale,
            getNaturalWidth: () => loupeNatW,
            getIsFit: () => loupeIsFit,
            setFitScale: (value) => { loupeFitScale = value; },
            computeFitScale: loupeComputeFitScale,
            centerFit: loupeCenterFit,
            zoomTo: loupeZoomTo,
            clampPan: loupeClampPan,
            applyTransform: loupeApplyTransform,
            updateZoomIndicator,
            requestFullImage: requestLoupeFullImage,
        });
    }

    async function ensureLibraryImageIndex(index) {
        if (index < libraryImages.length) return true;
        if (searchQuery === '__similar__') return false;
        while (index >= libraryImages.length && !rankingsExhausted) {
            const before = libraryImages.length;
            const loaded = await loadRankings(false);
            if (libraryImages.length <= before && !loaded) break;
        }
        return index < libraryImages.length;
    }

    function lightboxNext() {
        if (lightboxIndex < 0) return;
        const nextIndex = lightboxIndex + 1;
        if (nextIndex < libraryImages.length) {
            lightboxIndex = nextIndex;
            updateFilmstripCounter();
            showLoupeImage(libraryImages[lightboxIndex], 1);
            return;
        }
        const fromIndex = lightboxIndex;
        ensureLibraryImageIndex(nextIndex).then((ok) => {
            if (!ok || lightboxIndex !== fromIndex) return;
            lightboxIndex = nextIndex;
            updateFilmstripCounter();
            showLoupeImage(libraryImages[lightboxIndex], 1);
        });
    }

    function lightboxPrev() {
        if (lightboxIndex <= 0) return;
        lightboxIndex--;
        updateFilmstripCounter();
        showLoupeImage(libraryImages[lightboxIndex], -1);
    }

    function closeLightbox() {
        const loupe = document.getElementById('loupe');
        const previousFocus = loupePreviousFocus;
        loupePreviousFocus = null;
        if (loupe) {
            loupe.classList.remove('loupe-visible');
            if (loupeHideTimer) clearTimeout(loupeHideTimer);
            loupeHideTimer = setTimeout(() => {
                if (!document.body.classList.contains('loupe-open')) loupe.classList.add('hidden');
                loupeHideTimer = null;
            }, 150);
        }
        document.body.classList.remove('loupe-open');
        loupeImageToken++;
        clearWarmups();
        cancelLoupeProbes();
        if (loupeFullLoadTimer) {
            clearTimeout(loupeFullLoadTimer);
            loupeFullLoadTimer = null;
        }
        loupeCurrentImage = null;
        loupeStandaloneImage = null;
        loupeFullLoadToken = 0;
        loupeCurrentMediaStatus = null;
        loupeDisplayedTierRank = -1;
        loupeIsFit = true;
        loupeZoomMode = 'fit';
        loupeNatW = 0;
        loupeNatH = 0;
        updateZoomIndicator();
        lightboxIndex = -1;
        if (previousFocus && document.contains(previousFocus) && typeof previousFocus.focus === 'function') {
            previousFocus.focus({ preventScroll: true });
        }
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
        getLightboxIndex: () => lightboxIndex,
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
        libraryScrollRoot,
        openImageById: openLightboxById,
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
        const img = libraryImages.find(i => i.id === id);
        if (img) {
            openLightbox(img);
        } else {
            // Image is outside the active ordered list, so open it without mutating that list.
            fetch(`/api/image/${id}/exif`).then(r => r.json()).then(data => {
                const exif = data.exif || {};
                openStandaloneLightbox({
                    id,
                    filename: exif.filename || `Image ${id}`,
                    thumb_url: `/api/thumb/sm/${id}`,
                    aspect_ratio: 1.5,
                    elo: 0,
                    comparisons: 0,
                    flag: 'unflagged',
                    ...exif,
                });
            }).catch(() => showToast('Could not open image'));
        }
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
        startScan,
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
