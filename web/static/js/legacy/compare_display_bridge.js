import { createCompareImageController } from '../compare/image_controller.js';
import { createComparePairController } from '../compare/pair_controller.js';

export function createLegacyCompareDisplayBridge({
    displayedTiers,
    getCompareMode,
    getCompareIndex,
    setCompareIndex,
    getComparePairs,
    setComparePairs,
    setCompareStats,
    incrementCompareImageToken,
    getCompareImageToken,
    buildCompareUrl,
    takeWarmCache,
    fetchWarmJson,
    preloadImage,
    primeMediaStatuses,
    warmImageTiers,
    updateCompareProgress,
    scheduleCompareNeighborWarmup,
    scheduleCrossViewWarmup,
    showCompareEmpty,
    getMediaStatus,
    loadImageProbe,
    loupeTierUrl,
    documentImpl = document,
    createComparePairControllerImpl = createComparePairController,
    createCompareImageControllerImpl = createCompareImageController,
} = {}) {
    let comparePairController = null;

    function isCurrentCompareImage(token) {
        return comparePairController.isCurrentCompareImage(token);
    }

    const compareImageController = createCompareImageControllerImpl({
        displayedTiers,
        getMediaStatus,
        isCurrentCompareImage,
        loadImageProbe,
        loupeTierUrl,
    });

    function renderCompareImage(img, imgEl, side, token) {
        return compareImageController.renderCompareImage(img, imgEl, side, token);
    }

    function upgradeCompareImage(img, imgEl, side, token) {
        return compareImageController.upgradeCompareImage(img, imgEl, side, token);
    }

    function adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs) {
        return compareImageController.adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs);
    }

    comparePairController = createComparePairControllerImpl({
        getCompareMode,
        getCompareIndex,
        setCompareIndex,
        getComparePairs,
        setComparePairs,
        setCompareStats,
        incrementCompareImageToken,
        getCompareImageToken,
        buildCompareUrl,
        takeWarmCache,
        fetchWarmJson,
        preloadImage,
        primeMediaStatuses,
        renderCompareImage,
        warmImageTiers,
        updateCompareProgress,
        scheduleCompareNeighborWarmup,
        scheduleCrossViewWarmup,
        showCompareEmpty,
        documentImpl,
    });

    function fetchComparePairs() {
        return comparePairController.fetchComparePairs();
    }

    function showComparePair() {
        return comparePairController.showComparePair();
    }

    return {
        adoptCompareTier,
        fetchComparePairs,
        isCurrentCompareImage,
        renderCompareImage,
        showComparePair,
        upgradeCompareImage,
    };
}
