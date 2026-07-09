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

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

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
    const frac = clamp((clientY - rect.top) / Math.max(1, rect.height), 0, .999);
    const index = clamp(Math.floor(frac * months.length), 0, months.length - 1);
    return months[index] || null;
}

function yearOf(month) {
    return String(month?.key || '').slice(0, 4);
}

function positionForIndex(index) {
    if (months.length < 2) return 0;
    return clamp(index / (months.length - 1), 0, 1) * 100;
}

function scrubberHeight(scrub) {
    return scrub.querySelector('.ds-track')?.getBoundingClientRect().height || scrub.getBoundingClientRect().height || 0;
}

function markerItems(scrub) {
    const height = scrubberHeight(scrub);
    const allowMonthLabels = months.length > 1 && height / months.length >= 44;
    return months.map((month, index) => {
        const year = yearOf(month);
        const previousYear = index > 0 ? yearOf(months[index - 1]) : '';
        const monthPart = String(month.key || '').slice(5, 7);
        const isYear = index === 0 || month.key === 'undated' || year !== previousYear;
        return {
            key: month.key,
            label: isYear ? (month.key === 'undated' ? 'Undated' : year) : '',
            monthLabel: !isYear && allowMonthLabels ? label(month.key).split(' ')[0] : '',
            pos: positionForIndex(index),
            type: isYear ? 'year' : (monthPart === '01' ? 'quarter' : 'month'),
        };
    });
}

function readableLabels(markers, scrub) {
    const height = scrubberHeight(scrub);
    const minGap = height ? (44 / height) * 100 : 0;
    const undated = markers.find((item) => item.key === 'undated' && item.label);
    return markers.filter((item, index) => {
        if (!item.label) return false;
        if (undated && item.key !== 'undated' && Math.abs(undated.pos - item.pos) < minGap) return false;
        const next = markers.slice(index + 1).find((candidate) => candidate.label);
        return !(next?.key === 'undated' && Math.abs(next.pos - item.pos) < minGap);
    });
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
        `<button class="ds-tick ${item.type}" data-month="${item.key}" style="top:${item.pos}%"><span>${item.monthLabel}</span></button>`
    )).join('');
    labels.innerHTML = readableLabels(markers, scrub).map((item) => (
        `<button class="ds-label ${item.type}" data-month="${item.key}" style="top:${item.pos}%">${item.label}</button>`
    )).join('');
}

async function load() {
    const seq = ++generation;
    months = [];
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

function preview(month, { commitOnRelease = false } = {}) {
    if (!month) return;
    if (commitOnRelease) releaseJump = month;
    if (month.key !== lastKey) {
        lastKey = month.key;
        clearTimeout(jumpTimer);
    }
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.textContent = label(month.key);
    bubble.classList.add('on');
    const scrub = document.getElementById('date-scrubber');
    const marker = scrub?.querySelector('.ds-current');
    if (marker) marker.style.top = `${positionForIndex(months.indexOf(month))}%`;
}

function scrubTo(clientY) {
    const month = monthForY(clientY);
    if (!month) return;
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.style.top = `${clientY}px`;
    preview(month, { commitOnRelease: true });
}

function hoverTo(clientY) {
    const month = monthForY(clientY);
    if (!month) return;
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.style.top = `${clientY}px`;
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
        requestAnimationFrame(() => {
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
        document.getElementById('date-scrubber')?.classList.remove('dragging');
        document.getElementById('date-scrub-bubble')?.classList.remove('on');
    };
    document.getElementById('center').addEventListener('pointerup', stop);
    document.getElementById('center').addEventListener('pointercancel', stop);
    document.getElementById('center').addEventListener('pointerleave', () => {
        if (!dragging) document.getElementById('date-scrub-bubble')?.classList.remove('on');
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
    load();
}
