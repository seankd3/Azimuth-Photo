export function loupeNeighborOffsets(radius, direction = 0) {
    const offsets = [];
    for (let distance = 1; distance <= radius; distance++) {
        if (direction > 0) offsets.push(distance, -distance);
        else if (direction < 0) offsets.push(-distance, distance);
        else offsets.push(-distance, distance);
    }
    return offsets;
}


export function loupeHotSetTierIds(images, currentIndex, direction = 0) {
    if (currentIndex < 0 || currentIndex >= images.length) return null;
    const current = images[currentIndex];
    const md = [];
    const lg = current?.id ? [current.id] : [];
    const full = current?.id ? [current.id] : [];

    for (const offset of loupeNeighborOffsets(8, direction)) {
        const neighborIndex = currentIndex + offset;
        if (neighborIndex < 0 || neighborIndex >= images.length) continue;
        const id = images[neighborIndex]?.id;
        if (!id) continue;
        const distance = Math.abs(offset);
        if (distance <= 8) md.push(id);
        if (distance <= 4) lg.push(id);
        if (distance <= 1) full.push(id);
    }

    return { md, lg, full };
}


export function createLoupeNavigationController({
    getLibraryImages = () => [],
    getLightboxIndex = () => -1,
    setLightboxIndex = () => {},
    getSearchQuery = () => '',
    getRankingsExhausted = () => true,
    setStandaloneImage = () => {},
    updateFilmstripCounter = () => {},
    buildFilmstrip = () => {},
    clearFilmstrip = () => {},
    showLoupeImage = () => {},
    loadRankings = async () => 0,
} = {}) {
    async function ensureLibraryImageIndex(index) {
        if (index < getLibraryImages().length) return true;
        if (getSearchQuery() === '__similar__') return false;
        while (index >= getLibraryImages().length && !getRankingsExhausted()) {
            const before = getLibraryImages().length;
            const loaded = await loadRankings(false);
            if (getLibraryImages().length <= before && !loaded) break;
        }
        return index < getLibraryImages().length;
    }

    function openStandaloneLightbox(img) {
        setStandaloneImage(img);
        setLightboxIndex(-1);
        clearFilmstrip();
        showLoupeImage(img, 0);
    }

    function openLightbox(img) {
        const images = getLibraryImages();
        const index = images.findIndex(i => i.id === img.id);
        setLightboxIndex(index);
        if (index < 0) {
            openStandaloneLightbox(img);
            return;
        }
        setStandaloneImage(null);
        updateFilmstripCounter();
        buildFilmstrip();
        showLoupeImage(images[index], 0);
    }

    function lightboxNext() {
        const currentIndex = getLightboxIndex();
        if (currentIndex < 0) return;

        const nextIndex = currentIndex + 1;
        const images = getLibraryImages();
        if (nextIndex < images.length) {
            setLightboxIndex(nextIndex);
            updateFilmstripCounter();
            showLoupeImage(images[nextIndex], 1);
            return;
        }

        ensureLibraryImageIndex(nextIndex).then((ok) => {
            if (!ok || getLightboxIndex() !== currentIndex) return;
            const nextImages = getLibraryImages();
            setLightboxIndex(nextIndex);
            updateFilmstripCounter();
            showLoupeImage(nextImages[nextIndex], 1);
        });
    }

    function lightboxPrev() {
        const currentIndex = getLightboxIndex();
        if (currentIndex <= 0) return;
        const nextIndex = currentIndex - 1;
        setLightboxIndex(nextIndex);
        updateFilmstripCounter();
        showLoupeImage(getLibraryImages()[nextIndex], -1);
    }

    return {
        ensureLibraryImageIndex,
        lightboxNext,
        lightboxPrev,
        openLightbox,
        openStandaloneLightbox,
    };
}
