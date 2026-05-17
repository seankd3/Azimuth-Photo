import {
    FILTER_QUERY_KEYS,
    normalizeFilterState,
} from '../query_state.js';

export const FILTER_STORAGE_KEY = 'pa_filters';

const COMPARED_FILTER_TITLES = {
    compared: 'Ranked',
    uncompared: 'Unranked',
    confident: 'High confidence (10+)',
};

const FILTER_SELECT_IDS = {
    folder: 'filter-folder',
    taken: 'filter-taken',
    fileType: 'filter-type',
    camera: 'filter-camera',
    lens: 'filter-lens',
    people: 'filter-people',
};


function defaultStorage() {
    return globalThis.window?.sessionStorage || globalThis.sessionStorage;
}


function defaultLocation() {
    return globalThis.window?.location || globalThis.location;
}


function defaultDocument() {
    return globalThis.document;
}


export function filterStateFromUrlSearch(search = '', URLSearchParamsImpl = URLSearchParams) {
    const urlParams = new URLSearchParamsImpl(search);
    if (!FILTER_QUERY_KEYS.some(key => urlParams.has(key))) return null;
    return normalizeFilterState({
        orientation: urlParams.get('orientation') || '',
        compared: urlParams.get('compared') || '',
        rating: Number(urlParams.get('min_stars')) || '',
        folder: urlParams.get('folder') || '',
        flag: urlParams.get('flag') || '',
        taken: urlParams.get('date_taken') || '',
        fileType: urlParams.get('file_type') || '',
        camera: urlParams.get('camera') || '',
        lens: urlParams.get('lens') || '',
        people: urlParams.get('people') || '',
    });
}


export function saveFilters({
    getFilters = () => ({}),
    storage = defaultStorage(),
    storageKey = FILTER_STORAGE_KEY,
    syncLibraryUrlState,
} = {}) {
    const filters = normalizeFilterState(getFilters());
    try {
        storage?.setItem(storageKey, JSON.stringify(filters));
    } catch {}
    syncLibraryUrlState?.();
    return filters;
}


export function restoreFilters({
    storage = defaultStorage(),
    storageKey = FILTER_STORAGE_KEY,
    location = defaultLocation(),
    URLSearchParamsImpl = URLSearchParams,
    setFilters,
    applyFilterUiState,
} = {}) {
    try {
        const urlFilters = filterStateFromUrlSearch(location?.search || '', URLSearchParamsImpl);
        if (urlFilters) {
            setFilters?.(urlFilters);
            applyFilterUiState?.(urlFilters);
            return urlFilters;
        }

        const saved = storage?.getItem(storageKey);
        if (!saved) return null;
        const filters = normalizeFilterState(JSON.parse(saved));
        setFilters?.(filters);
        applyFilterUiState?.(filters);
        return filters;
    } catch {
        return null;
    }
}


export function applyFilterUiState({
    filters = {},
    document = defaultDocument(),
    updateMetadataFilterButton,
} = {}) {
    if (!document) return;
    const normalized = normalizeFilterState(filters);

    document.querySelectorAll('.bar-filters .filter-icon').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.filter-flag').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.filter-star').forEach(s => {
        const lit = normalized.rating && Number(s.dataset.star) <= Number(normalized.rating);
        s.classList.toggle('lit', lit);
        s.textContent = lit ? '★' : '☆';
    });

    if (normalized.orientation) {
        document.querySelectorAll('.filter-icon').forEach(btn => {
            if (btn.title?.toLowerCase() === normalized.orientation) btn.classList.add('active');
        });
    }
    if (normalized.compared) {
        document.querySelectorAll('.filter-icon').forEach(btn => {
            if (btn.title === COMPARED_FILTER_TITLES[normalized.compared]) btn.classList.add('active');
        });
    }
    Object.entries(FILTER_SELECT_IDS).forEach(([key, id]) => {
        const sel = document.getElementById(id);
        if (sel) sel.value = normalized[key] || '';
    });
    if (normalized.flag) {
        document.querySelectorAll('.filter-flag').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.flag === normalized.flag);
        });
    }
    updateMetadataFilterButton?.();
}


export function setFilter(key, value, {
    filters = {},
    updateMetadataFilterButton,
    saveFilters,
    reloadForFilters,
} = {}) {
    filters[key] = value;
    updateMetadataFilterButton?.();
    saveFilters?.();
    reloadForFilters?.();
    return filters;
}


export function clearLibraryFilters({
    emptyFilters = {},
    document = defaultDocument(),
    setFilters,
    updateMetadataFilterButton,
    saveFilters,
    reloadForFilters,
} = {}) {
    const filters = { ...emptyFilters };
    setFilters?.(filters);
    document?.querySelectorAll('.bar-filters .filter-icon').forEach(btn => btn.classList.remove('active'));
    document?.querySelectorAll('.filter-star').forEach(s => {
        s.classList.remove('lit', 'hovered');
        s.textContent = '☆';
    });
    Object.values(FILTER_SELECT_IDS).forEach(id => {
        const el = document?.getElementById(id);
        if (el) el.value = '';
    });
    updateMetadataFilterButton?.();
    saveFilters?.();
    reloadForFilters?.();
    return filters;
}


export function toggleFilter(key, value, btn, {
    filters = {},
    saveFilters,
    reloadForFilters,
} = {}) {
    if (filters[key] === value) {
        filters[key] = '';
        btn.classList.remove('active');
    } else {
        btn.parentElement.querySelectorAll('.filter-icon').forEach(b => b.classList.remove('active'));
        filters[key] = value;
        btn.classList.add('active');
    }
    saveFilters?.();
    reloadForFilters?.();
    return filters;
}


export function toggleStar(level, {
    filters = {},
    document = defaultDocument(),
    saveFilters,
    reloadForFilters,
} = {}) {
    const newValue = filters.rating == level ? '' : level;
    filters.rating = newValue;
    document?.querySelectorAll('.filter-star').forEach(s => {
        const star = parseInt(s.dataset.star);
        const lit = newValue && star <= newValue;
        s.classList.toggle('lit', lit);
        s.textContent = lit ? '★' : '☆';
    });
    saveFilters?.();
    reloadForFilters?.();
    return filters;
}
