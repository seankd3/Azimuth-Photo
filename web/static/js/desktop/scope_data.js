import { getRankings } from './api.js';
import { byId, rememberImages, scope, scopeParams, viewState } from './state.js';

export function collectionScopeActive() {
    return Boolean(scope.collectionId);
}

export function similarScopeActive() {
    return scope.similarIds.length > 0;
}

export async function loadScopePage({ limit = 100, offset = 0, sort = null, signal = null } = {}) {
    if (similarScopeActive()) {
        const ids = scope.similarIds.map(Number).filter((id) => id > 0);
        const images = ids.map((id) => byId.get(id)).filter(Boolean);
        const page = images.slice(offset, offset + limit);
        rememberImages(page);
        return {
            images: page,
            visible_images: images.length,
            hidden_pending_thumbnails: 0,
            total_images: images.length,
            sort_quality: null,
            source: 'similar',
        };
    }
    const params = scopeParams({ limit, offset });
    if (sort) params.set('sort', sort);
    const options = signal ? { fetchOptions: { signal } } : {};
    const data = await getRankings(params, options);
    if (data || !viewState.prefs.collapseStacks) return data;
    params.set('stacks', 'expanded');
    return getRankings(params, options);
}
