/** Local client-gallery creation/settings popover for collection action menus. */

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

async function requestJson(url, options = {}) {
    const response = await fetch(url, { headers: { Accept: 'application/json', ...options.headers }, ...options });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.error || 'Gallery request failed');
    return payload;
}

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
      <button class="primary" data-gallery-save>${gallery ? 'Save gallery' : 'Create gallery'}</button>${gallery ? '<button class="btn-danger" data-gallery-revoke>Revoke link</button>' : ''}`;
}

function formPayload(popover) {
    const cover = Number(popover.querySelector('[data-gallery-cover]')?.value);
    const password = popover.querySelector('[data-gallery-password]')?.value || undefined;
    return {
        title: popover.querySelector('[data-gallery-title]')?.value.trim(),
        layout: popover.querySelector('[data-gallery-layout]')?.value,
        theme: popover.querySelector('[data-gallery-theme]')?.value,
        cover_image_id: cover > 0 ? cover : null,
        download_size: popover.querySelector('[data-gallery-size]')?.value,
        allow_download_all: Boolean(popover.querySelector('[data-gallery-download-all]')?.checked),
        ...(password ? { password } : {}),
    };
}

/**
 * Open the collection's local delivery settings. `anchoredPopover` is the
 * existing desktop popover helper, keeping this module independent of panel UI.
 */
export async function openGalleryEditor({ button, collection, anchoredPopover, closePopover, showToast, getCollection }) {
    if (!collection?.id) return null;
    try {
        const [listed, detail] = await Promise.all([
            requestJson(`/api/user-collections/${collection.id}/galleries`),
            getCollection ? getCollection(collection.id, { limit: 500 }) : requestJson(`/api/user-collections/${collection.id}`),
        ]);
        const gallery = listed.galleries?.[0] || null;
        const images = detail?.collection?.images || detail?.images || [];
        const popover = (anchoredPopover || fallbackPopover)(button, galleryForm(collection, gallery, images));
        popover.classList.add('gallery-editor');
        popover.querySelector('[data-gallery-save]')?.addEventListener('click', async () => {
            const payload = formPayload(popover);
            try {
                const endpoint = gallery
                    ? `/api/user-collections/${collection.id}/galleries/${gallery.id}`
                    : `/api/user-collections/${collection.id}/galleries`;
                const result = await requestJson(endpoint, {
                    method: gallery ? 'PATCH' : 'POST',
                    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
                });
                (closePopover || (() => popover.remove()))();
                const url = result.gallery?.url;
                if (url && navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
                showToast?.(url ? 'Client gallery ready · link copied' : 'Client gallery saved');
            } catch (error) {
                showToast?.(error.message || 'Could not save client gallery');
            }
        });
        popover.querySelector('[data-gallery-revoke]')?.addEventListener('click', async (event) => {
            const revokeButton = event.currentTarget;
            if (revokeButton.dataset.confirm !== '1') {
                revokeButton.dataset.confirm = '1';
                revokeButton.textContent = 'Confirm revoke';
                return;
            }
            try {
                await requestJson(`/api/user-collections/${collection.id}/galleries/${gallery.id}`, { method: 'DELETE' });
                (closePopover || (() => popover.remove()))();
                showToast?.('Client gallery link revoked');
            } catch (error) {
                showToast?.(error.message || 'Could not revoke client gallery link');
            }
        });
        return popover;
    } catch (error) {
        showToast?.(error.message || 'Could not open client gallery settings');
        return null;
    }
}
