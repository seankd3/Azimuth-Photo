/** Per-client quiet sources — hide from library browsing without un-managing. */

const STORAGE_KEY = 'pa_d_quiet_sources';

let quietIds = loadQuietIds();
let revealQuietQuery = '';

function loadQuietIds() {
    try {
        const values = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        if (!Array.isArray(values)) return new Set();
        return new Set(values.map(Number).filter((id) => id > 0));
    } catch {
        return new Set();
    }
}

function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...quietIds]));
}

export function quietSourceIds() {
    return [...quietIds];
}

export function isSourceQuiet(sourceId) {
    return quietIds.has(Number(sourceId) || 0);
}

export function setSourceQuiet(sourceId, quiet) {
    const id = Number(sourceId) || 0;
    if (!id) return false;
    if (quiet) quietIds.add(id);
    else quietIds.delete(id);
    persist();
    return true;
}

export function toggleSourceQuiet(sourceId) {
    const id = Number(sourceId) || 0;
    if (!id) return false;
    const next = !quietIds.has(id);
    setSourceQuiet(id, next);
    return next;
}

export function revealQuietForQuery(query) {
    revealQuietQuery = String(query || '').trim();
}

export function clearQuietReveal() {
    revealQuietQuery = '';
}

export function quietRevealActive(query) {
    const q = String(query || '').trim();
    return Boolean(q) && q === revealQuietQuery;
}

/**
 * Source IDs to exclude for the current library request.
 * Direct navigation into a quiet source (folder under its path) bypasses that source.
 */
export function effectiveExcludeSources(sources = [], folderValues = [], { q = '', reveal = false } = {}) {
    if (!quietIds.size) return [];
    if (reveal || quietRevealActive(q)) return [];
    const folders = (Array.isArray(folderValues) ? folderValues : [folderValues])
        .map((value) => String(value || ''))
        .filter(Boolean);
    return [...quietIds].filter((id) => {
        const source = (sources || []).find((item) => Number(item.id) === Number(id));
        if (!source) return true;
        const path = String(source.path || '');
        if (!path || !folders.length) return true;
        return !folders.some((folder) => folder === path || folder.startsWith(`${path}/`) || folder.startsWith(`${path}\\`));
    });
}

let knownSources = [];

export function rememberSources(sources = []) {
    knownSources = Array.isArray(sources) ? sources : [];
}

export function applyExcludeSources(params, sources = knownSources, folderValues = [], { q = '' } = {}) {
    const excluded = effectiveExcludeSources(sources, folderValues, { q });
    if (excluded.length) params.set('exclude_sources', excluded.join(','));
    else params.delete('exclude_sources');
    return params;
}
