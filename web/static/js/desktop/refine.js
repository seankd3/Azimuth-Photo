import {
    describeScope, on, refineParams, scope, setActiveLens, setRankingsMeta,
} from './state.js';
import {
    compareUndo, getPropagationLast, getRankings, mosaicNext, mosaicPick, thumbUrl,
} from './api.js';
import { showToast } from './toast.js';
import { escapeHtml as esc } from './dom.js';

const MODE_KEY = 'pa_d_refine_mode';
const SIZE_KEY = 'pa_d_refine_size';
const STRATEGY_KEY = 'pa_d_refine_strategy';
const MAX_SCOPED_IDS = 2000;
const STALE_ROUNDS = 10;
const REPLACEMENT_TARGET = 24;
const REPLACEMENT_FETCH_MIN = 12;
const REPLACEMENT_LOW_WATER = 8;
const REPLACEMENT_PROBE_CONCURRENCY = 4;
const REPLACEMENT_PRELOAD_TIMEOUT_MS = 120;
const RECENT_EXCLUDE_LIMIT = 48;
const HISTORY_LIMIT = 20;
const WINNER_HOLD_MS = 400;

const STRATEGY_TIPS = {
    diverse: 'Diverse — spreads picks across visually different photos',
    random: 'Random — gives every photo an even chance',
    explore: 'Explore — prioritizes photos with the fewest comparisons',
    compete: 'Compete — groups photos with similar ratings',
};

const GRID_SIZES = {
    '2x2': { columns: 2, rows: 2, count: 4 },
    '3x2': { columns: 3, rows: 2, count: 6 },
    '3x3': { columns: 3, rows: 3, count: 9 },
    '4x3': { columns: 4, rows: 3, count: 12 },
};

let open = false;
let mode = readChoice(MODE_KEY, ['mosaic', 'duel'], 'mosaic');
let gridSize = readChoice(SIZE_KEY, Object.keys(GRID_SIZES), '3x3');
let strategy = readChoice(STRATEGY_KEY, ['diverse', 'explore', 'compete', 'random'], 'diverse');
let currentSet = [];
let age = [];
let replacements = [];
let recentIds = [];
let history = [];
let generation = 0;
let actionSeq = 0;
let filling = false;
let selectedIndex = -1;
let picks = 0;
let startedAt = 0;
let saveQueue = Promise.resolve();
let saveQueueActive = false;
let saveQueueToken = 0;
let pickActionQueue = Promise.resolve();

function readChoice(key, choices, fallback) {
    const saved = localStorage.getItem(key);
    return choices.includes(saved) ? saved : fallback;
}

function need() {
    return mode === 'duel' ? 2 : GRID_SIZES[gridSize].count;
}

function stageClassName() {
    return mode === 'duel' ? 'duel' : `mosaic grid-${gridSize}`;
}

function thumbTier() {
    return 'md';
}

function imageUrl(img) {
    return thumbUrl(thumbTier(), img.id);
}

function normalizeImage(img) {
    if (!img || img.id == null) return null;
    return {
        ...img,
        id: Number(img.id),
        thumb_url: imageUrl(img),
    };
}

function renderModes() {
    for (const button of document.querySelectorAll('#refine-modes button')) {
        const active = button.dataset.mode === mode;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
    document.getElementById('refine-size').value = gridSize;
    document.getElementById('refine-size').disabled = mode === 'duel';
    const strategySelect = document.getElementById('refine-strategy');
    strategySelect.value = strategy;
    strategySelect.dataset.tip = STRATEGY_TIPS[strategy];
}

function renderUndoState() {
    const button = document.getElementById('refine-undo');
    if (!button) return;
    button.disabled = history.length === 0;
    button.setAttribute('aria-disabled', button.disabled ? 'true' : 'false');
}

function renderStats() {
    document.getElementById('refine-picks').textContent = String(picks);
    const elapsed = Math.max(1, (Date.now() - startedAt) / 60000);
    document.getElementById('refine-pace').textContent = String(Math.round(picks / elapsed));
}

function renderSemanticPairing(active) {
    const badge = document.getElementById('refine-semantic-badge');
    if (!badge) return;
    badge.hidden = !active;
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
    try {
        const data = await getPropagationLast();
        const count = Number(data && data.count) || 0;
        pulsePropagation(count);
    } catch {
        /* Decorative status only. Pick save failures are handled separately. */
    }
}

function setImagesLoadedHandlers() {
    for (const img of document.querySelectorAll('#refine-stage .ref-card img')) {
        const markLoaded = () => img.classList.add('loaded');
        img.addEventListener('load', markLoaded, { once: true });
        if (img.complete && img.naturalWidth > 0) markLoaded();
    }
}

function renderSkeleton() {
    const stage = document.getElementById('refine-stage');
    stage.className = stageClassName();
    stage.innerHTML = '<button class="ref-card skel" aria-hidden="true" tabindex="-1"></button>'.repeat(need());
}

function renderSet() {
    const stage = document.getElementById('refine-stage');
    stage.className = stageClassName();
    if (currentSet.length < need()) {
        stage.innerHTML = '<div class="load-error"><h4>Not enough photos to refine</h4><p>Add a source if your library is empty, or try widening this view.</p></div>';
        selectedIndex = -1;
        return;
    }
    if (selectedIndex >= currentSet.length) selectedIndex = currentSet.length - 1;
    stage.innerHTML = currentSet.map((img, index) => {
        const selected = index === selectedIndex ? ' selected' : '';
        return `<button class="ref-card${selected}" data-id="${img.id}" data-index="${index}" aria-label="Pick ${esc(img.filename || img.id)}">`
            + `<img src="${esc(imageUrl(img))}" loading="lazy" decoding="async" alt="${esc(img.filename || '')}"></button>`;
    }).join('');
    setImagesLoadedHandlers();
}

function renderLoadError() {
    const stage = document.getElementById('refine-stage');
    stage.className = stageClassName();
    stage.innerHTML = '<div class="load-error"><h4>Couldn\'t load Refine</h4><p>The archive did not respond. Try again.</p><button class="btn" id="refine-retry" type="button">Try again</button></div>';
    stage.querySelector('#refine-retry')?.addEventListener('click', resetSet);
}

function updateSelectedCell() {
    for (const card of document.querySelectorAll('#refine-stage .ref-card[data-index]')) {
        card.classList.toggle('selected', Number(card.dataset.index) === selectedIndex);
    }
}

async function refreshQuality() {
    if (scope.collectionId || scope.similarIds.length) {
        document.getElementById('refine-quality').textContent = '';
        return;
    }
    let data = null;
    try {
        data = await getRankings(refineParams({ limit: 1, offset: 0, sort: 'elo' }));
    } catch {
        return;
    }
    if (!data) return;
    setRankingsMeta({ visibleImages: data.visible_images, sortQuality: data.sort_quality });
    const quality = data.sort_quality;
    document.getElementById('refine-quality').textContent = quality && quality.percent != null
        ? `${Math.round(Number(quality.percent))}% sorted`
        : '';
}

function refineQueryParams() {
    const params = refineParams();
    if (scope.collectionId) params.set('collection_id', String(scope.collectionId));
    if (scope.similarIds.length) {
        params.set('ids', scope.similarIds.map(Number).filter((id) => id > 0).slice(0, MAX_SCOPED_IDS).join(','));
    }
    return params;
}

function gridElo() {
    if (!currentSet.length) return 0;
    return currentSet.reduce((sum, img) => sum + (Number(img.elo) || 1200), 0) / currentSet.length;
}

function uniqueIds(values = []) {
    return [...new Set(values.map(Number).filter((id) => id > 0))];
}

async function fetchImages(count, excludeIds = []) {
    const data = await mosaicNext(
        count,
        refineQueryParams(),
        uniqueIds(excludeIds).join(','),
        strategy,
        gridElo(),
    );
    if (count === 2) renderSemanticPairing(data && data.pairing === 'semantic');
    return ((data && data.images) || []).map(normalizeImage).filter(Boolean);
}

function currentExcludeIds({ includeRecent = true } = {}) {
    const ids = [
        ...currentSet.map((img) => img.id),
        ...replacements.map((img) => img.id),
    ];
    if (includeRecent) ids.push(...recentIds);
    return uniqueIds(ids);
}

function rememberRecent(ids = []) {
    for (const id of ids) {
        const n = Number(id);
        if (!n) continue;
        recentIds = recentIds.filter((entry) => entry !== n);
        recentIds.push(n);
    }
    if (recentIds.length > RECENT_EXCLUDE_LIMIT) {
        recentIds = recentIds.slice(recentIds.length - RECENT_EXCLUDE_LIMIT);
    }
}

function probeImage(url, timeoutMs = REPLACEMENT_PRELOAD_TIMEOUT_MS) {
    return new Promise((resolve) => {
        const ImageCtor = globalThis.Image;
        if (!ImageCtor || !url) {
            resolve({ ok: false, timedOut: false });
            return;
        }
        let settled = false;
        const done = (ok, timedOut = false) => {
            if (settled) return;
            settled = true;
            resolve({ ok, timedOut });
        };
        const img = new ImageCtor();
        img.onload = () => done(true);
        img.onerror = () => done(false);
        img.src = url;
        setTimeout(() => done(false, true), timeoutMs);
    });
}

async function addReadyReplacement(img, token) {
    const probe = await probeImage(imageUrl(img));
    if (token !== generation) return false;
    if (!probe.ok && !probe.timedOut) return false;
    const currentIds = new Set(currentSet.map((entry) => entry.id));
    if (currentIds.has(img.id) || replacements.some((entry) => entry.id === img.id)) return false;
    if (replacements.length >= REPLACEMENT_TARGET) return false;
    replacements.push({ ...img, cache_probe_deferred: Boolean(probe.timedOut) });
    return true;
}

async function fillReplacements() {
    if (!open || filling || replacements.length >= REPLACEMENT_TARGET) return false;
    filling = true;
    const token = generation;
    try {
        const needed = Math.max(REPLACEMENT_FETCH_MIN, REPLACEMENT_TARGET - replacements.length);
        let candidates = await fetchImages(
            needed,
            currentExcludeIds({ includeRecent: true }),
        );
        if (!candidates.length && recentIds.length) {
            candidates = await fetchImages(
                needed,
                currentExcludeIds({ includeRecent: false }),
            );
        }
        if (token !== generation) return false;
        const seen = new Set(replacements.map((img) => img.id));
        candidates = candidates.filter((img) => {
            const duplicate = seen.has(img.id) || currentSet.some((entry) => entry.id === img.id);
            seen.add(img.id);
            return !duplicate;
        });
        for (let start = 0; start < candidates.length; start += REPLACEMENT_PROBE_CONCURRENCY) {
            if (token !== generation || replacements.length >= REPLACEMENT_TARGET) break;
            const chunk = candidates.slice(start, start + REPLACEMENT_PROBE_CONCURRENCY);
            await Promise.all(chunk.map((img) => addReadyReplacement(img, token)));
        }
    } catch {
        if (token === generation) showToast('Couldn’t load Refine replacements');
    } finally {
        if (token === generation) filling = false;
    }
    return true;
}

function maybeFillReplacements() {
    if (replacements.length < REPLACEMENT_LOW_WATER) fillReplacements();
}

function takeReplacement() {
    const existingIds = new Set(currentSet.map((img) => img.id));
    while (replacements.length) {
        const next = replacements.shift();
        if (next && !existingIds.has(next.id)) return next;
    }
    return null;
}

function waitForReplacement() {
    fillReplacements();
    return new Promise((resolve) => {
        let attempts = 0;
        const poll = () => {
            const next = takeReplacement();
            if (next || attempts >= 30 || !open) {
                resolve(next);
                return;
            }
            attempts += 1;
            setTimeout(poll, 160);
        };
        poll();
    });
}

function replacementIndices(pickedIndex) {
    let oldestIndex = -1;
    let oldestAge = -1;
    for (let i = 0; i < age.length; i++) {
        if (i !== pickedIndex && age[i] > oldestAge) {
            oldestAge = age[i];
            oldestIndex = i;
        }
    }
    const indices = [pickedIndex];
    if (oldestIndex >= 0 && oldestAge >= STALE_ROUNDS) indices.push(oldestIndex);
    return indices;
}

function swapCell(index, img) {
    const old = currentSet[index];
    if (!old || !img) return false;
    rememberRecent([old.id]);
    currentSet[index] = img;
    age[index] = 0;
    const card = document.querySelector(`#refine-stage .ref-card[data-index="${index}"]`);
    if (!card) {
        renderSet();
        return true;
    }
    card.classList.add('replacing');
    card.dataset.id = String(img.id);
    card.setAttribute('aria-label', `Pick ${img.filename || img.id}`);
    const image = card.querySelector('img');
    const finish = () => {
        card.classList.remove('replacing');
        if (image) image.classList.add('loaded');
    };
    if (image) {
        image.classList.remove('loaded');
        image.alt = img.filename || '';
        image.addEventListener('load', finish, { once: true });
        image.addEventListener('error', finish, { once: true });
        image.src = imageUrl(img);
        if (image.complete) finish();
    } else {
        finish();
    }
    maybeFillReplacements();
    return true;
}

async function replaceAt(index, token) {
    const card = document.querySelector(`#refine-stage .ref-card[data-index="${index}"]`);
    card?.classList.add('replacing');
    const next = takeReplacement() || await waitForReplacement();
    if (token !== generation || !next) {
        card?.classList.remove('replacing');
        return false;
    }
    return swapCell(index, next);
}

function holdWinnerThenReplace(index, token) {
    const card = document.querySelector(`#refine-stage .ref-card[data-index="${index}"]`);
    card?.classList.add('winner-hold');
    setTimeout(() => {
        if (token !== generation) return;
        card?.classList.remove('winner-hold');
        replaceAt(index, token);
    }, WINNER_HOLD_MS);
}

function enqueuePickSave(winnerId, loserIds) {
    const run = () => mosaicPick(winnerId, loserIds)
        .then((result) => ({ ok: Boolean(result && result.ok), result }))
        .catch((error) => ({ ok: false, error }));
    const queued = saveQueueActive ? saveQueue.then(run, run) : run();
    const token = ++saveQueueToken;
    saveQueueActive = true;
    saveQueue = queued.catch(() => {});
    saveQueue.finally(() => {
        if (token === saveQueueToken) saveQueueActive = false;
    });
    return queued;
}

function restoreSnapshot(snapshot) {
    generation += 1;
    currentSet = snapshot.images.slice();
    age = snapshot.age.slice();
    replacements = snapshot.replacements.slice();
    recentIds = snapshot.recentIds.slice();
    selectedIndex = snapshot.selectedIndex;
    renderSet();
    renderStats();
    fillReplacements();
}

async function resetSet() {
    const seq = ++generation;
    history = [];
    currentSet = [];
    age = [];
    replacements = [];
    recentIds = [];
    selectedIndex = -1;
    document.getElementById('refine-title').textContent = `Refining · ${describeScope()}`;
    renderSemanticPairing(false);
    renderModes();
    renderSkeleton();
    let images = [];
    try {
        images = await fetchImages(need());
    } catch {
        if (seq !== generation) return;
        renderLoadError();
        return;
    }
    if (seq !== generation) return;
    currentSet = images.slice(0, need());
    age = currentSet.map(() => 0);
    renderSet();
    fillReplacements();
    refreshQuality();
}

async function shuffleSet() {
    if (!open) return;
    const seq = ++generation;
    const exclude = currentSet.map((img) => img.id);
    replacements = [];
    selectedIndex = -1;
    renderSkeleton();
    let images = [];
    try {
        images = await fetchImages(need(), exclude);
    } catch {
        if (seq !== generation) return;
        renderLoadError();
        return;
    }
    if (seq !== generation) return;
    rememberRecent(exclude);
    currentSet = images.slice(0, need());
    age = currentSet.map(() => 0);
    renderSet();
    fillReplacements();
}

async function applyRefinePick(winnerId) {
    if (!open || currentSet.length < need()) return;
    const winner = Number(winnerId);
    const idx = currentSet.findIndex((img) => Number(img.id) === winner);
    if (idx < 0) return;
    const pickedCard = document.querySelector(`#refine-stage .ref-card[data-index="${idx}"]`);
    if (pickedCard?.classList.contains('replacing') || pickedCard?.classList.contains('winner-hold')) return;
    const loserIds = currentSet.filter((img) => Number(img.id) !== winner).map((img) => Number(img.id));
    if (!loserIds.length) return;
    const seq = ++actionSeq;
    const snapshot = {
        images: currentSet.slice(),
        age: age.slice(),
        replacements: replacements.slice(),
        recentIds: recentIds.slice(),
        selectedIndex,
    };
    const entry = { snapshot, actionSeq: seq, savePromise: null };
    history.push(entry);
    if (history.length > HISTORY_LIMIT) history.shift();
    renderUndoState();
    picks += 1;
    renderStats();

    const savePromise = enqueuePickSave(winner, loserIds);
    entry.savePromise = savePromise;

    for (let i = 0; i < age.length; i++) {
        if (i !== idx) age[i] += 1;
    }
    const indices = replacementIndices(idx);
    const token = generation;
    for (const index of indices) {
        if (mode === 'mosaic' && index === idx) holdWinnerThenReplace(index, token);
        else replaceAt(index, token);
    }
    selectedIndex = Math.min(idx, currentSet.length - 1);
    updateSelectedCell();
    fillReplacements();

    const saveResult = await savePromise;
    if (!saveResult.ok) {
        const historyIndex = history.indexOf(entry);
        if (historyIndex >= 0) history.splice(historyIndex, 1);
        renderUndoState();
        picks = Math.max(0, picks - 1);
        if (open && seq === actionSeq) {
            restoreSnapshot(snapshot);
            showToast('Couldn’t save pick · mosaic restored');
        } else {
            renderStats();
            showToast('Couldn’t save pick');
        }
        return;
    }
    refreshPropagation();
    if (picks % 10 === 0) refreshQuality();
}

export function pickRefine(winnerId) {
    const queuedWinner = Number(winnerId);
    const queuedGeneration = generation;
    pickActionQueue = pickActionQueue
        .catch(() => {})
        .then(() => {
            if (queuedGeneration !== generation) return null;
            return applyRefinePick(queuedWinner);
        });
    return pickActionQueue;
}

export async function undoRefine() {
    if (!open || !history.length) return;
    const entry = history.pop();
    renderUndoState();
    if (entry.savePromise) await entry.savePromise;
    const result = await compareUndo();
    if (!result || !result.ok) {
        history.push(entry);
        renderUndoState();
        showToast(result?.partial ? 'Undo partial — ranking drifted' : 'Nothing to undo');
        return;
    }
    picks = Math.max(0, picks - 1);
    restoreSnapshot(entry.snapshot);
    renderUndoState();
    refreshQuality();
    showToast('Pick undone');
}

export function mountRefine() {
    if (open) return;
    open = true;
    document.getElementById('view-refine').classList.add('active');
    picks = 0;
    startedAt = Date.now();
    renderStats();
    resetSet();
}

export function unmountRefine() {
    if (!open) return;
    open = false;
    generation += 1;
    selectedIndex = -1;
    document.getElementById('view-refine').classList.remove('active');
    document.getElementById('refine-head')?.classList.remove('controls-open');
    document.getElementById('refine-controls-toggle')?.setAttribute('aria-expanded', 'false');
    renderUndoState();
}

export function openRefine() {
    if (open) {
        closeRefine();
        return;
    }
    if (scope.similarIds.length > MAX_SCOPED_IDS) {
        showToast(`Refine can use up to ${MAX_SCOPED_IDS.toLocaleString('en-US')} similar photos`);
        return;
    }
    setActiveLens('refine');
}

export function closeRefine() {
    if (!open) return;
    setActiveLens('grid');
}

export function refineOpen() {
    return open;
}

function selectIndex(index) {
    if (!open || currentSet.length < need()) return false;
    selectedIndex = Math.max(0, Math.min(index, currentSet.length - 1));
    updateSelectedCell();
    return true;
}

function selectInVerticalDirection(direction) {
    const card = document.querySelector(`#refine-stage .ref-card[data-index="${selectedIndex}"]`);
    const cards = [...document.querySelectorAll('#refine-stage .ref-card[data-index]')];
    if (!card || !cards.length) return selectIndex(0);
    const currentRect = card.getBoundingClientRect();
    const currentCenter = currentRect.left + currentRect.width / 2;
    let bestIndex = selectedIndex;
    let bestScore = Number.POSITIVE_INFINITY;
    for (const candidate of cards) {
        const index = Number(candidate.dataset.index);
        if (index === selectedIndex) continue;
        const rect = candidate.getBoundingClientRect();
        const above = direction < 0 && rect.bottom <= currentRect.top + 1;
        const below = direction > 0 && rect.top >= currentRect.bottom - 1;
        if (!above && !below) continue;
        const center = rect.left + rect.width / 2;
        const vertical = direction < 0 ? currentRect.top - rect.bottom : rect.top - currentRect.bottom;
        const score = vertical * 1000 + Math.abs(center - currentCenter);
        if (score < bestScore) {
            bestScore = score;
            bestIndex = index;
        }
    }
    return selectIndex(bestIndex);
}

export function pickByKey(key) {
    if (!open) return false;
    if (mode === 'duel' && (key === 'ArrowLeft' || key === 'ArrowRight')) {
        const img = currentSet[key === 'ArrowLeft' ? 0 : 1];
        if (img) pickRefine(img.id);
        return true;
    }
    if (mode === 'mosaic') {
        if (/^[0-9]$/.test(String(key))) {
            const index = key === '0' ? 9 : Number(key) - 1;
            const img = currentSet[index];
            if (img) pickRefine(img.id);
            return true;
        }
        if (key === 'ArrowRight') return selectIndex(selectedIndex < 0 ? 0 : selectedIndex + 1);
        if (key === 'ArrowLeft') return selectIndex(selectedIndex < 0 ? 0 : selectedIndex - 1);
        if (key === 'ArrowDown') return selectInVerticalDirection(1);
        if (key === 'ArrowUp') return selectInVerticalDirection(-1);
        if (key === 'Enter' && selectedIndex >= 0) {
            const keepIndex = selectedIndex;
            const img = currentSet[selectedIndex];
            if (img) pickRefine(img.id);
            selectedIndex = keepIndex;
            updateSelectedCell();
            return true;
        }
    }
    return false;
}

export function initRefine() {
    const refineHead = document.getElementById('refine-head');
    const controlsToggle = document.getElementById('refine-controls-toggle');
    const controls = document.getElementById('refine-controls');
    const setControlsOpen = (nextOpen) => {
        refineHead?.classList.toggle('controls-open', nextOpen);
        controlsToggle?.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
    };
    controlsToggle?.addEventListener('click', (event) => {
        event.stopPropagation();
        setControlsOpen(!refineHead?.classList.contains('controls-open'));
    });
    controls?.addEventListener('click', (event) => event.stopPropagation());
    document.addEventListener('click', (event) => {
        if (!refineHead?.classList.contains('controls-open')) return;
        if (refineHead.contains(event.target)) return;
        setControlsOpen(false);
    });
    document.getElementById('refine-stage').addEventListener('click', (event) => {
        const card = event.target.closest('.ref-card[data-id]');
        if (!card) return;
        selectedIndex = Number(card.dataset.index);
        updateSelectedCell();
        pickRefine(Number(card.dataset.id));
    });
    document.getElementById('refine-close').addEventListener('click', closeRefine);
    document.getElementById('refine-undo').addEventListener('click', undoRefine);
    document.getElementById('refine-shuffle').addEventListener('click', shuffleSet);
    document.getElementById('refine-size').addEventListener('change', (event) => {
        gridSize = GRID_SIZES[event.target.value] ? event.target.value : '3x3';
        localStorage.setItem(SIZE_KEY, gridSize);
        if (open && mode === 'mosaic') resetSet();
        renderModes();
    });
    document.getElementById('refine-strategy').addEventListener('change', (event) => {
        strategy = event.target.value || 'diverse';
        localStorage.setItem(STRATEGY_KEY, strategy);
        if (open) resetSet();
    });
    for (const button of document.querySelectorAll('#refine-modes button')) {
        button.addEventListener('click', () => {
            mode = button.dataset.mode === 'duel' ? 'duel' : 'mosaic';
            localStorage.setItem(MODE_KEY, mode);
            if (open) resetSet();
            renderModes();
        });
    }
    on('scope', () => {
        if (open) resetSet();
    });
    on('refine:open', openRefine);
    renderModes();
    renderUndoState();
}
