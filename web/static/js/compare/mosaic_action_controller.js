import {
    mosaicLoserIds,
    mosaicReplacementIndices,
    postMosaicPick,
} from './mosaic.js';


export function createMosaicActionController({
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
    queryMosaicCells,
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
    replacementLowWater = 2,
    replacementWaitMs = 160,
    replacementWaitRetries = 30,
    setTimeoutImpl = globalThis.setTimeout,
    mosaicLoserIdsImpl = mosaicLoserIds,
    mosaicReplacementIndicesImpl = mosaicReplacementIndices,
    postMosaicPickImpl = postMosaicPick,
} = {}) {
    let pickSaveQueue = Promise.resolve();
    let pickSaveQueueActive = false;
    let pickSaveQueueToken = 0;

    function enqueueMosaicPick(winnerId, loserIds) {
        const run = () => Promise.resolve()
            .then(() => postMosaicPickImpl(winnerId, loserIds))
            .catch((error) => ({ ok: false, error }));
        const queued = pickSaveQueueActive ? pickSaveQueue.then(run, run) : run();
        const token = ++pickSaveQueueToken;
        pickSaveQueueActive = true;
        pickSaveQueue = queued.catch(() => {});
        pickSaveQueue.finally(() => {
            if (token === pickSaveQueueToken) {
                pickSaveQueueActive = false;
            }
        });
        return queued;
    }

    function markCellReplacing(cell) {
        cell?.classList?.add?.('mosaic-replacing');
        cell?.setAttribute?.('aria-busy', 'true');
    }

    function clearCellReplacing(cell) {
        cell?.classList?.remove?.('mosaic-replacing');
        cell?.removeAttribute?.('aria-busy');
    }

    function replaceMosaicCell(index, snapshot, {
        attempt = 0,
        onSettled = () => {},
    } = {}) {
        if (getMosaicRenderToken() !== snapshot.renderToken) {
            if (attempt > 0) onSettled();
            return false;
        }
        const targetCell = queryMosaicCells()[index];
        if (!targetCell) {
            if (attempt > 0) onSettled();
            return false;
        }
        const mosaicReplacements = getMosaicReplacements();
        if (mosaicReplacements.length === 0) {
            targetCell.classList.remove('mosaic-picked');
            markCellReplacing(targetCell);
            mosaicFillReplacements();
            if (attempt < replacementWaitRetries) {
                setTimeoutImpl(() => replaceMosaicCell(index, snapshot, {
                    attempt: attempt + 1,
                    onSettled,
                }), replacementWaitMs);
                return 'pending';
            }
            clearCellReplacing(targetCell);
            if (attempt > 0) onSettled();
            return false;
        }

        const newImg = mosaicReplacements.shift();
        if (mosaicReplacements.length < replacementLowWater) {
            mosaicFillReplacements();
        }
        markCellReplacing(targetCell);
        getMosaicImages()[index] = newImg;
        getMosaicAge()[index] = 0;
        targetCell.dataset.id = String(newImg.id);
        targetCell.onclick = () => mosaicClick(newImg.id);
        const imgEl = targetCell.querySelector('img');
        const finishSwap = () => {
            if (targetCell.dataset.id !== String(newImg.id)) return;
            targetCell.classList.remove('mosaic-picked');
            clearCellReplacing(targetCell);
            scheduleMosaicImageUpgrade(
                targetCell,
                newImg,
                targetCell.clientHeight || 220,
                getMosaicRenderToken(),
                index,
            );
            if (attempt > 0) onSettled();
        };
        if (imgEl) {
            imgEl.dataset.tierRank = '0';
            imgEl.alt = newImg.filename;
            imgEl.classList.add('loaded');
            targetCell.classList.remove('skeleton-cell');
            const nextSrc = newImg.thumb_url || '';
            const ImageCtor = targetCell.ownerDocument?.defaultView?.Image || globalThis.Image;
            if (ImageCtor && nextSrc) {
                let committed = false;
                const commit = () => {
                    if (committed) return;
                    committed = true;
                    imgEl.src = nextSrc;
                    finishSwap();
                };
                const probe = new ImageCtor();
                probe.onload = commit;
                probe.onerror = commit;
                probe.src = nextSrc;
                setTimeoutImpl(commit, newImg.cache_probe_deferred ? 220 : 80);
                return true;
            }
            imgEl.src = nextSrc;
        }
        finishSwap();
        return true;
    }

    function mosaicClick(id) {
        const mosaicImages = getMosaicImages();
        const idx = mosaicImages.findIndex(img => img.id === id);
        if (idx === -1) return false;
        const cells = queryMosaicCells();
        const pickedCell = cells[idx];
        if (pickedCell?.classList?.contains?.('mosaic-replacing')) return false;
        setMosaicBusy(true);
        setUndoCount(0);
        const actionSeq = incrementMosaicActionSeq();

        const otherIds = mosaicLoserIdsImpl(mosaicImages, id);

        const snapshot = {
            renderToken: getMosaicRenderToken(),
            images: mosaicImages.slice(),
            age: getMosaicAge().slice(),
            replacements: getMosaicReplacements().slice(),
            stats: { ...getCompareStats() },
            propagationCounts: { ...getMosaicPropagationCounts() },
        };

        const savePick = enqueueMosaicPick(id, otherIds);

        const propagated = getMosaicPropagationCounts()[id] || 0;
        bumpRankingSignals(otherIds.length + propagated, otherIds.length);
        updateCompareProgress();
        const needsPropagationPoll = propagated <= 0;
        if (propagated > 0) {
            showPropagationBadge(propagated);
        }

        if (pickedCell) pickedCell.classList.add('mosaic-picked');

        const mosaicAge = getMosaicAge();
        for (let i = 0; i < mosaicAge.length; i++) {
            if (i !== idx) mosaicAge[i]++;
        }

        const replaceIndices = mosaicReplacementIndicesImpl(mosaicAge, idx);

        let pendingReplacements = 0;
        const settlePendingReplacement = () => {
            pendingReplacements = Math.max(0, pendingReplacements - 1);
            if (pendingReplacements === 0) {
                setMosaicBusy(false);
            }
        };

        if (getMosaicRenderToken() === snapshot.renderToken) {
            for (const ri of replaceIndices) {
                const state = replaceMosaicCell(ri, snapshot, { onSettled: settlePendingReplacement });
                if (state === 'pending') pendingReplacements++;
            }
            mosaicFillReplacements();
        }

        if (pendingReplacements === 0) {
            setMosaicBusy(false);
        }

        savePick.then((saveResult) => {
            if (!saveResult.ok) {
                if (getMosaicRenderToken() === snapshot.renderToken && getMosaicActionSeq() === actionSeq) {
                    setMosaicImages(snapshot.images);
                    setMosaicAge(snapshot.age);
                    setMosaicReplacements(snapshot.replacements);
                    setCompareStats(snapshot.stats);
                    setMosaicPropagationCounts(snapshot.propagationCounts);
                    renderMosaic();
                    updateCompareProgress();
                    showToast('Failed to save pick; restored the previous grid');
                } else {
                    // The grid has already moved on. Avoid interrupting fast picking
                    // for a stale save failure from an older action.
                }
                return;
            }

            if (needsPropagationPoll) {
                fetchPropagationCount(0);
            }
            mosaicFillReplacements();
            precomputePropagation();

            if (getMosaicImages().length < 2) {
                showCompareEmpty();
            }
        });
        return true;
    }

    return {
        mosaicClick,
    };
}
