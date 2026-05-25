function bindOnce(el, key, eventName, handler) {
    if (!el || el.dataset[key] === '1') return;
    el.dataset[key] = '1';
    el.addEventListener(eventName, handler);
}


function all(documentImpl, selector) {
    return Array.from(documentImpl?.querySelectorAll?.(selector) || []);
}


export function bindSharedBottomBarControls({
    documentImpl = globalThis.document,
    clearSearch = () => {},
    setFilter = () => {},
    toggleMetadataFilters = () => {},
    toggleFilter = () => {},
    toggleStar = () => {},
    setThumbSize = () => {},
} = {}) {
    const clearBtn = documentImpl?.querySelector?.('[data-action="clear-search"]');
    bindOnce(clearBtn, 'paClearSearchBound', 'click', (event) => {
        event.preventDefault();
        clearSearch();
    });

    all(documentImpl, '[data-filter-control="select"][data-filter-key]').forEach((select) => {
        bindOnce(select, 'paFilterSelectBound', 'change', () => {
            setFilter(select.dataset.filterKey, select.value);
        });
    });

    const metadataBtn = documentImpl?.querySelector?.('[data-action="toggle-metadata-filters"]');
    bindOnce(metadataBtn, 'paMetadataFilterBound', 'click', (event) => {
        event.preventDefault();
        toggleMetadataFilters();
    });

    all(documentImpl, '[data-filter-control="toggle"][data-filter-key][data-filter-value]').forEach((btn) => {
        bindOnce(btn, 'paFilterToggleBound', 'click', (event) => {
            event.preventDefault();
            toggleFilter(btn.dataset.filterKey, btn.dataset.filterValue, btn);
        });
    });

    all(documentImpl, '.filter-star[data-star]').forEach((btn) => {
        bindOnce(btn, 'paFilterStarBound', 'click', (event) => {
            event.preventDefault();
            toggleStar(Number(btn.dataset.star || 0));
        });
    });

    const thumbSlider = documentImpl?.querySelector?.('[data-action="set-thumb-size"]');
    bindOnce(thumbSlider, 'paThumbSizeBound', 'input', () => {
        setThumbSize(thumbSlider.value);
    });

}
