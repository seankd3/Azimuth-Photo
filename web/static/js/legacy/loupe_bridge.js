import { createLoupeController } from '../loupe/controller.js';
import { loupeTierUrl as loupeTierUrlCore } from '../loupe/tiers.js';
import {
    createMediaStatusClient,
    createMediaStatusController,
} from '../media_status.js';

export function createLegacyLoupeBridge({
    mediaStatusClient = createMediaStatusClient({ maxAgeMs: 15000 }),
    createLoupeControllerImpl = createLoupeController,
    getLibraryImages,
    getSearchQuery,
    getRankingsExhausted,
    loadRankings,
    clearWarmups,
    currentWarmupGeneration,
    enqueueWarmup,
    warmImageTiers,
    preloadImage,
    withTimeoutImpl,
    getUiSettings,
    imageAspectRatio,
    eloToStars,
} = {}) {
    let loupeController = null;
    const mediaStatusController = createMediaStatusController({
        client: mediaStatusClient,
        getCurrentLoupeImage: () => loupeController?.getCurrentImage() || null,
        getLoupeImageToken: () => loupeController?.getImageToken() || 0,
        refreshLoupeMediaStatus: (...args) => loupeController?.refreshLoupeMediaStatus(...args),
    });

    function preloadImageWithTimeout(url, priority, timeoutMs) {
        return withTimeoutImpl(preloadImage(url, priority), timeoutMs);
    }

    function initController() {
        loupeController = createLoupeControllerImpl({
            getLibraryImages,
            getSearchQuery,
            getRankingsExhausted,
            loadRankings,
            clearWarmups,
            currentWarmupGeneration,
            enqueueWarmup,
            warmImageTiers,
            preloadImageWithTimeout,
            getMediaStatus,
            getUiSettings,
            imageAspectRatio,
            eloToStars,
        });
        return loupeController;
    }

    async function getMediaStatus(imageId, { force = false } = {}) {
        return mediaStatusController.getMediaStatus(imageId, { force });
    }

    function primeMediaStatuses(imageIds) {
        return mediaStatusController.primeMediaStatuses(imageIds);
    }

    function loupeTierUrl(tier, imageId, cachedOnly = false) {
        return loupeTierUrlCore(tier, imageId, cachedOnly);
    }

    return {
        closeLightbox: () => loupeController.closeLightbox(),
        ensureLibraryImageIndex: (index) => loupeController.ensureLibraryImageIndex(index),
        focusLoupe: () => loupeController.focusLoupe(),
        getController: () => loupeController,
        getCurrentImage: () => loupeController?.getCurrentImage() || null,
        getLightboxIndex: () => loupeController?.getLightboxIndex() ?? -1,
        getMediaStatus,
        getStandaloneImage: () => loupeController?.getStandaloneImage() || null,
        handleWarmTiersApplied: mediaStatusController.handleWarmTiersApplied,
        initController,
        initLoupeInteraction: () => loupeController.initLoupeInteraction(),
        lightboxNext: () => loupeController.lightboxNext(),
        lightboxPrev: () => loupeController.lightboxPrev(),
        loupeFocusableElements: (loupe) => loupeController.loupeFocusableElements(loupe),
        loupeTierUrl,
        openLightbox: (img) => loupeController.openLightbox(img),
        openStandaloneLightbox: (img) => loupeController.openStandaloneLightbox(img),
        preloadImageWithTimeout,
        primeMediaStatuses,
        renderLoupeStatusLine: (flag) => loupeController?.renderLoupeStatusLine(flag),
        trapLoupeFocus: (event) => loupeController.trapLoupeFocus(event),
        updateLoupeFlagDisplay: (flag) => loupeController.updateLoupeFlagDisplay(flag),
    };
}
