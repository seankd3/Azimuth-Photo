// Staged import — the Lightroom-Classic-grade Import canvas (IMPORT_SPEC v1).
// Full-screen takeover following the Develop precedent: Esc before commit
// discards the staged scan and returns exactly where the user was, zero writes.
import {
    commitImportScan, getImportJob, getImportScan, getImportSources,
    browseImportPath, startImportScan,
} from './api.js';
import { emit, on, scope, setScope } from './state.js';
import { switchLens } from './lenses.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { escapeHtml as esc, formatCount as fmt } from './dom.js';

const CATEGORY_TREES = { raw: 'RAWS', personal: 'Personal Photos', film: 'Film Scans', export: 'Exported Edits', video: 'Video' };
const CATEGORY_SHORT = { raw: 'RAW', personal: 'personal', film: 'film', export: 'exports', video: 'video' };
const SCAN_POLL_MS = 1000;
const JOB_POLL_MS = 1000;
const RENDER_CHUNK = 400;

let root = null;
let els = {};
let mounted = false;
let generation = 0;

let sources = [];
let source = null;              // selected {id, kind, label, path}
let includeSubfolders = true;
let expandedDirs = new Map();   // path -> child dirs (browse cache)

let scanId = null;
let scanStatus = 'idle';        // idle | scanning | done | error
let entries = [];
let checked = new Set();        // entry keys staged for import
let insertedEntryKeys = new Set();
let filter = 'new';             // new | all
let anchorIndex = null;         // shift-range anchor into the visible list
let mode = 'copy';
let skipSuspects = true;
let clearCard = true;
let thumbPx = Number(localStorage.getItem('importThumbPx')) || 148;
let categoryOverride = '';            // '' = the app decides (per-file EXIF/source diagnosis)
let committing = false;

let thumbObserver = null;
let sentinelObserver = null;
let renderedCount = 0;

// ---------------------------------------------------------------- helpers

function fmtBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value >= 1024 ** 4) return `${(value / 1024 ** 4).toFixed(1)} TB`;
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(value / 1024))} KB`;
}

function fmtEta(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 90) return `${total}s`;
    return `~${Math.round(total / 60)} min`;
}

function visibleEntries() {
    return filter === 'new' ? entries.filter((entry) => !entry.suspect) : entries;
}

function checkedEntries() {
    return entries.filter((entry) => checked.has(entry.key));
}

function effectiveCategory(entry) {
    if (entry.kind === 'video') return 'video';
    return categoryOverride || entry.category || 'raw';
}

// ---------------------------------------------------------------- sources rail

async function loadSources() {
    const token = generation;
    const data = await getImportSources();
    if (!mounted || token !== generation) return;
    sources = data?.sources || [];
    renderSources();
    const card = sources.find((row) => row.kind === 'card');
    if (card && !source) selectSource(card); // card fast-path: insert card → everything staged
}

function sourceRowHtml(row) {
    const active = source && source.path === row.path;
    if (row.kind === 'card') {
        const meta = [
            row.photo_count != null ? `${fmt(row.photo_count)} photos` : '',
            row.bytes ? fmtBytes(row.bytes) : '',
        ].filter(Boolean).join(' · ');
        return `<button class="imps-source imps-card ${active ? 'active' : ''}" data-path="${esc(row.path)}">`
            + `${icon('image')}<span class="imps-source-text"><b>${esc(row.label)}</b>`
            + (meta ? `<small>${esc(meta)}</small>` : '') + '</span></button>';
    }
    return `<button class="imps-source ${active ? 'active' : ''}" data-path="${esc(row.path)}" data-dir="1">`
        + `${icon('folder')}<span class="imps-source-text"><b>${esc(row.label)}</b></span>`
        + `<span class="imps-twist ${expandedDirs.has(row.path) ? 'open' : ''}" data-twist="${esc(row.path)}"></span></button>`
        + dirChildrenHtml(row.path, 1);
}

function dirChildrenHtml(path, depth) {
    const children = expandedDirs.get(path);
    if (!children) return '';
    if (!children.length) return `<div class="imps-dir-empty" style="--depth:${depth}">No subfolders</div>`;
    return children.map((dir) => {
        const active = source && source.path === dir.path;
        return `<button class="imps-source imps-dir ${active ? 'active' : ''}" style="--depth:${depth}" data-path="${esc(dir.path)}" data-dir="1" data-label="${esc(dir.name)}">`
            + `${icon('folder')}<span class="imps-source-text"><b>${esc(dir.name)}</b>`
            + (dir.file_count ? `<small>${fmt(dir.file_count)} files</small>` : '') + '</span>'
            + `<span class="imps-twist ${expandedDirs.has(dir.path) ? 'open' : ''}" data-twist="${esc(dir.path)}"></span></button>`
            + dirChildrenHtml(dir.path, depth + 1);
    }).join('');
}

function renderSources() {
    const cards = sources.filter((row) => row.kind === 'card');
    const roots = sources.filter((row) => row.kind === 'root');
    els.sources.innerHTML = (cards.length
        ? `<h3>Cards</h3>${cards.map(sourceRowHtml).join('')}`
        : '<h3>Cards</h3><div class="imps-rail-empty">No card detected</div>')
        + `<h3>Folders</h3>${roots.map(sourceRowHtml).join('')}`;
}

async function toggleDir(path) {
    if (expandedDirs.has(path)) {
        expandedDirs.delete(path);
    } else {
        const data = await browseImportPath(path);
        if (!mounted) return;
        expandedDirs.set(path, data?.dirs || []);
    }
    renderSources();
}

function selectSource(row) {
    source = row;
    mode = 'copy';
    categoryOverride = '';
    if (els.category) els.category.value = '';
    renderSources();
    syncModeSeg();
    startScan();
}

// ---------------------------------------------------------------- scan

async function startScan() {
    if (!source) return;
    const token = ++generation;
    scanId = null;
    scanStatus = 'scanning';
    entries = [];
    checked = new Set();
    insertedEntryKeys = new Set();
    anchorIndex = null;
    renderedCount = 0;
    els.grid.querySelectorAll('.imps-cell').forEach((cell) => cell.remove());
    syncAll();
    const result = await startImportScan(source.path, includeSubfolders);
    if (!mounted || token !== generation) return;
    if (!result?.scan_id) {
        scanStatus = 'error';
        syncAll();
        showToast('Couldn’t read that source');
        return;
    }
    scanId = result.scan_id;
    pollScan(token);
}

async function pollScan(token) {
    while (mounted && token === generation) {
        const page = await getImportScan(scanId, entries.length);
        if (!mounted || token !== generation) return;
        if (!page) { scanStatus = 'error'; syncAll(); return; }
        if (page.entries?.length) {
            for (const entry of page.entries) {
                if (insertedEntryKeys.has(entry.key)) continue;
                insertedEntryKeys.add(entry.key);
                entries.push(entry);
                if (!entry.suspect) checked.add(entry.key); // new photos arrive checked
            }
            appendCells();
        }
        scanStatus = page.status;
        syncAll();
        if (scanStatus !== 'scanning' && entries.length >= (page.total_seen || 0)) return;
        await new Promise((resolve) => setTimeout(resolve, SCAN_POLL_MS));
    }
}

// ---------------------------------------------------------------- staged grid

function cellHtml(entry, index) {
    const isChecked = checked.has(entry.key);
    return `<figure class="imps-cell ${entry.suspect ? 'suspect' : ''} ${isChecked ? 'on' : ''}" data-key="${esc(entry.key)}" data-idx="${index}" tabindex="-1">`
        + `<span class="imps-thumb"><img data-src="/api/import/scan/${esc(scanId)}/thumb/${esc(entry.key)}" loading="lazy" decoding="async" alt="${esc(entry.name)}">`
        + (entry.kind === 'video' ? '<span class="imps-kind"><i></i></span>' : '')
        + (entry.suspect ? '<span class="imps-badge">Already imported</span>' : '')
        + `<button class="imps-check" aria-label="Include in import" aria-pressed="${isChecked}">${icon('check')}</button></span>`
        + `<figcaption>${esc(entry.name)}</figcaption></figure>`;
}

function appendCells() {
    const visible = visibleEntries();
    if (renderedCount >= visible.length) return;
    const next = Math.min(visible.length, renderedCount + RENDER_CHUNK);
    const html = [];
    for (let index = renderedCount; index < next; index += 1) html.push(cellHtml(visible[index], index));
    els.sentinel.insertAdjacentHTML('beforebegin', html.join(''));
    for (let index = renderedCount; index < next; index += 1) {
        const cell = els.grid.children[index];
        const img = cell?.querySelector('img');
        if (img) thumbObserver.observe(img);
    }
    renderedCount = next;
}

function rerenderGrid() {
    renderedCount = 0;
    for (const img of els.grid.querySelectorAll('img')) thumbObserver.unobserve(img);
    els.grid.querySelectorAll('.imps-cell').forEach((cell) => cell.remove());
    appendCells();
}

function setChecked(keys, value) {
    for (const key of keys) {
        if (value) checked.add(key); else checked.delete(key);
    }
    for (const cell of els.grid.querySelectorAll('.imps-cell')) {
        const isOn = checked.has(cell.dataset.key);
        cell.classList.toggle('on', isOn);
        cell.querySelector('.imps-check')?.setAttribute('aria-pressed', String(isOn));
    }
    syncCommit();
    syncDestination();
}

function onGridClick(event) {
    const cell = event.target.closest('.imps-cell');
    if (!cell) return;
    const visible = visibleEntries();
    const index = Number(cell.dataset.idx);
    if (event.shiftKey && anchorIndex != null) {
        const [from, to] = [Math.min(anchorIndex, index), Math.max(anchorIndex, index)];
        const value = checked.has(visible[anchorIndex]?.key);
        setChecked(visible.slice(from, to + 1).map((entry) => entry.key), value);
    } else {
        const key = cell.dataset.key;
        setChecked([key], !checked.has(key));
        anchorIndex = index;
    }
}

// ---------------------------------------------------------------- right rail

function syncDestination() {
    if (mode === 'add') {
        els.destination.innerHTML = `<div class="imps-dest-add">Photos stay where they are — <b>${esc(source?.label || 'this folder')}</b> is registered as a source and watched from now on.</div>`;
        return;
    }
    const staged = checkedEntries();
    const trees = new Map(); // tree -> Map(year -> Map(date -> count))
    for (const entry of staged) {
        const tree = CATEGORY_TREES[effectiveCategory(entry)] || 'RAWS';
        const date = String(entry.taken_at || '').slice(0, 10) || 'Unknown date';
        const year = date.slice(0, 4);
        const years = trees.get(tree) || new Map();
        const dates = years.get(year) || new Map();
        dates.set(date, (dates.get(date) || 0) + 1);
        years.set(year, dates);
        trees.set(tree, years);
    }
    if (!trees.size) {
        els.destination.innerHTML = '<div class="imps-dest-add">Nothing staged yet.</div>';
        return;
    }
    const lines = [];
    for (const [tree, years] of [...trees.entries()].sort()) {
        lines.push(`<div class="imps-dest-tree">${esc(tree)}</div>`);
        for (const [year, dates] of [...years.entries()].sort()) {
            lines.push(`<div class="imps-dest-year">${esc(year)}</div>`);
            for (const [date, count] of [...dates.entries()].sort()) {
                lines.push(`<div class="imps-dest-date"><span>${esc(date)}</span><span class="imps-dest-count">${fmt(count)}</span></div>`);
            }
        }
    }
    els.destination.innerHTML = lines.join('');
}

// ---------------------------------------------------------------- chrome sync

function syncModeSeg() {
    const isCard = source?.kind === 'card';
    els.modeCopy.classList.toggle('active', mode === 'copy');
    els.modeCopy.setAttribute('aria-pressed', String(mode === 'copy'));
    els.modeAdd.classList.toggle('active', mode === 'add');
    els.modeAdd.setAttribute('aria-pressed', String(mode === 'add'));
    els.modeAdd.disabled = isCard; // cards are forced Copy: never trust removable media as storage
    els.modeAdd.dataset.tip = isCard ? 'Cards must be copied before import' : 'Register in place without copying';
    els.clearCardRow.hidden = !isCard;
}

function syncFilterSeg() {
    const suspects = entries.filter((entry) => entry.suspect).length;
    els.filterNew.classList.toggle('active', filter === 'new');
    els.filterAll.classList.toggle('active', filter === 'all');
    els.filterNew.textContent = `New ${fmt(entries.length - suspects)}`;
    els.filterAll.textContent = `All ${fmt(entries.length)}`;
}

function syncStatus() {
    if (!source) {
        els.status.textContent = 'Choose a card or folder to stage photos.';
    } else if (scanStatus === 'scanning') {
        els.status.textContent = `Scanning ${source.label}… ${fmt(entries.length)} found`;
    } else if (scanStatus === 'error') {
        els.status.textContent = 'That source couldn’t be read.';
    } else {
        const suspects = entries.filter((entry) => entry.suspect).length;
        els.status.textContent = `${fmt(entries.length)} photos in ${source.label}`
            + (suspects ? ` · ${fmt(suspects)} already imported` : '');
    }
    els.empty.hidden = Boolean(entries.length) || scanStatus === 'scanning';
    els.scanbar.classList.toggle('busy', scanStatus === 'scanning');
}

function syncCommit() {
    const staged = checkedEntries();
    const bytes = staged.reduce((total, entry) => total + (Number(entry.size) || 0), 0);
    const skipped = entries.filter((entry) => entry.suspect && !checked.has(entry.key)).length;
    const byCategory = new Map();
    for (const entry of staged) {
        const category = effectiveCategory(entry);
        byCategory.set(category, (byCategory.get(category) || 0) + 1);
    }
    const breakdown = byCategory.size > 1
        ? ' — ' + [...byCategory.entries()].sort((a, b) => b[1] - a[1])
            .map(([category, count]) => `${fmt(count)} ${CATEGORY_SHORT[category] || category}`).join(' · ')
        : '';
    els.summary.innerHTML = staged.length
        ? `Import <b>${fmt(staged.length)}</b> photo${staged.length === 1 ? '' : 's'} (${fmtBytes(bytes)})${breakdown}`
            + (skipped ? ` · ${fmt(skipped)} duplicate${skipped === 1 ? '' : 's'} skipped` : '')
        : 'Nothing staged';
    if (els.category && !committing) {
        const dominant = [...byCategory.entries()].sort((a, b) => b[1] - a[1])[0];
        els.category.options[0].text = dominant && !categoryOverride
            ? `Auto — ${CATEGORY_TREES[dominant[0]] || 'RAWS'}` : 'Auto';
    }
    els.commit.disabled = !staged.length || committing || scanStatus === 'scanning';
    els.commit.textContent = committing ? 'Importing…' : (scanStatus === 'scanning' ? 'Scanning…' : 'Import');
}

function syncAll() {
    syncModeSeg();
    syncFilterSeg();
    syncStatus();
    syncCommit();
    syncDestination();
}

// ---------------------------------------------------------------- commit + job

async function commit() {
    if (committing) return;
    const staged = checkedEntries();
    if (!staged.length || !scanId) return;
    committing = true;
    syncCommit();
    const body = {
        scan_id: scanId,
        keys: staged.map((entry) => entry.key),
        mode,
        skip_suspects: skipSuspects,
        clear_card: source?.kind === 'card' && clearCard,
        category: categoryOverride || null,
        keywords: els.keywords.value.split(',').map((word) => word.trim()).filter(Boolean),
    };
    const result = await commitImportScan(body);
    committing = false;
    if (!result?.job_id) {
        syncCommit();
        showToast('Couldn’t start the import');
        return;
    }
    const label = source?.label || `Import ${result.batch_id}`;
    const clearingCard = body.clear_card;
    closeImport({ keepGeneration: true });
    setScope({ import_batch: String(result.batch_id), importBatchLabel: label, sort: 'date_taken' });
    switchLens('grid');
    showToast(`Importing ${fmt(staged.length)} photos…`);
    watchJob(result.job_id, clearingCard, result.batch_id);
}

async function watchJob(jobId, clearingCard, batchId) {
    let lastEta = null;
    for (;;) {
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
        const job = await getImportJob(jobId);
        if (!job) {
            showToast('Import status lost — this import may still be running');
            if (String(scope.import_batch || '') === String(batchId || '')) {
                setScope({ import_batch: '', importBatchLabel: '' });
            }
            return;
        }
        if (clearingCard && job.card_free_eta_seconds != null && job.card_free_eta_seconds !== lastEta
            && job.phase === 'copying' && job.files_done > 0 && job.files_done % 250 === 0) {
            lastEta = job.card_free_eta_seconds;
            showToast(`Card free in ${fmtEta(job.card_free_eta_seconds)}`);
        }
        if (job.phase === 'complete' || job.phase === 'cancelled' || job.phase === 'failed') {
            emit('import:changed', { batch_id: job.batch_id });
            // Batch links land at completion; re-emit the scope so the waiting grid fills.
            if (String(scope.import_batch || '') === String(job.batch_id)) {
                setScope({ import_batch: String(job.batch_id) });
            }
            if (job.phase === 'complete') {
                const errors = job.errors?.length || 0;
                showToast(clearingCard
                    ? '✓ Card empty — safe to eject'
                    : `Imported ${fmt(job.files_done - (job.skipped_duplicates || 0) - errors)} photos`
                        + (job.skipped_duplicates ? ` · ${fmt(job.skipped_duplicates)} duplicates skipped` : ''));
                if (errors) showToast(`${fmt(errors)} file${errors === 1 ? '' : 's'} couldn’t be imported`);
            } else if (job.phase === 'failed') {
                showToast('Import failed — nothing was deleted');
            }
            return;
        }
    }
}

// ---------------------------------------------------------------- open/close

export function openImport() {
    if (mounted) return;
    mounted = true;
    generation += 1;
    root.classList.add('active');
    document.body.classList.add('import-stage-active');
    source = null;
    scanId = null;
    scanStatus = 'idle';
    entries = [];
    checked = new Set();
    expandedDirs = new Map();
    renderedCount = 0;
    els.grid.querySelectorAll('.imps-cell').forEach((cell) => cell.remove());
    els.keywords.value = '';
    categoryOverride = '';
    if (els.category) els.category.value = '';
    syncAll();
    loadSources();
}

export function closeImport(options = {}) {
    if (!mounted) return;
    mounted = false;
    if (!options.keepGeneration) generation += 1; // abandons scan pollers; server scan is just RAM
    root.classList.remove('active');
    document.body.classList.remove('import-stage-active');
}

export function importOpen() {
    return mounted;
}

// ---------------------------------------------------------------- init

export function initImportStage() {
    root = document.getElementById('view-import');
    if (!root) return;
    els = {
        sources: root.querySelector('#imps-sources'),
        subfolders: root.querySelector('#imps-subfolders'),
        modeCopy: root.querySelector('#imps-mode-copy'),
        modeAdd: root.querySelector('#imps-mode-add'),
        filterNew: root.querySelector('#imps-filter-new'),
        filterAll: root.querySelector('#imps-filter-all'),
        checkAll: root.querySelector('#imps-check-all'),
        uncheckAll: root.querySelector('#imps-uncheck-all'),
        size: root.querySelector('#imps-size'),
        status: root.querySelector('#imps-status'),
        scanbar: root.querySelector('#imps-scanbar'),
        scroll: root.querySelector('#imps-scroll'),
        grid: root.querySelector('#imps-grid'),
        sentinel: root.querySelector('#imps-sentinel'),
        empty: root.querySelector('#imps-empty'),
        skipSuspects: root.querySelector('#imps-skip-suspects'),
        clearCard: root.querySelector('#imps-clear-card'),
        clearCardRow: root.querySelector('#imps-clear-card-row'),
        keywords: root.querySelector('#imps-keywords'),
        destination: root.querySelector('#imps-destination'),
        summary: root.querySelector('#imps-summary'),
        category: root.querySelector('#imps-category'),
        commit: root.querySelector('#imps-commit'),
    };
    els.size.value = String(thumbPx);
    root.style.setProperty('--imps-thumb', `${thumbPx}px`);

    thumbObserver = new IntersectionObserver((observations) => {
        for (const observation of observations) {
            if (!observation.isIntersecting) continue;
            const img = observation.target;
            if (img.dataset.src) { img.src = img.dataset.src; delete img.dataset.src; }
            img.addEventListener('load', () => img.classList.add('ld'), { once: true });
            thumbObserver.unobserve(img);
        }
    }, { root: els.scroll, rootMargin: '600px' });
    sentinelObserver = new IntersectionObserver((observations) => {
        if (observations.some((observation) => observation.isIntersecting)) appendCells();
    }, { root: els.scroll, rootMargin: '900px' });
    sentinelObserver.observe(els.sentinel);

    root.querySelector('#imps-close').addEventListener('click', () => closeImport());
    els.sources.addEventListener('click', (event) => {
        const twist = event.target.closest('[data-twist]');
        if (twist) { event.stopPropagation(); toggleDir(twist.dataset.twist); return; }
        const row = event.target.closest('.imps-source');
        if (!row) return;
        const path = row.dataset.path;
        const known = sources.find((candidate) => candidate.path === path);
        selectSource(known || { id: `root:${path}`, kind: 'root', label: row.dataset.label || path.split('/').pop() || path, path });
    });
    els.subfolders.addEventListener('change', () => {
        includeSubfolders = els.subfolders.checked;
        if (source) startScan();
    });
    els.modeCopy.addEventListener('click', () => { mode = 'copy'; syncModeSeg(); syncDestination(); });
    els.modeAdd.addEventListener('click', () => {
        if (source?.kind === 'card') return;
        mode = 'add';
        syncModeSeg();
        syncDestination();
    });
    els.filterNew.addEventListener('click', () => { filter = 'new'; anchorIndex = null; syncFilterSeg(); rerenderGrid(); });
    els.filterAll.addEventListener('click', () => { filter = 'all'; anchorIndex = null; syncFilterSeg(); rerenderGrid(); });
    els.checkAll.addEventListener('click', () => setChecked(visibleEntries().map((entry) => entry.key), true));
    els.uncheckAll.addEventListener('click', () => setChecked(visibleEntries().map((entry) => entry.key), false));
    els.size.addEventListener('input', () => {
        thumbPx = Number(els.size.value);
        localStorage.setItem('importThumbPx', String(thumbPx));
        root.style.setProperty('--imps-thumb', `${thumbPx}px`);
    });
    els.skipSuspects.addEventListener('change', () => { skipSuspects = els.skipSuspects.checked; });
    els.clearCard.addEventListener('change', () => { clearCard = els.clearCard.checked; });
    els.grid.addEventListener('click', onGridClick);
    els.category.addEventListener('change', () => {
        categoryOverride = els.category.value;
        syncCommit();
        syncDestination();
    });
    els.commit.addEventListener('click', commit);

    document.getElementById('import-view')?.addEventListener('click', openImport);
    on('import:open', openImport);
    emit('import:ready');
}
