import { appendFilterParams } from '../query_state.js';
import { appendSearchParams } from '../search/query.js';


export function rankingQueryString({
    queryState,
    limit,
    offset = 0,
    sort = queryState?.sort,
} = {}) {
    const state = { ...(queryState || {}), sort };
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    params.set('sort', sort || state.sort);
    appendFilterParams(params, state.filters);
    appendSearchParams(params, state);
    return params.toString();
}
