// Photos tab: Google-Photos-grade day-grouped timeline.
// Real data: /api/rankings?sort=date_taken pages the photos and
// /api/date-histogram drives the scrubber, month view, and month
// jumps across the WHOLE archive (undated photos land in a proper
// "Undated" section at the end, matching the SQL sort order).

import { getDateHistogram, getRankings, thumbUrl } from './api.js';
import {
    byId, clearSelection, emit, on, rememberImages,
    scope, scopeActive, scopeParams, selState, selection, selectionChanged,
} from './state.js';
import { openViewer } from './viewer.js';

const PAGE = 120;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const FULL_MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
];
const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

let pane = null;
let timeline = null;
let sentinel = null;
let endEl = null;

let images = [];
let startOffset = 0;
let histogram = { months: [], undated: 0, total: 0 };
let monthOffsets = [];
let zoomIdx = 0;                 // 0 = 3-col · 1 = 5-col dense · 2 = month list
let generation = 0;
let loadingNext = false;
let loadingPrev = false;
let endReached = false;
let flatIds = [];
let suppressClickUntil = 0;
let currentSortQuality = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const fmtInt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));

function parseDate(value) {
    if (!value) return null;
    const d = new Date(String(value).replace(' ', 'T'));
    return Number.isNaN(d.getTime()) ? null : d;
}

const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const monthKeyOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

function fmtDayHead(d) {
    const base = `${DAY_NAMES[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
    return d.getFullYear() === new Date().getFullYear() ? base : `${base}, ${d.getFullYear()}`;
}

export function monthLabel(key) {
    if (key === 'undated') return 'Undated';
    if (key === 'similar') return scope.label || 'Similar';
    const [year, month] = key.split('-');
    return `${FULL_MONTHS[Number(month) - 1]} ${year}`;
}

/* ---------- lazy thumbnail loading ---------- */
const imgObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const img = entry.target;
        if (img.dataset.src) {
            img.src = img.dataset.src;
            delete img.dataset.src;
        }
        imgObserver.unobserve(img);
    }
}, { rootMargin: '600px 0px' });

/* ---------- cells & day sections ---------- */
function flagBadge(flag) {
    if (flag !== 'picked' && flag !== 'rejected') return '';
    return `<span class="c-flag ${flag}">${flag === 'picked' ? '★' : '✕'}</span>`;
}

function cellFor(img, mi) {
    const fig = document.createElement('figure');
    fig.className = 'mcell';
    fig.dataset.id = String(img.id);
    fig.dataset.mi = String(mi);
    fig.setAttribute('role', 'button');
    fig.setAttribute('aria-label', img.filename || `Photo ${img.id}`);
    fig.innerHTML =
        '<div class="c-check">✓</div>'
        + `<img alt="" loading="lazy" decoding="async" data-src="${esc(img.thumb_url || thumbUrl('sm', img.id))}">`
        + flagBadge(img.flag);
    const image = fig.querySelector('img');
    image.addEventListener('load', () => image.classList.add('ld'));
    imgObserver.observe(image);
    if (selection.has(Number(img.id))) fig.classList.add('sel');
    return fig;
}

function mkDaySection(dk, d) {
    const sec = document.createElement('section');
    sec.className = 'm-day';
    sec.dataset.day = dk;
    sec.dataset.month = d ? monthKeyOf(d) : 'undated';
    sec.innerHTML =
        '<div class="m-day-head">'
        + '<span class="m-day-check" role="checkbox" aria-checked="false" aria-label="Select day">✓</span>'
        + `<h3>${d ? esc(fmtDayHead(d)) : 'Undated'}</h3></div>`
        + '<div class="m-day-grid"></div>';
    sec.querySelector('.m-day-check').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDay(sec);
    });
    return sec;
}

const dayIds = (sec) => [...sec.querySelectorAll('.mcell[data-id]')].map((c) => Number(c.dataset.id));

function toggleDay(sec) {
    const ids = dayIds(sec);
    if (!ids.length) return;
    const all = ids.every((id) => selection.has(id));
    if (all) {
        ids.forEach((id) => selection.delete(id));
    } else {
        selState.mode = true;
        ids.forEach((id) => selection.add(id));
    }
    if (navigator.vibrate) navigator.vibrate(8);
    selectionChanged();
}

function updateDayChecks() {
    for (const sec of timeline.querySelectorAll('.m-day')) {
        const ids = dayIds(sec);
        const all = ids.length > 0 && ids.every((id) => selection.has(id));
        const chk = sec.querySelector('.m-day-check');
        if (chk) {
            chk.classList.toggle('all', all);
            chk.setAttribute('aria-checked', String(all));
        }
    }
}

function reindexCells() {
    flatIds = [];
    for (const cell of timeline.querySelectorAll('.mcell[data-id]')) {
        cell.dataset.mi = String(flatIds.length);
        flatIds.push(Number(cell.dataset.id));
    }
}

/* ---------- rendering ---------- */
function renderSkeleton() {
    timeline.innerHTML =
        '<section class="m-day"><div class="m-day-head"><h3 class="skel" style="width:140px;height:16px;border-radius:4px"></h3></div>'
        + `<div class="m-day-grid">${'<div class="skel-cell"></div>'.repeat(12)}</div></section>`;
}

function appendImages(batch) {
    const frag = document.createDocumentFragment();
    let lastDay = timeline.lastElementChild && timeline.lastElementChild.classList.contains('m-day')
        ? timeline.lastElementChild
        : null;
    for (const img of batch) {
        const d = parseDate(img.date_taken);
        const dk = d ? dayKey(d) : 'undated';
        if (!lastDay || lastDay.dataset.day !== dk) {
            lastDay = mkDaySection(dk, d);
            frag.appendChild(lastDay);
        }
        lastDay.querySelector('.m-day-grid').appendChild(cellFor(img, flatIds.length));
        flatIds.push(Number(img.id));
    }
    timeline.appendChild(frag);
    timeline.classList.toggle('selmode', selState.mode);
    updateDayChecks();
}

function renderFixedImages(batch) {
    timeline.classList.toggle('m-z5', zoomIdx === 1);
    timeline.innerHTML = '';
    flatIds = [];
    const sec = mkDaySection('fixed', null);
    sec.dataset.month = 'similar';
    const head = sec.querySelector('.m-day-head h3');
    if (head) head.textContent = scope.label || 'Results';
    const grid = sec.querySelector('.m-day-grid');
    for (const img of batch) {
        grid.appendChild(cellFor(img, flatIds.length));
        flatIds.push(Number(img.id));
    }
    timeline.appendChild(sec);
    timeline.classList.toggle('selmode', selState.mode);
    updateDayChecks();
}

function prependImages(batch) {
    const frag = document.createDocumentFragment();
    let currentDay = null;
    for (const img of batch) {
        const d = parseDate(img.date_taken);
        const dk = d ? dayKey(d) : 'undated';
        if (!currentDay || currentDay.dataset.day !== dk) {
            currentDay = mkDaySection(dk, d);
            frag.appendChild(currentDay);
        }
        currentDay.querySelector('.m-day-grid').appendChild(cellFor(img, 0));
    }
    // Merge with the existing first section when the day continues.
    const firstExisting = timeline.firstElementChild;
    if (
        currentDay && firstExisting
        && firstExisting.classList.contains('m-day')
        && firstExisting.dataset.day === currentDay.dataset.day
    ) {
        const targetGrid = currentDay.querySelector('.m-day-grid');
        for (const cell of [...firstExisting.querySelectorAll('.mcell')]) {
            targetGrid.appendChild(cell);
        }
        firstExisting.remove();
    }
    const prevHeight = pane.scrollHeight;
    timeline.insertBefore(frag, timeline.firstChild);
    pane.scrollTop += pane.scrollHeight - prevHeight;
    reindexCells();
    updateDayChecks();
}

/* ---------- month (zoomed-out) view ---------- */
function renderMonths() {
    timeline.classList.remove('m-z5');
    timeline.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.id = 'm-months';
    let lastYear = '';
    for (const entry of monthOffsets) {
        if (entry.key !== 'undated') {
            const year = entry.key.slice(0, 4);
            if (year !== lastYear) {
                lastYear = year;
                const head = document.createElement('div');
                head.className = 'm-month-year';
                head.textContent = year;
                wrap.appendChild(head);
            }
        }
        const card = document.createElement('button');
        card.className = 'm-month-card';
        card.setAttribute('aria-label', `${monthLabel(entry.key)}, ${entry.count} photos`);
        card.innerHTML = `<b>${esc(entry.key === 'undated' ? 'Undated' : FULL_MONTHS[Number(entry.key.slice(5)) - 1])}</b>`
            + `<span class="num">${fmtInt(entry.count)} photos</span>`;
        card.addEventListener('click', () => {
            setZoom(0);
            jumpToMonth(entry.key);
        });
        wrap.appendChild(card);
    }
    if (!monthOffsets.length) {
        wrap.innerHTML = '<div class="ms-empty" style="grid-column:span 2">Nothing here yet.</div>';
    }
    timeline.appendChild(wrap);
}

/* ---------- zoom levels ---------- */
export function setZoom(i) {
    const next = clamp(i, 0, 2);
    if (next === zoomIdx) return;
    const was = zoomIdx;
    zoomIdx = next;
    timeline.classList.remove('m-anim');
    void timeline.offsetWidth;
    timeline.classList.add('m-anim');
    if (zoomIdx === 2) {
        renderMonths();
    } else {
        timeline.classList.toggle('m-z5', zoomIdx === 1);
        if (was === 2) rebuildLoaded();
    }
    const ctl = document.getElementById('m-zoomctl');
    if (ctl) ctl.textContent = zoomIdx === 2 ? '▣' : zoomIdx === 1 ? '▤' : '▦';
    updateMonthPill(false);
    endEl.hidden = zoomIdx === 2 || !endReached;
}

export function stepZoom(dir) {
    setZoom(zoomIdx + dir);
}

export function zoomLevel() {
    return zoomIdx;
}

function rebuildLoaded() {
    timeline.innerHTML = '';
    flatIds = [];
    appendImages(images);
}

/* ---------- month pill ---------- */
let pillTimer = null;

export function updateMonthPill(show) {
    const pill = document.getElementById('m-monthpill');
    if (!pill) return;
    if (zoomIdx === 2) {
        pill.classList.remove('on');
        return;
    }
    const secs = timeline.querySelectorAll('.m-day');
    if (!secs.length) {
        pill.classList.remove('on');
        return;
    }
    const top = pane.scrollTop + 70;
    let cur = secs[0];
    for (const s of secs) {
        if (s.offsetTop <= top) cur = s;
        else break;
    }
    pill.textContent = monthLabel(cur.dataset.month);
    if (show) {
        pill.classList.add('on');
        clearTimeout(pillTimer);
        pillTimer = setTimeout(() => pill.classList.remove('on'), 1400);
    }
}

/* ---------- data loading ---------- */
function rankingParams(offset) {
    return scopeParams({ limit: PAGE, offset, sort: 'date_taken' });
}

async function loadHistogram() {
    const data = await getDateHistogram(scopeParams());
    if (!data) return;
    histogram = data;
    monthOffsets = [];
    let offset = 0;
    for (const m of data.months || []) {
        monthOffsets.push({ key: m.month, offset, count: m.count });
        offset += m.count;
    }
    if (data.undated > 0) {
        monthOffsets.push({ key: 'undated', offset, count: data.undated });
    }
    emit('histogram', histogram);
}

export async function reload() {
    const gen = ++generation;
    images = [];
    flatIds = [];
    startOffset = 0;
    endReached = false;
    currentSortQuality = null;
    endEl.hidden = true;
    renderSkeleton();
    renderScopeBar();
    if (scope.similarImages) {
        if (gen !== generation) return;
        images = scope.similarImages;
        rememberImages(images);
        histogram = { months: [], undated: 0, total: images.length };
        monthOffsets = [];
        renderFixedImages(images);
        endReached = true;
        endEl.hidden = true;
        renderScopeBar();
        updateMonthPill(false);
        pane.scrollTop = 0;
        return;
    }
    const [, page] = await Promise.all([
        loadHistogram(),
        getRankings(rankingParams(0)),
    ]);
    if (gen !== generation) return;
    timeline.innerHTML = '';
    timeline.classList.toggle('m-z5', zoomIdx === 1);
    if (page && Array.isArray(page.images)) {
        images = page.images;
        currentSortQuality = page.sort_quality || null;
        rememberImages(images);
        if (zoomIdx === 2) renderMonths();
        else appendImages(images);
        endReached = page.images.length < PAGE;
    } else {
        timeline.innerHTML = '<div class="ms-empty" style="padding:40px 16px;text-align:center">Couldn\'t load photos.</div>';
    }
    endEl.hidden = zoomIdx === 2 || !endReached;
    renderScopeBar();
    updateMonthPill(false);
    pane.scrollTop = 0;
}

export async function loadMore() {
    if (loadingNext || endReached || zoomIdx === 2) return;
    loadingNext = true;
    const gen = generation;
    const page = await getRankings(rankingParams(startOffset + images.length));
    loadingNext = false;
    if (gen !== generation || !page || !Array.isArray(page.images)) return;
    if (!page.images.length) {
        endReached = true;
        endEl.hidden = false;
        return;
    }
    images = images.concat(page.images);
    rememberImages(page.images);
    appendImages(page.images);
    if (page.images.length < PAGE) {
        endReached = true;
        endEl.hidden = false;
    }
}

async function loadPrev() {
    if (loadingPrev || startOffset <= 0 || zoomIdx === 2) return;
    loadingPrev = true;
    const gen = generation;
    const newStart = Math.max(0, startOffset - PAGE);
    const params = scopeParams({ limit: startOffset - newStart, offset: newStart, sort: 'date_taken' });
    const page = await getRankings(params);
    loadingPrev = false;
    if (gen !== generation || !page || !Array.isArray(page.images) || !page.images.length) return;
    images = page.images.concat(images);
    startOffset = newStart;
    rememberImages(page.images);
    prependImages(page.images);
}

/* ---------- jumps (scrubber + month view) ---------- */
export function monthForFraction(frac) {
    if (!monthOffsets.length) return null;
    const idx = clamp(Math.floor(frac * monthOffsets.length), 0, monthOffsets.length - 1);
    return monthOffsets[idx];
}

export async function jumpToMonth(key) {
    const entry = monthOffsets.find((m) => m.key === key);
    if (!entry) return;
    if (zoomIdx === 2) setZoom(0);
    const loadedEnd = startOffset + images.length;
    if (entry.offset >= startOffset && entry.offset < loadedEnd) {
        const sec = timeline.querySelector(`.m-day[data-month="${key}"]`);
        if (sec) {
            pane.scrollTo({ top: Math.max(0, sec.offsetTop - 6), behavior: 'auto' });
            updateMonthPill(true);
            return;
        }
    }
    // Jump outside the loaded window: restart the window at the month offset.
    const gen = ++generation;
    images = [];
    flatIds = [];
    startOffset = entry.offset;
    endReached = false;
    endEl.hidden = true;
    renderSkeleton();
    pane.scrollTop = 0;
    const page = await getRankings(rankingParams(entry.offset));
    if (gen !== generation) return;
    timeline.innerHTML = '';
    if (page && Array.isArray(page.images)) {
        images = page.images;
        rememberImages(images);
        appendImages(images);
        endReached = page.images.length < PAGE;
    }
    endEl.hidden = !endReached;
    updateMonthPill(true);
}

/* ---------- scope bar ---------- */
function renderScopeBar() {
    const bar = document.getElementById('m-scopebar');
    if (!bar) return;
    const active = scopeActive();
    document.body.classList.toggle('m-scoped', active);
    bar.classList.toggle('on', active);
    if (!active) {
        bar.innerHTML = '';
        return;
    }
    let html = '';
    const chip = (kind, label, clear, img = '') =>
        `<span class="chip">${img}<span class="chip-kind">${esc(kind)}</span><b>${esc(label)}</b>`
        + `<span class="chip-x" role="button" aria-label="Clear ${esc(kind)}" data-clear="${clear}">✕</span></span>`;
    if (scope.people) {
        const face = scope.thumb ? `<img src="${esc(scope.thumb)}" alt="">` : '';
        html += chip('person', scope.label || 'Person', 'people', face);
    }
    if (scope.q) html += chip('search', scope.q, 'q');
    if (scope.flag) html += chip('flag', scope.flag === 'picked' ? 'Picked' : 'Rejected', 'flag');
    if (scope.fileType) html += chip('type', scope.fileType.toUpperCase(), 'fileType');
    if (scope.camera) html += chip('camera', scope.camera, 'camera');
    if (scope.lens) html += chip('lens', scope.lens, 'lens');
    if (scope.minStars) html += chip('rating', `${scope.minStars}+ stars`, 'minStars');
    if (scope.similarId) html += chip('similar', scope.label || 'Similar', 'similarId');
    html += `<span class="m-scope-count num">${fmtInt(histogram.total)} photos</span>`;
    if (currentSortQuality && Number(currentSortQuality.total) > 0) {
        html += `<span class="m-scope-quality num">${fmtInt(currentSortQuality.percent)}% sorted</span>`;
    }
    bar.innerHTML = html;
    for (const x of bar.querySelectorAll('.chip-x')) {
        x.addEventListener('click', () => {
            const field = x.dataset.clear;
            scope[field] = '';
            if (field === 'similarId') {
                scope.similarImages = null;
                scope.label = '';
            }
            if (field === 'people') {
                scope.thumb = '';
                scope.label = '';
            }
            emit('scope', scope);
        });
    }
}

/* ---------- selection gestures (long-press + drag range) ---------- */
function toggleSel(id) {
    if (selection.has(id)) selection.delete(id);
    else selection.add(id);
    selectionChanged();
}

function installSelectionGestures() {
    let lp = null;
    let dragActive = false;
    let dragBase = null;
    let anchorMi = -1;
    let dragPend = null;

    function applyDragRange(mi) {
        const a = Math.min(anchorMi, mi);
        const b = Math.max(anchorMi, mi);
        selection.clear();
        for (const id of dragBase) selection.add(id);
        for (let i = a; i <= b; i++) {
            if (flatIds[i] != null) selection.add(flatIds[i]);
        }
        selectionChanged();
    }

    timeline.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse') return;
        const cell = e.target.closest('.mcell[data-id]');
        if (!cell) return;
        const mi = Number(cell.dataset.mi);
        lp = {
            x: e.clientX,
            y: e.clientY,
            fired: false,
            timer: setTimeout(() => {
                lp.fired = true;
                if (navigator.vibrate) navigator.vibrate(12);
                selState.mode = true;
                dragBase = new Set(selection);
                anchorMi = mi;
                dragActive = true;
                applyDragRange(mi);
            }, 340),
        };
    });
    timeline.addEventListener('pointermove', (e) => {
        if (!lp) return;
        if (!lp.fired) {
            if (Math.hypot(e.clientX - lp.x, e.clientY - lp.y) > 10) {
                clearTimeout(lp.timer);
                lp = null;
            }
            return;
        }
        if (dragPend == null) {
            requestAnimationFrame(() => {
                const p = dragPend;
                dragPend = null;
                if (!p || !dragActive) return;
                const el = document.elementFromPoint(p.x, p.y);
                const cell = el && el.closest ? el.closest('.mcell[data-id]') : null;
                if (cell) applyDragRange(Number(cell.dataset.mi));
                if (p.y > window.innerHeight - 120) pane.scrollTop += 9;
                else if (p.y < 100) pane.scrollTop -= 9;
            });
        }
        dragPend = { x: e.clientX, y: e.clientY };
    });
    const endLP = () => {
        if (lp) {
            clearTimeout(lp.timer);
            if (lp.fired) suppressClickUntil = Date.now() + 420;
        }
        lp = null;
        dragActive = false;
        dragBase = null;
        dragPend = null;
    };
    timeline.addEventListener('pointerup', endLP);
    timeline.addEventListener('pointercancel', endLP);
    timeline.addEventListener('touchmove', (e) => {
        if (dragActive) e.preventDefault();
    }, { passive: false });
    timeline.addEventListener('contextmenu', (e) => e.preventDefault());

    timeline.addEventListener('click', (e) => {
        if (Date.now() < suppressClickUntil) return;
        const cell = e.target.closest('.mcell[data-id]');
        if (!cell) return;
        const id = Number(cell.dataset.id);
        if (selState.mode) {
            toggleSel(id);
        } else {
            const index = images.findIndex((img) => Number(img.id) === id);
            if (index >= 0) openViewer(images, index, { loadMore });
        }
    });
}

/* ---------- event wiring ---------- */
function syncSelectionCells() {
    timeline.classList.toggle('selmode', selState.mode);
    for (const cell of timeline.querySelectorAll('.mcell[data-id]')) {
        cell.classList.toggle('sel', selection.has(Number(cell.dataset.id)));
    }
    updateDayChecks();
}

function syncFlagCells({ ids, flagOf }) {
    const wanted = new Set(ids);
    for (const cell of timeline.querySelectorAll('.mcell[data-id]')) {
        const id = Number(cell.dataset.id);
        if (!wanted.has(id)) continue;
        const old = cell.querySelector('.c-flag');
        if (old) old.remove();
        cell.insertAdjacentHTML('beforeend', flagBadge(flagOf(id)));
    }
}

export function initTimeline() {
    pane = document.getElementById('tab-photos');
    timeline = document.getElementById('m-timeline');
    sentinel = document.getElementById('m-sentinel');
    endEl = document.getElementById('m-end');

    new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
    }, { root: pane, rootMargin: '1200px 0px' }).observe(sentinel);

    let scrollPend = false;
    pane.addEventListener('scroll', () => {
        if (scrollPend) return;
        scrollPend = true;
        requestAnimationFrame(() => {
            scrollPend = false;
            if (zoomIdx === 2) return;
            updateMonthPill(true);
            emit('timeline-scroll', null);
            if (pane.scrollTop < 400 && startOffset > 0) loadPrev();
        });
    }, { passive: true });

    document.getElementById('m-zoomctl').addEventListener('click', () => {
        setZoom((zoomIdx + 1) % 3);
    });

    installSelectionGestures();
    on('selection', syncSelectionCells);
    on('flags', syncFlagCells);
    on('scope', () => {
        clearSelection();
        reload();
    });

    reload();
}

export function scrollInfo() {
    return {
        pane,
        hasMonths: monthOffsets.length > 0,
        zoom: zoomIdx,
    };
}
