function elementById(documentImpl, id) {
    return documentImpl?.getElementById?.(id) || null;
}


export function loupeComputeFitScale({
    documentImpl = globalThis.document,
    wrap = null,
    naturalWidth = 0,
    naturalHeight = 0,
} = {}) {
    const targetWrap = wrap || elementById(documentImpl, 'loupe-image-wrap');
    if (!targetWrap || !naturalWidth || !naturalHeight) return 1;
    return Math.min(targetWrap.clientWidth / naturalWidth, targetWrap.clientHeight / naturalHeight);
}


export function applyLoupeImageSize({
    documentImpl = globalThis.document,
    img = null,
    naturalWidth = 0,
    naturalHeight = 0,
} = {}) {
    const targetImg = img || elementById(documentImpl, 'loupe-img');
    if (!targetImg || !naturalWidth || !naturalHeight) return false;
    targetImg.style.width = `${naturalWidth}px`;
    targetImg.style.height = `${naturalHeight}px`;
    return true;
}


export function applyLoupeTransform({
    documentImpl = globalThis.document,
    img = null,
    panX = 0,
    panY = 0,
    scale = 1,
} = {}) {
    const targetImg = img || elementById(documentImpl, 'loupe-img');
    if (!targetImg) return '';
    const transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
    targetImg.style.transform = transform;
    return transform;
}


export function loupeZoomIndicatorText({
    currentImage = null,
    naturalWidth = 0,
    naturalHeight = 0,
    zoomMode = 'fit',
    scale = 1,
} = {}) {
    if (!currentImage || !naturalWidth || !naturalHeight) return '';
    if (zoomMode === 'fit') return 'Fit';
    if (zoomMode === 'one-to-one') return '100%';
    return `${Math.round(scale * 100)}%`;
}


export function updateLoupeZoomIndicator({
    documentImpl = globalThis.document,
    zoomEl = null,
    currentImage = null,
    naturalWidth = 0,
    naturalHeight = 0,
    zoomMode = 'fit',
    scale = 1,
} = {}) {
    const targetZoomEl = zoomEl || elementById(documentImpl, 'loupe-overlay-zoom');
    if (!targetZoomEl) return '';
    const text = loupeZoomIndicatorText({
        currentImage,
        naturalWidth,
        naturalHeight,
        zoomMode,
        scale,
    });
    targetZoomEl.textContent = text;
    return text;
}


export function clampLoupePan({
    wrap = null,
    naturalWidth = 0,
    naturalHeight = 0,
    scale = 1,
    panX = 0,
    panY = 0,
} = {}) {
    if (!wrap) return { panX, panY };
    const cw = wrap.clientWidth;
    const ch = wrap.clientHeight;
    const iw = naturalWidth * scale;
    const ih = naturalHeight * scale;
    let nextPanX = panX;
    let nextPanY = panY;

    if (iw <= cw) {
        nextPanX = (cw - iw) / 2;
    } else {
        nextPanX = Math.min(0, Math.max(cw - iw, nextPanX));
    }

    if (ih <= ch) {
        nextPanY = (ch - ih) / 2;
    } else {
        nextPanY = Math.min(0, Math.max(ch - ih, nextPanY));
    }

    return { panX: nextPanX, panY: nextPanY };
}
