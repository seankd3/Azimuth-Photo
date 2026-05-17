export function initLoupeInteraction({
    documentImpl = document,
    windowImpl = window,
    getPan,
    setPan,
    getScale,
    getNaturalWidth,
    getIsFit,
    setFitScale,
    computeFitScale,
    centerFit,
    zoomTo,
    clampPan,
    applyTransform,
    updateZoomIndicator,
    requestFullImage,
} = {}) {
    const wrap = documentImpl.getElementById('loupe-image-wrap');
    const img = documentImpl.getElementById('loupe-img');
    if (!wrap || !img) return;

    let dragMoved = false;
    let dragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let dragPanStartX = 0;
    let dragPanStartY = 0;

    wrap.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        const currentImg = documentImpl.getElementById('loupe-img');
        if (currentImg) currentImg.style.transition = 'opacity 0.15s';
        dragMoved = false;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        const pan = getPan();
        dragPanStartX = pan.x;
        dragPanStartY = pan.y;

        if (!getIsFit()) {
            dragging = true;
            wrap.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    windowImpl.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved = true;
        setPan({ x: dragPanStartX + dx, y: dragPanStartY + dy });
        clampPan();
        applyTransform();
    });

    windowImpl.addEventListener('mouseup', () => {
        if (dragging) {
            dragging = false;
            wrap.style.cursor = getIsFit() ? 'zoom-in' : 'grab';
        }
    });

    wrap.addEventListener('click', (e) => {
        if (dragMoved) {
            dragMoved = false;
            return;
        }

        if (getIsFit()) {
            const currentImg = documentImpl.getElementById('loupe-img');
            requestFullImage();
            if (currentImg) currentImg.style.transition = 'transform 0.2s ease-out, opacity 0.15s';
            zoomTo(1, e.clientX, e.clientY, 'one-to-one');
            windowImpl.setTimeout(() => {
                if (currentImg) currentImg.style.transition = 'opacity 0.15s';
            }, 200);
        } else {
            centerFit();
        }
    });

    wrap.addEventListener('wheel', (e) => {
        e.preventDefault();
        const currentImg = documentImpl.getElementById('loupe-img');
        requestFullImage();
        if (currentImg) currentImg.style.transition = 'opacity 0.15s';
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        zoomTo(getScale() * factor, e.clientX, e.clientY, 'custom');
    }, { passive: false });

    windowImpl.addEventListener('resize', () => {
        if (!getNaturalWidth()) return;
        setFitScale(computeFitScale());
        if (getIsFit()) {
            centerFit({ animate: false });
        } else {
            clampPan();
            applyTransform();
            updateZoomIndicator();
        }
    });
}
