export const SCROLL_POS_STORAGE_KEY = 'pa_scroll_pos';
export const SCROLL_OFFSET_STORAGE_KEY = 'pa_scroll_offset';


export function hideLibraryEmptyState() {
    document.getElementById('library-empty')?.classList.add('hidden');
}


export function updateLibraryEmptyState({
    hasImages = false,
    isDeferred = false,
    loadError = false,
    tasteUnavailable = false,
    tasteFallbackReason = '',
    latencyMs = 0,
    searchQuery = '',
    hasActiveTextSearch,
    hasActiveLibraryFilters,
    clearSearch,
    clearLibraryFilters,
    location = globalThis.window?.location || globalThis.location,
} = {}) {
    const empty = document.getElementById('library-empty');
    const text = document.getElementById('library-empty-text');
    const detail = document.getElementById('library-empty-detail');
    const action = document.getElementById('library-empty-action');
    if (!empty || !text || !action) return;
    if (hasImages) {
        empty.classList.add('hidden');
        return;
    }

    empty.classList.remove('hidden');
    if (loadError) {
        text.textContent = 'Library could not load photos';
        if (detail) detail.textContent = 'The photo list request failed. Refresh the page to try again.';
        action.textContent = 'Refresh';
        action.onclick = () => { location.reload(); };
    } else if (tasteUnavailable) {
        text.textContent = 'Taste sorting needs more signal';
        if (detail) detail.textContent = tasteFallbackReason || 'Make more direct Compare choices and wait for embeddings on those photos.';
        action.textContent = 'Compare Photos';
        action.onclick = () => { location.href = '/compare'; };
    } else if (isDeferred) {
        text.textContent = 'Library is catching up';
        if (detail) {
            const latency = Number(latencyMs || 0);
            detail.textContent = latency > 0
                ? `The catalog database is busy. Retrying after a ${latency.toFixed(0)}ms stale response.`
                : 'The catalog database is busy. Retrying shortly.';
        }
        action.textContent = 'Open Catalog';
        action.onclick = () => { location.href = '/catalog'; };
    } else if (hasActiveTextSearch?.(searchQuery)) {
        text.textContent = `No results for '${searchQuery}'`;
        if (detail) detail.textContent = 'Try a broader search, clear filters, or wait for AI embeddings/search indexing to finish.';
        action.textContent = 'Clear Search';
        action.onclick = () => clearSearch?.();
    } else if (hasActiveLibraryFilters?.()) {
        text.textContent = 'No photos match these filters';
        if (detail) detail.textContent = 'One of the active filters is hiding everything in the current Library view.';
        action.textContent = 'Clear Filters';
        action.onclick = () => clearLibraryFilters?.();
    } else {
        text.textContent = 'No photos in your catalog yet';
        if (detail) detail.textContent = 'Add a source folder in Catalog, then scan it to populate Library.';
        action.textContent = 'Open Catalog';
        action.onclick = () => { location.href = '/catalog'; };
    }
}


export function libraryScrollRoot() {
    return document.querySelector('.library-container');
}


export function saveScrollPosition({
    rankingsOffset = 0,
    scrollPosStorageKey,
    scrollOffsetStorageKey,
    sessionStorageImpl = sessionStorage,
} = {}) {
    const root = libraryScrollRoot();
    if (root) {
        sessionStorageImpl.setItem(scrollPosStorageKey, root.scrollTop);
        sessionStorageImpl.setItem(scrollOffsetStorageKey, rankingsOffset);
    }
}


export function restoreScrollPosition({
    getRankingsOffset,
    setRankingsOffset,
    setPendingScrollRestoreOffset,
    scrollPosStorageKey,
    scrollOffsetStorageKey,
    requestAnimationFrameImpl = requestAnimationFrame,
    sessionStorageImpl = sessionStorage,
} = {}) {
    const saved = sessionStorageImpl.getItem(scrollPosStorageKey);
    const savedOffset = sessionStorageImpl.getItem(scrollOffsetStorageKey);
    if (saved !== null && savedOffset !== null) {
        const offset = Number(savedOffset);
        const currentOffset = Number(getRankingsOffset?.() || 0);
        if (Number.isFinite(offset) && offset >= 0 && offset <= currentOffset) {
            setRankingsOffset?.(Math.max(currentOffset, offset));
        }
        const root = libraryScrollRoot();
        const scrollTop = Number(saved);
        if (root && Number.isFinite(scrollTop) && offset <= currentOffset) {
            requestAnimationFrameImpl(() => {
                requestAnimationFrameImpl(() => {
                    root.scrollTop = Math.max(0, scrollTop);
                });
            });
        }
        setPendingScrollRestoreOffset?.(0);
        sessionStorageImpl.removeItem(scrollPosStorageKey);
        sessionStorageImpl.removeItem(scrollOffsetStorageKey);
    }
}


export function updateBackToTopButton() {
    const btn = document.getElementById('back-to-top');
    const root = libraryScrollRoot();
    if (!btn || !root) return;
    const visible = root.scrollTop > 600;
    btn.classList.toggle('hidden', !visible);
    btn.classList.toggle('visible', visible);
}


export function scrollToTop() {
    libraryScrollRoot()?.scrollTo({ top: 0, behavior: 'smooth' });
}


export function scrollLibraryContainerToElement(el, behavior = 'smooth') {
    const root = libraryScrollRoot();
    if (!root || !el) {
        el?.scrollIntoView({ behavior, block: 'start' });
        return;
    }
    const rootRect = root.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    const top = root.scrollTop + rect.top - rootRect.top;
    root.scrollTo({ top: Math.max(0, top - 4), behavior });
}
