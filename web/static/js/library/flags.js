import {
    flagBadge,
    flagClass,
} from './display.js';


const VALID_IMAGE_FLAGS = new Set(['picked', 'unflagged', 'rejected']);


export function updateImageFlagLocal(imageId, flag, {
    documentImpl = document,
    images = [],
    loupeStandaloneImage = null,
    loupeCurrentImage = null,
    lightboxIndex = -1,
    updateLoupeFlagDisplay = null,
} = {}) {
    for (const img of images) {
        if (img.id === imageId) img.flag = flag;
    }
    if (loupeStandaloneImage?.id === imageId) loupeStandaloneImage.flag = flag;
    if (loupeCurrentImage?.id === imageId) loupeCurrentImage.flag = flag;

    documentImpl.querySelectorAll(`.rank-card[data-image-id="${imageId}"]`).forEach((card) => {
        card.classList.remove('flag-picked', 'flag-rejected');
        const cls = flagClass(flag);
        if (cls) card.classList.add(cls);
        card.querySelector('.rank-flag')?.remove();
        if (flag !== 'unflagged') {
            card.insertAdjacentHTML('beforeend', flagBadge(flag));
        }
    });

    documentImpl.querySelectorAll(`.filmstrip-thumb[data-image-id="${imageId}"]`).forEach((thumb) => {
        thumb.classList.remove('flag-picked', 'flag-rejected');
        const cls = flagClass(flag);
        if (cls) thumb.classList.add(cls);
    });

    if (
        (lightboxIndex >= 0 && images[lightboxIndex]?.id === imageId)
        || (lightboxIndex < 0 && loupeStandaloneImage?.id === imageId)
    ) {
        updateLoupeFlagDisplay?.(flag);
    }
}


export async function setImageFlag(imageId, flag, {
    fetchImpl = fetch,
    images = [],
    showToast = null,
    updateImageFlagLocalImpl,
} = {}) {
    if (!imageId || !VALID_IMAGE_FLAGS.has(flag)) return false;
    const img = images.find(item => item.id === imageId);
    const previousFlag = img?.flag || 'unflagged';
    updateImageFlagLocalImpl?.(imageId, flag);
    try {
        const res = await fetchImpl(`/api/image/${imageId}/flag`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flag }),
        });
        if (!res.ok) throw new Error('flag update failed');
        return true;
    } catch {
        updateImageFlagLocalImpl?.(imageId, previousFlag);
        showToast?.('Flag update failed');
        return false;
    }
}


export function setCurrentLibraryFlag(flag, {
    documentImpl = document,
    images = [],
    lightboxIndex = -1,
    loupeStandaloneImage = null,
    selectedLibraryIndex = -1,
    setImageFlagImpl,
} = {}) {
    const loupe = documentImpl.getElementById('loupe');
    const loupeOpen = loupe && !loupe.classList.contains('hidden');
    const img = loupeOpen
        ? (images[lightboxIndex] || loupeStandaloneImage)
        : images[selectedLibraryIndex];
    if (!img) return null;
    setImageFlagImpl?.(img.id, flag);
    return img;
}
