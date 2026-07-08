import { getSimilar } from './api.js';
import { byId, clearSelection, emit, on, rememberImages, setScope } from './state.js';
import { closeLoupe } from './loupe.js';
import { showToast } from './toast.js';

function labelFor(img) {
    return `Similar to ${img.filename || `photo ${img.id}`}`;
}

export async function findSimilar(imageId) {
    const id = Number(imageId);
    if (!id) return;
    const source = byId.get(id) || { id };
    const data = await getSimilar(id, 100);
    if (!data || data.error || data.ok === false) {
        showToast('Similar search failed');
        return;
    }
    const images = Array.isArray(data.images) ? data.images : [];
    rememberImages(images);
    closeLoupe();
    clearSelection();
    setScope({
        similarIds: images.map((img) => Number(img.id)).filter((value) => value > 0),
        similarLabel: labelFor(source),
        sort: 'elo',
    }, { pushHash: false });
    emit('similar:loaded', { imageId: id, images });
    showToast(images.length ? `${images.length} similar photos` : 'No similar photos found');
}

export function initSimilar() {
    on('similar:find', ({ imageId } = {}) => findSimilar(imageId));
}
