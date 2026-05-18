import { createMosaicActionController } from '../compare/mosaic_action_controller.js';
import { createMosaicRenderController } from '../compare/mosaic_render_controller.js';
import {
    MOSAIC_REPLACEMENT_LOW_WATER,
    createMosaicReplacementBuffer,
} from '../compare/mosaic_replacements.js';

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
    showCompareEmpty,
    setUndoCount,
    bumpRankingSignals,
    fetchPropagationCount,
    showPropagationBadge,
    showToast,
    createMosaicRenderControllerImpl = createMosaicRenderController,
    createMosaicReplacementBufferImpl = createMosaicReplacementBuffer,
    createMosaicActionControllerImpl = createMosaicActionController,
    replacementLowWater = MOSAIC_REPLACEMENT_LOW_WATER,
} = {}) {
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

    async function loadMosaicBatch() {
        const url = buildMosaicUrl({ n: getMosaicSize() });
        const data = (
            getMosaicStrategy() !== 'diverse'
                ? takeWarmCache(`compare:${url}`)
                : null
        ) || await fetchWarmJson(url);
        if (!data) return undefined;
        setCompareStats(data.stats || {});
        updateCompareProgress();

        if (data.images.length < 2) {
            showCompareEmpty();
            return undefined;
        }

        setMosaicImages(data.images);
        setMosaicAge(new Array(data.images.length).fill(0));
        setMosaicPickCount(0);
        setMosaicReplacements([]);
        setMosaicFilling(false);
        setMosaicBusy(false);
        const images = getMosaicImages();
        primeMediaStatuses(images.map((img) => img.id));
        renderMosaic();
        warmImageTiers({
            md: images.map((img) => img.id),
            lg: images.map((img) => img.id),
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
