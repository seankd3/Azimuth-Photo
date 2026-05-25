import {
    LOUPE_BLANK_SRC,
    LOUPE_TIER_RANKS,
    LOUPE_TIER_TIMEOUTS,
    loupeTierUrl as loupeTierUrlCore,
} from './tiers.js';
import {
    cancelLoupeProbes as cancelLoupeProbesCore,
    loadLoupeTier as loadLoupeTierCore,
    runLoupeProgressiveLoad as runLoupeProgressiveLoadCore,
} from './loading.js';
import {
    clearLoupeTierLoading as clearLoupeTierLoadingCore,
    renderLoupeStatusLine as renderLoupeStatusLineCore,
    setLoupeTierLoading as setLoupeTierLoadingCore,
} from './status.js';
import {
    renderLoupeMetadata as renderLoupeMetadataCore,
    renderLoupeMetadataOverlay,
} from './metadata.js';
import { createLoupeWarmupController } from './warmup.js';
import {
    buildFilmstrip as buildFilmstripCore,
    clearFilmstrip as clearFilmstripCore,
    updateFilmstripActive as updateFilmstripActiveCore,
    updateFilmstripCounter as updateFilmstripCounterCore,
} from './filmstrip.js';
import {
    focusLoupe as focusLoupeCore,
    loupeFocusableElements as loupeFocusableElementsCore,
    trapLoupeFocus as trapLoupeFocusCore,
} from './focus.js';
import {
    applyLoupeImageSize as applyLoupeImageSizeCore,
    applyLoupeTransform as applyLoupeTransformCore,
    clampLoupePan as clampLoupePanCore,
    loupeComputeFitScale as loupeComputeFitScaleCore,
    updateLoupeZoomIndicator as updateLoupeZoomIndicatorCore,
} from './zoom.js';
import { initLoupeInteraction as initLoupeInteractionCore } from './interaction.js';
import { createLoupeNavigationController } from './navigation.js';

export const DEFAULT_FILMSTRIP_WINDOW_RADIUS = 55;
export const DEFAULT_LOUPE_PRELOAD_RADIUS = 3;
export const DEFAULT_LOUPE_REF_LONG = 3840;

function defaultImageAspectRatio(img) {
    const ratio = Number(img?.aspect_ratio || img?.ratio || 0);
    return ratio > 0 ? ratio : 1.5;
}

function defaultRequestAnimationFrame(callback) {
    const requestFrame = globalThis.requestAnimationFrame || ((cb) => cb());
    return requestFrame(callback);
}

function defaultFetch(...args) {
    return globalThis.fetch(...args);
}

export function createLoupeController({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
    ImageImpl = globalThis.Image,
    requestAnimationFrameImpl = defaultRequestAnimationFrame,
    setTimeoutImpl = globalThis.setTimeout?.bind(globalThis) || (() => null),
    clearTimeoutImpl = globalThis.clearTimeout?.bind(globalThis) || (() => {}),
    fetchImpl = globalThis.fetch ? defaultFetch : null,
    getLibraryImages = () => [],
    getLibraryPoolTotal = () => getLibraryImages().length,
    getSearchQuery = () => '',
    getRankingsExhausted = () => true,
    loadRankings = async () => 0,
    clearWarmups = () => {},
    currentWarmupGeneration = () => 0,
    enqueueWarmup = () => {},
    warmImageTiers = () => {},
    preloadImageWithTimeout = async () => {},
    getMediaStatus = async () => null,
    getUiSettings = () => ({ show_loupe_cache_status: true }),
    imageAspectRatio = defaultImageAspectRatio,
    eloToStars = () => 0,
    filmstripWindowRadius = DEFAULT_FILMSTRIP_WINDOW_RADIUS,
    loupePreloadRadius = DEFAULT_LOUPE_PRELOAD_RADIUS,
    loupeRefLong = DEFAULT_LOUPE_REF_LONG,
} = {}) {
    let lightboxIndex = -1;
    let loupeStandaloneImage = null;
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
    let loupeInteractionReady = false;
    const loupeTierProbes = new Set();

    function getLightboxIndex() {
        return lightboxIndex;
    }

    function setLightboxIndex(index) {
        lightboxIndex = index;
    }

    function getStandaloneImage() {
        return loupeStandaloneImage;
    }

    function setStandaloneImage(img) {
        loupeStandaloneImage = img;
    }

    function getCurrentImage() {
        return loupeCurrentImage;
    }

    function getImageToken() {
        return loupeImageToken;
    }

    function clearFilmstrip() {
        clearFilmstripCore({
            documentImpl,
            images: getLibraryImages(),
            lightboxIndex,
            poolTotal: getLibraryPoolTotal(),
        });
    }

    function updateFilmstripCounter() {
        updateFilmstripCounterCore({
            documentImpl,
            images: getLibraryImages(),
            lightboxIndex,
            poolTotal: getLibraryPoolTotal(),
        });
    }

    function handleFilmstripSelect(index, direction) {
        lightboxIndex = index;
        updateFilmstripCounter();
        showLoupeImage(getLibraryImages()[index], direction);
    }

    function buildFilmstrip() {
        buildFilmstripCore({
            documentImpl,
            images: getLibraryImages(),
            lightboxIndex,
            poolTotal: getLibraryPoolTotal(),
            requestAnimationFrameImpl,
            windowRadius: filmstripWindowRadius,
            onSelect: handleFilmstripSelect,
        });
    }

    function updateFilmstripActive() {
        updateFilmstripActiveCore({
            documentImpl,
            images: getLibraryImages(),
            lightboxIndex,
            poolTotal: getLibraryPoolTotal(),
            requestAnimationFrameImpl,
            windowRadius: filmstripWindowRadius,
            onSelect: handleFilmstripSelect,
        });
    }

    const loupeNavigationController = createLoupeNavigationController({
        getLibraryImages,
        getLightboxIndex,
        setLightboxIndex,
        getSearchQuery,
        getRankingsExhausted,
        setStandaloneImage,
        updateFilmstripCounter,
        buildFilmstrip,
        clearFilmstrip,
        showLoupeImage,
        loadRankings,
    });

    const loupeWarmupController = createLoupeWarmupController({
        getLibraryImages,
        getLightboxIndex,
        getLoupeImageToken: getImageToken,
        getWarmupGeneration: currentWarmupGeneration,
        getCurrentLoupeImage: getCurrentImage,
        isCurrentLoupeImage,
        enqueueWarmup,
        warmImageTiers,
        preloadImageWithTimeout,
        getMediaStatus,
        loupeTierUrlImpl: loupeTierUrl,
        preloadRadius: loupePreloadRadius,
    });

    function openLightbox(img) {
        return loupeNavigationController.openLightbox(img);
    }

    function openStandaloneLightbox(img) {
        return loupeNavigationController.openStandaloneLightbox(img);
    }

    function ensureLibraryImageIndex(index) {
        return loupeNavigationController.ensureLibraryImageIndex(index);
    }

    function lightboxNext() {
        return loupeNavigationController.lightboxNext();
    }

    function lightboxPrev() {
        return loupeNavigationController.lightboxPrev();
    }

    function cancelLoupeProbes() {
        cancelLoupeProbesCore(loupeTierProbes, {
            clearLoupeTierLoadingImpl: clearLoupeTierLoading,
        });
    }

    function focusLoupe() {
        focusLoupeCore({ documentImpl });
    }

    function loupeFocusableElements(loupe) {
        return loupeFocusableElementsCore(loupe);
    }

    function trapLoupeFocus(e) {
        return trapLoupeFocusCore(e, {
            documentImpl,
            focusLoupeImpl: focusLoupe,
        });
    }

    function showLoupeImage(img, direction = 0) {
        const loupe = documentImpl?.getElementById?.('loupe');
        const loupeImg = documentImpl?.getElementById?.('loupe-img');
        if (!loupe || !loupeImg) return false;
        ensureLoupeInteraction();
        if (loupeHideTimer) {
            clearTimeoutImpl(loupeHideTimer);
            loupeHideTimer = null;
        }

        const token = ++loupeImageToken;
        clearWarmups();
        loupeCurrentImage = img;
        loupeFullLoadToken = 0;
        cancelLoupeProbes();
        if (loupeFullLoadTimer) {
            clearTimeoutImpl(loupeFullLoadTimer);
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

        documentImpl?.body?.classList?.add('loupe-open');
        try {
            const bar = documentImpl?.querySelector?.('.bottom-bar');
            if (bar) {
                documentImpl.documentElement.style.setProperty(
                    '--current-bottom-bar-height',
                    `${bar.offsetHeight}px`,
                );
            }
        } catch {
            // Keep the last measured bottom-bar height when layout probing fails.
        }
        const loupeWasHidden = loupe.classList.contains('hidden');
        if (loupeWasHidden) {
            loupePreviousFocus = documentImpl.activeElement;
            loupe.classList.remove('hidden');
            requestAnimationFrameImpl(() => {
                loupe.classList.add('loupe-visible');
                focusLoupe();
            });
        } else if (!loupe.contains(documentImpl.activeElement)) {
            focusLoupe();
        }
        loupeCenterFit({ animate: false });

        loupeImg.alt = img.filename || '';
        runLoupeProgressiveLoad(img, token);

        const { exifEl } = renderLoupeMetadataOverlay({
            documentImpl,
            image: img,
            eloToStarsImpl: eloToStars,
        });
        updateZoomIndicator();
        renderLoupeStatusLine(img.flag || 'unflagged');

        updateFilmstripActive();

        preloadLoupeNeighbors(direction);
        warmLoupeHotSet(direction);

        if (fetchImpl) {
            fetchImpl(`/api/image/${img.id}/exif`)
                .then((r) => r.json())
                .then((data) => {
                    if (!data.exif || !isCurrentLoupeImage(img, token)) return;
                    renderLoupeMetadata({ ...img, ...data.exif }, exifEl);
                })
                .catch(() => {});
        }
        return true;
    }

    function renderLoupeMetadata(metadata, exifEl = documentImpl?.getElementById?.('loupe-overlay-exif')) {
        return renderLoupeMetadataCore(metadata, exifEl);
    }

    function isCurrentLoupeImage(img, token) {
        const current = lightboxIndex >= 0 ? getLibraryImages()[lightboxIndex] : loupeStandaloneImage;
        return token === loupeImageToken && current?.id === img.id;
    }

    function loupeTierUrl(tier, imageId, cachedOnly = false) {
        return loupeTierUrlCore(tier, imageId, cachedOnly);
    }

    function updateLoupeFlagDisplay(flag) {
        return renderLoupeStatusLine(flag);
    }

    function renderLoupeStatusLine(flag = loupeCurrentImage?.flag || 'unflagged') {
        return renderLoupeStatusLineCore({
            documentImpl,
            displayedTierRank: loupeDisplayedTierRank,
            flag,
            loadingTierRank: loupeLoadingTierRank,
            mediaStatus: loupeCurrentMediaStatus,
            showCacheStatus: getUiSettings().show_loupe_cache_status,
        });
    }

    function setLoupeTierLoading(rank) {
        const updated = setLoupeTierLoadingCore({
            documentImpl,
            displayedTierRank: loupeDisplayedTierRank,
            rank,
        });
        if (updated) loupeLoadingTierRank = rank;
        return updated;
    }

    function clearLoupeTierLoading(rank = loupeLoadingTierRank) {
        const cleared = clearLoupeTierLoadingCore({
            documentImpl,
            loadingTierRank: loupeLoadingTierRank,
            rank,
        });
        if (cleared) loupeLoadingTierRank = -1;
        return cleared;
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
        return applyLoupeImageSizeCore({
            documentImpl,
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
            ImageImpl,
            documentImpl,
            setTimeoutImpl,
            clearTimeoutImpl,
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
        if (!img || !isCurrentLoupeImage(img, token) || loupeFullLoadToken === token) return false;
        loupeFullLoadToken = token;
        if (loupeFullLoadTimer) {
            clearTimeoutImpl(loupeFullLoadTimer);
            loupeFullLoadTimer = null;
        }
        loadLoupeTier(img, loupeTierUrl('full', img.id), LOUPE_TIER_RANKS.full, token, {
            adoptDimensions: true,
            timeoutMs: LOUPE_TIER_TIMEOUTS.full,
        }).then((loaded) => {
            if (!loaded && isCurrentLoupeImage(img, token) && loupeDisplayedTierRank < LOUPE_TIER_RANKS.full) {
                loupeFullLoadToken = 0;
            }
        });
        return true;
    }

    function loupeAdoptSourceDimensions(width, height) {
        const wrap = documentImpl?.getElementById?.('loupe-image-wrap');
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
        return loupeWarmupController.preloadLoupeNeighbors(direction);
    }

    function warmLoupeHotSet(direction = 0) {
        return loupeWarmupController.warmLoupeHotSet(direction);
    }

    async function preloadLoupeNeighbor(
        img,
        distance = 1,
        token = loupeImageToken,
        generation = currentWarmupGeneration(),
    ) {
        return loupeWarmupController.preloadLoupeNeighbor(img, distance, token, generation);
    }

    function loupeComputeFitScale() {
        return loupeComputeFitScaleCore({
            documentImpl,
            naturalWidth: loupeNatW,
            naturalHeight: loupeNatH,
        });
    }

    function loupeApplyTransform() {
        return applyLoupeTransformCore({
            documentImpl,
            panX: loupePanX,
            panY: loupePanY,
            scale: loupeScale,
        });
    }

    function updateZoomIndicator() {
        return updateLoupeZoomIndicatorCore({
            documentImpl,
            currentImage: loupeCurrentImage,
            naturalWidth: loupeNatW,
            naturalHeight: loupeNatH,
            zoomMode: loupeZoomMode,
            scale: loupeScale,
        });
    }

    function loupeCenterFit({ animate = true } = {}) {
        const wrap = documentImpl?.getElementById?.('loupe-image-wrap');
        const img = documentImpl?.getElementById?.('loupe-img');
        if (!wrap || !img) return false;
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
        if (animate && setTimeoutImpl) {
            setTimeoutImpl(() => {
                if (img) img.style.transition = 'opacity 0.15s';
            }, 200);
        }
        return true;
    }

    function loupeZoomTo(newScale, pivotX, pivotY, mode = 'custom') {
        const wrap = documentImpl?.getElementById?.('loupe-image-wrap');
        if (!wrap) return false;

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
        return true;
    }

    function loupeClampPan() {
        const wrap = documentImpl?.getElementById?.('loupe-image-wrap');
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
        return nextPan;
    }

    function ensureLoupeInteraction() {
        if (loupeInteractionReady) return true;
        loupeInteractionReady = Boolean(initLoupeInteraction());
        return loupeInteractionReady;
    }

    function initLoupeInteraction() {
        const bound = initLoupeInteractionCore({
            documentImpl,
            windowImpl,
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
            closeLightbox,
        });
        loupeInteractionReady = Boolean(bound);
        return bound;
    }

    function closeLightbox() {
        const loupe = documentImpl?.getElementById?.('loupe');
        const previousFocus = loupePreviousFocus;
        loupePreviousFocus = null;
        if (loupe) {
            loupe.classList.remove('loupe-visible');
            if (loupeHideTimer) clearTimeoutImpl(loupeHideTimer);
            loupeHideTimer = setTimeoutImpl(() => {
                loupe.classList.add('hidden');
                loupeHideTimer = null;
            }, 150);
        }
        documentImpl?.body?.classList?.remove('loupe-open');
        loupeImageToken++;
        clearWarmups();
        cancelLoupeProbes();
        if (loupeFullLoadTimer) {
            clearTimeoutImpl(loupeFullLoadTimer);
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

        const documentContains = typeof documentImpl?.contains === 'function'
            ? documentImpl.contains(previousFocus)
            : documentImpl?.body?.contains?.(previousFocus);
        if (previousFocus && documentContains && typeof previousFocus.focus === 'function') {
            previousFocus.focus({ preventScroll: true });
        }
    }

    function stateSnapshot() {
        return {
            lightboxIndex,
            loupeCurrentImage,
            loupeStandaloneImage,
            loupeImageToken,
            loupeScale,
            loupeFitScale,
            loupePanX,
            loupePanY,
            loupeNatW,
            loupeNatH,
            loupeIsFit,
            loupeZoomMode,
            loupeDisplayedTierRank,
            loupeFullLoadToken,
            loupeCurrentMediaStatus,
            loupeLoadingTierRank,
        };
    }

    return {
        applyLoupeMediaStatus,
        closeLightbox,
        ensureLibraryImageIndex,
        focusLoupe,
        getCurrentImage,
        getImageToken,
        getLightboxIndex,
        getStandaloneImage,
        initLoupeInteraction,
        isCurrentLoupeImage,
        lightboxNext,
        lightboxPrev,
        loupeFocusableElements,
        loupeTierUrl,
        openLightbox,
        openStandaloneLightbox,
        preloadLoupeNeighbor,
        preloadLoupeNeighbors,
        refreshLoupeMediaStatus,
        renderLoupeStatusLine,
        requestLoupeFullImage,
        showLoupeImage,
        stateSnapshot,
        trapLoupeFocus,
        updateLoupeFlagDisplay,
        warmLoupeHotSet,
    };
}
