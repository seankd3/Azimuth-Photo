export const SCROLL_POS_STORAGE_KEY = 'pa_scroll_pos';
export const SCROLL_OFFSET_STORAGE_KEY = 'pa_scroll_offset';


export function hideLibraryEmptyState() {
    document.getElementById('library-empty')?.classList.add('hidden');
}


export function updateLibraryEmptyState({
    hasImages = false,
    searchQuery = '',
    hasActiveTextSearch,
    hasActiveLibraryFilters,
    clearSearch,
    clearLibraryFilters,
    location = globalThis.window?.location || globalThis.location,
} = {}) {
    const empty = document.getElementById('library-empty');
    const text = document.getElementById('library-empty-text');
    const action = document.getElementById('library-empty-action');
    if (!empty || !text || !action) return;
    if (hasImages) {
        empty.classList.add('hidden');
        return;
    }

    empty.classList.remove('hidden');
    if (hasActiveTextSearch?.(searchQuery)) {
        text.textContent = `No results for '${searchQuery}'`;
        action.textContent = 'Clear Search';
        action.onclick = () => clearSearch?.();
    } else if (hasActiveLibraryFilters?.()) {
        text.textContent = 'No photos match these filters';
        action.textContent = 'Clear Filters';
        action.onclick = () => clearLibraryFilters?.();
    } else {
        text.textContent = 'No photos in your catalog yet';
        action.textContent = 'Scan Folder';
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
        if (Number.isFinite(offset)) setRankingsOffset?.(Math.max(Number(getRankingsOffset?.() || 0), offset));
        const root = libraryScrollRoot();
        if (root) {
            requestAnimationFrameImpl(() => {
                requestAnimationFrameImpl(() => {
                    root.scrollTop = Number(saved);
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
