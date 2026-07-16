// Mobile sharing owner: private collection links, one-photo links, and the
// Shared lens. All writes reuse the existing collection/share APIs.

import {
    createCollection,
    createCollectionShare,
    getCollection,
    getCollectionShare,
    getCollectionShareFavorites,
    listCollections,
    listSharedSurfaces,
    previewThumbUrl,
    revokeCollectionShare,
    writeFailureMessage,
} from './api.js';
import { applyFlags } from './flags.js';
import { rememberImages } from './state.js';
import { dismissSheetThen, openSheet } from './selection.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

const PHOTO_SHARE_PREFIX = 'Azimuth single-photo share:';

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));
const fmtInt = (value) => Number(value || 0).toLocaleString('en-US');

function formatDate(value) {
    if (value == null) return 'Never';
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString([], {
        year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
}

function relativeDate(value) {
    if (value == null) return 'never';
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) return 'unknown';
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const ranges = [
        ['year', 31536000], ['month', 2592000], ['week', 604800],
        ['day', 86400], ['hour', 3600], ['minute', 60],
    ];
    const formatter = new Intl.RelativeTimeFormat([], { numeric: 'auto' });
    for (const [unit, size] of ranges) {
        if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
    }
    return formatter.format(seconds, 'second');
}

function shareStats(share) {
    const count = Number(share?.view_count || 0);
    if (!count) return 'Never opened';
    return `Opened ${fmtInt(count)} ${count === 1 ? 'time' : 'times'} · last ${relativeDate(share.last_viewed_at)}`;
}

export async function shareLink(url, title) {
    if (navigator.share) {
        try {
            await navigator.share({ title, url });
            return true;
        } catch (error) {
            if (error?.name === 'AbortError') return false;
        }
    }
    try {
        await navigator.clipboard.writeText(url);
        showToast('Link copied');
        return true;
    } catch {
        showToast("Couldn't copy link");
        return false;
    }
}

function favoriteIds(data) {
    return ((data && data.favorites) || [])
        .map((row) => Number(row && row.image_id))
        .filter((id) => id > 0);
}

function passwordControl(share) {
    const protectedLink = Boolean(share?.protected);
    return '<div class="share-password-mobile">'
        + '<div class="share-password-mobile-head"><span>Password</span>'
        + (protectedLink ? '<b>Protected</b>' : '')
        + '</div>'
        + `<input class="sheet-input" id="ml-share-password" type="password" autocomplete="new-password" placeholder="${protectedLink ? 'Protected' : 'No password'}">`
        + (share ? `<button class="sheet-row" id="ml-share-password-save" data-mutating><span class="g">${icon('lock')}</span>${protectedLink ? 'Change password' : 'Set password'}</button>` : '')
        + (protectedLink ? `<button class="sheet-row" id="ml-share-password-clear" data-mutating><span class="g">${icon('x')}</span>Remove password</button>` : '')
        + '</div>';
}

function bindConfirm(sheet, buttonSelector, confirmSelector, action) {
    const button = sheet.querySelector(buttonSelector);
    const confirm = sheet.querySelector(confirmSelector);
    if (!button || !confirm) return;
    button.addEventListener('click', () => {
        button.hidden = true;
        confirm.hidden = false;
        confirm.querySelector('[data-yes]')?.focus();
    });
    confirm.querySelector('[data-no]')?.addEventListener('click', () => {
        confirm.hidden = true;
        button.hidden = false;
    });
    const yes = confirm.querySelector('[data-yes]');
    yes?.addEventListener('click', async () => {
        if (yes.disabled) return;
        yes.disabled = true;
        try {
            await action();
        } finally {
            if (document.contains(yes)) yes.disabled = false;
        }
    });
}

async function applyClientFavorites(collection, ids) {
    if (!ids.length) {
        showToast('No client picks yet');
        return;
    }
    let data;
    try {
        data = await getCollection(collection.id, 1000);
    } catch {
        showToast(writeFailureMessage());
        return;
    }
    rememberImages((data && data.collection && data.collection.images) || []);
    dismissSheetThen(() => applyFlags(ids, 'picked'));
}

async function renderCollectionShare(collection, share) {
    // Favorites are decoration on the share sheet — a failed load must not
    // leave the sheet dead, just render without picks.
    let picks = null;
    if (share) {
        try {
            picks = await getCollectionShareFavorites(collection.id);
        } catch { /* render without picks */ }
    }
    const ids = favoriteIds(picks);
    const sheet = openSheet(
        `<h3>Share ${esc(collection.name)}</h3>`
        + (share
            ? '<div class="sheet-meta">'
                + `<div><span>Link</span><b>${esc(share.url)}</b></div>`
                + `<div><span>Created</span><b>${esc(formatDate(share.created_at))}</b></div>`
                + `<div><span>Expires</span><b>${esc(formatDate(share.expires_at))}</b></div>`
                + `<div><span>Stats</span><b>${esc(shareStats(share))}</b></div></div>`
                + `<button class="sheet-row" id="ml-share-apply-picks" data-mutating ${ids.length ? '' : 'disabled'}>`
                + `<span class="g">${icon('heart')}</span><span class="body">Client picks</span>`
                + `<span class="n num">${fmtInt(picks?.count ?? ids.length)}</span></button>`
                + passwordControl(share)
                + '<button class="sheet-btn" id="ml-share-copy">Share…</button>'
                + `<button class="sheet-row" id="ml-share-rotate" data-mutating><span class="g">${icon('refresh-cw')}</span>Rotate link</button>`
                + '<div class="sheet-confirm" id="ml-share-rotate-confirm" hidden>Invalidate old link? <button data-yes="1">Yes</button><button data-no="1">No</button></div>'
                + `<button class="sheet-row" id="ml-share-revoke" data-mutating><span class="g">${icon('x')}</span>Revoke</button>`
                + '<div class="sheet-confirm" id="ml-share-revoke-confirm" hidden>Revoke link? <button data-yes="1">Yes</button><button data-no="1">No</button></div>'
            : '<div class="ms-empty">Create a private gallery link for this collection.</div>'
                + '<label class="share-expiry">Expires <select class="sheet-input" id="ml-share-expiry">'
                + '<option value="">Never</option><option value="7">7 days</option><option value="30">30 days</option>'
                + '</select></label>'
                + passwordControl(null)
                + '<button class="sheet-btn" id="ml-share-create" data-mutating>Create share link</button>')
    );

    sheet.querySelector('#ml-share-copy')?.addEventListener('click', () => shareLink(share.url, collection.name));
    sheet.querySelector('#ml-share-apply-picks')?.addEventListener('click', () => applyClientFavorites(collection, ids));
    sheet.querySelector('#ml-share-create')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (button.disabled) return;
        button.disabled = true;
        try {
            const expiry = sheet.querySelector('#ml-share-expiry')?.value || '';
            const password = sheet.querySelector('#ml-share-password')?.value || '';
            const result = await createCollectionShare(collection.id, {
                expiresInDays: expiry ? Number(expiry) : null,
                ...(password ? { password } : {}),
            });
            if (result?.ok) {
                showToast('Share link created');
                await renderCollectionShare(collection, result.share);
            } else showToast(writeFailureMessage());
        } finally {
            if (document.contains(button)) button.disabled = false;
        }
    });
    sheet.querySelector('#ml-share-password-save')?.addEventListener('click', async () => {
        const password = sheet.querySelector('#ml-share-password')?.value || '';
        if (!password) {
            showToast('Enter a password');
            return;
        }
        const result = await createCollectionShare(collection.id, { password });
        if (result?.ok) {
            showToast(share.protected ? 'Password changed' : 'Password set');
            await renderCollectionShare(collection, result.share);
        } else showToast(writeFailureMessage());
    });
    sheet.querySelector('#ml-share-password-clear')?.addEventListener('click', async () => {
        const result = await createCollectionShare(collection.id, { clearPassword: true });
        if (result?.ok) {
            showToast('Password removed');
            await renderCollectionShare(collection, result.share);
        } else showToast(writeFailureMessage());
    });
    bindConfirm(sheet, '#ml-share-rotate', '#ml-share-rotate-confirm', async () => {
        const result = await createCollectionShare(collection.id, { rotate: true });
        if (result?.ok) {
            showToast('New share link created');
            await renderCollectionShare(collection, result.share);
        } else showToast(writeFailureMessage());
    });
    bindConfirm(sheet, '#ml-share-revoke', '#ml-share-revoke-confirm', async () => {
        const result = await revokeCollectionShare(collection.id);
        if (result?.ok) {
            showToast('Share link revoked');
            await renderCollectionShare(collection, null);
        } else showToast(writeFailureMessage());
    });
}

export async function openCollectionShareSheet(collection) {
    openSheet(`<h3>Share ${esc(collection.name)}</h3><div class="ms-empty">Loading…</div>`);
    try {
        const data = await getCollectionShare(collection.id);
        await renderCollectionShare(collection, data && data.share);
    } catch {
        // A sheet stuck on "Loading…" is a lie — close it and say what happened.
        dismissSheetThen();
        showToast(writeFailureMessage());
    }
}

function photoShareName(image) {
    const filename = String(image?.filename || '').trim();
    return filename ? filename.replace(/\.[^.]+$/, '') : `Photo ${Number(image?.id) || ''}`.trim();
}

async function photoShareCollection(image) {
    const marker = `${PHOTO_SHARE_PREFIX}${Number(image.id)}`;
    const listed = await listCollections();
    const existing = (listed?.collections || []).find((collection) => collection.description === marker);
    if (existing) return existing;
    const created = await createCollection(photoShareName(image), [Number(image.id)], marker);
    return created?.ok ? created.collection : null;
}

export async function openPhotoShareSheet(image) {
    if (!image?.id) return;
    let collection = null;
    let share = null;
    const previewSrc = previewThumbUrl(image);
    const preview = previewSrc
        ? `<img src="${esc(previewSrc)}" alt="">`
        : '<i class="m-share-photo-placeholder" aria-hidden="true"></i>';
    const sheet = openSheet(
        `<h3>Share ${esc(photoShareName(image))}</h3>`
        + `<div class="m-share-photo-preview ${previewSrc ? '' : 'preview-pending'}">`
        + `${preview}<span>Private link · one photo</span></div>`
        + '<button class="sheet-btn" id="mv-share-create" data-mutating>Share photo…</button>'
    );
    sheet.querySelector('#mv-share-create')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = 'Preparing link…';
        try {
            collection = await photoShareCollection(image);
            if (!collection) {
                showToast(writeFailureMessage());
                return;
            }
            const current = await getCollectionShare(collection.id);
            share = current?.share;
            if (!share) {
                const created = await createCollectionShare(collection.id);
                share = created?.share;
            }
            if (!share?.url) {
                showToast(writeFailureMessage());
                return;
            }
            document.dispatchEvent(new CustomEvent('collections-changed'));
            await shareLink(share.url, photoShareName(image));
        } catch {
            showToast(writeFailureMessage());
        } finally {
            if (document.contains(button)) {
                button.disabled = false;
                button.textContent = share?.url ? 'Share again…' : 'Share photo…';
            }
        }
    });
}

function sharedUrl(item) {
    return item?.private_link?.url || item?.website?.url || '';
}

export async function renderSharedView(host, onBack) {
    host.innerHTML = '<div class="ml-head"><button class="ml-back" id="ml-shared-back">Back</button>'
        + '<h3>Shared with me</h3><span class="ml-head-spacer"></span></div>'
        + '<div class="m-shared-list">'
        + '<div class="m-shared-loading"><div class="skel-row"></div><div class="skel-row"></div></div></div>';
    host.querySelector('#ml-shared-back')?.addEventListener('click', onBack);
    let data = null;
    try {
        data = await listSharedSurfaces();
    } catch {
        // The retry state below owns recovery.
    }
    if (!host.isConnected) return;
    const list = host.querySelector('.m-shared-list');
    const items = data?.items || [];
    if (!data) {
        list.innerHTML = '<div class="m-feature-empty"><b>Shared galleries are unavailable</b>'
            + '<span>Check the connection, then try again.</span><button class="sheet-btn" id="ml-shared-retry">Try again</button></div>';
        list.querySelector('#ml-shared-retry')?.addEventListener('click', () => renderSharedView(host, onBack));
        return;
    }
    if (!items.length) {
        list.innerHTML = '<div class="m-feature-empty"><b>Nothing shared yet</b>'
            + '<span>Private links and published galleries will appear here.</span></div>';
        return;
    }
    list.innerHTML = items.map((item, index) => {
        const url = sharedUrl(item);
        const detail = item.private_link
            ? `${fmtInt(item.photo_count)} photos · private link`
            : `${fmtInt(item.photo_count)} photos · website`;
        const cover = item.cover_thumb_url
            ? `<img src="${esc(item.cover_thumb_url)}" alt="" loading="lazy" decoding="async">`
            : icon('images', 'icon icon-lg');
        return `<article class="m-shared-card" data-shared="${index}">`
            + `<button class="m-shared-open" ${url ? '' : 'disabled'}><span class="m-shared-cover">${cover}</span>`
            + `<span class="body"><b>${esc(item.name)}</b><small>${esc(detail)}</small></span>${icon('chevron-right')}</button>`
            + (url ? `<button class="m-shared-share" aria-label="Share ${esc(item.name)}">${icon('share-2')}</button>` : '')
            + '</article>';
    }).join('');
    for (const card of list.querySelectorAll('.m-shared-card[data-shared]')) {
        const item = items[Number(card.dataset.shared)];
        const url = sharedUrl(item);
        card.querySelector('.m-shared-open')?.addEventListener('click', () => {
            if (url) window.open(url, '_blank', 'noopener');
        });
        card.querySelector('.m-shared-share')?.addEventListener('click', () => shareLink(url, item.name));
    }
}
