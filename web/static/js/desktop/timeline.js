// Date river: a calm, scrollable overview of the current library scope.
// Month totals come from the lightweight date APIs; a month fetch is deferred
// until its band approaches the viewport, so a long archive stays immediate.

import { getDateGroups, getDateHistogram, getRankings, previewThumbUrl } from './api.js';
import { on, scopeParams, setActiveLens, setScope } from './state.js';
import { escapeHtml as esc, formatCount as fmt, MONTH_NAMES } from '../lib.js';

const MONTH_SAMPLE_LIMIT = 5000;

let mounted = false;
let initialized = false;
let generation = 0;
let months = [];
let monthSamples = new Map();
let monthObserver = null;
let thumbnailPollTimer = 0;
let scrubDragging = false;
let savedScrollTop = 0;

function monthLabel(key) {
    if (key === 'undated') return 'Undated';
    const [year, month] = String(key).split('-');
    return `${MONTH_NAMES.long[Number(month) - 1] || month} ${year}`;
}

function dayKey(image) {
    const raw = String(image?.date_taken || '');
    return /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : 'undated';
}

function dayLabel(key) {
    if (key === 'undated') return 'No date';
    const [year, month, day] = key.split('-').map(Number);
    return new Date(year, month - 1, day).toLocaleDateString(undefined, {
        weekday: 'short', month: 'short', day: 'numeric',
    });
}

function monthId(key) {
    return `timeline-month-${String(key).replace(/[^a-z0-9]/gi, '-')}`;
}

function activeMonthAt(clientY) {
    const rail = document.getElementById('timeline-scrubber');
    const rect = rail?.getBoundingClientRect();
    if (!rect || !months.length) return null;
    const ratio = Math.max(0, Math.min(1, (clientY - rect.top) / Math.max(1, rect.height)));
    return months[Math.min(months.length - 1, Math.round(ratio * (months.length - 1)))];
}

function jumpToMonth(month, behavior = 'smooth') {
    const band = document.getElementById(monthId(month?.key));
    band?.scrollIntoView({ behavior, block: 'start' });
}

function scopeForMonth(month) {
    const params = scopeParams({ limit: MONTH_SAMPLE_LIMIT, offset: 0, sort: 'date_taken' });
    params.set('date_taken', month.key === 'undated' ? 'undated' : month.key);
    return params;
}

function sampleThumbHtml(image) {
    const previewSrc = previewThumbUrl(image);
    return previewSrc
        ? `<img src="${esc(previewSrc)}" alt="" loading="lazy" decoding="async" fetchpriority="low">`
        : '<span class="preview-thumb-pending" aria-hidden="true"></span>';
}

function renderMonth(month) {
    const host = document.getElementById(monthId(month.key))?.querySelector('.timeline-days');
    if (!host) return;
    const samples = monthSamples.get(month.key);
    if (!samples) {
        host.innerHTML = '<div class="timeline-month-loading"><i class="skel"></i><i class="skel"></i><i class="skel"></i></div>';
        return;
    }
    const days = new Map();
    for (const image of samples) {
        const key = dayKey(image);
        if (!days.has(key)) days.set(key, []);
        days.get(key).push(image);
    }
    const dayRows = [...days.entries()];
    host.innerHTML = dayRows.length ? dayRows.map(([key, images]) => (
        `<button class="timeline-day" type="button" data-day="${key}" aria-label="Open ${esc(dayLabel(key))}, ${fmt(images.length)} photos">`
        + `<span class="timeline-day-label">${esc(dayLabel(key))}</span>`
        + `<span class="timeline-day-count">${fmt(images.length)}</span>`
        + `<span class="timeline-day-thumbs">${images.slice(0, 5).map(sampleThumbHtml).join('')}</span></button>`
    )).join('') : `<p class="timeline-empty-month">${month.count ? 'Thumbnails are still preparing for this month.' : 'No photos match this month.'}</p>`;
    for (const row of host.querySelectorAll('[data-day]')) {
        row.addEventListener('click', () => {
            setScope({ date_taken: row.dataset.day }, { merge: true });
            setActiveLens('grid');
        });
    }
    scheduleThumbnailPoll();
}

async function loadMonth(month, seq) {
    if (monthSamples.has(month.key)) return;
    try {
        const data = await getRankings(scopeForMonth(month));
        if (!mounted || seq !== generation) return;
        monthSamples.set(month.key, data?.images || []);
        renderMonth(month);
    } catch {
        if (!mounted || seq !== generation) return;
        monthSamples.set(month.key, []);
        renderMonth(month);
    }
}

function samplesMatch(current, incoming) {
    return current.length === incoming.length
        && current.every((image, index) => Number(image.id) === Number(incoming[index]?.id)
            && image.date_taken === incoming[index]?.date_taken
            && image.preview_ready === incoming[index]?.preview_ready);
}

function visibleSampledMonths() {
    const canvasRect = document.getElementById('canvas')?.getBoundingClientRect();
    if (!canvasRect) return [];
    return months.filter((month) => {
        if (!monthSamples.has(month.key)) return false;
        const rect = document.getElementById(monthId(month.key))?.getBoundingClientRect();
        return rect && rect.bottom >= canvasRect.top && rect.top <= canvasRect.bottom;
    });
}

function stopThumbnailPoll() {
    window.clearTimeout(thumbnailPollTimer);
    thumbnailPollTimer = 0;
}

function visiblePendingPreviews() {
    return visibleSampledMonths().some((month) => (
        monthSamples.get(month.key)?.some((image) => image.preview_ready === false)
    ));
}

function scheduleThumbnailPoll() {
    if (!mounted || thumbnailPollTimer || !visiblePendingPreviews()) return;
    thumbnailPollTimer = window.setTimeout(async () => {
        thumbnailPollTimer = 0;
        await refreshVisibleMonths(generation);
        scheduleThumbnailPoll();
    }, 3000);
}

async function refreshVisibleMonths(seq) {
    await Promise.all(visibleSampledMonths().map(async (month) => {
        try {
            const data = await getRankings(scopeForMonth(month));
            if (!mounted || seq !== generation) return;
            const incoming = data?.images || [];
            if (samplesMatch(monthSamples.get(month.key) || [], incoming)) return;
            monthSamples.set(month.key, incoming);
            renderMonth(month);
        } catch {
            // Keep the last visible month samples when the background refresh is unavailable.
        }
    }));
}

function observeMonths(seq) {
    if (monthObserver) monthObserver.disconnect();
    monthObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            const month = months.find((item) => item.key === entry.target.dataset.month);
            if (month) loadMonth(month, seq);
            monthObserver.unobserve(entry.target);
        }
    }, { root: document.getElementById('canvas'), rootMargin: '900px 0px' });
    for (const band of document.querySelectorAll('.timeline-month[data-month]')) monthObserver.observe(band);
}

function renderRiver() {
    const flow = document.getElementById('timeline-flow');
    const max = Math.max(1, ...months.map((month) => month.count));
    flow.innerHTML = months.length ? months.map((month) => (
        `<section class="timeline-month" id="${monthId(month.key)}" data-month="${month.key}">`
        + '<header class="timeline-month-head">'
        + `<div><h2>${esc(monthLabel(month.key))}</h2><span>${fmt(month.count)} photos</span></div>`
        + `<i class="timeline-density" title="${fmt(month.count)} photos" style="--density:${Math.max(.08, month.count / max)}"></i>`
        + '</header><div class="timeline-days"></div></section>'
    )).join('') : '<div class="grid-empty"><h3>No dates in this view.</h3><p>Try a broader filter or browse the grid.</p></div>';
    for (const month of months) renderMonth(month);
    renderScrubber();
}

function renderScrubber() {
    let rail = document.getElementById('timeline-scrubber');
    if (!rail) {
        rail = document.createElement('aside');
        rail.id = 'timeline-scrubber';
        rail.setAttribute('aria-label', 'Jump to month');
        document.getElementById('center').appendChild(rail);
    }
    rail.hidden = !months.length;
    rail.innerHTML = '<div class="timeline-scrub-track"></div><output></output>';
    const track = rail.querySelector('.timeline-scrub-track');
    track.innerHTML = months.map((month, index) => (
        `<button type="button" data-month="${month.key}" style="top:${months.length === 1 ? 50 : (index / (months.length - 1)) * 100}%" aria-label="${esc(monthLabel(month.key))}"></button>`
    )).join('');
    for (const button of track.querySelectorAll('[data-month]')) {
        button.addEventListener('click', () => jumpToMonth(months.find((month) => month.key === button.dataset.month)));
    }
}

function scrubTo(clientY) {
    const month = activeMonthAt(clientY);
    if (!month) return;
    const rail = document.getElementById('timeline-scrubber');
    const output = rail?.querySelector('output');
    if (output) output.value = monthLabel(month.key);
    jumpToMonth(month, 'auto');
}

function monthSignature(nextMonths) {
    return nextMonths.map((month) => `${month.key}:${month.count}`).join('|');
}

async function load({ keepVisible = false } = {}) {
    const seq = ++generation;
    if (!keepVisible) {
        monthSamples = new Map();
        document.getElementById('timeline-flow').innerHTML = '<div class="timeline-loading"><i class="skel"></i><i class="skel"></i><i class="skel"></i></div>';
    }
    try {
        const params = scopeParams();
        params.delete('sort');
        const [groups, histogram] = await Promise.all([getDateGroups(params), getDateHistogram(params)]);
        if (!mounted || seq !== generation) return;
        const counts = new Map((histogram?.months || []).map((item) => [item.month, Number(item.count) || 0]));
        const visibleGroups = new Map((groups?.groups || []).map((group) => [group.date || 'undated', Number(group.count) || 0]));
        const nextMonths = [...counts.entries()].map(([key, count]) => ({ key, count }));
        if (Number(histogram?.undated) || visibleGroups.has('undated')) {
            nextMonths.push({ key: 'undated', count: Number(histogram?.undated) || visibleGroups.get('undated') || 0 });
        }
        if (keepVisible && monthSignature(nextMonths) === monthSignature(months)) {
            observeMonths(seq);
            refreshVisibleMonths(seq);
            return;
        }
        months = nextMonths;
        if (keepVisible) monthSamples = new Map();
        renderRiver();
        observeMonths(seq);
    } catch {
        if (!mounted || seq !== generation) return;
        months = [];
        document.getElementById('timeline-flow').innerHTML = '<div class="load-error"><h4>Couldn\'t load Timeline</h4><p>The archive did not respond.</p><button class="btn" type="button">Try again</button></div>';
        document.querySelector('#timeline-flow .btn')?.addEventListener('click', load);
    }
}

export function initTimeline() {
    if (initialized) return;
    initialized = true;
    on('scope', () => {
        stopThumbnailPoll();
        generation += 1;
        months = [];
        monthSamples = new Map();
        if (mounted) load();
    });
    const rail = () => document.getElementById('timeline-scrubber');
    document.addEventListener('pointerdown', (event) => {
        if (!event.target.closest('#timeline-scrubber')) return;
        scrubDragging = true;
        event.target.setPointerCapture?.(event.pointerId);
        scrubTo(event.clientY);
    });
    document.addEventListener('pointermove', (event) => { if (scrubDragging) scrubTo(event.clientY); });
    document.addEventListener('pointerup', () => { scrubDragging = false; rail()?.querySelector('output')?.replaceChildren(); });
}

export function mountTimeline() {
    mounted = true;
    document.getElementById('view-timeline').classList.add('active');
    if (months.length) {
        observeMonths(generation);
        renderScrubber();
        scheduleThumbnailPoll();
        requestAnimationFrame(() => document.getElementById('canvas').scrollTo({ top: savedScrollTop, behavior: 'auto' }));
        load({ keepVisible: true });
    } else load();
}

export function unmountTimeline() {
    savedScrollTop = document.getElementById('canvas').scrollTop;
    mounted = false;
    generation += 1;
    stopThumbnailPoll();
    if (monthObserver) monthObserver.disconnect();
    monthObserver = null;
    document.getElementById('view-timeline').classList.remove('active');
    document.getElementById('timeline-scrubber')?.setAttribute('hidden', '');
}
