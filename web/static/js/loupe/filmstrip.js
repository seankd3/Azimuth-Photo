import { flagClass } from '../library/display.js';
import {
    imageAspectRatio,
    imageMetadataTitle,
} from '../media_metadata.js';


let filmstripBuiltFor = null;
let filmstripWindowStart = 0;
let filmstripWindowEnd = 0;


function defaultRequestAnimationFrame(callback) {
    const requestFrame = globalThis.requestAnimationFrame || ((cb) => cb());
    return requestFrame(callback);
}


export function clearFilmstrip({
    documentImpl = document,
    images = [],
    lightboxIndex = -1,
    poolTotal = images.length,
} = {}) {
    const scroll = documentImpl.getElementById('filmstrip-scroll');
    if (scroll) scroll.innerHTML = '';
    updateFilmstripCounter({ documentImpl, images, lightboxIndex, poolTotal });
    filmstripBuiltFor = null;
    filmstripWindowStart = 0;
    filmstripWindowEnd = 0;
}


export function updateFilmstripCounter({
    documentImpl = document,
    images = [],
    lightboxIndex = -1,
    poolTotal = images.length,
} = {}) {
    const counter = documentImpl.getElementById('filmstrip-counter');
    if (!counter) return '';
    const total = Math.max(images.length, Number(poolTotal || 0) || 0);
    const text = lightboxIndex >= 0 && images.length
        ? `${lightboxIndex + 1} / ${total}`
        : '';
    counter.textContent = text;
    return text;
}


export function buildFilmstrip({
    documentImpl = document,
    images = [],
    lightboxIndex = -1,
    poolTotal = images.length,
    onSelect = null,
    requestAnimationFrameImpl = defaultRequestAnimationFrame,
    windowRadius = 55,
} = {}) {
    const scroll = documentImpl.getElementById('filmstrip-scroll');
    if (!scroll) return false;
    updateFilmstripCounter({ documentImpl, images, lightboxIndex, poolTotal });
    if (lightboxIndex < 0) {
        clearFilmstrip({ documentImpl, images, lightboxIndex });
        return false;
    }
    const start = Math.max(0, lightboxIndex - windowRadius);
    const end = Math.min(images.length, lightboxIndex + windowRadius + 1);
    const windowStillUseful = (
        filmstripBuiltFor === images &&
        lightboxIndex >= filmstripWindowStart &&
        lightboxIndex < filmstripWindowEnd &&
        lightboxIndex - filmstripWindowStart > 10 &&
        filmstripWindowEnd - lightboxIndex > 10
    );

    if (windowStillUseful) {
        updateFilmstripActive({
            documentImpl,
            images,
            lightboxIndex,
            onSelect,
            requestAnimationFrameImpl,
            windowRadius,
        });
        return false;
    }

    filmstripBuiltFor = images;
    filmstripWindowStart = start;
    filmstripWindowEnd = end;
    scroll.innerHTML = '';
    for (let i = start; i < end; i++) {
        const img = images[i];
        const thumb = documentImpl.createElement('div');
        thumb.className = 'filmstrip-thumb' + (i === lightboxIndex ? ' active' : '') + (flagClass(img.flag) ? ' ' + flagClass(img.flag) : '');
        thumb.dataset.idx = i;
        thumb.dataset.imageId = img.id;
        thumb.style.aspectRatio = String(imageAspectRatio(img));
        thumb.title = imageMetadataTitle(img);
        thumb.onclick = () => {
            const direction = Math.sign(i - lightboxIndex);
            onSelect?.(i, direction);
        };
        const thumbImg = documentImpl.createElement('img');
        thumbImg.src = img.thumb_url || '';
        thumbImg.alt = img.filename || '';
        thumbImg.loading = 'lazy';
        thumb.appendChild(thumbImg);
        scroll.appendChild(thumb);
    }
    requestAnimationFrameImpl(() => centerFilmstripActive(scroll, true));
    return true;
}


export function updateFilmstripActive({
    documentImpl = document,
    images = [],
    lightboxIndex = -1,
    poolTotal = images.length,
    onSelect = null,
    requestAnimationFrameImpl = defaultRequestAnimationFrame,
    windowRadius = 55,
} = {}) {
    const scroll = documentImpl.getElementById('filmstrip-scroll');
    if (!scroll) return false;
    if (lightboxIndex < 0) return false;
    updateFilmstripCounter({ documentImpl, images, lightboxIndex, poolTotal });
    if (lightboxIndex < filmstripWindowStart || lightboxIndex >= filmstripWindowEnd) {
        return buildFilmstrip({
            documentImpl,
            images,
            lightboxIndex,
            onSelect,
            requestAnimationFrameImpl,
            windowRadius,
        });
    }
    scroll.querySelectorAll('.filmstrip-thumb').forEach((el) => {
        el.classList.toggle('active', Number(el.dataset.idx) === lightboxIndex);
    });
    centerFilmstripActive(scroll);
    return true;
}


export function centerFilmstripActive(scroll, instant) {
    const active = scroll.querySelector('.filmstrip-thumb.active');
    if (!active) return null;

    const maxScroll = Math.max(0, scroll.scrollWidth - scroll.clientWidth);
    const centeredLeft = active.offsetLeft + (active.offsetWidth / 2) - (scroll.clientWidth / 2);
    const targetLeft = Math.max(0, Math.min(maxScroll, centeredLeft));
    scroll.scrollTo({ left: targetLeft, behavior: instant ? 'auto' : 'smooth' });
    return targetLeft;
}
