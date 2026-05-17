import {
    adoptMosaicTier,
    mosaicGridElo,
    renderMosaicGrid,
    scheduleMosaicImageUpgrade,
    upgradeMosaicCellImage,
} from './mosaic.js';


export function createMosaicRenderController({
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
    preloadImage,
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
    cancelAnimationFrameImpl = globalThis.cancelAnimationFrame,
    requestAnimationFrameImpl = globalThis.requestAnimationFrame,
    mosaicGridEloImpl = mosaicGridElo,
    renderMosaicGridImpl = renderMosaicGrid,
    scheduleMosaicImageUpgradeImpl = scheduleMosaicImageUpgrade,
    upgradeMosaicCellImageImpl = upgradeMosaicCellImage,
    adoptMosaicTierImpl = adoptMosaicTier,
} = {}) {
    function gridElo() {
        return mosaicGridEloImpl(getMosaicImages());
    }

    function renderMosaic() {
        const grid = documentImpl.getElementById('mosaic-grid');
        if (!grid) return false;
        setSelectedMosaicIndex(-1);
        const token = incrementMosaicRenderToken();
        renderMosaicGridImpl({
            images: getMosaicImages(),
            grid,
            token,
            onPick: mosaicClick,
            preloadImage,
            scheduleImageUpgrade: scheduleMosaicImageUpgradeAdapter,
            documentImpl,
            windowImpl,
        });
        return true;
    }

    function scheduleMosaicRender() {
        const currentRaf = getMosaicResizeRaf();
        if (currentRaf) cancelAnimationFrameImpl(currentRaf);
        const nextRaf = requestAnimationFrameImpl(() => {
            setMosaicResizeRaf(null);
            if (getCompareMode() === 'mosaic' && getMosaicImages().length) renderMosaic();
        });
        setMosaicResizeRaf(nextRaf);
        return nextRaf;
    }

    function scheduleMosaicImageUpgradeAdapter(cell, img, rowH, token, index = 0) {
        return scheduleMosaicImageUpgradeImpl(cell, img, rowH, token, index, {
            upgradeImage: upgradeMosaicCellImageAdapter,
        });
    }

    async function upgradeMosaicCellImageAdapter(cell, img, rowH, token) {
        return upgradeMosaicCellImageImpl(cell, img, rowH, token, {
            getRenderToken: getMosaicRenderToken,
            getMediaStatus,
            adoptTier: adoptMosaicTierAdapter,
        });
    }

    async function adoptMosaicTierAdapter(cell, img, tier, cachedOnly, token, timeoutMs) {
        return adoptMosaicTierImpl(cell, img, tier, cachedOnly, token, timeoutMs, {
            getRenderToken: getMosaicRenderToken,
            loadImageProbeImpl: loadImageProbe,
            loupeTierUrlImpl: loupeTierUrl,
        });
    }

    return {
        adoptMosaicTier: adoptMosaicTierAdapter,
        mosaicGridElo: gridElo,
        renderMosaic,
        scheduleMosaicImageUpgrade: scheduleMosaicImageUpgradeAdapter,
        scheduleMosaicRender,
        upgradeMosaicCellImage: upgradeMosaicCellImageAdapter,
    };
}
