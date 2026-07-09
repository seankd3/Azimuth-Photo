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

export async function postJsonWithStatus(url, body = null) {
    try {
        const options = { method: 'POST', headers: { Accept: 'application/json' } };
        if (body != null) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }
        const response = await fetch(url, options);
        const data = await response.json().catch(() => null);
        return { ok: response.ok, status: response.status, data };
    } catch {
        return { ok: false, status: 0, data: null };
    }
}

export function thumbUrl(size, imageId) {
    return `/api/thumb/${size}/${imageId}`;
}

export function exportUrl(params) {
    return `/api/export?${params.toString()}`;
}

export async function getRankings(params, options = {}) {
    return fetchJson(`/api/rankings?${params.toString()}`, { defaultValue: null, ...options });
}

export async function getPeople(limit = 48) {
    return fetchJson(`/api/people?limit=${limit}`, { defaultValue: null });
}

export async function getFolders(maxDepth = 0) {
    const suffix = maxDepth == null ? '' : `?max_depth=${encodeURIComponent(maxDepth)}`;
    return fetchJson(`/api/folders${suffix}`, { defaultValue: null });
}

export async function getFolderTree() {
    return fetchJson('/api/folders/tree', { defaultValue: null });
}

export async function getFilterOptions() {
    return fetchJson('/api/filter-options', { defaultValue: null });
}

export async function getTags({ q = '', limit = 100 } = {}) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set('q', q);
    return fetchJson(`/api/tags?${params.toString()}`, { defaultValue: { tags: [] } });
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

export async function getImageCaption(imageId) {
    return fetchJson(`/api/image/${imageId}/caption`, { defaultValue: null });
}

export async function saveImageCaption(imageId, { caption, tags } = {}) {
    return postJson(`/api/image/${imageId}/caption`, { caption, tags });
}

export async function getCaptionStatus() {
    return fetchJson('/api/captions/status', { defaultValue: null });
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

export async function createCollection(name, imageIds = [], description = '', query = null) {
    const payload = query ? { name, query } : { name, description, image_ids: imageIds };
    return postJson('/api/user-collections', payload);
}

export async function renameCollection(collectionId, name) {
    return postJson(`/api/user-collections/${collectionId}/rename`, { name });
}

export async function deleteCollection(collectionId) {
    return postJson(`/api/user-collections/${collectionId}/delete`);
}

export async function updateCollection(collectionId, fields = {}) {
    return postJson(`/api/user-collections/${collectionId}`, fields);
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
    return postJson(`/api/user-collections/${collectionId}/share/revoke`);
}

export async function getCollectionPublish(collectionId) {
    return fetchJson(`/api/user-collections/${collectionId}/publish`, { defaultValue: null });
}

export async function publishCollection(collectionId, { slug = '', title = '' } = {}) {
    return postJsonWithStatus(`/api/user-collections/${collectionId}/publish`, { slug, title });
}

export async function revokeCollectionPublish(collectionId) {
    return postJsonWithStatus(`/api/user-collections/${collectionId}/publish/revoke`);
}

export async function listPublishes() {
    return fetchJson('/api/publishes', { defaultValue: { publishes: [] } });
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

export async function getCounts(params = new URLSearchParams()) {
    return fetchJson(`/api/counts?${params.toString()}`, { defaultValue: null });
}

export async function listStacks({ kind = '', limit = 50, offset = 0 } = {}) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (kind) params.set('kind', kind);
    return fetchJson(`/api/stacks?${params.toString()}`, { defaultValue: null });
}

export async function getStack(stackId) {
    return fetchJson(`/api/stacks/${encodeURIComponent(stackId)}`, { defaultValue: null });
}

export async function createStack(imageIds, representativeId = null) {
    const body = { image_ids: imageIds };
    if (representativeId) body.representative_id = representativeId;
    return postJson('/api/stacks', body);
}

export async function setStackRepresentative(stackId, imageId) {
    return postJson(`/api/stacks/${encodeURIComponent(stackId)}/representative`, { image_id: imageId });
}

export async function unstack(stackId) {
    return postJson(`/api/stacks/${encodeURIComponent(stackId)}/unstack`);
}

export async function rebuildStacks(kinds = null) {
    const body = Array.isArray(kinds) && kinds.length ? { kinds } : {};
    return postJson('/api/stacks/rebuild', body);
}

export async function getStackRebuildStatus() {
    return fetchJson('/api/stacks/rebuild/status', { defaultValue: null });
}

export async function getTrash({ limit = 200, offset = 0 } = {}) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return fetchJson(`/api/trash?${params.toString()}`, { defaultValue: null });
}

export async function trashImages(imageIds) {
    return postJson('/api/images/trash', { ids: imageIds });
}

export async function restoreImages(imageIds) {
    return postJson('/api/images/restore', { ids: imageIds });
}

export async function emptyTrash() {
    return postJson('/api/trash/empty');
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
