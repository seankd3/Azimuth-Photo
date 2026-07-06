// Mobile API layer. Every function talks to the real backend —
// payload shapes mirror the desktop modules exactly.

import { fetchJson } from '../api.js';

export { fetchJson };

export async function postJson(url, body) {
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok) return null;
        return await response.json();
    } catch {
        return null;
    }
}

export function thumbUrl(size, imageId) {
    return `/api/thumb/${size}/${imageId}`;
}

export async function getRankings(params) {
    return fetchJson(`/api/rankings?${params.toString()}`, { defaultValue: null });
}

export async function getDateHistogram(params) {
    return fetchJson(`/api/date-histogram?${params.toString()}`, { defaultValue: null });
}

export async function getCounts(params) {
    return fetchJson(`/api/counts?${params.toString()}`, { defaultValue: null });
}

// Flags: same payloads as static/js/library/flags.js and
// features/settings/routes.py (api_set_image_flag / api_batch_set_flag).
export async function writeFlag(imageId, flag) {
    return postJson(`/api/image/${imageId}/flag`, { flag });
}

export async function writeFlags(imageIds, flag) {
    return postJson('/api/images/flag', { image_ids: imageIds, flag });
}

// Refine: same payloads as static/js/compare mosaic flow and
// features/compare/routes.py (mosaic_pick / compare_undo).
export async function mosaicNext(n, params, exclude = '') {
    const query = new URLSearchParams(params);
    query.set('n', String(n));
    query.set('strategy', 'explore');
    if (exclude) query.set('exclude', exclude);
    return fetchJson(`/api/mosaic/next?${query.toString()}`, { defaultValue: null });
}

export async function mosaicPick(winnerId, loserIds) {
    return postJson('/api/mosaic/pick', { winner_id: winnerId, loser_ids: loserIds });
}

export async function compareUndo() {
    try {
        const response = await fetch('/api/compare/undo', { method: 'POST' });
        if (!response.ok) return null;
        return await response.json();
    } catch {
        return null;
    }
}

// Collections: typed bodies from features/collections/routes.py
// (CreateCollectionBody / CollectionImagesBody).
export async function listCollections() {
    return fetchJson('/api/user-collections', { defaultValue: null });
}

export async function getCollection(collectionId, limit = 500) {
    return fetchJson(`/api/user-collections/${collectionId}?limit=${limit}`, { defaultValue: null });
}

export async function createCollection(name, imageIds = []) {
    return postJson('/api/user-collections', {
        name,
        description: '',
        image_ids: imageIds,
    });
}

export async function addToCollection(collectionId, imageIds) {
    return postJson(`/api/user-collections/${collectionId}/images`, { image_ids: imageIds });
}

export async function removeFromCollection(collectionId, imageIds) {
    return postJson(`/api/user-collections/${collectionId}/images/remove`, { image_ids: imageIds });
}

export async function getPeople(limit = 24) {
    return fetchJson(`/api/people?limit=${limit}`, { defaultValue: null });
}

export async function getCatalog() {
    return fetchJson('/api/catalog', { defaultValue: null });
}

export async function getFilterOptions() {
    return fetchJson('/api/filter-options', { defaultValue: null });
}

export function exportUrl(imageIds, format = 'csv') {
    return `/api/export?format=${format}&ids=${imageIds.join(',')}`;
}
