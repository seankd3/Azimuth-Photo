import { setCompareModeView } from './view.js';


export function createCompareModeController({
    documentImpl = document,
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
    setCompareModeViewImpl = setCompareModeView,
} = {}) {
    function setMosaicStrategy(strategy) {
        clearWarmups();
        setMosaicStrategyValue(strategy);
        const btn = documentImpl.getElementById('strategy-' + strategy);
        if (btn) {
            btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        }
        loadMosaicBatch();
    }

    function mosaicShuffle() {
        loadMosaicBatch();
    }

    function setCompareMode(mode) {
        const nextMode = 'mosaic';
        clearWarmups();
        setCompareModeValue(nextMode);
        const transitionToken = incrementTransitionToken();
        setCompareModeViewImpl(nextMode, {
            transitionToken,
            isCurrentTransition,
            onMosaic: () => {
                incrementCompareImageToken();
                loadMosaicBatch();
            },
        });
    }

    return {
        mosaicShuffle,
        setCompareMode,
        setMosaicStrategy,
    };
}
