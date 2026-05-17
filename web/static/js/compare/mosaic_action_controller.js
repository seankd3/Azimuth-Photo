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
    mosaicLoserIdsImpl = mosaicLoserIds,
    mosaicReplacementIndicesImpl = mosaicReplacementIndices,
    postMosaicPickImpl = postMosaicPick,
} = {}) {
    function mosaicClick(id) {
        if (getMosaicBusy()) return false;
        const mosaicImages = getMosaicImages();
        const idx = mosaicImages.findIndex(img => img.id === id);
        if (idx === -1) return false;
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

        const savePick = postMosaicPickImpl(id, otherIds);

        const propagated = getMosaicPropagationCounts()[id] || 0;
        bumpRankingSignals(otherIds.length + propagated, otherIds.length);
        updateCompareProgress();
        const needsPropagationPoll = propagated <= 0;
        if (propagated > 0) {
            showPropagationBadge(propagated);
        }

        const cells = queryMosaicCells();
        const pickedCell = cells[idx];
        if (pickedCell) pickedCell.classList.add('mosaic-picked');

        const mosaicAge = getMosaicAge();
        for (let i = 0; i < mosaicAge.length; i++) {
            if (i !== idx) mosaicAge[i]++;
        }

        const replaceIndices = mosaicReplacementIndicesImpl(mosaicAge, idx);

        if (getMosaicRenderToken() === snapshot.renderToken) {
            for (const ri of replaceIndices) {
                const targetCell = cells[ri];
                if (!targetCell) continue;
                const mosaicReplacements = getMosaicReplacements();
                if (mosaicReplacements.length === 0) {
                    targetCell.classList.remove('mosaic-picked');
                    mosaicFillReplacements();
                    continue;
                }
                const newImg = mosaicReplacements.shift();
                if (mosaicReplacements.length < replacementLowWater) {
                    mosaicFillReplacements();
                }
                getMosaicImages()[ri] = newImg;
                getMosaicAge()[ri] = 0;
                targetCell.dataset.id = newImg.id;
                targetCell.onclick = () => mosaicClick(newImg.id);
                const imgEl = targetCell.querySelector('img');
                if (imgEl) {
                    imgEl.dataset.tierRank = '0';
                    imgEl.src = newImg.thumb_url;
                    imgEl.alt = newImg.filename;
                    imgEl.classList.add('loaded');
                    targetCell.classList.remove('skeleton-cell');
                }
                targetCell.classList.remove('mosaic-picked');
                scheduleMosaicImageUpgrade(
                    targetCell,
                    newImg,
                    targetCell.clientHeight || 220,
                    getMosaicRenderToken(),
                    ri,
                );
            }
            mosaicFillReplacements();
        }

        setMosaicBusy(false);

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
                    showToast('Failed to save pick');
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
