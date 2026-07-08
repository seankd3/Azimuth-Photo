import { fetchJson } from '../api.js';

export { fetchJson };

export async function postJson(url, body = null) {
    try {
        const options = { method: 'POST' };
        if (body != null) {
            options.headers = { 'Content-Type': 'application/json' };
            options.body = JSON.stringify(body);
        }
        const response = await fetch(url, options);
        if (!response.ok) return null;
        return await response.json();
    } catch {
        return null;
    }
}

export function thumbUrl(size, imageId) {
    return `/api/thumb/${size}/${imageId}`;
}

export function exportUrl(params) {
    return `/api/export?${params.toString()}`;
}

export async function getRankings(params) {
    return fetchJson(`/api/rankings?${params.toString()}`, { defaultValue: null });
}

export async function getPeople(limit = 48) {
    return fetchJson(`/api/people?limit=${limit}`, { defaultValue: null });
}

export async function getFolders() {
    return fetchJson('/api/folders?max_depth=0', { defaultValue: null });
}

export async function getFilterOptions() {
    return fetchJson('/api/filter-options', { defaultValue: null });
}

export async function labelPerson(personId, name) {
    return postJson(`/api/people/${personId}/label`, { name });
}

export async function ignorePerson(personId) {
    return postJson(`/api/people/${personId}/ignore`, {});
}

export async function mergePeople(sourcePersonId, targetPersonId) {
    return postJson('/api/people/merge', {
        source_person_id: sourcePersonId,
        target_person_id: targetPersonId,
    });
}

export async function rejectMergeSuggestion(suggestionId) {
    return postJson(`/api/people/merge-suggestions/${suggestionId}/reject`, {});
}

export async function getCatalog() {
    return fetchJson('/api/catalog', { defaultValue: null });
}

export async function addCatalogSource(path, scan = true) {
    return postJson('/api/catalog/sources', { path, scan });
}

export async function rescanCatalogSource(sourceId) {
    return postJson(`/api/catalog/sources/${sourceId}/rescan`);
}

export async function removeCatalogSource(sourceId, mode = 'keep') {
    return postJson(`/api/catalog/sources/${sourceId}/remove`, { mode });
}

export async function getScanStatus() {
    return fetchJson('/api/scan/status', { defaultValue: null });
}

export async function getImageExif(imageId) {
    return fetchJson(`/api/image/${imageId}/exif`, { defaultValue: null });
}

export async function getAiStatus() {
    return fetchJson('/api/ai/status', { defaultValue: null });
}

export async function getSettings() {
    return fetchJson('/api/settings', { defaultValue: null });
}

export async function saveSettings(fields) {
    return postJson('/api/settings', fields || {});
}

export async function resetSettings() {
    return postJson('/api/settings/reset');
}

export async function installAiModel(role = 'active') {
    return postJson(`/api/ai/model/install?role=${encodeURIComponent(role || 'active')}`);
}

export async function pauseAiEmbeddings() {
    return postJson('/api/ai/embeddings/pause');
}

export async function resumeAiEmbeddings() {
    return postJson('/api/ai/embeddings/resume');
}

export async function getCacheStatus() {
    return fetchJson('/api/cache/status', { defaultValue: null });
}

export async function startCachePregen() {
    return postJson('/api/cache/pregen/start');
}

export async function stopCachePregen() {
    return postJson('/api/cache/pregen/stop');
}

export async function clearCache() {
    return postJson('/api/cache/clear');
}

export async function getPeopleStatus() {
    return fetchJson('/api/people/status', { defaultValue: null });
}

export async function pausePeopleScan() {
    return postJson('/api/people/scan/pause');
}

export async function resumePeopleScan() {
    return postJson('/api/people/scan/resume');
}

export async function getRemoteAccess() {
    return fetchJson('/api/remote-access', { defaultValue: null });
}

export async function listCollections() {
    return fetchJson('/api/user-collections', { defaultValue: null });
}

export async function getCollection(collectionId, { limit = 500, offset = 0 } = {}) {
    return fetchJson(`/api/user-collections/${collectionId}?limit=${limit}&offset=${offset}`, { defaultValue: null });
}

export async function createCollection(name, imageIds = [], description = '') {
    return postJson('/api/user-collections', { name, description, image_ids: imageIds });
}

export async function renameCollection(collectionId, name) {
    return postJson(`/api/user-collections/${collectionId}/rename`, { name });
}

export async function deleteCollection(collectionId) {
    return postJson(`/api/user-collections/${collectionId}/delete`);
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

export async function createCollectionShare(collectionId, { rotate = false, expiresInDays = null } = {}) {
    return postJson(`/api/user-collections/${collectionId}/share`, {
        rotate,
        expires_in_days: expiresInDays,
    });
}

export async function revokeCollectionShare(collectionId) {
    return postJson(`/api/user-collections/${collectionId}/share/revoke`);
}

export async function getCollectionSuggestions() {
    return fetchJson('/api/collections/suggestions', { defaultValue: null });
}

export async function getSimilar(imageId, limit = 100) {
    return fetchJson(`/api/similar/${imageId}?limit=${limit}`, { defaultValue: null });
}

export async function getImportOptions() {
    return fetchJson('/api/imports/options', { defaultValue: null });
}

export async function getDateHistogram(params) {
    return fetchJson(`/api/date-histogram?${params.toString()}`, { defaultValue: null });
}

export async function getMapMarkers(params) {
    return fetchJson(`/api/map/markers?${params.toString()}`, { defaultValue: null });
}

export async function writeFlag(imageId, flag) {
    return postJson(`/api/image/${imageId}/flag`, { flag });
}

export async function writeFlags(imageIds, flag) {
    return postJson('/api/images/flag', { image_ids: imageIds, flag });
}

export async function mosaicNext(n, params, exclude = '', strategy = 'explore', gridElo = 0) {
    const query = new URLSearchParams(params);
    query.set('n', String(n));
    query.set('strategy', strategy === 'random' ? 'random' : strategy);
    if (Number(gridElo) > 0) query.set('grid_elo', String(gridElo));
    if (exclude) query.set('exclude', exclude);
    return fetchJson(`/api/mosaic/next?${query.toString()}`, { defaultValue: null });
}

export async function mosaicPick(winnerId, loserIds) {
    return postJson('/api/mosaic/pick', { winner_id: winnerId, loser_ids: loserIds });
}

export async function compareUndo() {
    return postJson('/api/compare/undo');
}

export async function getPropagationLast() {
    return fetchJson('/api/propagation/last', { defaultValue: null });
}
