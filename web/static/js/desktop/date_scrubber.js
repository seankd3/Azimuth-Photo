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

function monthForY(clientY) {
    const scrub = document.getElementById('date-scrubber');
    const rect = scrub.getBoundingClientRect();
    const frac = clamp((clientY - rect.top) / Math.max(1, rect.height), 0, .999);
    const index = clamp(Math.floor(frac * months.length), 0, months.length - 1);
    return months[index] || null;
}

function render() {
    let scrub = document.getElementById('date-scrubber');
    if (!scrub) {
        scrub = document.createElement('div');
        scrub.id = 'date-scrubber';
        scrub.innerHTML = '<div class="ds-track"></div><div id="date-scrub-bubble"></div>';
        document.getElementById('center').appendChild(scrub);
    }
    const on = active() && months.length > 0;
    scrub.classList.toggle('on', on);
    if (!on) return;
    const track = scrub.querySelector('.ds-track');
    const labeled = months.filter((_, index) => index === 0 || index === months.length - 1 || index % Math.ceil(months.length / 8) === 0);
    track.innerHTML = labeled.map((month) => `<button data-month="${month.key}">${label(month.key).replace(' ', '<br>')}</button>`).join('');
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

function jump(month) {
    if (!month) return;
    if (month.key !== lastKey) {
        lastKey = month.key;
        clearTimeout(jumpTimer);
        jumpTimer = setTimeout(() => jumpToOffset(month.offset), 120);
    }
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.textContent = label(month.key);
    bubble.classList.add('on');
}

function scrubTo(clientY) {
    const month = monthForY(clientY);
    if (!month) return;
    const bubble = document.getElementById('date-scrub-bubble');
    bubble.style.top = `${clientY}px`;
    jump(month);
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
        if (!dragging) return;
        pendingY = event.clientY;
        requestAnimationFrame(() => {
            const y = pendingY;
            pendingY = null;
            if (dragging && y != null) scrubTo(y);
        });
    });
    const stop = () => {
        dragging = false;
        document.getElementById('date-scrubber')?.classList.remove('dragging');
        document.getElementById('date-scrub-bubble')?.classList.remove('on');
    };
    document.getElementById('center').addEventListener('pointerup', stop);
    document.getElementById('center').addEventListener('pointercancel', stop);
    document.getElementById('center').addEventListener('click', (event) => {
        const button = event.target.closest('#date-scrubber button[data-month]');
        if (!button) return;
        jump(months.find((month) => month.key === button.dataset.month));
    });
    on('scope', load);
    on('lens', load);
    load();
}
