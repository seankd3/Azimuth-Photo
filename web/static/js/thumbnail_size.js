export function applyLibraryThumbSize(value, {
    documentImpl = globalThis.document,
} = {}) {
    const thumbHeight = Number.parseInt(value, 10);
    documentImpl.documentElement.style.setProperty('--thumb-height', `${thumbHeight}px`);
    const cards = documentImpl.querySelectorAll('.rank-card');
    const updates = [];
    for (const card of cards) {
        updates.push({ el: card, basis: thumbHeight * (Number.parseFloat(card.dataset.ar) || 1.5) });
    }
    for (const { el, basis } of updates) {
        el.style.height = `${thumbHeight}px`;
        el.style.flexBasis = `${basis}px`;
    }
    return thumbHeight;
}


export function createThumbnailSizeHandler({
    documentImpl = globalThis.document,
    getMosaicSize,
    setMosaicSize,
    mosaicSizeFromThumbHeight,
    clearWarmups,
    loadMosaicBatch,
    setThumbHeight,
    setTimeoutImpl = globalThis.setTimeout,
    clearTimeoutImpl = globalThis.clearTimeout,
    mosaicReloadDelayMs = 200,
} = {}) {
    let mosaicReloadTimer = null;
    return function setThumbSize(value) {
        const thumbHeight = Number.parseInt(value, 10);
        setThumbHeight?.(thumbHeight);

        const mosaicGrid = documentImpl.getElementById('mosaic-grid');
        if (mosaicGrid) {
            const newSize = mosaicSizeFromThumbHeight(thumbHeight);
            if (newSize !== getMosaicSize()) {
                setMosaicSize(newSize);
                // Debounce the expensive reload so a slider drag does not
                // trigger clearWarmups + loadMosaicBatch on every input event.
                if (mosaicReloadTimer !== null) clearTimeoutImpl(mosaicReloadTimer);
                mosaicReloadTimer = setTimeoutImpl(() => {
                    mosaicReloadTimer = null;
                    clearWarmups();
                    loadMosaicBatch();
                }, mosaicReloadDelayMs);
            }
            return thumbHeight;
        }

        return applyLibraryThumbSize(thumbHeight, { documentImpl });
    };
}
