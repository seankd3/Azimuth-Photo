export function libraryLoadMoreLabel({
    shownCount = 0,
    totalCount = 0,
    loading = false,
} = {}) {
    if (loading) return 'Loading…';
    const shown = Math.max(0, Number(shownCount) || 0);
    const total = Math.max(0, Number(totalCount) || 0);
    if (total > shown) return `Load more (${shown.toLocaleString()} of ${total.toLocaleString()})`;
    return 'Load more';
}


export function libraryLoadMoreMetaText({
    shownCount = 0,
    totalCount = 0,
    exhausted = false,
} = {}) {
    const shown = Math.max(0, Number(shownCount) || 0);
    const total = Math.max(0, Number(totalCount) || 0);
    if (!total || total <= shown) {
        return exhausted ? 'All matching photos are shown.' : '';
    }
    const remaining = total - shown;
    return `${remaining.toLocaleString()} more match${remaining === 1 ? '' : 'es'} in this search.`;
}


export function shouldShowLibraryLoadMore({
    hasActiveSearch = false,
    viewMode = 'grid',
    shownCount = 0,
    exhausted = false,
} = {}) {
    void hasActiveSearch;
    void viewMode;
    void shownCount;
    void exhausted;
    return false;
}


export function updateLibraryLoadMore({
    documentImpl = document,
    shownCount = 0,
    totalCount = 0,
    loading = false,
    exhausted = false,
    hasActiveSearch = false,
    viewMode = 'grid',
} = {}) {
    const root = documentImpl.getElementById('library-load-more');
    const button = documentImpl.getElementById('library-load-more-btn');
    const meta = documentImpl.getElementById('library-load-more-meta');
    if (!root || !button) return false;

    const visible = shouldShowLibraryLoadMore({
        hasActiveSearch,
        viewMode,
        shownCount,
        exhausted,
    });
    root.classList.toggle('hidden', !visible);
    button.disabled = Boolean(loading);
    button.textContent = libraryLoadMoreLabel({ shownCount, totalCount, loading });
    if (meta) {
        const detail = visible
            ? libraryLoadMoreMetaText({ shownCount, totalCount, exhausted })
            : '';
        meta.textContent = detail;
        meta.classList.toggle('hidden', !detail);
    }
    return visible;
}


export function bindLibraryLoadMoreButton({
    documentImpl = document,
    onLoadMore = () => {},
} = {}) {
    const button = documentImpl.getElementById('library-load-more-btn');
    if (!button || button.dataset.paLoadMoreBound === '1') return false;
    button.dataset.paLoadMoreBound = '1';
    button.addEventListener('click', (event) => {
        event.preventDefault();
        if (button.disabled) return;
        onLoadMore();
    });
    return true;
}
