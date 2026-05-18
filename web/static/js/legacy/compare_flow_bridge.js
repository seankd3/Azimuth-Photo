import { createCompareActionController } from '../compare/action_controller.js';
import { precomputePropagationCounts } from '../compare/propagation.js';
import { createCompareStatusController } from '../compare/status_controller.js';

export function createLegacyCompareFlowBridge({
    fetchImpl = fetch,
    getCompareStats,
    getMosaicImages,
    setMosaicPropagationCounts,
    getCompareBusy,
    setCompareBusy,
    setUndoCount,
    incrementUndoCount,
    incrementCompareActionSeq,
    getCompareActionSeq,
    getCompareIndex,
    setCompareIndex,
    getComparePairs,
    getCompareMode,
    showComparePair,
    showToast,
    createCompareStatusControllerImpl = createCompareStatusController,
    createCompareActionControllerImpl = createCompareActionController,
    precomputePropagationCountsImpl = precomputePropagationCounts,
} = {}) {
    const compareStatusController = createCompareStatusControllerImpl({
        getCompareStats,
        fetchImpl,
    });

    function bumpRankingSignals(signalDelta, directDelta = 0) {
        return compareStatusController.bumpRankingSignals(signalDelta, directDelta);
    }

    function updateCompareProgress() {
        return compareStatusController.updateCompareProgress();
    }

    function renderCoverageBar(stats = getCompareStats()) {
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
        return precomputePropagationCountsImpl(getMosaicImages(), {
            onCounts: setMosaicPropagationCounts,
        });
    }

    function fetchPropagationCount(directCount = 0) {
        return compareStatusController.fetchPropagationCount(directCount);
    }

    function showPropagationBadge(count) {
        return compareStatusController.showPropagationBadge(count);
    }

    const compareActionController = createCompareActionControllerImpl({
        getCompareBusy,
        setCompareBusy,
        setUndoCount,
        incrementUndoCount,
        incrementCompareActionSeq,
        getCompareActionSeq,
        getCompareIndex,
        setCompareIndex,
        getComparePairs,
        getCompareMode,
        showComparePair,
        showToast,
        fetchPropagationCount,
        bumpRankingSignals,
        updateCompareProgress,
    });

    function submitComparison(side) {
        return compareActionController.submitComparison(side);
    }

    function undoComparison() {
        return compareActionController.undoComparison();
    }

    return {
        bumpRankingSignals,
        fetchPropagationCount,
        mergeCoverageStats,
        precomputePropagation,
        renderCoverageBar,
        rollUpCounter,
        showPropagationBadge,
        submitComparison,
        undoComparison,
        updateCompareProgress,
        updateCoverageBar,
    };
}
