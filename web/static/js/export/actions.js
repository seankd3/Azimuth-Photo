import { rankingQueryString } from '../library/query.js';


export function fullRankingsExportUrl(format, {
    queryState = {},
    limit = 10000,
    offset = 0,
    sort = queryState?.sort || 'elo',
} = {}) {
    const params = new URLSearchParams(rankingQueryString({
        queryState,
        limit,
        offset,
        sort,
    }));
    params.set('format', format);
    return `/api/export?${params.toString()}`;
}


export function selectedImagesExportUrl(format, {
    imageIds = [],
} = {}) {
    const ids = imageIds.join(',');
    return `/api/export?format=${format}&ids=${ids}`;
}


export function exportRankings(format, {
    windowImpl = window,
    ...urlOptions
} = {}) {
    windowImpl.open(fullRankingsExportUrl(format, urlOptions), '_blank');
}


export function batchExport(format, {
    windowImpl = window,
    ...urlOptions
} = {}) {
    windowImpl.open(selectedImagesExportUrl(format, urlOptions), '_blank');
}
