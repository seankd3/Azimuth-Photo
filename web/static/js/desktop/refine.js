import { describeScope, on, refineParams, scope, setRankingsMeta } from './state.js';
import { compareUndo, getPropagationLast, getRankings, mosaicNext, mosaicPick, thumbUrl } from './api.js';
import { showToast } from './toast.js';
import { releaseFocus, trapFocus } from './focusTrap.js';

const MODE_KEY = 'pa_d_refine_mode';
const STRATEGY_KEY = 'pa_d_refine_strategy';
let open = false;
let mode = localStorage.getItem(MODE_KEY) || 'survey';
let strategy = localStorage.getItem(STRATEGY_KEY) || 'diverse';
let currentSet = [];
let nextPromise = null;
let history = [];
let generation = 0;
let busy = false;
let picks = 0;
let startedAt = 0;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

function need() {
    return mode === 'duel' ? 2 : 4;
}

function renderModes() {
    for (const button of document.querySelectorAll('#refine-modes button')) {
        button.classList.toggle('active', button.dataset.mode === mode);
    }
    document.getElementById('refine-strategy').value = strategy;
}

function renderStats() {
    document.getElementById('refine-picks').textContent = String(picks);
    const elapsed = Math.max(1, (Date.now() - startedAt) / 60000);
    document.getElementById('refine-pace').textContent = String(Math.round(picks / elapsed));
}

function pulsePropagation(count) {
    const badge = document.getElementById('propagation-badge');
    const n = Number(count || 0);
    if (!badge || n <= 0) return;
    badge.textContent = `+${n} propagated`;
    badge.classList.add('visible');
    clearTimeout(pulsePropagation.timer);
    pulsePropagation.timer = setTimeout(() => badge.classList.remove('visible'), 2200);
}

async function refreshPropagation() {
    const data = await getPropagationLast();
    const count = Number(data && data.count) || 0;
    pulsePropagation(count);
}

function renderSkeleton() {
    const stage = document.getElementById('refine-stage');
    stage.className = mode;
    stage.innerHTML = '<div class="ref-card skel"></div>'.repeat(need());
}

function renderSet() {
    const stage = document.getElementById('refine-stage');
    stage.className = mode;
    if (currentSet.length < need()) {
        stage.innerHTML = '<div class="load-error"><h4>Not enough photos to refine</h4><p>Try widening the current view.</p></div>';
        return;
    }
    stage.innerHTML = currentSet.map((img, index) => {
        const key = mode === 'duel' ? (index === 0 ? '←' : '→') : String(index + 1);
        return `<button class="ref-card" data-id="${img.id}" aria-label="Pick ${esc(img.filename || img.id)}">`
            + `<img src="${esc(thumbUrl('md', img.id))}" decoding="async" alt=""><span class="ref-key">${key}</span></button>`;
    }).join('');
}

async function refreshQuality() {
    const data = await getRankings(refineParams({ limit: 1, offset: 0, sort: 'elo' }));
    if (!data) return;
    setRankingsMeta({ visibleImages: data.visible_images, sortQuality: data.sort_quality });
    const quality = data.sort_quality;
    document.getElementById('refine-quality').textContent = quality && quality.percent != null
        ? `${Math.round(Number(quality.percent))}% sorted`
        : '';
}

function fetchSet(excludeIds = []) {
    const avgElo = currentSet.length
        ? currentSet.reduce((sum, img) => sum + (Number(img.elo) || 1200), 0) / currentSet.length
        : 0;
    return mosaicNext(need(), refineParams(), excludeIds.join(','), strategy, avgElo);
}

function prefetch() {
    nextPromise = fetchSet(currentSet.map((img) => img.id));
}

async function resetSet() {
    const seq = ++generation;
    history = [];
    currentSet = [];
    nextPromise = null;
    document.getElementById('refine-title').textContent = `Refining · ${describeScope()}`;
    renderModes();
    renderSkeleton();
    const data = await fetchSet();
    if (seq !== generation) return;
    currentSet = (data && data.images) || [];
    renderSet();
    if (currentSet.length >= need()) prefetch();
    refreshQuality();
}

async function shuffleSet() {
    if (!open || busy) return;
    const seq = ++generation;
    const exclude = currentSet.map((img) => img.id);
    nextPromise = null;
    renderSkeleton();
    const data = await fetchSet(exclude);
    if (seq !== generation) return;
    currentSet = (data && data.images) || [];
    renderSet();
    if (currentSet.length >= need()) prefetch();
}

async function advance() {
    const seq = ++generation;
    const promise = nextPromise || fetchSet(currentSet.map((img) => img.id));
    nextPromise = null;
    renderSkeleton();
    const data = await promise;
    if (seq !== generation) return null;
    currentSet = (data && data.images) || [];
    renderSet();
    if (currentSet.length >= need()) prefetch();
    return { seq, set: currentSet };
}

export async function pickRefine(winnerId) {
    if (!open || busy || currentSet.length < need()) return;
    const winner = currentSet.find((img) => Number(img.id) === Number(winnerId));
    if (!winner) return;
    const loserIds = currentSet.filter((img) => Number(img.id) !== Number(winnerId)).map((img) => Number(img.id));
    busy = true;
    const entry = { set: currentSet, seq: generation };
    history.push(entry);
    if (history.length > 20) history.shift();
    picks += 1;
    renderStats();
    const write = mosaicPick(winnerId, loserIds);
    const advanced = await advance();
    busy = false;
    const result = await write;
    if (!result || !result.ok) {
        picks = Math.max(0, picks - 1);
        const index = history.indexOf(entry);
        if (index >= 0) history.splice(index, 1);
        if (advanced && advanced.seq === generation && currentSet === advanced.set) {
            currentSet = entry.set;
            nextPromise = null;
            renderSet();
            if (currentSet.length >= need()) prefetch();
        }
        renderStats();
        showToast("Pick didn't save");
        return;
    }
    refreshPropagation();
    if (picks % 10 === 0) refreshQuality();
}

export async function undoRefine() {
    if (!open || busy || !history.length) return;
    busy = true;
    const previous = history.pop();
    const result = await compareUndo();
    busy = false;
    if (!result || !result.ok) {
        history.push(previous);
        showToast('Nothing to undo');
        return;
    }
    picks = Math.max(0, picks - 1);
    currentSet = previous.set;
    renderSet();
    renderStats();
    prefetch();
    refreshQuality();
    showToast('Pick undone');
}

export function openRefine() {
    if (open) return;
    if (scope.collectionId || scope.similarIds.length || scope.import_batch) {
        showToast('Refine needs backend filtering for this view first.');
        return;
    }
    open = true;
    startedAt = Date.now();
    const root = document.getElementById('refine');
    root.hidden = false;
    trapFocus(root, root);
    renderStats();
    resetSet();
}

export function closeRefine() {
    if (!open) return;
    open = false;
    const root = document.getElementById('refine');
    root.hidden = true;
    releaseFocus(root);
}

export function refineOpen() {
    return open;
}

export function pickByKey(key) {
    if (!open) return false;
    if (mode === 'survey' && /^[1-4]$/.test(key)) {
        const img = currentSet[Number(key) - 1];
        if (img) pickRefine(img.id);
        return true;
    }
    if (mode === 'duel' && (key === 'ArrowLeft' || key === 'ArrowRight')) {
        const img = currentSet[key === 'ArrowLeft' ? 0 : 1];
        if (img) pickRefine(img.id);
        return true;
    }
    return false;
}

export function initRefine() {
    document.getElementById('refine-stage').addEventListener('click', (event) => {
        const card = event.target.closest('.ref-card[data-id]');
        if (card) pickRefine(Number(card.dataset.id));
    });
    document.getElementById('refine-close').addEventListener('click', closeRefine);
    document.getElementById('refine-undo').addEventListener('click', undoRefine);
    document.getElementById('refine-shuffle').addEventListener('click', shuffleSet);
    document.getElementById('refine-strategy').addEventListener('change', (event) => {
        strategy = event.target.value || 'diverse';
        localStorage.setItem(STRATEGY_KEY, strategy);
        resetSet();
    });
    for (const button of document.querySelectorAll('#refine-modes button')) {
        button.addEventListener('click', () => {
            mode = button.dataset.mode;
            localStorage.setItem(MODE_KEY, mode);
            resetSet();
        });
    }
    on('scope', () => {
        if (open) resetSet();
    });
    on('refine:open', openRefine);
    renderModes();
}
