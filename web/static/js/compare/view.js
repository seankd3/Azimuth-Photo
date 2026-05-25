function byId(documentImpl, id) {
    return documentImpl?.getElementById?.(id) || null;
}


export function showCompareEmpty({ documentImpl = globalThis.document } = {}) {
    const images = byId(documentImpl, 'compare-images');
    const mosaic = byId(documentImpl, 'mosaic-container');
    const empty = byId(documentImpl, 'compare-empty');
    const hints = byId(documentImpl, 'compare-hints');
    if (images) images.classList.add('hidden');
    if (mosaic) mosaic.classList.add('hidden');
    if (hints) hints.classList.add('hidden');
    if (empty) empty.classList.remove('hidden');
}


export function setCompareModeView(mode, {
    documentImpl = globalThis.document,
    transitionMs = 150,
    transitionToken = 0,
    isCurrentTransition = () => true,
    onMosaic = () => {},
    onPair = () => {},
} = {}) {
    const btn = byId(documentImpl, 'mode-' + mode);
    if (btn) {
        btn.parentElement?.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }

    const abContainer = byId(documentImpl, 'compare-images');
    const mosaicContainer = byId(documentImpl, 'mosaic-container');
    const strategies = byId(documentImpl, 'bar-strategies');
    const hints = byId(documentImpl, 'bar-hints');
    const visibleContainer = [abContainer, mosaicContainer].find(el => el && !el.classList.contains('hidden'));

    if (visibleContainer) visibleContainer.classList.add('fading');

    setTimeout(() => {
        if (!isCurrentTransition(transitionToken)) return;
        if (mode === 'mosaic') {
            if (abContainer) abContainer.classList.add('hidden');
            if (mosaicContainer) {
                mosaicContainer.classList.remove('hidden');
                mosaicContainer.classList.remove('fading');
            }
            if (strategies) strategies.classList.remove('hidden');
            if (hints) hints.classList.add('hidden');
            onMosaic();
        } else {
            if (abContainer) {
                abContainer.classList.remove('hidden');
                abContainer.classList.remove('fading');
            }
            if (mosaicContainer) mosaicContainer.classList.add('hidden');
            if (strategies) strategies.classList.add('hidden');
            if (hints) hints.classList.remove('hidden');
            onPair();
        }
    }, transitionMs);
}
