import {
    compareThumbUrls,
    imageThumbUrls,
} from './warmup.js';

export const DEFAULT_CROSS_VIEW_WARM_DELAY_MS = 1000;
export const DEFAULT_COMPARE_NEIGHBOR_WARM_DELAY_MS = 350;


export function createNeighborWarmupController({
    getMosaicStrategy,
    getMosaicSize,
    getSearchQuery,
    getRankingsExhausted,
    getRankingsSort,
    getRankingsOffset,
    buildRankingsUrl,
    buildMosaicUrl,
    buildCompareUrl,
    currentQueryState,
    scheduleBackgroundWarm,
    warmRequests,
    initialRankingsPageSize = 48,
    rankingsPageSize = 100,
    compareNeighborPairs = 4,
    crossViewWarmDelayMs = DEFAULT_CROSS_VIEW_WARM_DELAY_MS,
    compareNeighborWarmDelayMs = DEFAULT_COMPARE_NEIGHBOR_WARM_DELAY_MS,
    imageThumbUrlsImpl = imageThumbUrls,
    compareThumbUrlsImpl = compareThumbUrls,
} = {}) {
    function scheduleRequests(key, requests, delay) {
        scheduleBackgroundWarm(
            key,
            (token, generation) => warmRequests(key, token, generation, requests),
            delay,
        );
    }

    function scheduleCrossViewWarmup(fromView) {
        if (fromView === 'compare') {
            const libraryUrl = buildRankingsUrl({
                sort: 'elo',
                limit: initialRankingsPageSize,
                offset: 0,
            });
            scheduleRequests(
                'crossview-library',
                [{
                    url: libraryUrl,
                    cacheKey: `library:${libraryUrl}`,
                    extract: imageThumbUrlsImpl,
                }],
                crossViewWarmDelayMs,
            );
        } else if (fromView === 'library') {
            const strategy = getMosaicStrategy();
            const warmStrategy = strategy === 'diverse' ? 'explore' : strategy;
            const compareUrl = buildMosaicUrl({
                strategy: warmStrategy,
                gridElo: 0,
                n: getMosaicSize(),
            });
            scheduleRequests(
                'crossview-compare',
                [{
                    url: compareUrl,
                    cacheKey: `compare:${compareUrl}`,
                    extract: imageThumbUrlsImpl,
                }],
                crossViewWarmDelayMs,
            );
        }
    }

    function scheduleLibraryNeighborWarmup() {
        if (getSearchQuery() || getRankingsExhausted()) return;
        const rankingsSort = getRankingsSort();
        const nextUrl = buildRankingsUrl({
            queryState: currentQueryState({ sort: rankingsSort }),
            sort: rankingsSort,
            limit: rankingsPageSize,
            offset: getRankingsOffset(),
        });
        scheduleRequests(
            'library-next-page',
            [{ url: nextUrl, cacheKey: `library:${nextUrl}`, extract: imageThumbUrlsImpl }],
        );
    }

    function scheduleCompareNeighborWarmup(mode) {
        if (mode === 'mosaic') return;
        scheduleRequests(
            'compare-next-pairs',
            [{
                url: buildCompareUrl(mode, compareNeighborPairs),
                extract: compareThumbUrlsImpl,
            }],
            compareNeighborWarmDelayMs,
        );
    }

    return {
        scheduleCompareNeighborWarmup,
        scheduleCrossViewWarmup,
        scheduleLibraryNeighborWarmup,
    };
}
