import { byId, emit, on, rememberImages, viewState } from './state.js';
import { thumbUrl, writeFlag } from './api.js';
import { applyFlags } from './selection.js';
import { openCollectionPicker } from './panel.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';

let index = 0;
let open = false;
let zoomed = false;
let returnCell = null;
let sessionImages = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&#34;', "'": '&#39;',
}[c]));

function images() {
    return sessionImages || viewState.images;
}

function current() {
    return images()[index] || null;
}

function caption(img) {
    return `${esc(img.filename || img.id)} - ${Math.round(Number(img.elo) || 0)} - ${esc(img.date_taken || 'undated')} - ${esc(img.camera_model || '')}`;
}

function renderStrip() {
    const host = document.getElementById('loupe-strip');
    host.innerHTML = images().map((img, i) => (
        `<img class="${i === index ? 'cur' : ''}" data-index="${i}" src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" alt="">`
    )).join('');
    for (const item of host.querySelectorAll('img[data-index]')) {
        item.addEventListener('click', () => {
            index = Number(item.dataset.index);
            render();
        });
    }
    const currentThumb = host.querySelector('.cur');
    if (currentThumb) currentThumb.scrollIntoView({ block: 'nearest', inline: 'center' });
}

function render() {
    const img = current();
    if (!img) return closeLoupe();
    const image = document.getElementById('loupe-img');
    zoomed = false;
    document.getElementById('loupe').classList.remove('zoomed');
    image.style.transform = '';
    image.style.transformOrigin = '50% 50%';
    image.src = thumbUrl('md', img.id);
    document.getElementById('loupe-cap').textContent = caption(img);
    renderStrip();
    const next = images()[index + 1];
    const prev = images()[index - 1];
    for (const neighbor of [next, prev]) {
        if (!neighbor) continue;
        const preload = new Image();
        preload.src = thumbUrl('md', neighbor.id);
    }
}

export function openLoupe(target = 0) {
    sessionImages = Array.isArray(target.images) && target.images.length ? target.images : null;
    if (sessionImages) rememberImages(sessionImages);
    const list = images();
    const startIndex = typeof target === 'object' ? Number(target.index || 0) : Number(target);
    const id = typeof target === 'object' ? Number(target.id) : null;
    const resolvedIndex = id ? list.findIndex((img) => Number(img.id) === id) : -1;
    returnCell = id
        ? document.querySelector(`.cell[data-id="${id}"]`)
        : document.querySelector(`.cell[data-idx="${startIndex}"]`);
    index = Math.max(0, Math.min(list.length - 1, resolvedIndex >= 0 ? resolvedIndex : startIndex));
    open = true;
    const root = document.getElementById('loupe');
    root.hidden = false;
    trapFocus(root, root);
    render();
}

export function closeLoupe() {
    if (!open) return;
    open = false;
    sessionImages = null;
    const root = document.getElementById('loupe');
    root.hidden = true;
    releaseFocus(root);
    if (returnCell) returnCell.focus({ preventScroll: true });
}

export function loupeOpen() {
    return open;
}

export function navLoupe(delta) {
    if (!open) return;
    index = Math.max(0, Math.min(images().length - 1, index + delta));
    render();
}

async function flagCurrent(flag) {
    const img = current();
    if (!img) return;
    const old = img.flag || 'unflagged';
    img.flag = flag;
    emit('flags', { imageIds: [img.id], flag });
    const result = await writeFlag(img.id, flag);
    if (!result || !result.ok) {
        img.flag = old;
        emit('flags', { imageIds: [img.id] });
        showToast("Flag change didn't save");
        return;
    }
    showToast(flag === 'picked' ? 'Picked' : flag === 'rejected' ? 'Rejected' : 'Flag cleared', {
        undo: async () => {
            img.flag = old;
            await writeFlag(img.id, old);
            emit('flags', { imageIds: [img.id] });
        },
    });
}

function toggleZoom(event) {
    const img = current();
    if (!img) return;
    const image = document.getElementById('loupe-img');
    if (zoomed) {
        image.src = thumbUrl('md', img.id);
        image.style.transform = '';
        image.style.transformOrigin = '50% 50%';
        zoomed = false;
    } else {
        const rect = image.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 100;
        const y = ((event.clientY - rect.top) / rect.height) * 100;
        image.src = thumbUrl('lg', img.id);
        image.style.transformOrigin = `${x}% ${y}%`;
        image.style.transform = 'scale(2)';
        zoomed = true;
    }
    document.getElementById('loupe').classList.toggle('zoomed', zoomed);
}

export function flagLoupeOrFocused(flag) {
    if (open) flagCurrent(flag);
    else {
        const img = viewState.images[viewState.focusIndex];
        if (img) applyFlags([img.id], flag);
    }
}

export function initLoupe() {
    on('loupe:open', ({ id, index: startIndex }) => openLoupe({ id, index: startIndex }));
    document.getElementById('loupe-stage').addEventListener('click', (event) => {
        if (!event.target.closest('#loupe-img')) return;
        toggleZoom(event);
    });
    document.getElementById('loupe-prev').addEventListener('click', (event) => {
        event.stopPropagation();
        navLoupe(-1);
    });
    document.getElementById('loupe-next').addEventListener('click', (event) => {
        event.stopPropagation();
        navLoupe(1);
    });
    document.getElementById('loupe-close').addEventListener('click', closeLoupe);
    document.getElementById('lp-pick').addEventListener('click', () => flagCurrent('picked'));
    document.getElementById('lp-reject').addEventListener('click', () => flagCurrent('rejected'));
    document.getElementById('lp-unflag').addEventListener('click', () => flagCurrent('unflagged'));
    document.getElementById('lp-coll').addEventListener('click', () => {
        const img = current();
        if (img) openCollectionPicker([img.id]);
    });
    document.getElementById('lp-similar').addEventListener('click', () => {
        const img = current();
        if (img) emit('similar:find', { imageId: img.id });
    });
    on('flags', ({ imageIds }) => {
        for (const id of imageIds || []) {
            const img = byId.get(Number(id));
            if (img && current() && Number(img.id) === Number(current().id)) render();
        }
    });
}
