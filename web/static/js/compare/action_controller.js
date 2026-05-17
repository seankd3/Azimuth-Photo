import {
    applyComparisonElos,
    buildComparisonPayload,
    postComparison,
    postUndoComparison,
    undoComparisonToastText,
} from './actions.js';


export function createCompareActionController({
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
    buildComparisonPayloadImpl = buildComparisonPayload,
    postComparisonImpl = postComparison,
    applyComparisonElosImpl = applyComparisonElos,
    postUndoComparisonImpl = postUndoComparison,
    undoComparisonToastTextImpl = undoComparisonToastText,
} = {}) {
    function submitComparison(side) {
        if (getCompareBusy() || getCompareIndex() >= getComparePairs().length) return false;
        setCompareBusy(true);
        setUndoCount(0);
        const actionSeq = incrementCompareActionSeq();

        const pair = getComparePairs()[getCompareIndex()];
        const previousIndex = getCompareIndex();
        const payload = buildComparisonPayloadImpl(pair, side, getCompareMode());

        setCompareIndex(previousIndex + 1);
        showComparePair();
        setCompareBusy(false);

        postComparisonImpl(payload).then((result) => {
            applyComparisonElosImpl(pair, side, result);
            fetchPropagationCount(1);
        }).catch(() => {
            if (getCompareActionSeq() === actionSeq && getCompareMode() !== 'mosaic') {
                setCompareIndex(previousIndex);
                showComparePair();
                showToast('Failed to save comparison; restored the previous pair');
            } else {
                showToast('Failed to save comparison');
            }
        });
        return true;
    }

    async function undoComparison() {
        if (getCompareBusy()) return false;
        const undoCount = incrementUndoCount();
        if (undoCount > 3) {
            showToast('Maximum undo reached');
            return false;
        }
        setCompareBusy(true);
        try {
            const result = await postUndoComparisonImpl();
            if (result.ok) {
                const comparisonsUndone = result.comparisonsUndone;
                bumpRankingSignals(-comparisonsUndone, -comparisonsUndone);
                updateCompareProgress();
                if (getCompareMode() !== 'mosaic' && getCompareIndex() > 0) {
                    setCompareIndex(getCompareIndex() - 1);
                    showComparePair();
                } else if (getCompareMode() === 'mosaic') {
                    showToast(undoComparisonToastTextImpl(comparisonsUndone));
                }
            } else {
                showToast('Undo failed');
            }
        } catch {
            showToast('Undo failed');
        } finally {
            setCompareBusy(false);
        }
        return true;
    }

    return {
        submitComparison,
        undoComparison,
    };
}
