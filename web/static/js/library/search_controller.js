function searchInput(documentImpl) {
    return documentImpl?.getElementById?.('search-input') || null;
}


function clearSearchTimer(clearSearchDebounce = null) {
    if (clearSearchDebounce) clearSearchDebounce();
}


export function applySearchQueryChange(query, {
    getSearchQuery = () => '',
    setSearchQuery = () => {},
    setDeepSearchRequested = () => {},
    getSortField = () => '',
    hasActiveTextSearch = value => Boolean(value),
    saveSearchState = () => {},
    updateSimilaritySortOption = () => {},
    applySortState = () => {},
    saveSearchSortState = () => {},
    clearPersistedSearchState = () => {},
    restoreSortState = () => {},
    updateSearchControls = () => {},
    reloadForFilters = () => {},
    updateDateScrubber = () => {},
} = {}) {
    const trimmed = String(query || '').trim();
    const wasSearching = hasActiveTextSearch(getSearchQuery());
    setSearchQuery(trimmed);
    setDeepSearchRequested(false);
    if (hasActiveTextSearch(trimmed)) {
        saveSearchState();
        updateSimilaritySortOption();
        if (!wasSearching) {
            applySortState('similarity', true, { persist: false });
        } else {
            saveSearchSortState();
        }
    } else {
        clearPersistedSearchState();
        if (getSortField() === 'similarity') {
            restoreSortState();
            if (getSortField() === 'similarity') {
                applySortState('elo', true, { persist: false, persistSearch: false });
            }
        }
    }
    updateSearchControls();
    reloadForFilters();
    updateDateScrubber();
}


export function initSearchInputControls({
    documentImpl = globalThis.document,
    getSearchDebounce = () => null,
    setSearchDebounce = () => {},
    setTimeoutImpl = globalThis.setTimeout,
    clearTimeoutImpl = globalThis.clearTimeout,
    hasActiveTextSearch = value => Boolean(value),
    applySearchQueryChangeImpl = applySearchQueryChange,
    clearSearchImpl = () => {},
    delayMs = 300,
} = {}) {
    const input = searchInput(documentImpl);
    if (!input) return false;
    input.addEventListener('input', (e) => {
        clearTimeoutImpl(getSearchDebounce());
        e.target.classList.add('searching');
        const deepBtn = documentImpl.getElementById('deep-search-btn');
        if (deepBtn) {
            deepBtn.disabled = !hasActiveTextSearch(e.target.value.trim());
            deepBtn.classList.remove('active');
        }
        const timer = setTimeoutImpl(() => {
            e.target.classList.remove('searching');
            setSearchDebounce(null);
            applySearchQueryChangeImpl(e.target.value);
        }, delayMs);
        setSearchDebounce(timer);
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') clearSearchImpl();
    });
    return true;
}


export function clearSearch({
    documentImpl = globalThis.document,
    clearSearchDebounce = null,
    getSortField = () => '',
    setSearchQuery = () => {},
    setDeepSearchRequested = () => {},
    clearPersistedSearchState = () => {},
    restoreSortState = () => {},
    applySortState = () => {},
    updateSearchControls = () => {},
    reloadForFilters = () => {},
} = {}) {
    const wasSimilaritySort = getSortField() === 'similarity';
    setSearchQuery('');
    setDeepSearchRequested(false);
    clearSearchTimer(clearSearchDebounce);
    clearPersistedSearchState();
    if (wasSimilaritySort) {
        restoreSortState();
        if (getSortField() === 'similarity') {
            applySortState('elo', true, { persist: false, persistSearch: false });
        }
    }
    const input = searchInput(documentImpl);
    if (input) {
        input.value = '';
        input.classList.remove('searching');
    }
    const sortToggles = documentImpl?.getElementById?.('sort-toggles');
    if (sortToggles) sortToggles.style.opacity = '';
    updateSearchControls();
    reloadForFilters();
}


export function runDeepSearch({
    documentImpl = globalThis.document,
    clearSearchDebounce = null,
    getSearchQuery = () => '',
    setSearchQuery = () => {},
    setDeepSearchRequested = () => {},
    getSortField = () => '',
    hasActiveTextSearch = value => Boolean(value),
    saveSearchState = () => {},
    updateSimilaritySortOption = () => {},
    applySortState = () => {},
    saveSearchSortState = () => {},
    updateSearchControls = () => {},
    reloadForFilters = () => {},
    updateDateScrubber = () => {},
} = {}) {
    const input = searchInput(documentImpl);
    const query = ((input && input.value) || getSearchQuery() || '').trim();
    if (!query || query === '__similar__') {
        if (input) input.focus();
        return false;
    }
    const wasSearching = hasActiveTextSearch(getSearchQuery());
    clearSearchTimer(clearSearchDebounce);
    if (input) input.classList.remove('searching');
    setSearchQuery(query);
    setDeepSearchRequested(true);
    saveSearchState();
    updateSimilaritySortOption();
    if (!wasSearching || getSortField() !== 'similarity') {
        applySortState('similarity', true, { persist: false });
    } else {
        saveSearchSortState();
    }
    updateSearchControls();
    reloadForFilters();
    updateDateScrubber();
    return true;
}
