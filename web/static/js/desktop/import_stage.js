// Staged import — the Lightroom-Classic-grade Import canvas (IMPORT_SPEC v1).
// Full-screen takeover following the Develop precedent: Esc parks the stage —
// the scan keeps collecting in the background, a peek chip in the main chrome
// shows it live, and reopening resumes exactly where the user was. Zero writes
// until commit; the stage only clears on commit or picking a new source.
import {
    commitImportScan, getImportJob, getImportScan, getImportSources,
    browseImportPath, startImportScan, uploadFilmScans,
} from './api.js';
import { emit, on, scope, setScope } from './state.js';
import { switchLens } from './lenses.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { escapeHtml as esc, formatCount as fmt } from './dom.js';

const CATEGORY_TREES = { raw: 'Raws', personal: 'Snapshots', film: 'Raws/Film Scans', export: 'Edits', video: 'Video' };
const CATEGORY_SHORT = { raw: 'RAW', personal: 'personal', film: 'film', export: 'exports', video: 'video' };
const SCAN_POLL_MS = 1000;
const JOB_POLL_MS = 1000;
const JOB_POLL_MAX_MS = 15_000;
const JOB_POLL_WARN_MISSES = 3;
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
let mode = 'move';
let skipSuspects = true;
let clearCard = false;
let thumbPx = Number(localStorage.getItem('importThumbPx')) || 148;
let categoryOverride = '';            // '' = the app decides (per-file EXIF/source diagnosis)
let committing = false;
let validating = false;         // resume revalidation in flight: hold commit until the stage is proven alive

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

// Cards and staged folders default to Move — a photographer pulling from a
// card wants the card drained, not duplicated — and the last choice is
// remembered per source kind (the importThumbPx precedent).
const MODES = ['move', 'copy', 'add'];

function rememberedMode(kind) {
    if (kind === 'film') return 'copy';
    const saved = localStorage.getItem(`importMode.${kind}`);
    return MODES.includes(saved) ? saved : 'move';
}

function setMode(next) {
    mode = next;
    if (source && source.kind !== 'film') localStorage.setItem(`importMode.${source.kind}`, next);
    syncModeSeg();
    syncCommit();
    syncDestination();
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
    return `<button class="imps-source ${active ? 'active' : ''}" data-path="${esc(row.path)}" data-dir="1" data-lib="${row.library ? '1' : ''}">`
        + `${icon('folder')}<span class="imps-source-text"><b>${esc(row.label)}</b></span>`
        + `<span class="imps-twist ${expandedDirs.has(row.path) ? 'open' : ''}" data-twist="${esc(row.path)}"></span></button>`
        + dirChildrenHtml(row.path, 1, Boolean(row.library));
}

function dirChildrenHtml(path, depth, library) {
    const children = expandedDirs.get(path);
    if (!children) return '';
    if (!children.length) return `<div class="imps-dir-empty" style="--depth:${depth}">No subfolders</div>`;
    return children.map((dir) => {
        const active = source && source.path === dir.path;
        return `<button class="imps-source imps-dir ${active ? 'active' : ''}" style="--depth:${depth}" data-path="${esc(dir.path)}" data-dir="1" data-label="${esc(dir.name)}" data-lib="${library ? '1' : ''}">`
            + `${icon('folder')}<span class="imps-source-text"><b>${esc(dir.name)}</b>`
            + (dir.file_count ? `<small>${fmt(dir.file_count)} files</small>` : '') + '</span>'
            + `<span class="imps-twist ${expandedDirs.has(dir.path) ? 'open' : ''}" data-twist="${esc(dir.path)}"></span></button>`
            + dirChildrenHtml(dir.path, depth + 1, library);
    }).join('');
}

function renderSources() {
    const cards = sources.filter((row) => row.kind === 'card');
    const roots = sources.filter((row) => row.kind === 'root');
    els.sources.innerHTML = (cards.length
        ? `<h3>Cards</h3>${cards.map(sourceRowHtml).join('')}`
        : '<h3>Cards</h3><div class="imps-rail-empty">No card detected</div>')
        + `<h3>Folders</h3>${roots.map(sourceRowHtml).join('')}`
        // One Import entry in the chrome; film scans stay reachable as a source here.
        + `<h3>Film scans</h3><button class="imps-source ${source?.kind === 'film' ? 'active' : ''}" data-film="1">`
        + `${icon('archive')}<span class="imps-source-text"><b>${source?.kind === 'film' ? esc(source.label) : 'Choose ZIP or TIFF…'}</b></span></button>`;
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
    mode = rememberedMode(row.kind === 'card' ? 'card' : 'root');
    categoryOverride = '';
    if (els.category) els.category.value = '';
    renderSources();
    syncModeSeg();
    startScan();
}

// Film scans arrive as lab ZIPs or loose TIFFs picked in the OS dialog: upload,
// extract server-side, then stage the extraction exactly like any other source.
// Destination is Raws/Film Scans/<archive name>/ — scan dates are not shoot dates.
async function startFilmImport(files) {
    openImport();
    resetStage();
    const token = generation;
    source = { id: 'film:uploading', kind: 'film', label: 'film scans', path: '' };
    mode = 'copy';
    scanStatus = 'scanning';
    loadSources(); // repopulate the rail; the card fast-path yields to the film source
    syncAll();
    const form = new FormData();
    for (const file of files) form.append('files', file);
    let staged = null;
    try {
        staged = await uploadFilmScans(form);
    } catch (error) {
        if (token !== generation) return;
        scanStatus = 'error';
        syncAll();
        showToast(error?.message || 'Couldn’t read those scans');
        return;
    }
    if (token !== generation) return;
    if (!staged?.scan_id) {
        scanStatus = 'error';
        syncAll();
        showToast('Couldn’t stage those scans');
        return;
    }
    source = { id: `film:${staged.path}`, kind: 'film', label: staged.label, path: staged.path };
    scanId = staged.scan_id;
    syncAll();
    if (staged.skipped?.length) {
        showToast(`${fmt(staged.skipped.length)} file${staged.skipped.length === 1 ? '' : 's'} skipped — ${staged.skipped[0].reason}`);
    }
    pollScan(token);
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
    const result = await startImportScan(source.path, includeSubfolders).catch(() => null);
    if (token !== generation) return; // parking mid-start must not orphan the scan
    if (!result?.scan_id) {
        scanStatus = 'error';
        syncAll();
        showToast('Couldn’t read that source');
        return;
    }
    scanId = result.scan_id;
    pollScan(token);
}

// fetchJson throws on non-OK; a dead scan (server restart, expired stage) must
// read as null here, not as an unhandled rejection that kills the poll loop.
async function fetchScanPage(offset) {
    try {
        return await getImportScan(scanId, offset);
    } catch {
        return null;
    }
}

async function pollScan(token) {
    while (token === generation) { // survives close: the stage keeps filling while parked
        const page = await fetchScanPage(entries.length);
        if (token !== generation) return;
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

function fileExt(name) {
    const dot = String(name || '').lastIndexOf('.');
    return dot > 0 ? name.slice(dot + 1).toUpperCase() : 'VIDEO';
}

function cellHtml(entry, index) {
    const isChecked = checked.has(entry.key);
    // Videos are custody-only (imported, stored, backed up — no playback):
    // a deliberate filmstrip tile, never a decoded frame or a fake thumbnail.
    const media = entry.kind === 'video'
        ? `<span class="imps-video" role="img" aria-label="${esc(entry.name)}">${icon('film')}<b>${esc(fileExt(entry.name))}</b></span>`
        : `<img data-src="/api/import/scan/${esc(scanId)}/thumb/${esc(entry.key)}" loading="lazy" decoding="async" alt="${esc(entry.name)}">`;
    return `<figure class="imps-cell ${entry.suspect ? 'suspect' : ''} ${isChecked ? 'on' : ''}" data-key="${esc(entry.key)}" data-idx="${index}" tabindex="-1">`
        + `<span class="imps-thumb">${media}`
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
    if (source?.kind === 'film') {
        // The truth for film: one folder per archive/batch, never date-guessed.
        const staged = checkedEntries();
        if (!staged.length) {
            els.destination.innerHTML = '<div class="imps-dest-add">Nothing staged yet.</div>';
            return;
        }
        const folders = new Map();
        for (const entry of staged) {
            const parts = String(entry.rel_path || '').split('/');
            const folder = parts.length > 1 ? parts[0] : (source.label || 'Film scans');
            folders.set(folder, (folders.get(folder) || 0) + 1);
        }
        const lines = ['<div class="imps-dest-tree">Raws/Film Scans</div>'];
        for (const [folder, count] of [...folders.entries()].sort()) {
            lines.push(`<div class="imps-dest-date"><span>${esc(folder)}</span><span class="imps-dest-count">${fmt(count)}</span></div>`);
        }
        els.destination.innerHTML = lines.join('');
        return;
    }
    const staged = checkedEntries();
    const trees = new Map(); // tree -> Map(year -> Map(date -> count))
    for (const entry of staged) {
        const tree = CATEGORY_TREES[effectiveCategory(entry)] || 'Raws';
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
    // Move from inside the library would relocate catalog data, not drain a
    // card: the server keeps the sources, so say so up front.
    const lines = mode === 'move' && source?.library
        ? ['<div class="imps-dest-add">Already in your library space — copying.</div>'] : [];
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
    const isFilm = source?.kind === 'film';
    for (const [button, name] of [[els.modeMove, 'move'], [els.modeCopy, 'copy'], [els.modeAdd, 'add']]) {
        button.classList.toggle('active', mode === name);
        button.setAttribute('aria-pressed', String(mode === name));
    }
    els.modeMove.disabled = isFilm; // film staging is server-side spill, forced Copy
    els.modeMove.dataset.tip = source?.library
        ? 'Already in your library space — copying'
        : 'Copy into the library, verify every byte, then clear the source';
    els.modeAdd.disabled = isCard || isFilm; // cards must land in the library first
    els.modeAdd.dataset.tip = isCard ? 'Cards must be copied or moved before import'
        : (isFilm ? 'Film scans are copied into the library' : 'Register in place without copying');
    els.clearCardRow.hidden = !isCard || mode !== 'copy'; // Move already drains the card
    // Film destination is the archive folder — a category override would lie.
    if (els.category) els.category.disabled = isFilm;
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
        els.status.textContent = 'Choose a card, folder, or film scans to stage photos.';
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
    const verb = mode === 'move' && !source?.library ? 'Move' : 'Import';
    els.summary.innerHTML = staged.length
        ? `${verb} <b>${fmt(staged.length)}</b> photo${staged.length === 1 ? '' : 's'} (${fmtBytes(bytes)})${breakdown}`
            + (skipped ? ` · ${fmt(skipped)} duplicate${skipped === 1 ? '' : 's'} skipped` : '')
        : 'Nothing staged';
    if (els.category && !committing) {
        const dominant = [...byCategory.entries()].sort((a, b) => b[1] - a[1])[0];
        els.category.options[0].text = dominant && !categoryOverride
            ? `Auto — ${CATEGORY_TREES[dominant[0]] || 'Raws'}` : 'Auto';
    }
    // "Ask me when ambiguous": the override only surfaces when detection could
    // not settle a staged file (source_kind unknown). The answer applies to the
    // whole source and is remembered server-side, so it never asks again.
    if (els.asWrap) {
        const unclear = staged.filter((entry) => entry.source_kind === 'unknown').length;
        els.asWrap.hidden = source?.kind === 'film' || (!unclear && !categoryOverride);
        els.asText.textContent = unclear ? `${fmt(unclear)} unclear — file source as` : 'file source as';
    }
    els.commit.disabled = !staged.length || committing || validating
        || scanStatus === 'scanning' || scanStatus === 'error'; // server rejects non-done scans
    els.commit.textContent = committing ? 'Importing…' : (scanStatus === 'scanning' ? 'Scanning…' : 'Import');
}

// The peek chip: while a stage is parked, the main chrome shows it live.
function syncPeek() {
    if (!els.peek) return;
    const show = !mounted && stageAlive();
    els.peek.hidden = !show;
    if (!show) return;
    els.peek.classList.toggle('busy', scanStatus === 'scanning');
    els.peekText.textContent = scanStatus === 'scanning'
        ? `Import — scanning ${source.label}… ${fmt(entries.length)}`
        : (scanStatus === 'error'
            ? `Import — ${source.label} couldn’t be read`
            : `Import — ${fmt(checked.size)} staged in ${source.label}`);
}

function syncAll() {
    syncModeSeg();
    syncFilterSeg();
    syncStatus();
    syncCommit();
    syncDestination();
    syncPeek();
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
        clear_card: source?.kind === 'card' && mode === 'copy' && clearCard,
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
    const clearingCard = body.clear_card || (mode === 'move' && source?.kind === 'card');
    closeImport();
    resetStage();
    setScope({ import_batch: String(result.batch_id), importBatchLabel: label, sort: 'date_taken' });
    switchLens('grid');
    showToast(`Importing ${fmt(staged.length)} photos…`);
    watchJob(result.job_id, clearingCard, result.batch_id);
}

// fetchJson throws on non-OK; a failed poll must read as a retryable miss
// here, not as an unhandled rejection that kills the watcher (the
// fetchScanPage precedent). A 404 is decisive — jobs live in server RAM, so
// a missing id means the registry is gone and status can never come back.
async function fetchJob(jobId) {
    try {
        return { job: await getImportJob(jobId), transient: false };
    } catch (error) {
        return { job: null, transient: error?.status !== 404 };
    }
}

async function watchJob(jobId, clearingCard, batchId) {
    let lastEta = null;
    let misses = 0;
    for (;;) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(JOB_POLL_MS * 2 ** misses, JOB_POLL_MAX_MS)));
        const { job, transient } = await fetchJob(jobId);
        if (!job && transient) {
            // The import keeps running server-side: back off and keep watching
            // so completion/failure/safe-to-eject always eventually render.
            misses += 1;
            if (misses === JOB_POLL_WARN_MISSES) showToast('Can’t reach the library — still watching this import');
            continue;
        }
        if (!job) {
            showToast('Import status lost — this import may still be running');
            if (String(scope.import_batch || '') === String(batchId || '')) {
                setScope({ import_batch: '', importBatchLabel: '' });
            }
            return;
        }
        misses = 0;
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

function stageAlive() {
    return Boolean(source && (entries.length || scanStatus === 'scanning'));
}

function resetStage() {
    generation += 1; // abandons scan pollers; server scan is just RAM
    source = null;
    scanId = null;
    scanStatus = 'idle';
    entries = [];
    checked = new Set();
    insertedEntryKeys = new Set();
    expandedDirs = new Map();
    anchorIndex = null;
    renderedCount = 0;
    els.grid.querySelectorAll('.imps-cell').forEach((cell) => cell.remove());
    els.keywords.value = '';
    categoryOverride = '';
    if (els.category) els.category.value = '';
    syncPeek();
}

// A parked done-scan can outlive the server's in-RAM stage; confirm it still
// answers before letting the user commit against a ghost.
async function revalidateStage() {
    if (!scanId || scanStatus === 'scanning') return;
    const token = generation;
    validating = true;
    syncCommit();
    const page = await fetchScanPage(entries.length);
    validating = false;
    if (!mounted || token !== generation) return;
    if (!page) {
        showToast('That staged import expired — rescanning');
        startScan();
        return;
    }
    syncCommit();
}

export function openImport() {
    if (mounted) return;
    mounted = true;
    root.classList.add('active');
    document.body.classList.add('import-stage-active');
    if (stageAlive()) {
        renderSources();
        syncAll();
        revalidateStage();
        return;
    }
    resetStage();
    syncAll();
    loadSources();
}

export function closeImport() {
    if (!mounted) return;
    mounted = false;
    root.classList.remove('active');
    document.body.classList.remove('import-stage-active');
    syncPeek();
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
        modeMove: root.querySelector('#imps-mode-move'),
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
        asWrap: root.querySelector('#imps-as'),
        asText: root.querySelector('#imps-as-text'),
        category: root.querySelector('#imps-category'),
        commit: root.querySelector('#imps-commit'),
        peek: document.getElementById('import-peek'),
        peekText: document.getElementById('import-peek-text'),
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

    const filmInput = document.getElementById('import-film-file');
    const pickFilmFiles = () => filmInput?.click();
    root.querySelector('#imps-close').addEventListener('click', () => closeImport());
    els.sources.addEventListener('click', (event) => {
        const twist = event.target.closest('[data-twist]');
        if (twist) { event.stopPropagation(); toggleDir(twist.dataset.twist); return; }
        const row = event.target.closest('.imps-source');
        if (!row) return;
        if (row.dataset.film) { pickFilmFiles(); return; }
        const path = row.dataset.path;
        const known = sources.find((candidate) => candidate.path === path);
        selectSource(known || {
            id: `root:${path}`, kind: 'root', label: row.dataset.label || path.split('/').pop() || path,
            path, library: Boolean(row.dataset.lib),
        });
    });
    els.subfolders.addEventListener('change', () => {
        includeSubfolders = els.subfolders.checked;
        if (source) startScan();
    });
    els.modeMove.addEventListener('click', () => {
        if (source?.kind === 'film') return;
        setMode('move');
    });
    els.modeCopy.addEventListener('click', () => setMode('copy'));
    els.modeAdd.addEventListener('click', () => {
        if (source?.kind === 'card' || source?.kind === 'film') return;
        setMode('add');
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

    els.peek?.addEventListener('click', openImport);
    document.getElementById('import-view')?.addEventListener('click', openImport);
    filmInput?.addEventListener('change', () => {
        const files = [...(filmInput.files || [])];
        filmInput.value = ''; // picking the same archive again must re-fire change
        if (files.length) startFilmImport(files);
    });
    on('import:open', openImport);
    on('import:film', pickFilmFiles);
    emit('import:ready');
}
