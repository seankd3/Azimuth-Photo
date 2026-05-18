import { createCompareModeController } from '../compare/mode_controller.js';

export function createLegacyCompareModeBridge({
    documentImpl,
    clearWarmups,
    setMosaicStrategyValue,
    setCompareModeValue,
    incrementTransitionToken,
    isCurrentTransition,
    incrementCompareImageToken,
    loadMosaicBatch,
    resetComparePairs,
    fetchComparePairs,
    showComparePair,
    createCompareModeControllerImpl = createCompareModeController,
} = {}) {
    const controller = createCompareModeControllerImpl({
        documentImpl,
        clearWarmups,
        setMosaicStrategyValue,
        setCompareModeValue,
        incrementTransitionToken,
        isCurrentTransition,
        incrementCompareImageToken,
        loadMosaicBatch,
        resetComparePairs,
        fetchComparePairs,
        showComparePair,
    });

    return {
        mosaicShuffle: (...args) => controller.mosaicShuffle(...args),
        setCompareMode: (...args) => controller.setCompareMode(...args),
        setMosaicStrategy: (...args) => controller.setMosaicStrategy(...args),
    };
}
