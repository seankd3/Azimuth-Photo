import { appendFilterParams, appendScopeParams } from '../query_state.js';
import { appendSearchParams } from '../search/query.js';


export const MOSAIC_NEIGHBOR_LIMIT = 8;
export const COMPARE_NEIGHBOR_PAIRS = 4;


export function buildMosaicUrl({
    strategy,
    queryState,
    gridElo,
    n,
    exclude = '',
} = {}) {
    const state = queryState || {};
    const params = new URLSearchParams();
    params.set('n', String(n));
    params.set('strategy', strategy);
    params.set('grid_elo', String(gridElo));
    appendFilterParams(params, state.filters);
    appendSearchParams(params, state);
    appendScopeParams(params, state);
    if (exclude) params.set('exclude', exclude);
    return `/api/mosaic/next?${params.toString()}`;
}


export function buildCompareUrl({
    mode,
    n,
    queryState,
} = {}) {
    const state = queryState || {};
    const params = new URLSearchParams();
    params.set('n', String(n));
    params.set('mode', mode);
    appendFilterParams(params, state.filters);
    appendSearchParams(params, state);
    appendScopeParams(params, state);
    return `/api/compare/next?${params.toString()}`;
}
