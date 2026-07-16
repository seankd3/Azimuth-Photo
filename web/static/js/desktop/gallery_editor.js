/** Client-gallery fields and API adapter for the unified Deliver popover. */

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

async function requestJson(url, options = {}) {
    const response = await fetch(url, { headers: { Accept: 'application/json', ...options.headers }, ...options });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.error || 'Gallery request failed');
    return payload;
}

export async function loadGalleryDelivery(collectionId, getCollection) {
    const [listed, detail] = await Promise.all([
        requestJson(`/api/user-collections/${collectionId}/galleries`),
        getCollection(collectionId, { limit: 500 }),
    ]);
    return {
        gallery: listed.galleries?.[0] || null,
        images: detail?.collection?.images || detail?.images || [],
    };
}

export function galleryDeliveryFields(gallery, images) {
    const current = gallery || { layout: 'grid', theme: 'light', download_size: 'lg', allow_download_all: true };
    const coverOptions = ['<option value="">First photo</option>'].concat((images || []).map((image) => (
        `<option value="${Number(image.id)}"${Number(current.cover_image_id) === Number(image.id) ? ' selected' : ''}>${esc(image.filename || `Photo ${image.id}`)}</option>`
    ))).join('');
    return '<div class="deliver-gallery-fields">'
        + `<label>Layout<select data-gallery-layout>${['grid', 'masonry', 'slideshow'].map((value) => `<option value="${value}"${current.layout === value ? ' selected' : ''}>${value[0].toUpperCase() + value.slice(1)}</option>`).join('')}</select></label>`
        + `<label>Theme<select data-gallery-theme>${['light', 'dark', 'warm'].map((value) => `<option value="${value}"${current.theme === value ? ' selected' : ''}>${value[0].toUpperCase() + value.slice(1)}</option>`).join('')}</select></label>`
        + `<label>Cover photo<select data-gallery-cover>${coverOptions}</select></label>`
        + `<label>Photo download size<select data-gallery-size>${[['sm', 'Small'], ['md', 'Medium'], ['lg', 'Large'], ['original', 'Original']].map(([value, label]) => `<option value="${value}"${current.download_size === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`
        + `<label class="gallery-download-all"><input data-gallery-download-all type="checkbox"${current.allow_download_all ? ' checked' : ''}> Let client download all as ZIP</label>`
        + '</div>';
}

export function galleryDeliveryPayload(root, { title, password = '', clearPassword = false }) {
    const cover = Number(root.querySelector('[data-gallery-cover]')?.value);
    return {
        title: title.trim(),
        layout: root.querySelector('[data-gallery-layout]')?.value,
        theme: root.querySelector('[data-gallery-theme]')?.value,
        cover_image_id: cover > 0 ? cover : null,
        download_size: root.querySelector('[data-gallery-size]')?.value,
        allow_download_all: Boolean(root.querySelector('[data-gallery-download-all]')?.checked),
        ...(password ? { password } : {}),
        ...(clearPassword ? { clear_password: true } : {}),
    };
}

export async function saveGalleryDelivery(collectionId, gallery, payload) {
    const endpoint = gallery
        ? `/api/user-collections/${collectionId}/galleries/${gallery.id}`
        : `/api/user-collections/${collectionId}/galleries`;
    return requestJson(endpoint, {
        method: gallery ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

export async function revokeGalleryDelivery(collectionId, galleryId) {
    return requestJson(`/api/user-collections/${collectionId}/galleries/${galleryId}`, { method: 'DELETE' });
}
