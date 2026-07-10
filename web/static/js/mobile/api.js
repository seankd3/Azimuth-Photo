// Mobile API layer. Every function talks to the real backend —
// payload shapes mirror the desktop modules exactly.

import { fetchJson } from '../api.js';
import { isOffline } from './state.js';

export { fetchJson };

let lastWriteFailure = null;

export function writeFailureMessage() {
    return lastWriteFailure === 'offline' || isOffline()
        ? 'Offline — couldn’t save'
        : 'Couldn’t save — try again';
}

export async function postJson(url, body) {
    if (isOffline()) {
        lastWriteFailure = 'offline';
        return null;
    }
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            lastWriteFailure = 'server';
            return null;
        }
        lastWriteFailure = null;
        return await response.json();
    } catch {
        lastWriteFailure = isOffline() ? 'offline' : 'server';
        return null;
    }
}

export function thumbUrl(size, imageId) {
    return `/api/thumb/${size}/${imageId}`;
}

export async function getRankings(params) {
    return fetchJson(`/api/rankings?${params.toString()}`, { defaultValue: null });
}

export async function getStack(stackId) {
    return fetchJson(`/api/stacks/${encodeURIComponent(stackId)}`, { defaultValue: null });
}

export async function getSimilar(imageId, limit = 100) {
    return fetchJson(`/api/similar/${imageId}?limit=${limit}`, { defaultValue: null });
}

export async function getExif(imageId) {
    return fetchJson(`/api/image/${imageId}/exif`, { defaultValue: null });
}

export async function getImageCaption(imageId) {
    try {
        const response = await fetch(`/api/image/${imageId}/caption`);
        if (response.status === 404) {
            return { has_caption: false, caption: '', tags: [], not_found: true };
        }
        if (!response.ok) {
            return { has_caption: false, caption: '', tags: [], error: true, status: response.status };
        }
        return await response.json();
    } catch {
        return { has_caption: false, caption: '', tags: [], error: true, status: 0 };
    }
}

export async function getDateHistogram(params) {
    return fetchJson(`/api/date-histogram?${params.toString()}`, { defaultValue: null });
}

export async function getCounts(params) {
    return fetchJson(`/api/counts?${params.toString()}`, { defaultValue: null });
}

// Flags: same typed payloads as the shared image flag API.
export async function writeFlag(imageId, flag) {
    return postJson(`/api/image/${imageId}/flag`, { flag });
}

export async function writeFlags(imageIds, flag) {
    return postJson('/api/images/flag', { image_ids: imageIds, flag });
}

// Refine: same typed payloads as the compare mosaic and undo API.
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
    if (isOffline()) {
        lastWriteFailure = 'offline';
        return null;
    }
    try {
        const response = await fetch('/api/compare/undo', { method: 'POST' });
        if (!response.ok) {
            lastWriteFailure = 'server';
            return null;
        }
        lastWriteFailure = null;
        return await response.json();
    } catch {
        lastWriteFailure = isOffline() ? 'offline' : 'server';
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

export async function createCollection(name, imageIds = [], description = '') {
    return postJson('/api/user-collections', {
        name,
        description,
        image_ids: imageIds,
    });
}

export async function renameCollection(collectionId, name) {
    return postJson(`/api/user-collections/${collectionId}/rename`, { name });
}

export async function deleteCollection(collectionId) {
    return postJson(`/api/user-collections/${collectionId}/delete`, {});
}

export async function addToCollection(collectionId, imageIds) {
    return postJson(`/api/user-collections/${collectionId}/images`, { image_ids: imageIds });
}

export async function removeFromCollection(collectionId, imageIds) {
    return postJson(`/api/user-collections/${collectionId}/images/remove`, { image_ids: imageIds });
}

export async function getCollectionShare(collectionId) {
    return fetchJson(`/api/user-collections/${collectionId}/share`, { defaultValue: null });
}

export async function getCollectionShareFavorites(collectionId) {
    return fetchJson(`/api/user-collections/${collectionId}/share/favorites`, { defaultValue: { favorites: [], count: 0 } });
}

export async function createCollectionShare(
    collectionId,
    {
        rotate = false,
        expiresInDays = null,
        password = undefined,
        clearPassword = false,
    } = {},
) {
    const payload = {
        rotate,
        expires_in_days: expiresInDays,
    };
    if (password !== undefined) payload.password = password;
    if (clearPassword) payload.clear_password = true;
    return postJson(`/api/user-collections/${collectionId}/share`, {
        ...payload,
    });
}

export async function revokeCollectionShare(collectionId) {
    return postJson(`/api/user-collections/${collectionId}/share/revoke`, {});
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

export async function getTags(limit = 24, q = '') {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set('q', q);
    return fetchJson(`/api/tags?${params.toString()}`, { defaultValue: { tags: [] } });
}

export async function getFoldersTree() {
    return fetchJson('/api/folders/tree', { defaultValue: { sources: [] } });
}

export async function labelPerson(personId, name) {
    return postJson(`/api/people/${personId}/label`, { name });
}

export async function ignorePerson(personId) {
    return postJson(`/api/people/${personId}/ignore`, {});
}

export async function getAiStatus() {
    return fetchJson('/api/ai/status', { defaultValue: null });
}

export async function getCacheStatus() {
    return fetchJson('/api/cache/status', { defaultValue: null });
}

export async function getPeopleStatus() {
    return fetchJson('/api/people/status', { defaultValue: null });
}

export async function setBackgroundWork(kind, action) {
    const urls = {
        ai: {
            pause: '/api/ai/embeddings/pause',
            resume: '/api/ai/embeddings/resume',
        },
        cache: {
            pause: '/api/cache/pregen/stop',
            resume: '/api/cache/pregen/start',
        },
        people: {
            pause: '/api/people/scan/pause',
            resume: '/api/people/scan/resume',
        },
    };
    const url = urls[kind] && urls[kind][action];
    if (!url) return null;
    return postJson(url, {});
}

export function exportUrl(imageIds, format = 'csv', size = '') {
    const params = new URLSearchParams({ format, ids: imageIds.join(',') });
    if (size) params.set('size', size);
    return `/api/export?${params.toString()}`;
}
