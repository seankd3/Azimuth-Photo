import { getCollection, getRankings } from './api.js';
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

export async function loadCollectionImages(collectionId = scope.collectionId) {
    if (!collectionId) return [];
    const first = await getCollection(collectionId, { limit: 1000, offset: 0 });
    const collection = first && first.collection;
    if (!collection) return [];
    const total = Number(collection.image_count) || 0;
    let images = collection.images || [];
    let offset = images.length;
    while (offset < total) {
        const page = await getCollection(collectionId, { limit: 1000, offset });
        const incoming = (page && page.collection && page.collection.images) || [];
        if (!incoming.length) break;
        images = images.concat(incoming);
        offset += incoming.length;
    }
    rememberImages(images);
    return images;
}

export async function loadCollectionImageIds(collectionId = scope.collectionId) {
    const images = await loadCollectionImages(collectionId);
    return images.map((img) => Number(img.id)).filter((id) => id > 0);
}
