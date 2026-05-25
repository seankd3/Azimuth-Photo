import {
    fetchPropagationCount as fetchPropagationCountCore,
} from './propagation.js';
import {
    bumpRankingSignals as bumpRankingSignalsCore,
    mergeCoverageStats as mergeCoverageStatsCore,
    renderCompareProgress as renderCompareProgressCore,
    renderCoverageBar as renderCoverageBarCore,
    rollUpCounter as rollUpCounterCore,
    showPropagationBadge as showPropagationBadgeCore,
} from './status.js';


export const COVERAGE_STATS_THROTTLE_MS = 30000;
export const COVERAGE_STATS_RETRY_MS = 5000;


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export function createCompareStatusController({
    getCompareStats,
    fetchImpl = globalThis.fetch,
    nowImpl = () => Date.now(),
    coverageStatsThrottleMs = COVERAGE_STATS_THROTTLE_MS,
    coverageStatsRetryMs = COVERAGE_STATS_RETRY_MS,
    bumpRankingSignalsImpl = bumpRankingSignalsCore,
    mergeCoverageStatsImpl = mergeCoverageStatsCore,
    renderCompareProgressImpl = renderCompareProgressCore,
    renderCoverageBarImpl = renderCoverageBarCore,
    rollUpCounterImpl = rollUpCounterCore,
    fetchPropagationCountImpl = fetchPropagationCountCore,
    showPropagationBadgeImpl = showPropagationBadgeCore,
} = {}) {
    let displayedComparisons = -1;
    let coverageStatsLastFetched = 0;
    let coverageStatsFetchPromise = null;

    function bumpRankingSignals(signalDelta, directDelta = 0) {
        return bumpRankingSignalsImpl(getCompareStats(), signalDelta, directDelta);
    }

    function renderCoverageBar(stats = getCompareStats()) {
        return renderCoverageBarImpl(stats);
    }

    function mergeCoverageStats(stats) {
        return mergeCoverageStatsImpl(getCompareStats(), stats);
    }

    function rollUpCounter(el, from, to) {
        return rollUpCounterImpl(el, from, to);
    }

    function updateCompareProgress() {
        displayedComparisons = renderCompareProgressImpl({
            stats: getCompareStats(),
            displayedComparisons,
            updateCoverageBarImpl: updateCoverageBar,
            rollUpCounterImpl: rollUpCounter,
        });
        return displayedComparisons;
    }

    function updateCoverageBar() {
        if (!renderCoverageBar()) return false;
        const now = nowImpl();
        if (coverageStatsFetchPromise || now - coverageStatsLastFetched < coverageStatsThrottleMs) {
            return false;
        }
        coverageStatsLastFetched = now;
        coverageStatsFetchPromise = fetchImpl('/api/stats')
            .then((res) => responseDataOrError(res, 'Coverage stats unavailable'))
            .then((stats) => {
                mergeCoverageStats(stats);
                renderCoverageBar(stats);
                updateCompareProgress();
            })
            .catch(() => {
                coverageStatsLastFetched = nowImpl() - Math.max(0, coverageStatsThrottleMs - coverageStatsRetryMs);
            })
            .finally(() => {
                coverageStatsFetchPromise = null;
            });
        return true;
    }

    function showPropagationBadge(count) {
        return showPropagationBadgeImpl(count);
    }

    function fetchPropagationCount(directCount = 0) {
        return fetchPropagationCountImpl(directCount, {
            fetchImpl,
            onApply: (total, direct) => {
                bumpRankingSignals(total, direct);
                updateCompareProgress();
            },
            onBadge: showPropagationBadge,
        });
    }

    return {
        bumpRankingSignals,
        fetchPropagationCount,
        mergeCoverageStats,
        renderCoverageBar,
        rollUpCounter,
        showPropagationBadge,
        updateCompareProgress,
        updateCoverageBar,
        coverageStatsFetchPromise: () => coverageStatsFetchPromise,
    };
}
