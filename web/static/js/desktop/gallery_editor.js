/** Client-gallery fields and API adapter for the unified Deliver popover. */

import { fetchJson } from './api.js';
import { esc } from './dom.js';

export async function listGalleryDeliveries(collectionId) {
    return fetchJson(`/api/user-collections/${collectionId}/galleries`);
}

<<<<<<< HEAD
export async function loadGalleryDelivery(collectionId, getCollection) {
    const [listed, detail] = await Promise.all([
        listGalleryDeliveries(collectionId),
        getCollection(collectionId, { limit: 500 }),
    ]);
    return {
        gallery: listed.galleries?.[0] || null,
        images: detail?.collection?.images || detail?.images || [],
=======
function fallbackPopover(button, content) {
    document.querySelector('.gallery-editor[data-gallery-owned]')?.remove();
    const popover = document.createElement('div');
    const rect = button.getBoundingClientRect();
    popover.className = 'pop-menu gallery-editor';
    popover.dataset.galleryOwned = '1';
    popover.tabIndex = -1;
    popover.innerHTML = content;
    popover.style.position = 'fixed';
    popover.style.left = `${Math.max(8, Math.min(window.innerWidth - 300, rect.left))}px`;
    popover.style.top = `${Math.max(8, Math.min(window.innerHeight - 16, rect.bottom + 6))}px`;
    document.body.append(popover);
    return popover;
}

function galleryForm(collection, gallery, images) {
    const current = gallery || { title: collection.name, layout: 'grid', theme: 'light', download_size: 'lg', allow_download_all: true };
    const coverOptions = [`<option value="">First photo</option>`].concat((images || []).map((image) => (
        `<option value="${Number(image.id)}"${Number(current.cover_image_id) === Number(image.id) ? ' selected' : ''}>${esc(image.filename || `Photo ${image.id}`)}</option>`
    ))).join('');
    return `<strong>${gallery ? 'Gallery settings' : 'Create client gallery'}</strong>
      <label>Title<input data-gallery-title value="${esc(current.title || collection.name)}"></label>
      <label>Layout<select data-gallery-layout>${['grid', 'masonry', 'slideshow'].map((x) => `<option value="${x}"${current.layout === x ? ' selected' : ''}>${x[0].toUpperCase() + x.slice(1)}</option>`).join('')}</select></label>
      <label>Theme<select data-gallery-theme>${['light', 'dark', 'warm'].map((x) => `<option value="${x}"${current.theme === x ? ' selected' : ''}>${x[0].toUpperCase() + x.slice(1)}</option>`).join('')}</select></label>
      <label>Cover photo<select data-gallery-cover>${coverOptions}</select></label>
      <label>Photo download size<select data-gallery-size>${[['sm', 'Small'], ['md', 'Medium'], ['lg', 'Large'], ['original', 'Original']].map(([x, label]) => `<option value="${x}"${current.download_size === x ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
      <label class="gallery-download-all"><input data-gallery-download-all type="checkbox"${current.allow_download_all ? ' checked' : ''}> Let client download all as ZIP</label>
      <label>Password (optional)<input data-gallery-password type="password" placeholder="Leave unchanged"></label>
      ${gallery?.protected ? '<label class="gallery-download-all"><input data-gallery-clear-password type="checkbox"> Remove password</label>' : ''}
      <button class="primary" data-gallery-save>${gallery ? 'Save gallery' : 'Create gallery'}</button>`;
}

function formPayload(popover) {
    const cover = Number(popover.querySelector('[data-gallery-cover]')?.value);
    const password = popover.querySelector('[data-gallery-password]')?.value || undefined;
    const clearPassword = Boolean(popover.querySelector('[data-gallery-clear-password]')?.checked);
    return {
        title: popover.querySelector('[data-gallery-title]')?.value.trim(),
        layout: popover.querySelector('[data-gallery-layout]')?.value,
        theme: popover.querySelector('[data-gallery-theme]')?.value,
        cover_image_id: cover > 0 ? cover : null,
        download_size: popover.querySelector('[data-gallery-size]')?.value,
        allow_download_all: Boolean(popover.querySelector('[data-gallery-download-all]')?.checked),
        ...(clearPassword ? { clear_password: true } : (password ? { password } : {})),
>>>>>>> origin/main
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
    return fetchJson(endpoint, {
        method: gallery ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

export async function revokeGalleryDelivery(collectionId, galleryId) {
    return fetchJson(`/api/user-collections/${collectionId}/galleries/${galleryId}`, { method: 'DELETE' });
}
