import { createCollection, removeFromCollection, thumbUrl } from './api.js';
import { emit, on, selection, selectionChanged, setImages, setRankingsMeta, viewState } from './state.js';
import { loadScopePage } from './scope_data.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { toggleSelection } from './selection.js';

const GAP_KEY = 'pa_d_event_gap';
const PAGE_SIZE = 100;
const SORTED_SIGNAL_MIN = 3;
let mounted = false;
let initialized = false;
let offset = 0;
let done = false;
let loading = false;
let generation = 0;
let gapHours = Number(localStorage.getItem(GAP_KEY) || 6);
let images = [];
let groups = [];
const imageIndexes = new Map();
let observer = null;
let imageObserver = null;
let menu = null;
let savedScrollTop = 0;
const expandedEvents = new Set();

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function captureMs(img) {
    const raw = img.date_taken || img.file_modified_at || img.created_at || '';
    const parsed = raw ? Date.parse(raw) : Number.NaN;
    return Number.isFinite(parsed) ? parsed : null;
}

function titleFor(group) {
    const first = new Date(group.start);
    const last = new Date(group.end);
    const sameDay = first.toDateString() === last.toDateString();
    const opts = { month: 'short', day: 'numeric', year: 'numeric' };
    if (sameDay) return first.toLocaleDateString(undefined, opts);
    return `${first.toLocaleDateString(undefined, opts)} - ${last.toLocaleDateString(undefined, opts)}`;
}

function aspect(img) {
    const ar = Number(img.aspect_ratio) || (Number(img.width) && Number(img.height) ? Number(img.width) / Number(img.height) : 1.5);
    return Math.max(.45, Math.min(3.8, ar));
}

function signals(img) {
    return (Number(img.comparisons) || 0) + (Number(img.propagated_updates) || 0);
}

function groupImages(sourceImages) {
    const dated = sourceImages
        .map((img, index) => ({ img, index, ms: captureMs(img) }))
        .filter((item) => item.ms != null)
        .sort((a, b) => b.ms - a.ms);
    const gapMs = gapHours * 3600 * 1000;
    const result = [];
    for (const item of dated) {
        const last = result[result.length - 1];
        if (!last || Math.abs(last.lastMs - item.ms) > gapMs) {
            result.push({
                id: `ev-${item.ms}`,
                start: item.ms,
                end: item.ms,
                lastMs: item.ms,
                images: [item.img],
            });
        } else {
            last.images.push(item.img);
            last.end = Math.min(last.end, item.ms);
            last.start = Math.max(last.start, item.ms);
            last.lastMs = item.ms;
        }
    }
    return result;
}

function buildGroups() {
    groups = groupImages(images);
}

function appendGroups(incoming) {
    const additions = groupImages(incoming);
    if (!additions.length) return groups.length;
    let changedFrom = groups.length;
    const last = groups[groups.length - 1];
    const first = additions[0];
    if (last && Math.abs(last.lastMs - first.start) <= gapHours * 3600 * 1000) {
        changedFrom -= 1;
        last.images.push(...first.images);
        last.start = Math.max(last.start, first.start);
        last.end = Math.min(last.end, first.end);
        last.lastMs = first.lastMs;
        additions.shift();
    }
    groups.push(...additions);
    return changedFrom;
}

function cellHtml(img, index) {
    const selected = selection.has(Number(img.id));
    return `<figure class="cell ${selected ? 'sel' : ''}" data-id="${img.id}" data-idx="${index}" tabindex="-1" aria-selected="${selected ? 'true' : 'false'}" style="--ar:${aspect(img)}">`
        + `<img data-src="${esc(img.thumb_url || thumbUrl('sm', img.id))}" loading="lazy" decoding="async" fetchpriority="low" alt="${esc(img.filename || '')}">`
        + `<button class="c-check" aria-label="Select photo" tabindex="-1">${icon('check')}</button>`
        + `<span class="c-idx">${index + 1}</span><span class="c-elo"><span class="elo-chip">${Math.round(Number(img.elo) || 0)}</span></span></figure>`;
}

function patchCells(imageIds = null) {
    const ids = Array.isArray(imageIds) ? imageIds.map(Number).filter((id) => id > 0) : [];
    const cells = ids.length
        ? ids.flatMap((id) => [...document.querySelectorAll(`#events-flow .cell[data-id="${id}"]`)])
        : [...document.querySelectorAll('#events-flow .cell[data-id]')];
    for (const cell of cells) {
        const selected = selection.has(Number(cell.dataset.id));
        cell.classList.toggle('sel', selected);
        cell.setAttribute('aria-selected', String(selected));
    }
}

function coverageDots(group) {
    const count = group.images.length;
    const covered = group.images.filter((img) => signals(img) >= SORTED_SIGNAL_MIN).length;
    const filled = count ? Math.round((covered / count) * 5) : 0;
    return Array.from({ length: 5 }, (_, i) => `<i class="${i < filled ? 'q' : ''}"></i>`).join('');
}

function renderSkeleton() {
    document.getElementById('events-flow').innerHTML = '<div class="event-block">'
        + '<div class="event-head"><div class="skel" style="width:220px;height:18px"></div></div>'
        + '<div class="event-body"><div class="event-hero skel"></div><div class="event-tiles">'
        + Array.from({ length: 12 }, (_, i) => `<div class="cell skel-cell" style="--ar:${[1.4, .8, 1.8][i % 3]}"></div>`).join('')
        + '</div></div></div>';
}

function groupHtml(group, groupIndex) {
    const title = titleFor(group);
    const hero = [...group.images].sort((a, b) => (Number(b.elo) || 0) - (Number(a.elo) || 0))[0];
    const expanded = expandedEvents.has(group.id) || group.images.length <= 18;
    const visibleTiles = (expanded ? group.images : group.images.slice(0, 18)).filter((img) => Number(img.id) !== Number(hero.id));
    const hidden = Math.max(0, group.images.length - visibleTiles.length - 1);
    return `<article class="event-block" data-group="${groupIndex}">`
        + `<header class="event-head"><h2>${esc(title)}</h2><span class="ev-count">${fmt(group.images.length)} photos</span>`
        + `<span class="ev-dots" data-tip="Ranking coverage">${coverageDots(group)}</span><span class="ev-spacer"></span>`
        + `<button class="ev-menu-btn" data-menu="${groupIndex}" data-tip="Event actions" aria-label="Event actions">${icon('ellipsis')}</button></header>`
        + '<div class="event-body">'
        + `<figure class="event-hero" data-id="${hero.id}"><img data-src="${esc(thumbUrl('md', hero.id))}" fetchpriority="low" alt="${esc(hero.filename || '')}"><figcaption class="hero-cap"><span>${esc(hero.filename || '')}</span><span>${Math.round(Number(hero.elo) || 0)}</span></figcaption></figure>`
        + `<div class="event-tiles">${visibleTiles.map((img) => cellHtml(img, imageIndexes.get(Number(img.id)) ?? 0)).join('')}`
        + `${hidden > 0 ? `<button class="ev-more" data-expand="${groupIndex}">+${fmt(hidden)} more</button>` : ''}</div></div></article>`;
}

function render() {
    if (!mounted) return;
    buildGroups();
    const flow = document.getElementById('events-flow');
    if (!groups.length && !loading) {
        flow.innerHTML = '<div class="grid-empty"><h3>No dated photos in this view.</h3><p>Add a source if your library is empty, or try another view.</p></div>'
            + '<div id="events-sentinel"></div><div class="grid-end" id="events-end" hidden>End of view</div>';
        document.getElementById('events-end').hidden = !done || images.length === 0;
        return;
    }
    flow.innerHTML = groups.map(groupHtml).join('')
        + '<div id="events-sentinel"></div><div class="grid-end" id="events-end" hidden>End of view</div>';
    document.getElementById('events-end').hidden = !done || images.length === 0;
    observeImages(flow, { reset: true });
}

function observeImages(root, { reset = false } = {}) {
    if (reset && imageObserver) imageObserver.disconnect();
    if (!imageObserver || reset) {
        imageObserver = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                const img = entry.target;
                if (entry.isIntersecting && !img.src) img.src = img.dataset.src;
            }
        }, { root: document.getElementById('canvas'), rootMargin: '700px 0px' });
    }
    for (const img of root.querySelectorAll('img[data-src]')) {
        img.addEventListener('load', () => img.classList.add('ld'), { once: true });
        imageObserver.observe(img);
    }
}

function renderAppendedGroups(changedFrom) {
    if (!mounted) return;
    const flow = document.getElementById('events-flow');
    const sentinel = document.getElementById('events-sentinel');
    if (!sentinel) {
        render();
        return;
    }
    for (const article of flow.querySelectorAll('.event-block[data-group]')) {
        if (Number(article.dataset.group) < changedFrom) continue;
        for (const img of article.querySelectorAll('img[data-src]')) imageObserver?.unobserve(img);
        article.remove();
    }
    const fragment = document.createRange().createContextualFragment(
        groups.slice(changedFrom).map((group, index) => groupHtml(group, changedFrom + index)).join(''),
    );
    const added = [...fragment.querySelectorAll('.event-block')];
    sentinel.before(fragment);
    for (const article of added) observeImages(article);
    document.getElementById('events-end').hidden = !done || images.length === 0;
}

async function loadPage() {
    if (!mounted || loading || done) return;
    loading = true;
    const seq = generation;
    try {
        const data = await loadScopePage({ limit: PAGE_SIZE, offset, sort: 'date_taken' });
        if (seq !== generation) return;
        if (!data) {
            document.getElementById('events-flow').innerHTML = '<div class="load-error"><h4>Couldn\'t load events</h4><p>The archive did not respond.</p><button class="btn" id="events-retry">Try again</button></div>';
            document.getElementById('events-retry')?.addEventListener('click', loadPage);
            return;
        }
        const incoming = data.images || [];
        const firstPage = images.length === 0;
        incoming.forEach((img, index) => imageIndexes.set(Number(img.id), images.length + index));
        offset += incoming.length;
        done = incoming.length < PAGE_SIZE;
        images = images.concat(incoming);
        setImages(images);
        if (offset === incoming.length) setRankingsMeta({ visibleImages: data.visible_images, sortQuality: data.sort_quality });
        if (firstPage) {
            render();
            setupSentinel();
        } else {
            renderAppendedGroups(appendGroups(incoming));
            setupSentinel();
        }
    } catch {
        if (seq !== generation) return;
        document.getElementById('events-flow').innerHTML = '<div class="load-error"><h4>Couldn\'t load events</h4><p>The archive did not respond.</p><button class="btn" id="events-retry">Try again</button></div>';
        document.getElementById('events-retry')?.addEventListener('click', loadPage);
    } finally {
        if (seq === generation) loading = false;
    }
}

function setupSentinel() {
    if (observer) observer.disconnect();
    const sentinel = document.getElementById('events-sentinel');
    if (!sentinel) return;
    observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadPage();
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px' });
    observer.observe(sentinel);
}

function closeMenu() {
    if (menu) {
        releaseFocus(menu);
        menu.remove();
    }
    menu = null;
}

function openMenu(button, groupIndex) {
    closeMenu();
    const group = groups[groupIndex];
    if (!group) return;
    const ids = group.images.map((img) => Number(img.id)).filter((id) => id > 0);
    menu = document.createElement('div');
    menu.className = 'pop-menu on event-menu grid-pop-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<button data-act="collection" role="menuitem">${icon('folder-plus')} Make collection from event</button>`
        + `<button data-act="refine" role="menuitem">${icon('zap')} Open in Refine</button>`
        + `<button data-act="select" role="menuitem">${icon('check')} Select all in event</button>`;
    document.body.appendChild(menu);
    const rect = button.getBoundingClientRect();
    menu.style.left = `${Math.min(window.innerWidth - 230, rect.right - 210)}px`;
    menu.style.top = `${rect.bottom + 6}px`;
    trapFocus(menu, menu.querySelector('button'));
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeMenu();
            button.focus({ preventScroll: true });
            return;
        }
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        event.preventDefault();
        const buttons = [...menu.querySelectorAll('button')];
        const index = Math.max(0, buttons.indexOf(document.activeElement));
        buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus();
    });
    menu.addEventListener('click', async (event) => {
        const action = event.target.closest('button')?.dataset.act;
        if (!action) return;
        closeMenu();
        if (action === 'collection') {
            const result = await createCollection(titleFor(group), ids);
            if (result && result.ok) {
                const coll = result.collection || {};
                showToast('Collection created from event', {
                    undo: coll.id ? async () => {
                        await removeFromCollection(coll.id, ids);
                        showToast('Event photos removed from collection');
                    } : null,
                });
            } else showToast("Couldn't create collection");
        } else if (action === 'refine') {
            showToast('Opening Refine for this event.');
            emit('refine:open');
        } else if (action === 'select') {
            ids.forEach((id) => selection.add(id));
            selectionChanged(ids);
            patchCells(ids);
        }
    });
}

function resetData() {
    generation += 1;
    offset = 0;
    done = false;
    loading = false;
    images = [];
    groups = [];
    imageIndexes.clear();
    expandedEvents.clear();
    setImages([]);
    setRankingsMeta({ visibleImages: 0, sortQuality: null });
}

function reload({ skeleton = true } = {}) {
    if (!mounted) return;
    resetData();
    if (skeleton) renderSkeleton();
    loadPage();
}

async function revalidate() {
    const seq = generation;
    try {
        const data = await loadScopePage({ limit: PAGE_SIZE, offset: 0, sort: 'date_taken' });
        if (!mounted || seq !== generation || !data) return;
        const incoming = data.images || [];
        const changed = incoming.length !== Math.min(images.length, PAGE_SIZE)
            || incoming.some((image, i) => Number(image.id) !== Number(images[i]?.id));
        if (!changed) return;
        resetData();
        images = incoming;
        incoming.forEach((image, i) => imageIndexes.set(Number(image.id), i));
        offset = incoming.length;
        done = incoming.length < PAGE_SIZE;
        setImages(images);
        setRankingsMeta({ visibleImages: data.visible_images, sortQuality: data.sort_quality });
        render();
        setupSentinel();
    } catch {
        // Keep the last successful event view visible while the refresh is unavailable.
    }
}

export function initEvents() {
    if (initialized) return;
    initialized = true;
    document.getElementById('events-flow').addEventListener('click', (event) => {
        const menuButton = event.target.closest('[data-menu]');
        if (menuButton) {
            openMenu(menuButton, Number(menuButton.dataset.menu));
            return;
        }
        const expand = event.target.closest('[data-expand]');
        if (expand) {
            const group = groups[Number(expand.dataset.expand)];
            if (group) expandedEvents.add(group.id);
            render();
            return;
        }
        const check = event.target.closest('.c-check');
        if (check) {
            const cell = check.closest('.cell[data-id]');
            if (cell) toggleSelection(cell.dataset.id, cell.dataset.idx, { range: event.shiftKey });
            return;
        }
        const cell = event.target.closest('.cell[data-id], .event-hero[data-id]');
        if (cell) emit('loupe:open', { id: Number(cell.dataset.id), index: images.findIndex((img) => Number(img.id) === Number(cell.dataset.id)) });
    });
    document.addEventListener('pointerdown', (event) => {
        if (menu && !menu.contains(event.target) && !event.target.closest('.ev-menu-btn')) closeMenu();
    });
    on('scope', () => {
        resetData();
        if (mounted) reload();
    });
    on('selection', ({ imageIds } = {}) => patchCells(imageIds));
    on('flags', ({ imageIds } = {}) => patchCells(imageIds));
}

export function mountEvents() {
    mounted = true;
    document.getElementById('view-events').classList.add('active');
    if (images.length) {
        observeImages(document.getElementById('events-flow'));
        setupSentinel();
        requestAnimationFrame(() => document.getElementById('canvas').scrollTo({ top: savedScrollTop, behavior: 'auto' }));
        revalidate();
    } else reload();
}

export function unmountEvents() {
    savedScrollTop = document.getElementById('canvas').scrollTop;
    mounted = false;
    generation += 1;
    closeMenu();
    if (observer) observer.disconnect();
    if (imageObserver) imageObserver.disconnect();
    observer = null;
    imageObserver = null;
    document.getElementById('view-events').classList.remove('active');
}

export function setEventGap(hours) {
    gapHours = [3, 6, 24].includes(Number(hours)) ? Number(hours) : 6;
    localStorage.setItem(GAP_KEY, String(gapHours));
    render();
}

export function eventGap() {
    return gapHours;
}
