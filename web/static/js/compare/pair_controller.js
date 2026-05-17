export function createComparePairController({
    getCompareMode,
    getCompareIndex,
    setCompareIndex,
    getComparePairs,
    setComparePairs,
    setCompareStats,
    incrementCompareImageToken,
    getCompareImageToken,
    buildCompareUrl,
    takeWarmCache,
    fetchWarmJson,
    preloadImage,
    primeMediaStatuses,
    renderCompareImage,
    warmImageTiers,
    updateCompareProgress,
    scheduleCompareNeighborWarmup,
    scheduleCrossViewWarmup,
    showCompareEmpty,
    documentImpl = globalThis.document,
    pairBatchSize = 8,
    prefetchLowWater = 4,
} = {}) {
    async function fetchComparePairs() {
        const url = buildCompareUrl(getCompareMode(), pairBatchSize);
        const useCache = getCompareIndex() === 0 && getComparePairs().length === 0;
        const data = (useCache ? takeWarmCache(`compare:${url}`) : null) || await fetchWarmJson(url);
        if (!data) return null;
        setCompareStats(data.stats || {});

        if (data.pairs.length === 0 && getComparePairs().length === 0) {
            showCompareEmpty();
            return data;
        }

        for (const pair of data.pairs) {
            getComparePairs().push(pair);
            preloadImage(pair.left.thumb_url);
            preloadImage(pair.right.thumb_url);
        }

        if (getCompareIndex() === 0) {
            scheduleCompareNeighborWarmup(getCompareMode());
        }
        scheduleCrossViewWarmup('compare');
        return data;
    }

    function showComparePair() {
        if (getCompareIndex() >= getComparePairs().length) {
            setCompareIndex(0);
            setComparePairs([]);
            fetchComparePairs().then(() => {
                if (getComparePairs().length > 0) showComparePair();
            });
            return false;
        }

        const pair = getComparePairs()[getCompareIndex()];
        const token = incrementCompareImageToken();
        const leftImg = documentImpl.getElementById('compare-left-img');
        const rightImg = documentImpl.getElementById('compare-right-img');
        const leftInfo = documentImpl.getElementById('compare-left-info');
        const rightInfo = documentImpl.getElementById('compare-right-info');

        if (leftInfo) leftInfo.textContent = `${pair.left.filename} — ${pair.left.elo}`;
        if (rightInfo) rightInfo.textContent = `${pair.right.filename} — ${pair.right.elo}`;
        primeMediaStatuses([pair.left.id, pair.right.id]);
        renderCompareImage(pair.left, leftImg, 'left', token);
        renderCompareImage(pair.right, rightImg, 'right', token);
        warmImageTiers({
            lg: [pair.left.id, pair.right.id],
            full: [pair.left.id, pair.right.id],
        });

        updateCompareProgress();

        if (getComparePairs().length - getCompareIndex() < prefetchLowWater) {
            fetchComparePairs();
        }
        return true;
    }

    function isCurrentCompareImage(token) {
        return token === getCompareImageToken() && getCompareIndex() < getComparePairs().length;
    }

    return {
        fetchComparePairs,
        isCurrentCompareImage,
        showComparePair,
    };
}
