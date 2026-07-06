export function bindLibraryKeyboard({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
    getSelectedLibraryIndex,
    getLibraryImages,
    hasBatchSelection,
    selectLibraryCard,
    deselectLibraryCard,
    findCardInDirection,
    openLightbox,
    lightboxNext,
    lightboxPrev,
    closeLightbox,
    setCurrentLibraryFlag,
    trapLoupeFocus,
    batchFlag,
    clearBatchSelection,
    saveScrollPosition,
    comparePath = '/compare',
} = {}) {
    if (!documentImpl?.addEventListener) return;

    documentImpl.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const tagName = e.target?.tagName;
        if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT' || e.target?.isContentEditable) return;

        const loupe = documentImpl.getElementById('loupe');
        const loupeOpen = loupe?.classList?.contains('loupe-visible');

        if (loupeOpen) {
            if (e.key === 'ArrowRight') { e.preventDefault(); lightboxNext(); }
            else if (e.key === 'ArrowLeft') { e.preventDefault(); lightboxPrev(); }
            else if (e.key.toLowerCase() === 'p') { e.preventDefault(); setCurrentLibraryFlag('picked'); }
            else if (e.key.toLowerCase() === 'x') { e.preventDefault(); setCurrentLibraryFlag('rejected'); }
            else if (e.key.toLowerCase() === 'u') { e.preventDefault(); setCurrentLibraryFlag('unflagged'); }
            else if (e.key === 'Escape' || e.key === 'Enter') { e.preventDefault(); closeLightbox(); }
            else if (e.key === 'Tab') { trapLoupeFocus(e); }
            return;
        }

        const cards = documentImpl.querySelectorAll('.rank-card');
        if (!cards.length) return;

        const selectedLibraryIndex = getSelectedLibraryIndex();
        const libraryImages = getLibraryImages();

        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (selectedLibraryIndex < 0) {
                selectLibraryCard(0, cards);
                return;
            }
            if (e.key === 'ArrowRight') {
                selectLibraryCard(Math.min(selectedLibraryIndex + 1, cards.length - 1), cards);
            } else if (e.key === 'ArrowLeft') {
                selectLibraryCard(Math.max(selectedLibraryIndex - 1, 0), cards);
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                const target = findCardInDirection(cards, selectedLibraryIndex, e.key === 'ArrowDown' ? 1 : -1);
                selectLibraryCard(target, cards);
            }
        } else if (e.key === 'Enter' && selectedLibraryIndex >= 0 && selectedLibraryIndex < libraryImages.length) {
            e.preventDefault();
            openLightbox(libraryImages[selectedLibraryIndex]);
        } else if (e.key.toLowerCase() === 'p') {
            e.preventDefault();
            if (hasBatchSelection()) batchFlag('picked');
            else setCurrentLibraryFlag('picked');
        } else if (e.key.toLowerCase() === 'x') {
            e.preventDefault();
            if (hasBatchSelection()) batchFlag('rejected');
            else setCurrentLibraryFlag('rejected');
        } else if (e.key.toLowerCase() === 'u') {
            e.preventDefault();
            if (hasBatchSelection()) batchFlag('unflagged');
            else setCurrentLibraryFlag('unflagged');
        } else if (e.key === 'Escape') {
            // While the collection sheet is open it owns Escape (its own
            // listener closes it); do not also clear the batch selection.
            if (documentImpl.querySelector('.collection-sheet-host')) return;
            e.preventDefault();
            if (hasBatchSelection()) clearBatchSelection();
            else if (selectedLibraryIndex >= 0) deselectLibraryCard(cards);
        } else if (e.key === 'Tab') {
            e.preventDefault();
            saveScrollPosition();
            windowImpl.location.href = comparePath;
        }
    });
}
