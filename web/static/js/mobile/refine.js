// Refine tab: duel (n=2) and survey (n=4) over the current scope.
// Picks are REAL: POST /api/mosaic/pick with the exact payload the
// desktop mosaic uses ({winner_id, loser_ids}); undo is a real
// POST /api/compare/undo. The sorted % is the real sort_quality
// from /api/rankings offset=0 — no fake ticking.

import { getRankings, mosaicNext, mosaicPick, compareUndo, thumbUrl } from './api.js';
import { on, rememberImages, scope, scopeActive, scopeParams } from './state.js';
import { showToast } from './toast.js';

const RING_CIRCUMFERENCE = 62.83;
const MAX_SCOPED_IDS = 2000;

let root = null;
let built = false;
let mode = 'duel';               // 'duel' (n=2) | 'survey' (n=4)
let currentSet = [];
let nextSetPromise = null;
let history = [];                // previous sets, for undo restore
let picks = 0;
let streak = 0;
let busy = false;
let generationCounter = 0;
let qualityTimer = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const need = () => (mode === 'duel' ? 2 : 4);

function shell() {
    root.innerHTML =
        '<div class="mr-top">'
        + '<svg class="mr-ring" width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">'
        + '<circle class="rr-bg" cx="13" cy="13" r="10"/><circle class="rr-fg" cx="13" cy="13" r="10"/></svg>'
        + '<span class="mr-title" id="mr-title"></span>'
        + '<span class="mr-pct num" id="mr-pct">—</span>'
        + '</div>'
        + '<div class="mr-modes" id="mr-modes">'
        + '<button data-mode="duel" class="active">Duel</button>'
        + '<button data-mode="survey">Survey</button>'
        + '</div>'
        + '<div class="mr-stage duel" id="mr-stage"></div>'
        + '<div class="mr-bottom">'
        + '<div class="mr-stats">'
        + '<div class="mr-stat"><b id="mr-picks" class="num">0</b><span>picks</span></div>'
        + '<div class="mr-stat"><b id="mr-streak" class="num">0</b><span>streak</span></div>'
        + '</div>'
        + '<button class="mr-undo" id="mr-undo">Undo</button>'
        + '</div>';

    root.querySelector('#mr-modes').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-mode]');
        if (!btn || btn.dataset.mode === mode) return;
        mode = btn.dataset.mode;
        for (const b of root.querySelectorAll('#mr-modes button')) {
            b.classList.toggle('active', b.dataset.mode === mode);
        }
        resetSet();
    });
    root.querySelector('#mr-undo').addEventListener('click', undo);
    root.querySelector('#mr-stage').addEventListener('click', (e) => {
        const card = e.target.closest('.mr-card[data-id]');
        if (card) pick(Number(card.dataset.id));
    });
}

function renderTitle() {
    const title = root.querySelector('#mr-title');
    if (title) title.textContent = scopeActive() ? (scope.label || scope.q || 'This scope') : 'All Photos';
}

function renderQuality(quality) {
    const pct = quality && Number(quality.total) > 0 ? Number(quality.percent) || 0 : null;
    const label = root.querySelector('#mr-pct');
    const ring = root.querySelector('.rr-fg');
    if (label) label.textContent = pct == null ? '—' : `${pct}% sorted`;
    if (ring) {
        ring.style.strokeDashoffset = pct == null
            ? RING_CIRCUMFERENCE
            : String(RING_CIRCUMFERENCE * (1 - pct / 100));
    }
}

async function refreshQuality() {
    if (scope.similarImages && scope.similarImages.length) {
        renderQuality(null);
        return;
    }
    const data = await getRankings(scopeParams({ limit: 1, offset: 0, sort: 'elo' }));
    if (data && data.sort_quality) renderQuality(data.sort_quality);
}

function scheduleQualityRefresh() {
    clearTimeout(qualityTimer);
    qualityTimer = setTimeout(refreshQuality, 1200);
}

function renderSkeleton() {
    const stage = root.querySelector('#mr-stage');
    stage.className = `mr-stage ${mode}`;
    stage.innerHTML = '<div class="mr-card skel"></div>'.repeat(need());
}

function renderSet() {
    const stage = root.querySelector('#mr-stage');
    stage.className = `mr-stage ${mode}`;
    if (currentSet.length < need()) {
        stage.className = 'mr-stage';
        stage.innerHTML = '<div class="mr-empty">Not enough photos to refine in this scope.<br>Try widening the scope.</div>';
        return;
    }
    stage.innerHTML = currentSet.map((img) =>
        `<button class="mr-card" data-id="${img.id}" aria-label="Pick ${esc(img.filename || img.id)}">`
        + `<img src="${esc(thumbUrl('md', img.id))}" decoding="async" alt=""></button>`
    ).join('');
}

function fetchSet(excludeIds = []) {
    const params = scopeParams();
    if (scope.similarImages && scope.similarImages.length) {
        const ids = scope.similarImages.map((img) => Number(img.id)).filter((id) => id > 0).slice(0, MAX_SCOPED_IDS);
        params.set('ids', ids.join(','));
    }
    return mosaicNext(need(), params, excludeIds.join(','));
}

function prefetchNext() {
    nextSetPromise = fetchSet(currentSet.map((img) => img.id));
}

async function resetSet() {
    const gen = ++generationCounter;
    history = [];
    nextSetPromise = null;
    renderTitle();
    renderSkeleton();
    const data = await fetchSet();
    if (gen !== generationCounter) return;
    currentSet = (data && data.images) || [];
    rememberImages(currentSet);
    renderSet();
    if (currentSet.length >= need()) prefetchNext();
    refreshQuality();
}

async function advance() {
    const gen = generationCounter;
    const promise = nextSetPromise || fetchSet(currentSet.map((img) => img.id));
    nextSetPromise = null;
    renderSkeleton();
    const data = await promise;
    if (gen !== generationCounter) return;
    currentSet = (data && data.images) || [];
    rememberImages(currentSet);
    renderSet();
    if (currentSet.length >= need()) prefetchNext();
}

async function pick(winnerId) {
    if (busy || currentSet.length < need()) return;
    busy = true;
    const winner = currentSet.find((img) => Number(img.id) === winnerId);
    const loserIds = currentSet.filter((img) => Number(img.id) !== winnerId).map((img) => Number(img.id));
    if (!winner || !loserIds.length) {
        busy = false;
        return;
    }
    const card = root.querySelector(`.mr-card[data-id="${winnerId}"]`);
    if (card) card.classList.add('picked');
    if (navigator.vibrate) navigator.vibrate(8);

    history.push({ set: currentSet, winnerId });
    if (history.length > 20) history.shift();
    picks += 1;
    streak += 1;
    root.querySelector('#mr-picks').textContent = String(picks);
    root.querySelector('#mr-streak').textContent = String(streak);

    // Advance immediately (speed covenant: the loop never waits on the write).
    const write = mosaicPick(winnerId, loserIds);
    await advance();
    busy = false;

    const result = await write;
    if (!result || !result.ok) {
        history.pop();
        picks = Math.max(0, picks - 1);
        streak = 0;
        root.querySelector('#mr-picks').textContent = String(picks);
        root.querySelector('#mr-streak').textContent = String(streak);
        showToast("Pick didn't save — check connection");
        return;
    }
    scheduleQualityRefresh();
}

async function undo() {
    if (busy || !history.length) return;
    busy = true;
    const last = history.pop();
    const result = await compareUndo();
    busy = false;
    if (!result || !result.ok) {
        history.push(last);
        showToast('Nothing to undo');
        return;
    }
    picks = Math.max(0, picks - 1);
    streak = 0;
    root.querySelector('#mr-picks').textContent = String(picks);
    root.querySelector('#mr-streak').textContent = String(streak);
    currentSet = last.set;
    renderSet();
    prefetchNext();
    scheduleQualityRefresh();
    showToast('Pick undone');
}

export function initRefine() {
    root = document.getElementById('m-refine');
    on('scope', () => {
        if (built) resetSet();
    });
}

export function showRefine() {
    if (!built) {
        built = true;
        shell();
        resetSet();
    } else {
        renderTitle();
        refreshQuality();
    }
}
