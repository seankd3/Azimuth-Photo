import {
    flagClass,
    similarLibraryCardHtml,
} from './display.js';


export function setSimilarSearchControls(filename, {
    documentImpl = document,
} = {}) {
    const input = documentImpl.getElementById('search-input');
    if (input) input.value = `Similar to: ${filename}`;
    const clearBtn = documentImpl.getElementById('search-clear');
    if (clearBtn) clearBtn.classList.remove('hidden');
    const sortToggles = documentImpl.getElementById('sort-toggles');
    if (sortToggles) sortToggles.style.opacity = '0.3';
}


export function renderSimilarCards(images, {
    documentImpl = document,
    grid = documentImpl.getElementById('rankings-grid'),
    thumbHeight = 220,
    openLightbox = () => {},
} = {}) {
    for (let i = 0; i < images.length; i++) {
        const simg = images[i];
        const ar = simg.aspect_ratio || 1.5;

        const card = documentImpl.createElement('div');
        card.className = 'rank-card skeleton-cell' + (flagClass(simg.flag) ? ' ' + flagClass(simg.flag) : '');
        card.dataset.imageId = simg.id;
        card.dataset.ar = ar;
        card.style.height = thumbHeight + 'px';
        card.style.flexGrow = ar;
        card.style.flexBasis = (thumbHeight * ar) + 'px';
        card.onclick = () => openLightbox(simg);
        card.innerHTML = similarLibraryCardHtml(simg);
        grid.appendChild(card);
    }
}


function similarPoolStats(data) {
    return {
        filtered_pool: Number(data.visible_images ?? data.images?.length ?? 0),
        filtered_pool_visible: Number(data.visible_images ?? data.images?.length ?? 0),
        filtered_pool_total: Number(data.total_images ?? data.images?.length ?? 0),
    };
}


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export function createFindSimilarAction({
    documentImpl = document,
    fetchImpl = fetch,
    getLightboxIndex = () => -1,
    getLibraryImages = () => [],
    setLibraryImages = () => {},
    setRankingsOffset = () => {},
    setRankingsExhausted = () => {},
    getThumbHeight = () => 220,
    getCompareStats = () => ({}),
    setCompareStats = () => {},
    setSearchQuery = () => {},
    bumpLibraryRequestGeneration = () => 0,
    getLibraryRequestGeneration = () => 0,
    closeLightbox = () => {},
    clearWarmups = () => {},
    clearPersistedSearchState = () => {},
    updateDateScrubber = () => {},
    clearBatchSelection = () => {},
    updateCompareProgress = () => {},
    openLightbox = () => {},
    showToast = () => {},
} = {}) {
    return async function findSimilar() {
        const libraryImages = getLibraryImages();
        const lightboxIndex = getLightboxIndex();
        if (lightboxIndex < 0 || lightboxIndex >= libraryImages.length) return;
        const img = libraryImages[lightboxIndex];

        const requestGeneration = bumpLibraryRequestGeneration();
        let data;
        try {
            const res = await fetchImpl(`/api/similar/${img.id}?limit=100`);
            data = await responseDataOrError(res, 'Similar search failed');
        } catch (err) {
            if (requestGeneration === getLibraryRequestGeneration()) {
                showToast(err.message || 'Similar search failed');
            }
            return;
        }
        if (requestGeneration !== getLibraryRequestGeneration()) return;

        closeLightbox();
        clearWarmups();
        setSearchQuery('__similar__');
        clearPersistedSearchState();
        setSimilarSearchControls(img.filename, { documentImpl });
        updateDateScrubber();

        clearBatchSelection();
        setLibraryImages([]);
        setRankingsOffset(0);
        setRankingsExhausted(true);
        const grid = documentImpl.getElementById('rankings-grid');
        if (!grid) return;
        grid.innerHTML = '';
        const images = Array.isArray(data.images) ? data.images : [];
        setCompareStats({
            ...getCompareStats(),
            ...similarPoolStats(data),
        });
        updateCompareProgress();

        renderSimilarCards(images, {
            documentImpl,
            grid,
            thumbHeight: getThumbHeight(),
            openLightbox,
        });
        setLibraryImages(images.slice());
        setRankingsOffset(images.length);
    };
}
