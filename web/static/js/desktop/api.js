import { FetchJsonError, fetchJson as sharedFetchJson, fetchOptionsWithTimeout } from '../api.js';
import { showToast } from './toast.js';

function reportApiFailure({ status = 0, error = null } = {}) {
    const cause = error?.cause || error;
    if (!status || cause?.name === 'AbortError' || cause?.name === 'TimeoutError') {
        showToast("The library isn't responding.");
        return;
    }
    showToast(`Request failed (${status})`);
}

async function requestWithStatus(url, options = {}) {
    const { timeoutMs, ...fetchOptions } = options;
    const defaultTimeoutMs = /^(POST|PUT|PATCH|DELETE)$/i.test(fetchOptions.method || 'GET') ? 20_000 : 10_000;
    let response = null;
    try {
        response = await fetch(url, fetchOptionsWithTimeout(fetchOptions, timeoutMs ?? defaultTimeoutMs));
    } catch (error) {
        reportApiFailure({ error });
        return { ok: false, status: 0, data: null };
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) reportApiFailure({ status: response.status });
    return { ok: response.ok, status: response.status, data };
}

export async function fetchJson(url, options = {}) {
    try {
        return await sharedFetchJson(url, { timeoutMs: 10_000, ...options });
    } catch (error) {
        reportApiFailure({ status: error?.status, error });
        throw error;
    }
}

export async function requestJson(url, options = {}) {
    const result = await requestWithStatus(url, options);
    if (result.ok) return result.data;
    throw new FetchJsonError(result.data?.detail || result.data?.error || 'Request failed', {
        url,
        status: result.status,
    });
}

function jsonRequestOptions(method, body = null, options = {}) {
    const requestOptions = { ...options, method, headers: { Accept: 'application/json', ...(options.headers || {}) } };
    if (body != null) {
        requestOptions.headers['Content-Type'] = 'application/json';
        requestOptions.body = JSON.stringify(body);
    }
    return requestOptions;
}

export async function postJson(url, body = null, options = {}) {
    const result = await requestWithStatus(url, jsonRequestOptions('POST', body, options));
    return result.ok ? result.data : null;
}

export async function postJsonWithStatus(url, body = null, options = {}) {
    return requestWithStatus(url, jsonRequestOptions('POST', body, options));
}

export async function deleteJsonWithStatus(url) {
    return requestWithStatus(url, jsonRequestOptions('DELETE'));
}

export function thumbUrl(size, imageId) {
    return `/api/thumb/${size}/${imageId}`;
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

export async function revealFolder(path, sourceId = null) {
    const body = { path };
    if (Number(sourceId) > 0) body.source_id = Number(sourceId);
    return postJsonWithStatus('/api/reveal', body);
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

export async function browseCatalogFolders(path = '') {
    const params = new URLSearchParams();
    if (path) params.set('path', path);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return fetchJson(`/api/catalog/browse${suffix}`, { defaultValue: null });
}

export async function addCatalogSource(path, scan = true) {
    return postJsonWithStatus('/api/catalog/sources', { path, scan });
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

export async function getCatalogBackups() {
    return fetchJson('/api/system/backup/list', { defaultValue: null });
}

export async function createCatalogBackup() {
    return postJsonWithStatus('/api/system/backup/now');
}

export async function prepareCatalogRestore(name) {
    return postJsonWithStatus('/api/system/backup/restore', { name });
}

export async function getCatalogRestoreStatus() {
    return fetchJson('/api/system/backup/restore-status', { defaultValue: null });
}

export async function discardCatalogRestore() {
    return deleteJsonWithStatus('/api/system/backup/restore-staged');
}

export async function getIntegrityStatus() {
    return fetchJson('/api/system/integrity/status', { defaultValue: null });
}

export async function startIntegrityScan(limit = 50) {
    return postJsonWithStatus('/api/system/integrity/scan', { limit });
}

export async function getMetadataStatus() {
    return fetchJson('/api/catalog/metadata/status', { defaultValue: null });
}

export async function startMetadataScan() {
    return postJson('/api/catalog/metadata/start');
}

export async function stopMetadataScan() {
    return postJson('/api/catalog/metadata/stop');
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

export async function pauseCaptionScan() {
    return postJson('/api/captions/scan/pause');
}

export async function resumeCaptionScan() {
    return postJson('/api/captions/scan/resume');
}

export async function getAiStatus() {
    return fetchJson('/api/ai/status', { defaultValue: null });
}

export async function getSettings() {
    return fetchJson('/api/settings', { defaultValue: null });
}

export async function getVersion() {
    return fetchJson('/api/version', { defaultValue: null });
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

export async function getPairStatus() {
    return fetchJson('/api/pair/status', { defaultValue: null });
}

export async function getSyncStatus() {
    return fetchJson('/api/sync/status', { defaultValue: null });
}

export async function listDevices() {
    return fetchJson('/api/devices', { defaultValue: null });
}

export async function createDeviceLink() {
    return postJson('/api/devices/link', {});
}

export async function revokeDevice(deviceId) {
    return postJson(`/api/devices/${deviceId}/revoke`, {});
}

export async function discoverHubs() {
    return fetchJson('/api/discover', { defaultValue: null });
}

export async function connectToHub({ hubUrl, code, deviceName = '', platform = 'desktop' } = {}) {
    return postJson('/api/pair/connect', {
        hub_url: hubUrl,
        code,
        device_name: deviceName,
        platform,
    });
}

export async function applyRemoteAccessServe() {
    return postJson('/api/remote-access/serve');
}

export async function listCollections() {
    return fetchJson('/api/user-collections', { defaultValue: null });
}

export async function getCollection(collectionId, { limit = 500, offset = 0, signal = null } = {}) {
    const fetchOptions = signal ? { signal } : undefined;
    return fetchJson(`/api/user-collections/${collectionId}?limit=${limit}&offset=${offset}`, { defaultValue: null, fetchOptions });
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

export async function listSharedSurfaces() {
    return fetchJson('/api/shares', { defaultValue: { items: [] } });
}

async function publishingMutation(method, url, body = null) {
    const result = await requestWithStatus(url, jsonRequestOptions(method, body));
    if (!result.ok) {
        throw new Error(result.data?.error || result.data?.detail || `Request failed (${result.status})`);
    }
    return result.data;
}

export async function getCollectionTree() {
    return fetchJson('/api/collections/tree', { defaultValue: { nodes: [], links: [], root_ids: [] } });
}

export async function getCollectionGraphImages(collectionId) {
    return fetchJson(`/api/collections/${collectionId}/images?recursive=0`, {
        defaultValue: { image_ids: [], images: [] },
    });
}

export async function getPublishedTree(area) {
    return fetchJson(`/api/published/tree?area=${encodeURIComponent(area)}`, {
        defaultValue: { area, nodes: [], links: [], root_ids: [] },
    });
}

export async function createPublishedNode(fields) {
    return publishingMutation('POST', '/api/published/nodes', fields);
}

export async function patchPublishedNode(nodeId, fields) {
    return publishingMutation('PATCH', `/api/published/nodes/${nodeId}`, fields);
}

export async function deletePublishedNode(nodeId) {
    return publishingMutation('DELETE', `/api/published/nodes/${nodeId}`);
}

export async function getPublishedNodeDiff(nodeId) {
    return fetchJson(`/api/published/nodes/${nodeId}/diff`, { defaultValue: null });
}

export async function updatePublishedNode(nodeId, diff) {
    return publishingMutation('POST', `/api/published/nodes/${nodeId}/update`, diff);
}

export async function sharePublishedNode(nodeId, password) {
    return publishingMutation('POST', `/api/published/nodes/${nodeId}/share`, { password });
}

export async function revokePublishedNodeShare(nodeId) {
    return publishingMutation('DELETE', `/api/published/nodes/${nodeId}/share`);
}

export async function exportPublishedWebsite() {
    return publishingMutation('POST', '/api/published/export?area=website');
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

export async function listImports(limit = 20) {
    return fetchJson(`/api/imports?limit=${encodeURIComponent(limit)}`, { defaultValue: { imports: [] } });
}

export async function getDateHistogram(params) {
    return fetchJson(`/api/date-histogram?${params.toString()}`, { defaultValue: null });
}

export async function getDateGroups(params) {
    return fetchJson(`/api/date-groups?${params.toString()}`, { defaultValue: null });
}

export async function listSavedViews() {
    return fetchJson('/api/saved-views', { defaultValue: { views: [] } });
}

async function savedViewMutation(method, url, body = null) {
    const result = await requestWithStatus(url, jsonRequestOptions(method, body));
    return result.ok ? result.data : null;
}

export const createSavedView = (name, query) =>
    savedViewMutation('POST', '/api/saved-views', { name, query });
export const deleteSavedView = (id) =>
    savedViewMutation('DELETE', `/api/saved-views/${encodeURIComponent(id)}`);

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

export async function getTrash({ limit = 200, offset = 0, signal = null } = {}) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    const fetchOptions = signal ? { signal } : undefined;
    return fetchJson(`/api/trash?${params.toString()}`, { defaultValue: null, fetchOptions });
}

export async function trashImages(imageIds) {
    return postJsonWithStatus('/api/images/trash', { ids: imageIds });
}

export async function restoreImages(imageIds) {
    return postJsonWithStatus('/api/images/restore', { ids: imageIds });
}

export async function emptyTrash() {
    return postJsonWithStatus('/api/trash/empty');
}

export async function getMapMarkers(params) {
    return fetchJson(`/api/map/markers?${params.toString()}`, { defaultValue: null });
}

export async function writeFlag(imageId, flag) {
    return postJsonWithStatus(`/api/image/${imageId}/flag`, { flag });
}

export async function writeFlags(imageIds, flag) {
    return postJsonWithStatus('/api/images/flag', { image_ids: imageIds, flag });
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


// --- staged import (IMPORT_SPEC v1) ---

export async function getImportSources() {
    return fetchJson('/api/import/sources', { defaultValue: { sources: [] } });
}

export async function browseImportPath(path) {
    return fetchJson(`/api/import/browse?path=${encodeURIComponent(path)}`, { defaultValue: { dirs: [] } });
}

export async function startImportScan(path, includeSubfolders) {
    return postJson('/api/import/scan', { path, include_subfolders: includeSubfolders });
}

export async function getImportScan(scanId, offset = 0) {
    return fetchJson(`/api/import/scan/${scanId}?offset=${offset}`, { defaultValue: null });
}

export async function commitImportScan(body) {
    return postJson('/api/import/commit', body);
}

export async function getImportJob(jobId) {
    return fetchJson(`/api/import/jobs/${jobId}`, { defaultValue: null });
}
