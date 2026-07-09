import { getDateHistogram } from './api.js';
import { jumpToOffset } from './grid.js';
import { collectionScopeActive } from './scope_data.js';
import { on, scope, scopeParams, viewState } from './state.js';

let months = [];
let generation = 0;
let dragging = false;
let pendingY = null;
let lastKey = '';
let jumpTimer = null;
let releaseJump = null;
let framePending = false;
let scrollFramePending = false;
let currentKey = '';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const MONTH_LABEL_PX = 14;
const QUARTER_LABEL_PX = 10;
const EDGE_INSET_PX = 7;

function label(key) {
    if (key === 'undated') return 'Undated';
    const [year, month] = String(key).split('-').map(Number);
    if (!year || !month) return String(key || '');
    return new Date(year, month - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
}

function active() {
    return viewState.activeLens === 'grid' && scope.sort === 'date_taken' && !collectionScopeActive();
}

function setGutter(on) {
    document.getElementById('grid-flow')?.classList.toggle('has-scrubber', Boolean(on));
    document.getElementById('canvas')?.classList.toggle('has-scrubber', Boolean(on));
}

function monthForY(clientY) {
    const scrub = document.getElementById('date-scrubber');
    const rect = scrub.querySelector('.ds-track')?.getBoundingClientRect() || scrub.getBoundingClientRect();
    const pos = clamp((clientY - rect.top) / Math.max(1, rect.height), 0, 1) * 100;
    return months.reduce((best, month) => {
        const delta = Math.abs(positionForMonth(month) - pos);
        return !best || delta < best.delta ? { month, delta } : best;
    }, null)?.month || null;
}

function yearOf(month) {
    return String(month?.key || '').slice(0, 4);
}

function monthOrdinal(key) {
    const match = String(key || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return null;
    return (Number(match[1]) * 12) + Number(match[2]) - 1;
}

function datedOrdinals() {
    return months.map((month) => monthOrdinal(month.key)).filter((value) => value != null);
}

function positionForMonth(month) {
    if (!month) return 0;
    if (month.key === 'undated') return 100;
    const ordinal = monthOrdinal(month.key);
    const ordinals = datedOrdinals();
    const min = Math.min(...ordinals);
    const max = Math.max(...ordinals);
    if (ordinal == null || !Number.isFinite(min) || !Number.isFinite(max) || min === max) return 0;
    const descending = ordinals.length < 2 || ordinals[0] >= ordinals[ordinals.length - 1];
    const frac = descending ? (max - ordinal) / Math.max(1, max - min) : (ordinal - min) / Math.max(1, max - min);
    return clamp(frac, 0, 1) * (months.some((item) => item.key === 'undated') ? 96 : 100);
}

function scrubberHeight(scrub) {
    return scrub.querySelector('.ds-track')?.getBoundingClientRect().height || scrub.getBoundingClientRect().height || 0;
}

function markerLabel(month, index) {
    const year = yearOf(month);
    const previousYear = index > 0 ? yearOf(months[index - 1]) : '';
    if (month.key === 'undated') return 'Undated';
    if (index === 0 || year !== previousYear) return year;
    return label(month.key).split(' ')[0];
}

function isQuarterMonth(key) {
    const month = Number(String(key || '').slice(5, 7));
    return month === 1 || month === 4 || month === 7 || month === 10;
}

function markerItems(scrub) {
    const height = scrubberHeight(scrub);
    const labelMode = height / Math.max(1, months.length) >= MONTH_LABEL_PX
        ? 'month'
        : height / Math.max(1, months.length) >= QUARTER_LABEL_PX ? 'quarter' : 'year';
    return months.map((month, index) => {
        const year = yearOf(month);
        const previousYear = index > 0 ? yearOf(months[index - 1]) : '';
        const monthPart = String(month.key || '').slice(5, 7);
        const isYear = index === 0 || month.key === 'undated' || year !== previousYear;
        const isQuarter = !isYear && isQuarterMonth(month.key);
        const showMonth = !isYear && (labelMode === 'month' || (labelMode === 'quarter' && isQuarter));
        return {
            key: month.key,
            label: isYear || showMonth ? markerLabel(month, index) : '',
            pos: positionForMonth(month),
            type: isYear ? 'year' : (monthPart === '01' || isQuarter ? 'quarter' : 'month'),
            current: month.key === currentKey,
        };
    });
}

function readableLabels(markers, scrub) {
    const height = scrubberHeight(scrub);
    const minGap = height ? (MONTH_LABEL_PX / height) * 100 : 0;
    const clampPos = (pos) => (height ? clamp(pos, (EDGE_INSET_PX / height) * 100, 100 - (EDGE_INSET_PX / height) * 100) : pos);
    markers = markers.map((item) => ({ ...item, pos: clampPos(item.pos) }));
    const fits = (list, item) => list.every((other) => Math.abs(other.pos - item.pos) >= minGap);
    const shown = [];
    for (const item of markers) {
        if (item.label && item.type === 'year' && (!shown.length || fits(shown, item))) shown.push(item);
    }
    for (const item of markers) {
        if (item.label && item.type !== 'year' && fits(shown, item)) shown.push(item);
    }
    const current = markers.find((item) => item.current && item.label);
    if (current && !shown.includes(current)) {
        for (let i = shown.length - 1; i >= 0; i -= 1) {
            if (shown[i].type !== 'year' && Math.abs(shown[i].pos - current.pos) < minGap) shown.splice(i, 1);
        }
        shown.push(current);
    }
    shown.sort((a, b) => a.pos - b.pos);
    return shown;
}

function render() {
    let scrub = document.getElementById('date-scrubber');
    if (!scrub) {
        scrub = document.createElement('div');
        scrub.id = 'date-scrubber';
        scrub.innerHTML = '<div class="ds-rail"><div class="ds-track"><div class="ds-current"></div></div><div class="ds-labels"></div></div><div id="date-scrub-bubble"></div>';
        document.getElementById('center').appendChild(scrub);
    }
    const on = active() && months.length > 0;
    scrub.classList.toggle('on', on);
    setGutter(on);
    if (!on) return;
    const track = scrub.querySelector('.ds-track');
    const labels = scrub.querySelector('.ds-labels');
    const markers = markerItems(scrub);
    track.innerHTML = '<div class="ds-current"></div>' + markers.map((item) => (
        `<button class="ds-tick ${item.type}${item.current ? ' current' : ''}" data-month="${item.key}" style="top:${item.pos}%"></button>`
    )).join('');
    labels.innerHTML = readableLabels(markers, scrub).map((item) => (
        `<button class="ds-label ${item.type}${item.current ? ' current' : ''}" data-month="${item.key}" style="top:${item.pos}%">${item.label}</button>`
    )).join('');
    if (!currentKey && months.length) setCurrentMonth(months[0]);
}

async function load() {
    const seq = ++generation;
    months = [];
    currentKey = '';
    lastKey = '';
    render();
    if (!active()) return;
    const params = scopeParams();
    params.delete('sort');
    const data = await getDateHistogram(params);
    if (seq !== generation || !data) return;
    let offset = 0;
    months = (data.months || []).map((month) => {
        const item = { key: month.month, count: Number(month.count) || 0, offset };
        offset += item.count;
        return item;
    });
    if (Number(data.undated) > 0) months.push({ key: 'undated', count: Number(data.undated), offset });
    render();
}

function setCurrentMonth(month) {
    if (!month) return;
    currentKey = month.key;
    const scrub = document.getElementById('date-scrubber');
    document.querySelectorAll('#date-scrubber .current').forEach((node) => node.classList.remove('current'));
    document.querySelectorAll(`#date-scrubber [data-month="${month.key}"]`).forEach((node) => node.classList.add('current'));
    const marker = scrub?.querySelector('.ds-current');
    if (marker) marker.style.transform = `translateY(${(scrubberHeight(scrub) * positionForMonth(month)) / 100}px)`;
}

function updateViewportMonth() {
    if (!active() || !months.length) return;
    const canvas = document.getElementById('canvas');
    const maxScroll = Math.max(1, (canvas?.scrollHeight || 0) - (canvas?.clientHeight || 0));
    const pos = clamp((canvas?.scrollTop || 0) / maxScroll, 0, 1) * 100;
    const month = months.reduce((best, item) => {
        const delta = Math.abs(positionForMonth(item) - pos);
        return !best || delta < best.delta ? { month: item, delta } : best;
    }, null)?.month;
    if (month && month.key !== currentKey) setCurrentMonth(month);
}

function scheduleViewportMonthUpdate() {
    if (scrollFramePending) return;
    scrollFramePending = true;
    requestAnimationFrame(() => {
        scrollFramePending = false;
        updateViewportMonth();
    });
}

function preview(month, { commitOnRelease = false } = {}) {
    if (!month) return;
    if (commitOnRelease) releaseJump = month;
    if (month.key !== lastKey) {
        lastKey = month.key;
        clearTimeout(jumpTimer);
        setCurrentMonth(month);
    }
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.textContent = label(month.key);
    bubble.classList.add('on');
}

function scrubTo(clientY) {
    const month = monthForY(clientY);
    if (!month) return;
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.style.transform = `translateY(calc(${clientY}px - 50%))`;
    preview(month, { commitOnRelease: true });
}

function hoverTo(clientY) {
    const month = monthForY(clientY);
    if (!month) return;
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.style.transform = `translateY(calc(${clientY}px - 50%))`;
    preview(month);
}

export function initDateScrubber() {
    document.getElementById('center').addEventListener('pointerdown', (event) => {
        const scrub = event.target.closest('#date-scrubber');
        if (!scrub || !active() || !months.length) return;
        dragging = true;
        lastKey = '';
        scrub.classList.add('dragging');
        scrub.setPointerCapture(event.pointerId);
        scrubTo(event.clientY);
        event.preventDefault();
    });
    document.getElementById('center').addEventListener('pointermove', (event) => {
        if (!dragging) {
            if (event.target.closest('#date-scrubber') && active() && months.length) hoverTo(event.clientY);
            return;
        }
        pendingY = event.clientY;
        if (framePending) return;
        framePending = true;
        requestAnimationFrame(() => {
            framePending = false;
            const y = pendingY;
            pendingY = null;
            if (dragging && y != null) scrubTo(y);
        });
    });
    const stop = () => {
        if (releaseJump) {
            clearTimeout(jumpTimer);
            jumpToOffset(releaseJump.offset);
            releaseJump = null;
        }
        dragging = false;
        framePending = false;
        document.getElementById('date-scrubber')?.classList.remove('dragging');
        document.getElementById('date-scrub-bubble')?.classList.remove('on');
        updateViewportMonth();
    };
    document.getElementById('center').addEventListener('pointerup', stop);
    document.getElementById('center').addEventListener('pointercancel', stop);
    document.getElementById('center').addEventListener('pointerleave', () => {
        if (!dragging) {
            document.getElementById('date-scrub-bubble')?.classList.remove('on');
            updateViewportMonth();
        }
    });
    document.getElementById('center').addEventListener('click', (event) => {
        const button = event.target.closest('#date-scrubber button[data-month]');
        if (!button) return;
        const month = months.find((item) => item.key === button.dataset.month);
        if (!month) return;
        lastKey = month.key;
        clearTimeout(jumpTimer);
        preview(month);
        jumpToOffset(month.offset);
        const bubble = document.getElementById('date-scrub-bubble');
        bubble.textContent = label(month.key);
        bubble.classList.add('on');
        window.setTimeout(() => bubble.classList.remove('on'), 600);
    });
    on('scope', load);
    on('lens', load);
    document.getElementById('canvas')?.addEventListener('scroll', scheduleViewportMonthUpdate, { passive: true });
    load();
}
