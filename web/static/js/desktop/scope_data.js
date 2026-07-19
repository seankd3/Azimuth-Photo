import { getRankings } from './api.js';
import { scope, scopeParams, viewState } from './state.js';

export function collectionScopeActive() {
    return Boolean(scope.collectionId);
}

export function similarScopeActive() {
    return scope.similarIds.length > 0;
}

export async function loadScopePage({
    limit = 100,
    offset = 0,
    sort = null,
    signal = null,
    forGridResults = false,
} = {}) {
    if (similarScopeActive()) {
        const ids = scope.similarIds.map(Number).filter((id) => id > 0);
        // Resolve through rankings so quiet exclusion still applies server-side.
        const params = scopeParams({ limit, offset, ids: ids.join(',') }, { forGridResults });
        if (sort) params.set('sort', sort);
        const options = signal ? { fetchOptions: { signal } } : {};
        return getRankings(params, options);
    }
    const params = scopeParams({ limit, offset }, { forGridResults });
    if (sort) params.set('sort', sort);
    const options = signal ? { fetchOptions: { signal } } : {};
    const data = await getRankings(params, options);
    if (data || !viewState.prefs.collapseStacks) return data;
    params.set('stacks', 'expanded');
    return getRankings(params, options);
}
