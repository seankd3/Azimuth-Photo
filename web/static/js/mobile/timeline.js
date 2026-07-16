// Photos tab: Google-Photos-grade day-grouped timeline.
// Real data: /api/rankings?sort=date_taken pages the photos and
// /api/date-histogram drives the scrubber, month view, and month
// jumps across the WHOLE archive (undated photos land in a proper
// "Undated" section at the end, matching the SQL sort order).

import { getDateHistogram, getRankings, getStack, thumbUrl } from './api.js';
import {
    byId, clearScope, clearSelection, emit, on, rememberImages,
    isOffline, scope, scopeActive, scopeParams, selState, selection, selectionChanged,
    setViewPrefs, viewPrefs,
} from './state.js';
import { dismissLayer } from './history.js';
import { dismissSheetThen, openSheet } from './selection.js';
import { showToast } from './toast.js';
import { openViewer } from './viewer.js';
import { tick } from './haptics.js';
import { icon } from '../icons.js';
import { personLabel } from '../people_labels.js';
import { openPersonSheet } from './search.js';

const PAGE = 120;
const MAX_WINDOW = PAGE * 3;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const FULL_MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
];
const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const SORT_OPTIONS = [
    { value: 'date_taken', label: 'Date ↓', detail: 'Newest first', glyph: 'calendar' },
    { value: 'date_taken_asc', label: 'Date ↑', detail: 'Oldest first', glyph: 'calendar' },
    { value: 'elo', label: 'Rating ↓', detail: 'Highest first', glyph: 'star' },
    { value: 'elo_asc', label: 'Rating ↑', detail: 'Lowest first', glyph: 'star' },
    { value: 'taste', label: 'Taste', detail: 'your eye, learned from Refine', glyph: 'sparkles' },
];
const COMPARED_LABELS = {
    compared: 'Ranked',
    uncompared: 'Unranked',
    direct_uncompared: 'Not compared yet',
    confident: 'High confidence',
};

let pane = null;
let timeline = null;
let sentinel = null;
let endEl = null;
let expandedStack = null;
let stackRequest = 0;
const stackCache = new Map();

let images = [];
let startOffset = 0;
let histogram = { months: [], undated: 0, total: 0 };
let monthOffsets = [];
let zoomIdx = 0;                 // 0 = 3-col · 1 = 5-col dense · 2 = month list
let generation = 0;
let loadingNext = false;
let loadingPrev = false;
let initialLoading = false;
let endReached = false;
let flatIds = [];
let suppressClickUntil = 0;
let currentSortQuality = null;
let longPressPending = false;
let cancelLongPressGesture = () => {};
let tasteAvailable = false;

async function loadTasteStatus() {
    const data = await getRankings(new URLSearchParams({ sort: 'taste', limit: '0' })).catch(() => null);
    tasteAvailable = Boolean(data?.taste_available);
}

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
    return `<span class="c-flag ${flag}">${flag === 'picked' ? icon('heart') : icon('x')}</span>`;
}

function stackBadge(img) {
    const stackId = Number(img.stack_id) || 0;
    const stackCount = Number(img.stack_count) || 0;
    if (!stackId || stackCount <= 1) return '';
    return `<button type="button" class="c-stack" data-stack-id="${stackId}" aria-label="Expand stack of ${stackCount} photos" aria-expanded="false">${icon('layers')}<b>${stackCount}</b></button>`;
}

function cellFor(img, mi) {
    const fig = document.createElement('figure');
    const stackCount = Number(img.stack_count) || 0;
    fig.className = 'mcell';
    fig.dataset.id = String(img.id);
    fig.dataset.mi = String(mi);
    fig.setAttribute('role', 'button');
    fig.setAttribute(
        'aria-label',
        `${img.filename || `Photo ${img.id}`}${stackCount > 1 ? `, stack of ${stackCount} photos` : ''}`,
    );
    fig.innerHTML =
        `<div class="c-check">${icon('check')}</div>`
        + `<img alt="" loading="lazy" decoding="async" fetchpriority="low" data-src="${esc(img.thumb_url || thumbUrl('sm', img.id))}">`
        + stackBadge(img)
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
        + `<span class="m-day-check" role="checkbox" aria-checked="false" aria-label="Select day">${icon('check')}</span>`
        + `<h3>${d ? esc(fmtDayHead(d)) : 'Undated'}</h3></div>`
        + '<div class="m-day-grid"></div>';
    sec.querySelector('.m-day-check').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDay(sec);
    });
    return sec;
}

function mkMonthHeader(key) {
    const header = document.createElement('div');
    header.className = 'm-month-head';
    header.dataset.month = key;
    header.textContent = monthLabel(key);
    return header;
}

const dayIds = (sec) => [...sec.querySelectorAll('.mcell[data-id]:not([data-stack-member])')]
    .map((c) => Number(c.dataset.id));

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
    tick(8);
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
    for (const cell of timeline.querySelectorAll('.mcell[data-id]:not([data-stack-member])')) {
        cell.dataset.mi = String(flatIds.length);
        flatIds.push(Number(cell.dataset.id));
    }
}

function closeExpandedStack() {
    if (!expandedStack) return false;
    const { badge, tray } = expandedStack;
    if (badge?.isConnected) {
        badge.classList.remove('expanded', 'loading');
        badge.disabled = false;
        badge.setAttribute('aria-expanded', 'false');
        badge.setAttribute('aria-label', badge.getAttribute('aria-label')?.replace(/^Collapse/, 'Expand') || 'Expand stack');
    }
    if (tray?.isConnected) tray.remove();
    expandedStack = null;
    return true;
}

function stackMemberCell(member) {
    const cell = cellFor({ ...member, stack_id: null, stack_count: null }, -1);
    cell.dataset.stackMember = '1';
    return cell;
}

async function expandStack(stackId, cell, badge) {
    const id = Number(stackId) || 0;
    if (!id || !cell || !badge) return;
    if (expandedStack?.id === id) {
        stackRequest += 1;
        closeExpandedStack();
        return;
    }
    for (const pending of timeline.querySelectorAll('.c-stack.loading')) {
        pending.classList.remove('loading');
        pending.disabled = false;
    }
    stackRequest += 1;
    closeExpandedStack();
    const request = stackRequest;
    const gen = generation;
    badge.classList.add('loading');
    badge.disabled = true;
    let data = stackCache.get(id) || null;
    if (!data) {
        try {
            data = await getStack(id);
        } catch {
            data = null;
        }
    }
    if (request !== stackRequest || gen !== generation || !cell.isConnected) return;
    badge.classList.remove('loading');
    badge.disabled = false;
    const members = Array.isArray(data?.members) ? data.members.filter(Boolean) : [];
    const extras = members.filter((member) => Number(member.id) !== Number(cell.dataset.id));
    if (!extras.length) {
        showToast('Couldn’t expand this stack');
        return;
    }
    stackCache.set(id, data);
    rememberImages(members);
    const tray = document.createElement('div');
    tray.className = 'm-stack-tray';
    tray.dataset.stackId = String(id);
    tray.innerHTML = `<div class="m-stack-head"><span>${extras.length.toLocaleString('en-US')} more in this stack</span></div>`;
    const grid = document.createElement('div');
    grid.className = 'm-stack-members';
    for (const member of extras) grid.appendChild(stackMemberCell(member));
    tray.appendChild(grid);
    cell.insertAdjacentElement('afterend', tray);
    badge.classList.add('expanded');
    badge.setAttribute('aria-expanded', 'true');
    badge.setAttribute('aria-label', `Collapse stack of ${Number(data.member_count || members.length)} photos`);
    expandedStack = { id, badge, tray, members };
}

/* ---------- rendering ---------- */
function renderSkeleton() {
    closeExpandedStack();
    timeline.innerHTML =
        '<section class="m-day"><div class="m-day-head"><h3 class="skel" style="width:140px;height:16px;border-radius:4px"></h3></div>'
        + `<div class="m-day-grid">${'<div class="skel-cell"></div>'.repeat(12)}</div></section>`;
}

function renderOfflineEmpty() {
    timeline.innerHTML =
        '<button class="ms-empty m-offline-empty" type="button">'
        + '<b>You’re offline</b>'
        + '<span>Some thumbnails may still show</span>'
        + '<small>Tap to retry</small>'
        + '</button>';
    timeline.querySelector('.m-offline-empty')?.addEventListener('click', reload);
}

function renderEmpty() {
    timeline.innerHTML = '<div class="ms-empty" style="padding:48px 24px;text-align:center">'
        + '<b>No photos yet</b><br><span>Add a source in the desktop app. Photos will appear here as they’re scanned.</span></div>';
}

function appendImages(batch) {
    const frag = document.createDocumentFragment();
    let lastDay = timeline.lastElementChild && timeline.lastElementChild.classList.contains('m-day')
        ? timeline.lastElementChild
        : null;
    for (const img of batch) {
        const d = parseDate(img.date_taken);
        const dk = d ? dayKey(d) : 'undated';
        const month = d ? monthKeyOf(d) : 'undated';
        const previousMonth = lastDay?.dataset.month;
        if (!lastDay || previousMonth !== month) frag.appendChild(mkMonthHeader(month));
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

function firstVisibleCell() {
    const paneTop = pane.getBoundingClientRect().top;
    return [...timeline.querySelectorAll('.mcell[data-id]:not([data-stack-member])')]
        .find((cell) => cell.getBoundingClientRect().bottom >= paneTop) || null;
}

function trimWindowFromStart() {
    if (images.length <= MAX_WINDOW) return;
    const anchor = firstVisibleCell();
    const anchorId = anchor?.dataset.id;
    const oldTop = anchor?.getBoundingClientRect().top || 0;
    const dropped = images.length - MAX_WINDOW;
    images = images.slice(dropped);
    startOffset += dropped;
    rebuildLoaded();
    const nextAnchor = anchorId && timeline.querySelector(`.mcell[data-id="${anchorId}"]`);
    if (nextAnchor) pane.scrollTop += nextAnchor.getBoundingClientRect().top - oldTop;
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
    closeExpandedStack();
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
    closeExpandedStack();
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
        wrap.innerHTML = '<div class="ms-empty" style="grid-column:span 2">No photos yet. Add a source in the desktop app.</div>';
    }
    timeline.appendChild(wrap);
}

/* ---------- zoom levels ---------- */
function zoomAnchorAt(clientX, clientY) {
    const cell = document.elementFromPoint(clientX, clientY)?.closest('.mcell[data-id]');
    if (!cell || !timeline.contains(cell)) return null;
    return {
        id: cell.dataset.id,
        top: cell.getBoundingClientRect().top - pane.getBoundingClientRect().top,
    };
}

function restoreZoomAnchor(anchor) {
    if (!anchor) return;
    requestAnimationFrame(() => {
        const cell = timeline.querySelector(`.mcell[data-id="${anchor.id}"]`);
        if (!cell) return;
        pane.scrollTop += cell.getBoundingClientRect().top - pane.getBoundingClientRect().top - anchor.top;
    });
}

export function setZoom(i, { anchor = null } = {}) {
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
    if (ctl) ctl.innerHTML = icon(zoomIdx === 2 ? 'rows-3' : zoomIdx === 1 ? 'grid-3x3' : 'layout-grid', 'icon icon-lg');
    updateMonthPill(false);
    endEl.hidden = zoomIdx === 2 || !endReached;
    restoreZoomAnchor(anchor);
}

export function stepZoom(dir) {
    setZoom(zoomIdx + dir);
}

export function zoomLevel() {
    return zoomIdx;
}

// Semantic zoom stays deliberately discrete (day grid → dense grid → months),
// but the pinch itself is continuous: each threshold can be crossed in either
// direction during one gesture. Keeping the touched photo anchored means the
// switch feels like a zoom instead of a navigation jump.
function installPinchZoom() {
    let pinch = null;

    const distance = (touches) => Math.hypot(
        touches[0].clientX - touches[1].clientX,
        touches[0].clientY - touches[1].clientY,
    );
    const midpoint = (touches) => ({
        x: (touches[0].clientX + touches[1].clientX) / 2,
        y: (touches[0].clientY + touches[1].clientY) / 2,
    });
    const reset = () => {
        pinch = null;
        timeline.classList.remove('m-pinching');
    };

    pane.addEventListener('touchstart', (event) => {
        if (event.touches.length !== 2 || selection.size || initialLoading) return;
        const startDistance = distance(event.touches);
        if (startDistance < 24) return;
        pinch = { startDistance, startZoom: zoomIdx };
        timeline.classList.add('m-pinching');
    }, { passive: true });

    pane.addEventListener('touchmove', (event) => {
        if (!pinch || event.touches.length !== 2) return;
        event.preventDefault();
        const currentDistance = distance(event.touches);
        const point = midpoint(event.touches);
        // A doubling/halving spans the full semantic zoom range. Round only
        // after measuring the continuous pinch so one gesture can cross both
        // levels naturally instead of being capped at a single step.
        const delta = Math.log2(pinch.startDistance / Math.max(currentDistance, 1)) * 2;
        const target = clamp(Math.round(pinch.startZoom + delta), 0, 2);
        if (target === zoomIdx) return;
        setZoom(target, { anchor: zoomAnchorAt(point.x, point.y) });
    }, { passive: false });

    pane.addEventListener('touchend', (event) => {
        if (event.touches.length < 2) reset();
    }, { passive: true });
    pane.addEventListener('touchcancel', reset, { passive: true });
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
    return scopeParams({ limit: PAGE, offset, sort: viewPrefs.sort || 'date_taken' });
}

async function loadHistogram() {
    let data = null;
    try {
        data = await getDateHistogram(scopeParams());
    } catch {
        return;
    }
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
    initialLoading = true;
    stackRequest += 1;
    closeExpandedStack();
    images = [];
    flatIds = [];
    startOffset = 0;
    endReached = false;
    currentSortQuality = null;
    endEl.textContent = scopeActive() ? "That's all for this filter." : "That's everything.";
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
        initialLoading = false;
        return;
    }
    let page = null;
    try {
        [, page] = await Promise.all([
            loadHistogram(),
            getRankings(rankingParams(0)),
        ]);
    } catch {
        page = null;
    }
    if (gen !== generation) return;
    timeline.innerHTML = '';
    timeline.classList.toggle('m-z5', zoomIdx === 1);
    if (page && Array.isArray(page.images)) {
        images = page.images;
        currentSortQuality = page.sort_quality || null;
        rememberImages(images);
        if (zoomIdx === 2) renderMonths();
        else if (images.length) appendImages(images);
        else renderEmpty();
        endReached = page.images.length < PAGE;
    } else if (isOffline()) {
        renderOfflineEmpty();
    } else {
        timeline.innerHTML = '<div class="ms-empty" style="padding:40px 16px;text-align:center">Couldn\'t load photos.</div>';
    }
    endEl.hidden = zoomIdx === 2 || !endReached || images.length === 0;
    renderScopeBar();
    updateMonthPill(false);
    pane.scrollTop = 0;
    initialLoading = false;
}

export async function loadMore() {
    if (initialLoading || loadingNext || endReached || zoomIdx === 2) return;
    loadingNext = true;
    const gen = generation;
    let page = null;
    try {
        page = await getRankings(rankingParams(startOffset + images.length));
    } catch {
        loadingNext = false;
        showToast('Couldn’t load more photos');
        return;
    }
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
    trimWindowFromStart();
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
    const params = scopeParams({ limit: startOffset - newStart, offset: newStart, sort: viewPrefs.sort || 'date_taken' });
    let page = null;
    try {
        page = await getRankings(params);
    } catch {
        loadingPrev = false;
        showToast('Couldn’t load earlier photos');
        return;
    }
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

export function monthCenterFraction(key) {
    const idx = monthOffsets.findIndex((m) => m.key === key);
    if (idx < 0 || !monthOffsets.length) return null;
    return (idx + 0.5) / monthOffsets.length;
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
    let page = null;
    try {
        page = await getRankings(rankingParams(entry.offset));
    } catch {
        if (gen !== generation) return;
        timeline.innerHTML = '<div class="ms-empty" style="padding:40px 16px;text-align:center">Couldn\'t load photos.</div>';
        endEl.hidden = true;
        return;
    }
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
    const chips = [];
    const chip = (kind, label, clear, img = '') =>
        `<span class="chip">${img}<span class="chip-kind">${esc(kind)}</span><b>${esc(label)}</b>`
        + `<span class="chip-x" role="button" aria-label="Clear ${esc(kind)}" data-clear="${clear}">${icon('x')}</span></span>`;
    if (scope.people) {
        const face = scope.thumb ? `<img src="${esc(scope.thumb)}" alt="">` : '';
        chips.push(chip('person', personLabel({ label: scope.peopleLabel }), 'people', face));
    }
    if (scope.q) chips.push(chip('search', scope.q, 'q'));
    if (scope.flag) chips.push(chip('flag', scope.flag === 'picked' ? 'Favorited' : 'Rejected', 'flag'));
    if (scope.fileType) chips.push(chip('type', scope.fileType.toUpperCase(), 'fileType'));
    if (scope.camera) chips.push(chip('camera', scope.camera, 'camera'));
    if (scope.lens) chips.push(chip('lens', scope.lens, 'lens'));
    if (scope.tag) chips.push(chip('tag', scope.tag, 'tag'));
    if (scope.orientation) chips.push(chip('orientation', scope.orientation === 'landscape' ? 'Landscape' : 'Portrait', 'orientation'));
    if (scope.folder) chips.push(chip('folder', scope.label || scope.folder.split('/').filter(Boolean).pop() || scope.folder, 'folder'));
    if (scope.compared) chips.push(chip('ranking', COMPARED_LABELS[scope.compared] || scope.compared, 'compared'));
    if (scope.minStars) chips.push(chip('rating', `${scope.minStars}+ stars`, 'minStars'));
    if (scope.similarId) chips.push(chip('similar', scope.label || 'Similar', 'similarId'));
    if (chips.length > 1) chips.push(`<button class="chip ghost" data-clear-all="1">${icon('x')}<span>Clear all</span></button>`);
    let html = chips.join('');
    html += `<span class="m-scope-count num">${fmtInt(histogram.total)} photos</span>`;
    if (currentSortQuality && Number(currentSortQuality.total) > 0) {
        html += `<span class="m-scope-quality num">${fmtInt(currentSortQuality.percent)}% sorted</span>`;
    }
    bar.innerHTML = html;
    const personChip = bar.querySelector('.chip-x[data-clear="people"]')?.closest('.chip');
    if (personChip) {
        personChip.setAttribute('role', 'button');
        personChip.tabIndex = 0;
        const openPersonActions = (event) => {
            if (event.target.closest('.chip-x')) return;
            openPersonSheet({ id: scope.people, label: scope.peopleLabel });
        };
        personChip.addEventListener('click', openPersonActions);
        personChip.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openPersonActions(event);
            }
        });
    }
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
                scope.peopleLabel = '';
            }
            if (field === 'folder' && scope.label) scope.label = '';
            emit('scope', scope);
        });
    }
    bar.querySelector('[data-clear-all]')?.addEventListener('click', () => {
        clearScope();
    });
}

function openPhotoOptionsSheet() {
    const sortRows = SORT_OPTIONS.map((option) => {
        const active = option.value === viewPrefs.sort;
        const tasteDisabled = option.value === 'taste' && !tasteAvailable;
        const detail = tasteDisabled ? 'Refine a few duels to teach it' : option.detail;
        return `<button class="sheet-row m-sort-row${active ? ' active' : ''}" data-sort="${esc(option.value)}"${tasteDisabled ? ' disabled' : ''}>`
            + `<span class="g">${icon(option.glyph)}</span>`
            + `<span class="body">${esc(option.label)}<span class="sub">${esc(detail)}</span></span>`
            + `<span class="n">${active ? icon('check') : ''}</span></button>`;
    }).join('');
    const sheet = openSheet(
        '<h3>Photos</h3>'
        + '<div class="sheet-label">Sort</div>'
        + sortRows
        + '<div class="sheet-label">Options</div>'
        + `<button class="sheet-row m-toggle-row${viewPrefs.collapseStacks ? ' active' : ''}" id="m-stack-toggle">`
        + `<span class="g">${icon('layers')}</span>`
        + '<span class="body">Group stacks<span class="sub">Show only each stack representative</span></span>'
        + `<span class="m-switch" aria-hidden="true"><span></span></span></button>`
    );
    for (const row of sheet.querySelectorAll('[data-sort]')) {
        row.addEventListener('click', () => {
            const sort = row.dataset.sort || 'date_taken';
            if (sort === viewPrefs.sort) return;
            dismissSheetThen(() => setViewPrefs({ sort }));
        });
    }
    sheet.querySelector('#m-stack-toggle')?.addEventListener('click', () => {
        dismissSheetThen(() => setViewPrefs({ collapseStacks: !viewPrefs.collapseStacks }));
    });
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
    let dragPoint = null;
    let dragFrame = null;

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

    function edgeScrollSpeed(y) {
        const bottomActions = document.getElementById('m-sel-actions');
        const tabbar = document.getElementById('m-tabbar');
        const scopebar = document.getElementById('m-scopebar');
        const topLimit = (scopebar && scopebar.classList.contains('on'))
            ? scopebar.getBoundingClientRect().bottom + 16
            : 84;
        const tabbarH = tabbar ? tabbar.getBoundingClientRect().height : 0;
        const actionsH = bottomActions && bottomActions.classList.contains('on')
            ? bottomActions.getBoundingClientRect().height + 8
            : 0;
        const bottomLimit = window.innerHeight - tabbarH - actionsH - 16;
        const zone = 112;
        if (y < topLimit + zone) {
            const p = clamp((topLimit + zone - y) / zone, 0, 1);
            return -(4 + p * p * 30);
        }
        if (y > bottomLimit - zone) {
            const p = clamp((y - (bottomLimit - zone)) / zone, 0, 1);
            return 4 + p * p * 30;
        }
        return 0;
    }

    function dragLoop() {
        dragFrame = null;
        if (!dragActive || !dragPoint) return;
        const p = dragPoint;
        const el = document.elementFromPoint(p.x, p.y);
        const cell = el && el.closest ? el.closest('.mcell[data-id]') : null;
        if (cell) applyDragRange(Number(cell.dataset.mi));
        const speed = edgeScrollSpeed(p.y);
        if (speed) {
            pane.scrollTop += speed;
            dragFrame = requestAnimationFrame(dragLoop);
        }
    }

    function scheduleDragLoop() {
        if (dragFrame == null) dragFrame = requestAnimationFrame(dragLoop);
    }

    cancelLongPressGesture = () => {
        if (!lp) return;
        clearTimeout(lp.timer);
        lp = null;
        longPressPending = false;
    };

    timeline.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse') return;
        if (e.target.closest('.c-stack, .m-stack-tray')) return;
        const cell = e.target.closest('.mcell[data-id]');
        if (!cell) return;
        const mi = Number(cell.dataset.mi);
        longPressPending = true;
        lp = {
            x: e.clientX,
            y: e.clientY,
            fired: false,
            timer: setTimeout(() => {
                longPressPending = false;
                lp.fired = true;
                if (navigator.vibrate) navigator.vibrate(12);
                selState.mode = true;
                dragBase = new Set(selection);
                anchorMi = mi;
                dragActive = true;
                applyDragRange(mi);
                dragPoint = { x: lp.x, y: lp.y };
                scheduleDragLoop();
            }, 340),
        };
    });
    timeline.addEventListener('pointermove', (e) => {
        if (!lp) return;
        if (!lp.fired) {
            if (Math.hypot(e.clientX - lp.x, e.clientY - lp.y) > 10) {
                clearTimeout(lp.timer);
                lp = null;
                longPressPending = false;
            }
            return;
        }
        dragPoint = { x: e.clientX, y: e.clientY };
        scheduleDragLoop();
    });
    const endLP = () => {
        if (lp) {
            clearTimeout(lp.timer);
            if (lp.fired) suppressClickUntil = Date.now() + 420;
        }
        lp = null;
        longPressPending = false;
        dragActive = false;
        dragBase = null;
        dragPoint = null;
        if (dragFrame != null) cancelAnimationFrame(dragFrame);
        dragFrame = null;
    };
    timeline.addEventListener('pointerup', endLP);
    timeline.addEventListener('pointercancel', endLP);
    timeline.addEventListener('touchmove', (e) => {
        if (dragActive) e.preventDefault();
    }, { passive: false });
    timeline.addEventListener('contextmenu', (e) => e.preventDefault());

    timeline.addEventListener('click', (e) => {
        if (Date.now() < suppressClickUntil) return;
        const badge = e.target.closest('.c-stack[data-stack-id]');
        if (badge) {
            const owner = badge.closest('.mcell[data-id]');
            expandStack(badge.dataset.stackId, owner, badge);
            return;
        }
        const cell = e.target.closest('.mcell[data-id]');
        if (!cell) return;
        const id = Number(cell.dataset.id);
        if (cell.dataset.stackMember) {
            const members = expandedStack?.members || [];
            const memberIndex = members.findIndex((member) => Number(member.id) === id);
            if (memberIndex >= 0) openViewer(members, memberIndex);
            return;
        }
        if (selState.mode) {
            toggleSel(id);
        } else {
            const index = images.findIndex((img) => Number(img.id) === id);
            if (index >= 0) openViewer(images, index, { loadMore });
        }
    });
}

function installPullToRefresh() {
    const pullEl = document.createElement('div');
    pullEl.id = 'm-pull';
    pullEl.innerHTML = '<span></span>';
    pane.appendChild(pullEl);

    let pull = null;
    let refreshing = false;

    const resetPull = () => {
        pull = null;
        pullEl.classList.remove('on', 'ready', 'refreshing');
        pullEl.style.setProperty('--pull-p', '0');
    };

    timeline.addEventListener('touchstart', (e) => {
        if (refreshing || selection.size || isOffline() || e.touches.length !== 1 || pane.scrollTop > 0) return;
        if (longPressPending || e.target.closest?.('.mcell[data-id]')) return;
        const t = e.touches[0];
        pull = { y: t.clientY, dy: 0, active: false };
    }, { passive: true });

    timeline.addEventListener('touchmove', (e) => {
        if (selection.size || isOffline()) {
            resetPull();
            return;
        }
        if (!pull || e.touches.length !== 1) return;
        const dy = e.touches[0].clientY - pull.y;
        if (dy <= 0 || pane.scrollTop > 0) {
            resetPull();
            return;
        }
        pull.dy = dy;
        if (dy > 6) pull.active = true;
        if (!pull.active) return;
        cancelLongPressGesture();
        e.preventDefault();
        const visual = Math.min(96, dy * 0.72);
        const progress = clamp(dy / 70, 0, 1);
        pullEl.classList.add('on');
        pullEl.classList.toggle('ready', dy >= 70);
        pullEl.style.setProperty('--pull-p', progress.toFixed(3));
    }, { passive: false });

    const finish = async () => {
        if (!pull) return;
        const shouldRefresh = pull.active && pull.dy >= 70;
        pull = null;
        if (!shouldRefresh) {
            resetPull();
            return;
        }
        refreshing = true;
        pullEl.classList.add('on', 'refreshing');
        pullEl.classList.remove('ready');
        pullEl.style.setProperty('--pull-p', '1');
        try {
            await reload();
        } finally {
            refreshing = false;
            resetPull();
        }
    };

    timeline.addEventListener('touchend', finish, { passive: true });
    timeline.addEventListener('touchcancel', resetPull, { passive: true });
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
    document.getElementById('m-photo-options').addEventListener('click', openPhotoOptionsSheet);

    installSelectionGestures();
    installPullToRefresh();
    installPinchZoom();
    on('selection', syncSelectionCells);
    on('flags', syncFlagCells);
    on('scope', () => {
        if (selection.size) dismissLayer('selection', clearSelection);
        else clearSelection();
        reload();
    });
    on('view-prefs', reload);

    reload();
    loadTasteStatus();
}

export function scrollInfo() {
    return {
        pane,
        hasMonths: monthOffsets.length > 0,
        zoom: zoomIdx,
    };
}
