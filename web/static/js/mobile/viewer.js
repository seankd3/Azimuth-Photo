// GP-class photo viewer on a pure-black canvas.
// Gesture grammar (from the One prototype):
//   2 fingers  → live pinch zoom + pan (midpoint-anchored)
//   1 finger   → pan when zoomed · swipe up to favorite · swipe down to close · swipe left/right to browse at 1x
//   double-tap → 1x ↔ 2.5x at the tap point
// Flags are real writes with undo.

import { getExif, getImageCaption, getSimilar, thumbUrl, writeRating } from './api.js';
import { applyFlags } from './flags.js';
import { byId, emit, nav as appNav, on, rememberImages, setScope } from './state.js';
import { dismissSheetThen, openCollectionSheet, openSheet } from './selection.js';
import { showToast } from './toast.js';
import { dismissLayer, dismissLayerThen, pushLayer, registerLayer, syncLayerClosed } from './history.js';
import { icon } from '../icons.js';
import { createMomentum } from './viewer_momentum.js';
import { openPhotoShareSheet } from './sharing.js';
import { isAvailableOffline, toggleOfflineAvailability } from './offline.js';
import { cacheCaptionRequest } from './caption_cache.js';

let root = null;
let stage = null;
let img = null;
let cap = null;
let flagBadge = null;

let openState = false;
let list = [];
let index = -1;
let needMore = null;
let loadToken = 0;
let incomingStageImage = null;
const mediumPreloads = new Map();
const captionCache = new Map();

let zScale = 1;
let tx = 0;
let ty = 0;
let zoomed = false;
let panMomentum = null;

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const LARGE_IMAGE_TIMEOUT_MS = 8000;
const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

function current() {
    return list[index] || null;
}

function viewerRequestCurrent(imageId, generation) {
    return generation === loadToken && Number(current()?.id || 0) === Number(imageId || 0);
}

function applyT() {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${zScale})`;
}

function clampPan() {
    const rect = stage.getBoundingClientRect();
    const maxX = (rect.width * (zScale - 1)) / 2;
    const maxY = (rect.height * (zScale - 1)) / 2;
    tx = clamp(tx, -maxX, maxX);
    ty = clamp(ty, -maxY, maxY);
}

function resetZoom() {
    panMomentum?.stop();
    zScale = 1;
    tx = 0;
    ty = 0;
    zoomed = false;
    root.classList.remove('zoomed');
    img.style.transform = '';
}

function setZoomTo(scale, clientX, clientY) {
    const rect = stage.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    zScale = scale;
    tx = (cx - clientX) * (scale - 1);
    ty = (cy - clientY) * (scale - 1);
    clampPan();
    zoomed = scale > 1;
    root.classList.toggle('zoomed', zoomed);
    if (zoomed) loadLg();
    applyT();
}

function loadLg() {
    const image = current();
    if (!image) return;
    const token = loadToken;
    const lg = new Image();
    lg.decoding = 'async';
    lg.fetchPriority = 'high';
    let settled = false;
    const finish = ({ offline = false } = {}) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        if (offline && token === loadToken) setViewerOffline(true);
    };
    const timeout = window.setTimeout(() => finish({ offline: true }), LARGE_IMAGE_TIMEOUT_MS);
    lg.onload = () => {
        if (token === loadToken) {
            img.src = lg.src;
            setViewerOffline(false);
        }
        finish();
    };
    lg.onerror = () => finish({ offline: true });
    lg.src = thumbUrl('lg', image.id);
}

function setViewerOffline(offline) {
    let chip = stage?.querySelector('.viewer-offline-chip');
    if (!chip && stage) {
        chip = document.createElement('span');
        chip.className = 'viewer-offline-chip';
        chip.textContent = 'Original offline';
        chip.hidden = true;
        stage.append(chip);
    }
    if (chip) chip.hidden = !offline;
}

function preload(offset) {
    const neighbor = list[index + offset];
    if (neighbor) {
        let pre = mediumPreloads.get(Number(neighbor.id));
        if (!pre) {
            pre = new Image();
            pre.fetchPriority = 'low';
            pre.src = thumbUrl('md', neighbor.id);
            mediumPreloads.set(Number(neighbor.id), pre);
        }
        preloadCaption(neighbor.id);
    }
}

function preloadCaption(imageId) {
    const id = Number(imageId);
    if (!id) return Promise.resolve(null);
    return cacheCaptionRequest(captionCache, id, () => getImageCaption(id));
}

function upgradeToMedium(image, token) {
    const medium = new Image();
    medium.decoding = 'async';
    medium.fetchPriority = 'high';
    medium.onload = async () => {
        if (medium.decode) await medium.decode().catch(() => {});
        if (!viewerRequestCurrent(image.id, token)) return;
        img.src = medium.src;
    };
    medium.src = thumbUrl('md', image.id);
}

function showCurrent({ stageReady = false } = {}) {
    const image = current();
    if (!image) return;
    loadToken += 1;
    const token = loadToken;
    setViewerOffline(false);
    resetZoom();
    if (!stageReady) {
        img.fetchPriority = 'high';
        // preview_ready exception: opening Viewer intentionally uses its progressive proxy-to-full loader.
        img.src = image.thumb_url || thumbUrl('sm', image.id);
        upgradeToMedium(image, token);
    }
    loadLg();
    const date = image.date_taken ? String(image.date_taken).slice(0, 16).replace('T', ' · ') : '';
    cap.textContent = [image.filename, date].filter(Boolean).join('  —  ');
    preloadCaption(image.id).then((data) => {
        if (!viewerRequestCurrent(image.id, token)) return;
        const caption = data?.has_caption ? String(data.caption || '').trim() : '';
        cap.textContent = [caption || image.filename, date].filter(Boolean).join('  —  ');
    });
    syncFlagButtons();
    syncOfflineButton();
    preload(1);
    preload(-1);
    if (needMore && index >= list.length - 5) needMore();
}

function syncFlagButtons() {
    const image = current();
    const flag = image ? (byId.get(Number(image.id)) || image).flag || 'unflagged' : 'unflagged';
    const favorite = document.getElementById('mv-pick');
    const reject = document.getElementById('mv-reject');
    favorite.classList.toggle('on-pick', flag === 'picked');
    favorite.setAttribute('aria-pressed', String(flag === 'picked'));
    reject.classList.toggle('on-reject', flag === 'rejected');
    reject.setAttribute('aria-pressed', String(flag === 'rejected'));
    if (flagBadge) {
        flagBadge.hidden = flag === 'unflagged';
        flagBadge.className = `mv-flag ${flag}`;
        flagBadge.textContent = flag === 'picked' ? 'Favorited' : 'Rejected';
    }
}

function syncOfflineButton() {
    const image = current();
    const button = document.getElementById('mv-offline');
    const available = Boolean(image && isAvailableOffline(image.id));
    button.classList.toggle('on-offline', available);
    button.setAttribute('aria-pressed', String(available));
    button.setAttribute('aria-label', available ? 'Remove offline availability' : 'Make available offline');
}

function favoriteSwipe() {
    const image = current();
    if (!image) return;
    root.classList.remove('cull-picked');
    void root.offsetWidth;
    root.classList.add('cull-picked');
    window.setTimeout(() => root.classList.remove('cull-picked'), 260);
    void applyFlags([image.id], 'picked');
}

function settleDismissSwipe() {
    img.style.transition = 'transform .16s cubic-bezier(.2,.7,.2,1)';
    root.style.transition = 'background .16s cubic-bezier(.2,.7,.2,1)';
    img.style.transform = `translateY(${window.innerHeight * .22}px) scale(.78)`;
    root.style.background = 'rgba(0,0,0,0)';
    window.setTimeout(() => {
        img.style.transition = '';
        root.style.transition = '';
        dismissViewer();
    }, 150);
}

function nav(dir) {
    const next = index + dir;
    if (next < 0 || next >= list.length) return;
    index = next;
    showCurrent();
}

function settlePhotoSwipe(direction) {
    const next = index + direction;
    if (next < 0 || next >= list.length) {
        img.style.transform = '';
        return;
    }
    const neighbor = list[next];
    const preloaded = mediumPreloads.get(Number(neighbor.id));
    if (!preloaded?.complete || !preloaded.naturalWidth) {
        img.style.transition = 'transform .16s cubic-bezier(.2,.7,.2,1)';
        img.style.transform = `translateX(${direction > 0 ? -window.innerWidth : window.innerWidth}px)`;
        window.setTimeout(() => {
            img.style.transition = '';
            nav(direction);
        }, 150);
        return;
    }
    incomingStageImage?.remove();
    incomingStageImage = null;
    const travel = direction > 0 ? -window.innerWidth : window.innerWidth;
    const incoming = document.createElement('img');
    incoming.className = 'viewer-swipe-incoming';
    incoming.alt = '';
    incoming.decoding = 'async';
    incoming.src = preloaded.src;
    incoming.style.transform = `translateX(${-travel}px)`;
    stage.append(incoming);
    incomingStageImage = incoming;
    const finish = (event) => {
        if (incomingStageImage !== incoming) return;
        if (event.target !== incoming || event.propertyName !== 'transform') return;
        incoming.removeEventListener('transitionend', finish);
        img.src = incoming.src;
        incoming.remove();
        incomingStageImage = null;
        img.style.transition = '';
        img.style.transform = '';
        index = next;
        showCurrent({ stageReady: true });
    };
    incoming.addEventListener('transitionend', finish);
    window.setTimeout(() => finish({ target: incoming, propertyName: 'transform' }), 350);
    requestAnimationFrame(() => {
        img.style.transition = 'transform .16s cubic-bezier(.2,.7,.2,1)';
        incoming.style.transition = 'transform .16s cubic-bezier(.2,.7,.2,1)';
        img.style.transform = `translateX(${travel}px)`;
        incoming.style.transform = 'translateX(0)';
    });
}

export function openViewer(imageList, startIndex, { loadMore = null } = {}) {
    list = imageList;
    index = startIndex;
    needMore = loadMore;
    openState = true;
    root.hidden = false;
    document.body.classList.add('viewer-open');
    document.body.style.overflow = 'hidden';
    showCurrent();
    pushLayer('viewer');
}

export function closeViewer({ fromHistory = false } = {}) {
    if (!openState) return;
    resetZoom();
    openState = false;
    root.hidden = true;
    root.style.background = '';
    incomingStageImage?.remove();
    incomingStageImage = null;
    img.style.transform = '';
    document.body.classList.remove('viewer-open');
    document.body.style.overflow = '';
    if (!fromHistory) syncLayerClosed('viewer');
}

function dismissViewer() {
    dismissLayer('viewer', closeViewer);
}

function dismissViewerThen(afterClose = null) {
    dismissLayerThen('viewer', closeViewer, afterClose);
}

function imageRating(image) {
    const value = Number(image?.rating ?? image?.stars ?? image?._lr_rating ?? 0);
    return Number.isFinite(value) ? clamp(Math.round(value), 0, 5) : 0;
}

function syncRatingButtons(sheet, rating) {
    for (const button of sheet.querySelectorAll('[data-rating]')) {
        const value = Number(button.dataset.rating);
        const active = value <= rating;
        button.classList.toggle('on', active);
        button.setAttribute('aria-pressed', String(active));
    }
}

function infoSheet() {
    const image = current();
    if (!image) return;
    const fmtBytes = (b) => {
        if (b == null) return '—';
        const units = ['B', 'KB', 'MB', 'GB'];
        let v = Number(b);
        let i = 0;
        while (v >= 1024 && i < units.length - 1) {
            v /= 1024;
            i += 1;
        }
        return `${v >= 10 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
    };
    const rows = [
        ['File', image.filename],
        ['Taken', image.date_taken || 'Undated'],
        ['Camera', [image.camera_make, image.camera_model].filter(Boolean).join(' ') || '—'],
        ['Lens', image.lens || '—'],
        ['Size', `${image.width || '?'} × ${image.height || '?'} · ${fmtBytes(image.file_size)}`],
        ['Flag', image.flag || 'unflagged'],
    ];
    const sheet = openSheet(
        '<h3>Info</h3>'
        + `<button class="sheet-row" id="mv-similar"><span class="g">${icon('scan-search')}</span>Find similar</button>`
        + '<div class="sheet-rating"><span>Rating</span><div class="sheet-stars" role="group" aria-label="Star rating">'
        + [1, 2, 3, 4, 5].map((rating) =>
            `<button type="button" data-rating="${rating}" aria-label="Set ${rating} star rating">${icon('star')}</button>`
        ).join('')
        + '</div></div>'
        + '<div class="sheet-caption" id="mv-caption">'
        + '<div class="sheet-caption-label">Caption</div>'
        + '<div class="sheet-caption-text sheet-caption-muted">Loading…</div>'
        + '</div>'
        + '<div class="sheet-meta">'
        + rows.map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')
        + '</div>'
        + '<details class="sheet-details"><summary>More details</summary>'
        + '<div class="sheet-meta" id="mv-exif"><div><span>Loading</span><b>…</b></div></div></details>'
    );
    syncRatingButtons(sheet, imageRating(image));
    const ratingGeneration = loadToken;
    fetch(`/api/image/${image.id}/rating`, { headers: { Accept: 'application/json' } })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
            if (!data || !viewerRequestCurrent(image.id, ratingGeneration)) return;
            image.rating = data.rating;
            const known = byId.get(Number(image.id));
            if (known) known.rating = data.rating;
            if (sheet.isConnected) syncRatingButtons(sheet, imageRating(image));
        })
        .catch(() => {});
    for (const button of sheet.querySelectorAll('[data-rating]')) {
        button.addEventListener('click', () => {
            const target = image;
            const value = Number(button.dataset.rating);
            const previous = imageRating(target);
            const rating = previous === value ? 0 : value;
            target.rating = rating;
            const known = byId.get(Number(target.id));
            if (known) known.rating = rating;
            syncRatingButtons(sheet, rating);
            void writeRating(target.id, rating).then((result) => {
                if (result.status !== 'failed' || imageRating(target) !== rating) return;
                target.rating = previous;
                if (known) known.rating = previous;
                if (sheet.isConnected) syncRatingButtons(sheet, previous);
            });
        });
    }
    sheet.querySelector('#mv-similar').addEventListener('click', async () => {
        let data = null;
        try {
            data = await getSimilar(image.id, 100);
        } catch {
            showToast('Couldn’t find similar photos');
            return;
        }
        const results = (data && data.images) || [];
        if (!results.length) {
            showToast('No similar photos found');
            return;
        }
        rememberImages(results);
        setScope({
            similarId: String(image.id),
            similarImages: results,
            label: `Similar to ${image.filename || `photo ${image.id}`}`,
        });
        dismissSheetThen(() => {
            dismissViewerThen(() => {
                appNav.setTab('photos');
                showToast(`${results.length} similar photos`);
            });
        });
    });
    const generation = loadToken;
    loadCaptionBlock(sheet, image.id, generation);
    loadExifDetails(sheet, image.id, generation);
}

function scopeToTag(tag) {
    if (!tag) return;
    setScope({ tag, label: `#${tag}` });
    dismissSheetThen(() => {
        dismissViewerThen(() => {
            appNav.setTab('photos');
        });
    });
}

async function loadCaptionBlock(sheet, imageId, generation) {
    const host = sheet.querySelector('#mv-caption');
    if (!host) return;
    const data = await getImageCaption(imageId);
    if (!host.isConnected || !viewerRequestCurrent(imageId, generation)) return;
    if (data?.error) {
        host.innerHTML = '<div class="sheet-caption-label">Caption</div>'
            + '<button type="button" class="sheet-caption-text sheet-caption-muted" id="mv-caption-retry">Couldn\'t load — tap to retry</button>';
        host.querySelector('#mv-caption-retry')?.addEventListener('click', () => {
            host.innerHTML = '<div class="sheet-caption-label">Caption</div>'
                + '<div class="sheet-caption-text sheet-caption-muted">Loading…</div>';
            loadCaptionBlock(sheet, imageId, generation);
        });
        return;
    }
    const hasCaption = Boolean(data && data.has_caption);
    const text = hasCaption ? String(data.caption || '').trim() : '';
    const tags = (hasCaption && Array.isArray(data.tags)) ? data.tags.filter(Boolean) : [];
    let html = '<div class="sheet-caption-label">Caption</div>';
    if (!hasCaption || !text) {
        html += '<div class="sheet-caption-text sheet-caption-muted">Not yet captioned</div>';
    } else {
        html += `<div class="sheet-caption-text">${esc(text)}</div>`;
    }
    if (tags.length) {
        html += '<div class="ms-pills sheet-caption-tags">'
            + tags.map((tag) =>
                `<button type="button" class="ms-pill" data-caption-tag="${esc(tag)}">#${esc(tag)}</button>`
            ).join('')
            + '</div>';
    }
    host.innerHTML = html;
    for (const chip of host.querySelectorAll('[data-caption-tag]')) {
        chip.addEventListener('click', () => {
            if (!viewerRequestCurrent(imageId, generation)) return;
            scopeToTag(chip.dataset.captionTag || '');
        });
    }
}

function detailValue(value) {
    if (value == null || value === '') return '—';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

async function loadExifDetails(sheet, imageId, generation) {
    const target = sheet.querySelector('#mv-exif');
    if (!target) return;
    let data = null;
    try {
        data = await getExif(imageId);
    } catch {
        if (!target.isConnected || !viewerRequestCurrent(imageId, generation)) return;
        target.innerHTML = '<div><span>Details</span><b>Couldn\'t load EXIF</b></div>';
        return;
    }
    if (!target.isConnected || !viewerRequestCurrent(imageId, generation)) return;
    const exif = (data && data.exif) || {};
    const entries = Object.entries(exif)
        .filter(([, value]) => value != null && value !== '')
        .sort(([a], [b]) => a.localeCompare(b));
    if (!entries.length) {
        target.innerHTML = '<div><span>Details</span><b>No EXIF found</b></div>';
        return;
    }
    target.innerHTML = entries.map(([key, value]) =>
        `<div><span>${esc(key.replaceAll('_', ' '))}</span><b>${esc(detailValue(value))}</b></div>`
    ).join('');
}

/* ---------- touch gesture grammar ---------- */
function installGestures() {
    let gest = null;
    let sw = null;
    let pin = null;
    let lastTapT = 0;
    let lastTapX = 0;
    let lastTapY = 0;
    const tdist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);

    stage.addEventListener('touchstart', (e) => {
        if (e.touches.length === 2) {
            const [a, b] = [e.touches[0], e.touches[1]];
            gest = 'pinch';
            pin = {
                d0: tdist(a, b), s0: zScale, tx0: tx, ty0: ty,
                mx0: (a.clientX + b.clientX) / 2, my0: (a.clientY + b.clientY) / 2,
            };
            sw = null;
            root.classList.add('dragging');
            return;
        }
        if (e.touches.length !== 1) return;
        const t = e.touches[0];
        if (zoomed) {
            gest = 'pan';
            sw = { x: t.clientX, y: t.clientY, tx0: tx, ty0: ty, moved: 0 };
            panMomentum?.stop();
            panMomentum?.record(t.clientX, t.clientY);
            root.classList.add('dragging');
        } else {
            gest = 'swipe';
            sw = { x: t.clientX, y: t.clientY, mode: null, res: 0, startedAt: performance.now() };
        }
    }, { passive: true });

    stage.addEventListener('touchmove', (e) => {
        if (gest === 'pinch' && pin && e.touches.length === 2) {
            const [a, b] = [e.touches[0], e.touches[1]];
            const rect = stage.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const mx = (a.clientX + b.clientX) / 2;
            const my = (a.clientY + b.clientY) / 2;
            const s = clamp(pin.s0 * tdist(a, b) / pin.d0, 1, 5);
            const p0x = (pin.mx0 - cx - pin.tx0) / pin.s0;
            const p0y = (pin.my0 - cy - pin.ty0) / pin.s0;
            zScale = s;
            tx = (mx - cx) - p0x * s;
            ty = (my - cy) - p0y * s;
            clampPan();
            if (s > 1.15) loadLg();
            applyT();
            return;
        }
        if (gest === 'pan' && sw && e.touches.length === 1) {
            const t = e.touches[0];
            sw.moved = Math.max(sw.moved || 0, Math.hypot(t.clientX - sw.x, t.clientY - sw.y));
            tx = sw.tx0 + (t.clientX - sw.x);
            ty = sw.ty0 + (t.clientY - sw.y);
            clampPan();
            applyT();
            panMomentum?.record(t.clientX, t.clientY);
            return;
        }
        if (gest === 'swipe' && sw && e.touches.length === 1) {
            const t = e.touches[0];
            let dx = t.clientX - sw.x;
            const dy = t.clientY - sw.y;
            if (!sw.mode) {
                if (Math.abs(dy) > 14 && Math.abs(dy) > Math.abs(dx)) sw.mode = dy > 0 ? 'down' : 'up';
                else if (Math.abs(dx) > 14) sw.mode = 'h';
                if (sw.mode) root.classList.add('dragging');
            }
            if (sw.mode === 'down') {
                const p = clamp(dy / 300, 0, 1);
                img.style.transform = `translateY(${Math.max(0, dy)}px) scale(${1 - p * 0.12})`;
                root.style.background = `rgba(0,0,0,${1 - p * 0.6})`;
            } else if (sw.mode === 'up') {
                const p = clamp(-dy / 300, 0, 1);
                img.style.transform = `translateY(${Math.min(0, dy)}px) scale(${1 - p * 0.12})`;
                root.style.background = `rgba(0,0,0,${1 - p * 0.6})`;
            } else if (sw.mode === 'h') {
                if ((index <= 0 && dx > 0) || (index >= list.length - 1 && dx < 0)) dx *= 0.35;
                sw.res = dx;
                img.style.transform = `translateX(${dx}px)`;
            }
        }
    }, { passive: true });

    stage.addEventListener('touchend', (e) => {
        if (gest === 'pinch') {
            pin = null;
            const settle = () => {
                if (zScale <= 1.05) resetZoom();
                else {
                    zoomed = true;
                    root.classList.add('zoomed');
                    loadLg();
                }
            };
            if (e.touches.length === 1) {
                settle();
                if (zoomed) {
                    const t = e.touches[0];
                    gest = 'pan';
                    sw = { x: t.clientX, y: t.clientY, tx0: tx, ty0: ty };
                    return;
                }
            } else {
                settle();
            }
            gest = null;
            sw = null;
            root.classList.remove('dragging');
            return;
        }
        if (gest === 'pan') {
            if (e.touches.length === 0) {
                if (sw && (sw.moved || 0) < 10 && e.changedTouches.length) {
                    const t = e.changedTouches[0];
                    const now = Date.now();
                    if (now - lastTapT < 300 && Math.hypot(t.clientX - lastTapX, t.clientY - lastTapY) < 40) {
                        lastTapT = 0;
                        resetZoom();
                    } else {
                        lastTapT = now;
                        lastTapX = t.clientX;
                        lastTapY = t.clientY;
                    }
                }
                gest = null;
                panMomentum?.release();
                sw = null;
                root.classList.remove('dragging');
            }
            return;
        }
        if (gest === 'swipe' && sw) {
            const t = e.changedTouches[0];
            const dx = t.clientX - sw.x;
            const dy = t.clientY - sw.y;
            root.classList.remove('dragging');
            root.style.background = '';
            if (!sw.mode) {
                const now = Date.now();
                if (now - lastTapT < 300 && Math.hypot(t.clientX - lastTapX, t.clientY - lastTapY) < 40) {
                    lastTapT = 0;
                    if (zoomed) resetZoom();
                    else setZoomTo(2.5, t.clientX, t.clientY);
                } else {
                    lastTapT = now;
                    lastTapX = t.clientX;
                    lastTapY = t.clientY;
                }
                gest = null;
                sw = null;
                return;
            }
            const elapsed = Math.max(1, performance.now() - (sw.startedAt || performance.now()));
            const vx = dx / elapsed;
            const vy = dy / elapsed;
            if (sw.mode === 'down' && (dy > 90 || vy > 0.75)) {
                settleDismissSwipe();
            } else if (sw.mode === 'up' && (dy < -60 || vy < -0.75)) {
                img.style.transform = '';
                favoriteSwipe();
            } else if (sw.mode === 'h' && (Math.abs(sw.res) > 70 || Math.abs(vx) > 0.65)) {
                const dir = dx < 0 ? 1 : -1;
                settlePhotoSwipe(dir);
            } else {
                img.style.transform = '';
            }
            gest = null;
            sw = null;
        }
    }, { passive: true });

    stage.addEventListener('touchcancel', () => {
        panMomentum?.stop();
        gest = null;
        sw = null;
        pin = null;
        root.classList.remove('dragging');
        root.style.background = '';
        if (zoomed) {
            clampPan();
            applyT();
        } else {
            img.style.transform = '';
        }
    }, { passive: true });
}

export function initViewer() {
    root = document.getElementById('m-viewer');
    stage = document.getElementById('mv-stage');
    img = document.getElementById('mv-img');
    cap = document.getElementById('mv-cap');
    panMomentum = createMomentum({
        read: () => ({ x: tx, y: ty }),
        write: (x, y) => {
            tx = x;
            ty = y;
            applyT();
        },
        constrain: (x, y) => {
            const rect = stage.getBoundingClientRect();
            const maxX = (rect.width * (zScale - 1)) / 2;
            const maxY = (rect.height * (zScale - 1)) / 2;
            return { x: clamp(x, -maxX, maxX), y: clamp(y, -maxY, maxY) };
        },
    });
    flagBadge = document.createElement('div');
    flagBadge.id = 'mv-flag';
    flagBadge.hidden = true;
    stage.appendChild(flagBadge);
    registerLayer('viewer', { close: closeViewer });

    document.getElementById('mv-close').addEventListener('click', dismissViewer);
    document.getElementById('mv-pick').addEventListener('click', () => {
        const image = current();
        const flag = image ? (byId.get(Number(image.id)) || image).flag || 'unflagged' : '';
        if (image) applyFlags([image.id], flag === 'picked' ? 'unflagged' : 'picked');
    });
    document.getElementById('mv-reject').addEventListener('click', () => {
        const image = current();
        const flag = image ? (byId.get(Number(image.id)) || image).flag || 'unflagged' : '';
        if (image) applyFlags([image.id], flag === 'rejected' ? 'unflagged' : 'rejected');
    });
    document.getElementById('mv-coll').addEventListener('click', () => {
        const image = current();
        if (image) openCollectionSheet([Number(image.id)]);
    });
    document.getElementById('mv-share').addEventListener('click', () => {
        const image = current();
        if (image) openPhotoShareSheet(image);
    });
    document.getElementById('mv-offline').addEventListener('click', async () => {
        const image = current();
        if (!image) return;
        const button = document.getElementById('mv-offline');
        button.disabled = true;
        try {
            await toggleOfflineAvailability(image);
            syncOfflineButton();
        } finally {
            button.disabled = false;
        }
    });
    document.getElementById('mv-info').addEventListener('click', infoSheet);

    window.addEventListener('keydown', (e) => {
        if (!openState) return;
        if (e.key === 'Escape') dismissViewer();
        else if (e.key === 'ArrowRight') nav(1);
        else if (e.key === 'ArrowLeft') nav(-1);
    });
    const reflow = () => {
        if (!openState || !zoomed) return;
        clampPan();
        applyT();
    };
    window.addEventListener('resize', reflow);
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', reflow);
        window.visualViewport.addEventListener('scroll', reflow);
    }

    on('flags', syncFlagButtons);
    on('rating-write', ({ status, imageId }) => {
        if (status !== 'committed') return;
        emit('rating', { imageId, rating: imageRating(byId.get(imageId)) });
    });
    on('offline-availability', syncOfflineButton);
    installGestures();
}
