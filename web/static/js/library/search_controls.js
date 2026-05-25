import {
    hasActiveTextSearch as defaultHasActiveTextSearch,
    SIMILAR_SEARCH_SENTINEL,
} from '../search/query.js';


export function updateSortDirIcon({
    documentImpl = document,
    sortDesc = true,
} = {}) {
    const btn = documentImpl.getElementById('sort-dir-btn');
    if (btn) btn.classList.toggle('active', !sortDesc);
}


export function updateSimilaritySortOption({
    documentImpl = document,
    active = false,
} = {}) {
    const select = documentImpl.getElementById('sort-field');
    if (!select) return;
    let opt = select.querySelector('option[value="similarity"]');
    if (active) {
        if (!opt) {
            opt = documentImpl.createElement('option');
            opt.value = 'similarity';
            opt.textContent = 'Similarity';
            select.appendChild(opt);
        }
    } else if (opt) {
        opt.remove();
    }
}


export function syncSortControls({
    documentImpl = document,
    sortField = 'elo',
    sortDesc = true,
} = {}) {
    const select = documentImpl.getElementById('sort-field');
    if (select && select.querySelector(`option[value="${sortField}"]`)) {
        select.value = sortField;
    }
    updateSortDirIcon({ documentImpl, sortDesc });
}


export function updateCompareSearchIndicator({
    documentImpl = document,
    searchQuery = '',
    active = false,
    afterUpdate = null,
} = {}) {
    const chip = documentImpl.getElementById('compare-search-active');
    if (!chip) return false;
    const queryEl = documentImpl.getElementById('compare-search-query');
    chip.classList.toggle('hidden', !active);
    if (queryEl) queryEl.textContent = active ? searchQuery : '';
    afterUpdate?.();
    return true;
}


export function updateSearchControls({
    documentImpl = document,
    searchQuery = '',
    sortField = 'elo',
    sortDesc = true,
    hasActiveTextSearch = defaultHasActiveTextSearch,
    afterCompareSearchIndicator = null,
} = {}) {
    const input = documentImpl.getElementById('search-input');
    const clearBtn = documentImpl.getElementById('search-clear');
    if (input && searchQuery !== SIMILAR_SEARCH_SENTINEL) input.value = searchQuery;
    if (clearBtn) clearBtn.classList.toggle('hidden', !searchQuery);
    updateSimilaritySortOption({
        documentImpl,
        active: hasActiveTextSearch(searchQuery),
    });
    syncSortControls({ documentImpl, sortField, sortDesc });
    updateCompareSearchIndicator({
        documentImpl,
        searchQuery,
        active: hasActiveTextSearch(searchQuery),
        afterUpdate: afterCompareSearchIndicator,
    });
    updateSortDirIcon({ documentImpl, sortDesc });
}
