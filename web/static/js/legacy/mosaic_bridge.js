import { createMosaicActionController } from '../compare/mosaic_action_controller.js';
import { createMosaicRenderController } from '../compare/mosaic_render_controller.js';
import {
    MOSAIC_REPLACEMENT_LOW_WATER,
    createMosaicReplacementBuffer,
} from '../compare/mosaic_replacements.js';
import { showCompareMosaic as showCompareMosaicCore } from '../compare/view.js';

export function createLegacyMosaicBridge({
    documentImpl = document,
    getMosaicSize,
    getMosaicStrategy,
    getMosaicImages,
    setMosaicImages,
    getMosaicAge,
    setMosaicAge,
    setMosaicPickCount,
    getMosaicResizeRaf,
    setMosaicResizeRaf,
    incrementMosaicRenderToken,
    getMosaicRenderToken,
    setSelectedMosaicIndex,
    getMosaicReplacements,
    setMosaicReplacements,
    getMosaicFilling,
    setMosaicFilling,
    getMosaicBusy,
    setMosaicBusy,
    incrementMosaicActionSeq,
    getMosaicActionSeq,
    getMosaicPropagationCounts,
    setMosaicPropagationCounts,
    getCompareMode,
    getCompareStats,
    setCompareStats,
    buildMosaicUrl,
    takeWarmCache,
    fetchWarmJson,
    primeMediaStatuses,
    warmImageTiers,
    currentWarmupGeneration,
    enqueueWarmup,
    getMediaStatus,
    loadImageProbe,
    loupeTierUrl,
    preloadImage,
    updateCompareProgress,
    precomputePropagation,
    scheduleCompareNeighborWarmup,
    scheduleCrossViewWarmup,
    showCompareMosaic = () => showCompareMosaicCore({ documentImpl }),
    showCompareEmpty,
    setUndoCount,
    bumpRankingSignals,
    fetchPropagationCount,
    showPropagationBadge,
    showToast,
    setTimeoutImpl = globalThis.setTimeout,
    clearTimeoutImpl = globalThis.clearTimeout,
    deferredRetryMs = 900,
    createMosaicRenderControllerImpl = createMosaicRenderController,
    createMosaicReplacementBufferImpl = createMosaicReplacementBuffer,
    createMosaicActionControllerImpl = createMosaicActionController,
    replacementLowWater = MOSAIC_REPLACEMENT_LOW_WATER,
} = {}) {
    let deferredMosaicRetryTimer = null;
    let mosaicLoadSeq = 0;

    function clearDeferredMosaicRetry() {
        if (!deferredMosaicRetryTimer) return;
        clearTimeoutImpl?.(deferredMosaicRetryTimer);
        deferredMosaicRetryTimer = null;
    }

    const mosaicRenderController = createMosaicRenderControllerImpl({
        getMosaicImages,
        getCompareMode,
        getMosaicResizeRaf,
        setMosaicResizeRaf,
        incrementMosaicRenderToken,
        getMosaicRenderToken,
        setSelectedMosaicIndex,
        getMediaStatus,
        loadImageProbe,
        loupeTierUrl,
        mosaicClick,
        preloadImage: (...args) => preloadImage(...args),
    });

    function mosaicGridElo() {
        return mosaicRenderController.mosaicGridElo();
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

    function upgradeMosaicCellImage(cell, img, rowH, token) {
        return mosaicRenderController.upgradeMosaicCellImage(cell, img, rowH, token);
    }

    function adoptMosaicTier(cell, img, tier, cachedOnly, token, timeoutMs) {
        return mosaicRenderController.adoptMosaicTier(cell, img, tier, cachedOnly, token, timeoutMs);
    }

    const mosaicReplacementBuffer = createMosaicReplacementBufferImpl({
        getMosaicFilling,
        setMosaicFilling,
        getMosaicImages,
        getMosaicReplacements,
        getMosaicRenderToken,
        getWarmupGeneration: currentWarmupGeneration,
        enqueueWarmup,
        buildMosaicUrl,
        loadImageProbe,
        setCompareStats,
        updateCompareProgress,
    });

    function mosaicFillReplacements() {
        return mosaicReplacementBuffer.fillReplacements();
    }

    const mosaicActionController = createMosaicActionControllerImpl({
        getMosaicBusy,
        setMosaicBusy,
        setUndoCount,
        incrementMosaicActionSeq,
        getMosaicActionSeq,
        getMosaicImages,
        setMosaicImages,
        getMosaicAge,
        setMosaicAge,
        getMosaicReplacements,
        setMosaicReplacements,
        getMosaicRenderToken,
        getMosaicPropagationCounts,
        setMosaicPropagationCounts,
        getCompareStats,
        setCompareStats,
        queryMosaicCells: () => documentImpl.querySelectorAll('.mosaic-cell'),
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
        replacementLowWater,
    });

    function mosaicClick(id) {
        return mosaicActionController.mosaicClick(id);
    }

    async function loadMosaicBatch({ retrying = false } = {}) {
        const loadSeq = ++mosaicLoadSeq;
        const url = buildMosaicUrl({ n: getMosaicSize() });
        const data = (
            getMosaicStrategy() !== 'diverse'
                ? takeWarmCache(`compare:${url}`)
                : null
        ) || await fetchWarmJson(url);
        // A newer load (shuffle, strategy switch) started while we awaited;
        // let it own the grid instead of overwriting it with stale results.
        if (loadSeq !== mosaicLoadSeq) return undefined;
        if (!data) {
            if (!retrying) {
                showToast?.('Compare could not load photos. Retrying shortly.');
            }
            if (!deferredMosaicRetryTimer) {
                deferredMosaicRetryTimer = setTimeoutImpl?.(() => {
                    deferredMosaicRetryTimer = null;
                    return Promise.resolve(loadMosaicBatch({ retrying: true })).catch(() => {});
                }, deferredRetryMs);
            }
            return undefined;
        }
        setCompareStats(data.stats || {});
        updateCompareProgress();
        const images = Array.isArray(data.images) ? data.images : [];

        if (data.status_stale && images.length === 0) {
            if (!getMosaicImages().length && !retrying) {
                showToast?.('Compare is waiting for the catalog database. Retrying shortly.');
            }
            if (!deferredMosaicRetryTimer) {
                deferredMosaicRetryTimer = setTimeoutImpl?.(() => {
                    deferredMosaicRetryTimer = null;
                    return Promise.resolve(loadMosaicBatch({ retrying: true })).catch(() => {});
                }, deferredRetryMs);
            }
            return data;
        }
        clearDeferredMosaicRetry();

        if (images.length < 2) {
            showCompareEmpty();
            return undefined;
        }

        showCompareMosaic();
        setMosaicImages(images);
        setMosaicAge(new Array(images.length).fill(0));
        setMosaicPickCount(0);
        setMosaicReplacements([]);
        setMosaicFilling(false);
        setMosaicBusy(false);
        const currentImages = getMosaicImages();
        primeMediaStatuses(currentImages.map((img) => img.id));
        renderMosaic();
        warmImageTiers({
            md: currentImages.map((img) => img.id),
            lg: currentImages.map((img) => img.id),
        });
        mosaicFillReplacements();
        precomputePropagation();
        scheduleCompareNeighborWarmup('mosaic');
        scheduleCrossViewWarmup('compare');
        return data;
    }

    return {
        adoptMosaicTier,
        loadMosaicBatch,
        mosaicClick,
        mosaicFillReplacements,
        mosaicGridElo,
        renderMosaic,
        scheduleMosaicImageUpgrade,
        scheduleMosaicRender,
        upgradeMosaicCellImage,
    };
}
