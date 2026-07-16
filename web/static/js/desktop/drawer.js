import {
    addCatalogSource, applyRemoteAccessServe, clearCache, connectToHub, createDeviceLink, discoverHubs,
    getAiStatus, getCacheStatus, getCaptionStatus, getCatalog, getMetadataStatus, getPairStatus,
    getFreeable, getFreeUpJob, getPeopleStatus, getRemoteAccess, getScanStatus, getSettings, getSyncStatus, getVersion,
    installAiModel, listDevices,
    pauseAiEmbeddings,
    pauseCaptionScan, pausePeopleScan, removeCatalogSource, rescanCatalogSource, resumeAiEmbeddings,
    resumeCaptionScan, resumePeopleScan, revokeDevice, saveSettings, startCachePregen, startMetadataScan, stopCachePregen,
    stopMetadataScan, startFreeUpSpace, cancelFreeUpJob, getStorageOverview, revealFolder,
} from './api.js';
import {
    emit, on, patchPrefs, scope, setActiveLens, setThumbSize, viewState,
} from './state.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { afterMotion } from './motion.js';
import { showToast } from './toast.js';
import { confirmTypedCount } from './trash.js';
import {
    bindSourcePicker, clearSourcePickerSelection, renderSourceAddUi, setSourceAddError,
} from './source_picker.js';
import {
    bindLibraryHealth, refreshLibraryHealth, renderLibraryHealth, stopLibraryHealthPolling,
} from './library_health.js';
import { openSourceRevealMenu } from './source_reveal_menu.js';
import { INACTIVE_WORKER_STATES, normalizeWorkerState } from '../worker_state.js';

let open = false;
let drawerTimer = null;
let activityTimer = null;
let installTimer = null;
let scanTimer = null;
let scanSourceId = null;
let installTimerGeneration = 0;
let scanTimerGeneration = 0;
let catalog = null;
let storageOverview = null;
let aiStatus = null;
let cacheStatus = null;
let peopleStatus = null;
let captionStatus = null;
let metadataStatus = null;
let remoteAccess = null;
let pairStatus = null;
let syncStatus = null;
let freeableStatus = null;
let freeupJob = null;
let freeupOlderDays = 30;
let freeupTimer = null;
let devicesPayload = null;
let linkSession = null;
let discoverPayload = null;
let settingsPageData = null;
let versionData = null;
let savedSettings = {};
let openSettingSections = new Set();
let publishReturn = null;
let publishingFocusPending = false;
let thumbnailCachePolicy = 'keep';
let systemSurfaceRender = null;
let pendingModelPreset = null;
const settingTimers = new Map();
const busyActions = new Set();
const workerActionGenerations = new Map();
const workerActionsInFlight = new Set();
let sourceAddFlow = null;
let sourceAddFlowReturn = null;
// Shared contract: const INACTIVE_WORKER_STATES = new Set(['idle', 'ready', 'paused', 'complete', 'caught_up', 'error', 'disabled', 'unavailable', 'stale']);
const SETTING_DEFS = {
    embed_model_preset: { type: 'select' },
    memory_cache_gb: { type: 'number', min: 0, max: 64, step: 0.25, unit: 'GB' },
    ssd_cache_gb: { type: 'number', min: 0, max: 4096, step: 1, unit: 'GB' },
    cache_profile: { type: 'select' },
    ssd_cache_dir: { type: 'text' },
    search_similarity_threshold: { type: 'number', min: 0.1, max: 0.8, step: 0.05 },
    refine_semantic_pairing: { type: 'checkbox' },
    show_loupe_cache_status: { type: 'checkbox' },
    import_root: { type: 'text' },
    thumb_size_sm: { type: 'number', min: 64, max: 4096, step: 1, unit: 'px' },
    thumb_size_md: { type: 'number', min: 128, max: 8192, step: 1, unit: 'px' },
    thumb_size_lg: { type: 'number', min: 128, max: 8192, step: 1, unit: 'px' },
    thumb_quality: { type: 'number', min: 40, max: 100, step: 1, unit: '%' },
    face_model_id: { type: 'text' },
    face_model_dir: { type: 'text' },
    face_detection_size: { type: 'number', min: 160, max: 1280, step: 32, unit: 'px' },
    face_similarity_threshold: { type: 'number', min: 0.1, max: 0.9, step: 0.01 },
    face_merge_suggestion_threshold: { type: 'number', min: 0.1, max: 0.95, step: 0.01 },
    people_scan_enabled: { type: 'checkbox' },
    people_auto_install: { type: 'checkbox' },
    caption_scan_enabled: { type: 'checkbox' },
    caption_model_preset: { type: 'select' },
    caption_batch_size: { type: 'number', min: 1, max: 4, step: 1 },
    publish_dir: { type: 'text' },
    publish_site_base_url: { type: 'text' },
    share_brand_name: { type: 'text' },
    require_device_token: { type: 'checkbox' },
};

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function bytes(value) {
    const n = Number(value) || 0;
    if (!n) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = n;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function pct(value) {
    return Math.max(0, Math.min(100, Number(value) || 0));
}

function progress(done, total) {
    const d = Number(done) || 0;
    const t = Number(total) || 0;
    return t > 0 ? pct((d / t) * 100) : 0;
}

function workerStateIsActive(status) {
    const worker = (status && status.worker) || {};
    const index = (status && status.embedding_index) || {};
    const state = normalizeWorkerState(index.worker_state || (status && status.worker_state) || worker.state);
    return Boolean(state) && !INACTIVE_WORKER_STATES.has(state);
}

function cachePregenStateIsActive(status) {
    const pregen = (status && status.pregen) || {};
    return !pregen.manual_pause && workerStateIsActive({ worker: pregen });
}

function metadataStateIsActive(status) {
    return !status?.manual_pause && workerStateIsActive(status);
}


function applySettingsData(data) {
    if (!data) return;
    settingsPageData = data;
    savedSettings = { ...(data.settings || {}) };
    aiStatus = data.ai_status || aiStatus;
    cacheStatus = data.cache_stats || cacheStatus;
    peopleStatus = data.people_status || peopleStatus;
    metadataStatus = data.metadata_status || metadataStatus;
    catalog = data.catalog || catalog;
}

function settingValue(field) {
    if (field === 'embed_model_preset' && pendingModelPreset != null) return pendingModelPreset;
    if (Object.prototype.hasOwnProperty.call(savedSettings, field)) return savedSettings[field];
    const defaults = settingsPageData?.defaults || {};
    if (Object.prototype.hasOwnProperty.call(defaults, field)) return defaults[field];
    return '';
}

function normalizeSettingValue(field, value) {
    const def = SETTING_DEFS[field] || {};
    if (def.type === 'checkbox') return Boolean(value);
    if (def.type === 'number') {
        const n = Number(value);
        if (!Number.isFinite(n)) return 0;
        return n;
    }
    return String(value == null ? '' : value);
}

async function applySetting(field, value, { patch = null, undoPatch = null, control = null } = {}) {
    const next = normalizeSettingValue(field, value);
    const previous = savedSettings[field];
    const payload = patch || { [field]: next };
    const result = await saveSettings(payload);
    if (!result?.ok) {
        showToast('Couldn’t save setting');
        return false;
    }
    applySettingsData(result);
    showToast('Setting saved', {
        undo: async () => {
            const rollback = undoPatch || (field === 'caption_model_preset'
                ? captionPresetConfig(previous)
                : { [field]: previous });
            const undone = await saveSettings(rollback);
            if (!undone?.ok) throw new Error('undo failed');
            applySettingsData(undone);
            renderCurrentSystemSurface();
            showToast('Setting restored');
        },
    });
    renderActivity();
    if (control && document.activeElement === control) patchSettingSurface(field);
    else renderCurrentSystemSurface();
    if (field === 'publish_dir' && publishReturn && String(next).trim()) {
        showToast('Publishing folder saved');
        returnToPublish();
    }
    return true;
}

function applyPreference(patch) {
    const previous = Object.fromEntries(Object.keys(patch).map((key) => [key, viewState.prefs[key]]));
    patchPrefs(patch);
    showToast('Setting saved', { undo: () => patchPrefs(previous) });
}

function embeddingPresetConfig(key = settingValue('embed_model_preset')) {
    const preset = (settingsPageData && settingsPageData.embedding_model_presets || [])
        .find((item) => item.key === key);
    if (!preset) return { embed_model_preset: key || '' };
    return {
        embed_model_preset: preset.key,
        embed_model_id: preset.model_id,
        embed_model_revision: preset.revision || 'main',
        embed_model_dir: preset.model_dir,
        embed_model_dim: Number(preset.dimension || 0),
    };
}

function captionPresetConfig(key = settingValue('caption_model_preset')) {
    const preset = (settingsPageData && settingsPageData.caption_model_presets || [])
        .find((item) => item.key === key);
    if (!preset) return { caption_model_preset: key || '' };
    return {
        caption_model_preset: preset.key,
        caption_model_id: preset.model_id,
        caption_model_revision: preset.revision || 'main',
        caption_model_dir: preset.model_dir,
        caption_model_quantization: preset.quantization || 'bnb-4bit',
        caption_prompt_version: preset.prompt_version || 'caption-json-v1',
    };
}

function collectModelSettings() {
    return embeddingPresetConfig(settingValue('embed_model_preset'));
}

function collectCaptionSettings() {
    return captionPresetConfig(settingValue('caption_model_preset'));
}

function recommendedMemoryGb(settings = {}) {
    const systemRam = Number(settings.system_memory_gb || 0);
    if (systemRam >= 32) return 4;
    if (systemRam >= 16) return 2;
    if (systemRam >= 8) return 1;
    return 0.5;
}

function peopleProgress(status) {
    const worker = (status && status.worker) || {};
    if (worker.progress_pct != null) return pct(worker.progress_pct);
    const counts = (status && status.counts) || {};
    const scanned = Object.values(counts.scan || {})
        .reduce((total, count) => total + (Number(count) || 0), 0);
    const pending = Number(counts.pending_cached_images) || 0;
    const countProgress = progress(scanned, scanned + pending);
    return workerStateIsActive(status) ? Math.max(5, countProgress) : countProgress;
}

function captionProgress(status) {
    const worker = status?.worker || {};
    const counts = status?.counts || {};
    return worker.progress_pct != null ? pct(worker.progress_pct) : progress(counts.captioned || 0, (counts.captioned || 0) + (counts.pending_cached_images || 0));
}

function activeProgress() {
    const ai = pct(aiStatus && aiStatus.progress_pct);
    const pregen = cacheStatus && cacheStatus.pregen ? cacheStatus.pregen : {};
    const cacheProgress = pct((pregen.preview && pregen.preview.progress_pct) || pregen.progress_pct);
    const cache = cachePregenStateIsActive(cacheStatus) && cacheProgress <= 0 ? 50 : cacheProgress;
    const people = peopleProgress(peopleStatus);
    const caption = captionProgress(captionStatus);
    const metadata = metadataStateIsActive(metadataStatus) ? 50 : 0;
    return { ai, cache, people, captions: caption, metadata };
}

function modelStateLine(status = aiStatus || {}) {
    const index = status.embedding_index || {};
    const state = String(index.worker_state || status.worker_state || '').replace(/_/g, ' ');
    if (index.installing || status.installing) return `installing${index.install_message || status.install_message ? ` · ${index.install_message || status.install_message}` : ''}`;
    if (state === 'loading model' || state === 'loading') return `loading${index.worker_message || status.worker_message ? ` · ${index.worker_message || status.worker_message}` : ''}`;
    if (state === 'error' || status.worker_error) return `error${status.worker_error ? ` · ${status.worker_error}` : ''}`;
    if (index.installed || status.model_installed) {
        const remaining = Number(index.remaining ?? status.remaining ?? 0);
        if (remaining <= 0) return 'ready';
        return `${state || 'indexing'} · ${fmt(remaining)} remaining`;
    }
    return 'not installed';
}

function modelLine() {
    const status = aiStatus || {};
    const index = status.embedding_index || {};
    const modelId = index.model_id || status.model_id || settingValue('embed_model_preset') || 'No model selected';
    const dim = Number(index.dimension || status.model_dimension || 0);
    return `${modelId}${dim ? ` · ${fmt(dim)}d` : ''} · ${modelStateLine(status)}`;
}

function cacheUsageLine() {
    if (cacheStatus && (cacheStatus.status_stale || cacheStatus.counts_stale)) {
        return `Cache status is stale${cacheStatus.latency_ms ? ` · last check timed out after ${fmt(cacheStatus.latency_ms)} ms` : ''}`;
    }
    const memory = (cacheStatus && cacheStatus.memory) || {};
    const disk = (cacheStatus && cacheStatus.disk) || {};
    const pregen = (cacheStatus && cacheStatus.pregen) || {};
    return `RAM ${bytes(memory.used_bytes)} / ${bytes(memory.limit_bytes)} · SSD ${bytes(disk.used_bytes)} / ${bytes(disk.limit_bytes)} · ${pregen.state || 'idle'}`;
}

function peopleLine() {
    const worker = (peopleStatus && peopleStatus.worker) || {};
    const counts = (peopleStatus && peopleStatus.counts) || {};
    const pending = Number(counts.pending_cached_images || 0);
    const faces = Number(counts.detected_faces || counts.people || 0);
    const enabled = settingValue('people_scan_enabled');
    return `${enabled ? 'enabled' : 'disabled'} · ${fmt(faces)} faces · ${pending ? `${fmt(pending)} pending · ` : ''}${worker.state || 'idle'}`;
}

function captionLine() {
    const worker = (captionStatus && captionStatus.worker) || {};
    const counts = (captionStatus && captionStatus.counts) || {};
    const captioned = Number(counts.captioned || 0);
    const pending = Number(counts.pending_cached_images || 0);
    const active = Boolean(captionStatus && captionStatus.active);
    return `${active ? 'enabled' : 'paused'} · ${fmt(captioned)} captioned${pending ? ` · ${fmt(pending)} pending` : ''} · ${worker.state || 'idle'}`;
}

function metadataLine() {
    if (!metadataStatus) return 'Status unknown';
    const worker = metadataStatus.worker || {};
    const state = metadataStatus.manual_pause ? 'paused' : worker.state || (metadataStatus.active ? 'running' : 'idle');
    const count = Number(metadataStatus.pending || metadataStatus.remaining || 0);
    return `${state}${count ? ` · ${fmt(count)} pending` : ''}`;
}

function statusText(name, data) {
    if (name === 'AI') {
        return `${fmt(data.embedded)} / ${fmt(data.total_images)} · ${data.worker_state || 'idle'}`;
    }
    if (name === 'Cache') {
        const pregen = (data && data.pregen) || {};
        const preview = pregen.preview || {};
        const priority = pregen.priority_scope ? ` · Prioritizing: ${pregen.priority_scope}` : '';
        return `${fmt(preview.count)} / ${fmt(preview.total)} · ${pregen.state || 'idle'}${priority}`;
    }
    if (name === 'Captions') {
        const worker = (data && data.worker) || {};
        const counts = (data && data.counts) || {};
        return `${fmt(counts.captioned || 0)} captioned · ${fmt(counts.pending_cached_images || 0)} pending · ${worker.state || 'idle'}`;
    }
    if (name === 'Metadata') {
        return metadataLine();
    }
    const worker = (data && data.worker) || {};
    const counts = (data && data.counts) || {};
    return `${fmt(counts.detected_faces || counts.people || 0)} faces · ${worker.state || 'idle'}`;
}

function renderActivity() {
    const widget = document.getElementById('activity-widget');
    const pop = document.getElementById('activity-popover');
    if (!widget || !pop) return;
    const values = activeProgress();
    for (const [key, value] of Object.entries(values)) {
        const bar = widget.querySelector(`[data-worker="${key}"]`);
        if (bar) bar.style.height = `${Math.max(3, Math.round((value / 100) * 14))}px`;
    }
    const aiPaused = aiStatus && aiStatus.embedding_manual_pause;
    const cachePaused = cacheStatus && cacheStatus.pregen && cacheStatus.pregen.manual_pause;
    const peoplePaused = peopleStatus && peopleStatus.worker && peopleStatus.worker.manual_pause;
    const captionsPaused = captionStatus && !captionStatus.active;
    const metadataPaused = metadataStatus && metadataStatus.manual_pause;
    const workerActive = workerStateIsActive(aiStatus)
        || cachePregenStateIsActive(cacheStatus)
        || workerStateIsActive(peopleStatus)
        || workerStateIsActive(captionStatus)
        || metadataStateIsActive(metadataStatus);
    widget.classList.toggle('paused', Boolean(aiPaused || cachePaused || peoplePaused || captionsPaused || metadataPaused));
    widget.classList.toggle('active', workerActive || Object.values(values).some((value) => value > 0 && value < 100));
    pop.innerHTML = [
        ['AI', values.ai, aiStatus || {}],
        ['Cache', values.cache, cacheStatus || {}],
        ['People', values.people, peopleStatus || {}],
        ['Captions', values.captions, captionStatus || {}],
        ['Metadata', values.metadata, metadataStatus || {}],
    ].map(([name, value, data]) => (
        `<div class="ap-row"><span>${name}</span><span class="ap-track"><i style="width:${value}%"></i></span><span class="ap-val">${esc(statusText(name, data))}</span></div>`
    )).join('');
}

async function refreshActivity() {
    if (document.hidden) return;
    const [ai, cache, people, captions, metadata] = await Promise.all([
        getAiStatus().catch(() => null),
        getCacheStatus().catch(() => null),
        getPeopleStatus().catch(() => null),
        getCaptionStatus().catch(() => null),
        getMetadataStatus().catch(() => null),
    ]);
    aiStatus = ai || aiStatus;
    cacheStatus = cache || cacheStatus;
    peopleStatus = people || peopleStatus;
    captionStatus = captions || captionStatus;
    metadataStatus = metadata || metadataStatus;
    renderActivity();
    if (open) patchDrawerStatus();
}

function sourceName(source) {
    return source.display_name || source.name || source.path || `Source ${source.id}`;
}

function sourceCount(source) {
    return source.active_image_count != null ? source.active_image_count : source.image_count;
}

function sourceStatusLine(source) {
    const online = Number(source.online) === 1 || source.online === true;
    return `${fmt(sourceCount(source))} photos · ${lastScan(source)}${online ? '' : ' · offline'}`;
}

function lastScan(source) {
    const raw = source.last_scan_at || source.scanned_at || source.updated_at;
    if (!raw) return 'not scanned';
    const stamp = Number(raw);
    const date = Number.isFinite(stamp) ? new Date(stamp * 1000) : new Date(raw);
    if (Number.isNaN(date.getTime())) return 'not scanned';
    return `scanned ${date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;
}

function archiveHomeName(path) {
    const clean = String(path || '').replace(/\/+$/, '');
    return clean.split('/').filter(Boolean).pop() || clean || 'Archive home';
}

function archiveSummary(overview) {
    const parts = ['Your archive'];
    if (overview?.photo_count != null) parts.push(`${fmt(overview.photo_count)} photos`);
    if (overview?.originals_bytes != null) parts.push(bytes(overview.originals_bytes));
    if (overview?.disk_free_bytes != null) {
        parts.push(`${bytes(overview.disk_free_bytes)} free${overview.disk_label ? ` on ${overview.disk_label}` : ''}`);
    }
    return parts.join(' · ');
}

function renderArchiveOverview() {
    const overview = storageOverview;
    const homePath = overview?.home_path || '';
    const home = archiveHomeName(homePath);
    return '<section class="dr-sec"><h3>Archive</h3><div class="setting-status archive-overview">'
        + `<b>${esc(archiveSummary(overview))}</b>`
        + (homePath
            ? `<div class="archive-home"><span><b title="${esc(home)}">${esc(home)}</b><code title="${esc(homePath)}">${esc(homePath)}</code></span><button class="mini-btn" type="button" data-archive-open="${esc(homePath)}">Open</button></div>`
            : '<span class="archive-unavailable">Archive details are unavailable right now.</span>')
        + '</div></section>';
}

function bindArchiveOpen(scopeEl) {
    scopeEl.querySelector('[data-archive-open]')?.addEventListener('click', async (event) => {
        const result = await revealFolder(event.currentTarget.dataset.archiveOpen || '');
        if (result?.ok && result?.data?.ok) showToast('Opened archive home');
        else showToast(result?.data?.error || 'Couldn’t open archive home');
    });
}

function patchArchiveOverview(body) {
    const card = body.querySelector('.archive-overview');
    if (!card || !storageOverview) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = renderArchiveOverview();
    const next = wrap.querySelector('.archive-overview');
    if (!next || card.innerHTML === next.innerHTML) return;
    card.innerHTML = next.innerHTML;
    bindArchiveOpen(card);
}

function dateTime(value) {
    const raw = Number(value || 0);
    if (!raw) return 'not published';
    const date = new Date(raw * 1000);
    if (Number.isNaN(date.getTime())) return 'not published';
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function renderSources() {
    const sources = (catalog && catalog.sources) || [];
    const rows = sources.length ? sources.map((source) => {
        const online = Number(source.online) === 1 || source.online === true;
        const id = Number(source.id);
        const scanning = scanSourceId === id;
        const path = source.path || '';
        return `<article class="src-card" data-source-id="${id}" data-source-path="${esc(path)}" data-source-count="${Number(sourceCount(source)) || 0}">`
            + `<span class="sc-dot ${online ? 'on' : ''}"></span><div>`
            + `<div class="sc-name" title="${esc(sourceName(source))}">${esc(sourceName(source))}</div>`
            + `<div class="sc-sub">${esc(sourceStatusLine(source))}</div>`
            + '<div class="src-actions">'
            + `<button class="mini-btn" data-act="rescan" ${online ? '' : 'aria-disabled="true" disabled'}>Rescan</button>`
            + '<button class="mini-btn btn-danger" data-act="remove">Remove</button></div>'
            + `<div class="remove-choice" hidden><button class="mini-btn" data-mode="keep">Keep photos</button><button class="mini-btn btn-danger" data-mode="delete">Remove from library index</button><button class="mini-btn" data-remove-cancel>Cancel</button></div>`
            + `<div class="scan-progress" ${scanning ? '' : 'hidden'}>Scanning…</div>`
            + '</div></article>';
    }).join('') : '<div class="source-empty"><b>No photo folders yet.</b><span>Add a folder to catalog your archive. Azimuth Photo reads originals in place; original photo files are never moved or changed.</span></div>';
    return '<section class="dr-sec"><h3>Sources</h3>'
        + `<div id="drawer-sources">${rows}</div>`
        + renderSourceAddUi()
        + '</section>';
}

function workerRow(key, label, value, detail, paused, actionLabel, note = '') {
    return '<div class="work-row" data-worker-row="' + key + '">'
        + '<div class="wr-body"><div class="wr-top">'
        + `<span>${esc(label)}</span><span class="v">${esc(detail)}</span></div>`
        + `<div class="wr-track"><i style="width:${pct(value)}%"></i></div>`
        + (note ? `<small class="wr-note">${esc(note)}</small>` : '')
        + '</div>'
        + `<button class="mini-btn" data-worker-action="${key}">${esc(actionLabel || (paused ? 'Resume' : 'Pause'))}</button></div>`;
}

function workItems() {
    const pregen = (cacheStatus && cacheStatus.pregen) || {};
    const preview = pregen.preview || {};
    const worker = (peopleStatus && peopleStatus.worker) || {};
    const peoplePct = peopleProgress(peopleStatus);
    const captionPct = captionProgress(captionStatus);
    const metadataPaused = metadataStatus && metadataStatus.manual_pause;
    const metadataActive = metadataStateIsActive(metadataStatus);
    return [
        ['ai', 'Visual search index', aiStatus ? aiStatus.progress_pct : 0, aiStatus ? statusText('AI', aiStatus) : 'Status unknown', aiStatus && aiStatus.embedding_manual_pause, null, 'Resume also wakes the preview cache.'],
        ['cache', 'Preview cache', (preview.progress_pct || pregen.progress_pct || 0), cacheStatus ? statusText('Cache', cacheStatus) : 'Status unknown', pregen.manual_pause || pregen.state === 'paused', pregen.manual_pause || pregen.state === 'paused' ? 'Resume' : 'Pause', 'Pause also pauses the visual search index and People scan.'],
        ['people', 'People scan', peoplePct, peopleStatus ? statusText('People', peopleStatus) : 'Status unknown', worker.manual_pause || !settingValue('people_scan_enabled'), null, 'Resume also wakes the preview cache.'],
        ['captions', 'Captions', captionPct, captionStatus ? statusText('Captions', captionStatus) : 'Status unknown', captionStatus && !captionStatus.active],
        ['metadata', 'Metadata', metadataActive ? 50 : 0, metadataLine(), metadataPaused],
    ].map(([key, label, value, detail, paused, actionLabel, note = '']) => ({
        key, label, value, detail, paused: Boolean(paused), actionLabel, note,
    }));
}

function renderWork() {
    return '<section class="dr-sec"><h3>Background work</h3>'
        + workItems().map((item) => workerRow(
            item.key, item.label, item.value, item.detail, item.paused, item.actionLabel, item.note,
        )).join('')
        + '</section>';
}

function renderStorage() {
    const tiers = (cacheStatus && cacheStatus.disk && cacheStatus.disk.tiers) || {};
    const chips = ['sm', 'md', 'lg'].map((size) => {
        const tier = tiers[size] || {};
        return `<span class="tier-chip" data-tier-size="${size}"><b>${size.toUpperCase()}</b><span data-tier-count>${fmt(tier.count)} files</span><span data-tier-bytes>${bytes(tier.bytes)}</span></span>`;
    }).join('');
    const totalFiles = Object.values(tiers).reduce((sum, tier) => sum + Number(tier?.count || 0), 0);
    return '<section class="dr-sec"><h3>Cache</h3>'
        + '<p class="setting-hint">Previews only — your photos live in the Archive.</p>'
        + `<div class="tier-chips">${chips}</div>`
        + `<button class="btn btn-danger" id="clear-cache-btn" data-cache-files="${totalFiles}">Clear cache</button>`
        + '</section>';
}

function renderPeekStorage() {
    const tiers = (cacheStatus && cacheStatus.disk && cacheStatus.disk.tiers) || {};
    const chips = ['sm', 'md', 'lg'].map((size) => {
        const tier = tiers[size] || {};
        return `<span class="tier-chip" data-tier-size="${size}"><b>${size.toUpperCase()}</b><span data-tier-count>${fmt(tier.count)} files</span><span data-tier-bytes>${bytes(tier.bytes)}</span></span>`;
    }).join('');
    return `<section class="dr-sec"><h3>Cache</h3><div class="tier-chips">${chips}</div></section>`;
}

function renderPeekSources() {
    const sources = (catalog && catalog.sources) || [];
    const rows = sources.length ? sources.map((source) => {
        const online = Number(source.online) === 1 || source.online === true;
        const id = Number(source.id);
        return `<article class="src-card" data-source-id="${id}">`
            + `<span class="sc-dot ${online ? 'on' : ''}"></span><div>`
            + `<div class="sc-name" title="${esc(sourceName(source))}">${esc(sourceName(source))}</div>`
            + `<div class="sc-sub">${esc(sourceStatusLine(source))}</div>`
            + `<div class="scan-progress" ${scanSourceId === id ? '' : 'hidden'}>Scanning…</div>`
            + '</div></article>';
    }).join('') : '<div class="source-empty"><b>No sources connected.</b><span>Open System settings to add a photo folder.</span></div>';
    return `<section class="dr-sec"><h3>Sources</h3><div id="drawer-sources">${rows}</div></section>`;
}

function renderPeekHealth() {
    const sources = (catalog && catalog.sources) || [];
    const offline = sources.filter((source) => !(Number(source.online) === 1 || source.online === true)).length;
    const detail = offline ? `${offline} source${offline === 1 ? '' : 's'} offline` : 'All connected sources reachable';
    return `<section class="dr-sec"><h3>Library health</h3><div class="setting-status">${esc(detail)}</div></section>`;
}

function freeupActive() {
    return Boolean(freeupJob && ['queued', 'confirming', 'deleting'].includes(freeupJob.phase));
}

function hubDisplayName() {
    try {
        return new URL(pairStatus?.hub_url || '').hostname.split('.')[0] || 'your hub';
    } catch {
        return 'your hub';
    }
}

function freeupSummary() {
    if (freeableStatus?.unavailable) return 'Hub unavailable — reconnect to check what is safe to free.';
    if (!freeableStatus) return 'Checking synced originals…';
    if (!Number(freeableStatus.files || 0)) return 'No synced originals match this age.';
    return `${bytes(freeableStatus.bytes)} safe to free — everything already on ${hubDisplayName()}`;
}

function freeupJobDetail() {
    if (!freeupJob) return '';
    if (freeupJob.phase === 'confirming') return 'Confirming originals with the hub…';
    const done = Number(freeupJob.files_done || 0);
    const total = Number(freeupJob.files_total || 0);
    if (freeupJob.phase === 'cancelled') return `Stopped · ${bytes(freeupJob.bytes_freed)} freed`;
    if (freeupJob.phase === 'failed') return 'Stopped — the remaining originals are untouched';
    if (freeupJob.phase === 'completed') return `Done · ${bytes(freeupJob.bytes_freed)} freed`;
    return `${fmt(done)} of ${fmt(total)} files · ${bytes(freeupJob.bytes_freed)} freed`;
}

function renderFreeUp() {
    if (!pairStatus || pairStatus.mode !== 'satellite' || !pairStatus.has_hub) return '';
    const active = freeupActive();
    const canStart = Number(freeableStatus?.files || 0) > 0;
    const progressValue = freeupJob
        ? progress(freeupJob.files_done, freeupJob.files_total)
        : 0;
    const jobRow = freeupJob
        ? '<div class="work-row freeup-progress" data-freeup-job>'
            + '<div class="wr-body"><div class="wr-top"><span>Local originals</span>'
            + `<span class="v" data-freeup-job-detail>${esc(freeupJobDetail())}</span></div>`
            + `<div class="wr-track"><i data-freeup-progress style="width:${progressValue}%"></i></div></div></div>`
        : '';
    return '<section class="dr-sec" id="freeup-panel"><h3>Free up space</h3>'
        + `<div class="freeup-summary" data-freeup-summary>${esc(freeupSummary())}</div>`
        + '<div class="freeup-controls">'
        + '<label for="freeup-age"><span>Older than</span><select id="freeup-age"'
        + `${active ? ' disabled' : ''}>`
        + `<option value="30"${freeupOlderDays === 30 ? ' selected' : ''}>30 days</option>`
        + `<option value="90"${freeupOlderDays === 90 ? ' selected' : ''}>90 days</option>`
        + `<option value="365"${freeupOlderDays === 365 ? ' selected' : ''}>1 year</option>`
        + `<option value="0"${freeupOlderDays === 0 ? ' selected' : ''}>Everything synced</option>`
        + '</select></label>'
        + `<button class="btn primary" id="freeup-btn" type="button"${active || canStart ? '' : ' disabled'}>${active ? 'Cancel' : 'Free up space'}</button>`
        + '</div>' + jobRow + '</section>';
}

function formatSeen(value) {
    if (value == null) return 'Never';
    const then = Number(value) * (Number(value) > 1e12 ? 1 : 1000);
    if (!Number.isFinite(then)) return '—';
    const delta = Math.max(0, Date.now() - then);
    if (delta < 60_000) return 'Just now';
    if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
    if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
    return new Date(then).toLocaleDateString();
}

function renderDevices() {
    if (!pairStatus || pairStatus.mode !== 'hub') return '';
    const devices = (devicesPayload && devicesPayload.devices) || [];
    const active = devices.filter((device) => !device.revoked);
    const rows = active.length
        ? active.map((device) => (
            `<div class="device-row" data-device-id="${esc(device.id)}">`
            + `<div><b>${esc(device.name)}</b>`
            + `<span class="device-meta" data-device-status>${esc(device.platform || 'unknown')} · last seen ${esc(formatSeen(device.last_seen))}</span></div>`
            + `<button class="mini-btn btn-danger" type="button" data-revoke-device="${esc(device.id)}">Revoke</button>`
            + `</div>`
        )).join('')
        : '<div class="setting-hint">No linked devices yet.</div>';
    const link = linkSession
        ? `<div class="pair-link-card">`
            + `<div class="pair-code" aria-label="Pairing code">${esc(linkSession.code)}</div>`
            + (linkSession.qr_png_base64
                ? `<img class="pair-qr" alt="Pairing QR code" src="data:image/png;base64,${esc(linkSession.qr_png_base64)}">`
                : '')
            + `<div class="setting-hint">Code expires in 10 minutes. One device can use it once.</div>`
            + `</div>`
        : '';
    return '<section class="dr-sec" id="devices-panel"><h3>Devices</h3>'
        + '<div class="drawer-action-row">'
        + '<span>Link a phone or another computer to this library.</span>'
        + '<button class="btn primary" id="link-device-btn" type="button">Link a device</button>'
        + '</div>'
        + link
        + `<div class="device-list">${rows}</div>`
        + settingToggle('require_device_token', 'Require device token for sync')
        + '<div class="setting-hint">Off by default. When on, only paired devices can call sync endpoints.</div>'
        + '</section>';
}

function renderConnectServer() {
    if (!pairStatus || pairStatus.mode === 'hub') return '';
    const hubs = (discoverPayload && discoverPayload.hubs) || [];
    const discovered = hubs.length
        ? hubs.map((hub) => (
            `<button class="discovered-hub" type="button" data-hub-url="${esc(hub.url)}">`
            + `<b>${esc(hub.name || 'Azimuth Photo')}</b>`
            + `<span>${esc(hub.url)}</span>`
            + '</button>'
        )).join('')
        : '<div class="setting-hint">No hubs found on the network yet.</div>';
    const connected = pairStatus && pairStatus.has_hub
        ? `<div class="setting-status" data-setting-status="connection">Connected to <code>${esc(pairStatus.hub_url)}</code></div>`
        : '';
    const updateBanner = syncStatus && (syncStatus.server_update_available || syncStatus.server_incompatible) && !sessionStorage.getItem('azimuth-server-update-dismissed')
        ? '<div class="setting-status warn server-update-banner" role="status"><span>Your Azimuth Photo server needs an update</span><button class="mini-btn" id="dismiss-server-update" type="button">Dismiss</button></div>'
        : '';
    return '<section class="dr-sec" id="connect-server-panel"><h3>Connect to server</h3>'
        + connected
        + updateBanner
        + '<div class="drawer-action-row">'
        + '<span>Find a hub on your network, or enter its address.</span>'
        + '<button class="mini-btn" id="discover-hubs-btn" type="button">Scan network</button>'
        + '</div>'
        + `<div class="discovered-hubs">${discovered}</div>`
        + '<label class="setting-row" for="connect-hub-url"><span><b>Hub URL</b></span>'
        + `<input class="drawer-input" id="connect-hub-url" type="text" spellcheck="false" autocomplete="off" placeholder="http://nas.local:8000" value="${esc((pairStatus && pairStatus.hub_url) || '')}">`
        + '</label>'
        + '<label class="setting-row" for="connect-pair-code"><span><b>Pair code</b></span>'
        + '<input class="drawer-input" id="connect-pair-code" type="text" spellcheck="false" autocomplete="off" placeholder="8-character code">'
        + '</label>'
        + '<div class="setting-actions">'
        + '<button class="btn primary" id="connect-hub-btn" type="button">Connect</button>'
        + '</div></section>';
}

function remoteQrMarkup(text) {
    if (!text || typeof window.qrcode !== 'function') return '';
    try {
        const qr = window.qrcode(0, 'M');
        qr.addData(text);
        qr.make();
        const size = qr.getModuleCount();
        const cell = 3;
        let rects = '';
        for (let row = 0; row < size; row += 1) {
            for (let col = 0; col < size; col += 1) {
                if (!qr.isDark(row, col)) continue;
                rects += `<rect x="${col * cell}" y="${row * cell}" width="${cell}" height="${cell}" fill="currentColor"/>`;
            }
        }
        const dim = size * cell;
        return `<div class="remote-qr" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${dim} ${dim}" width="132" height="132" role="img">${rects}</svg></div>`;
    } catch {
        return '';
    }
}

function renderRemote() {
    // Hub mode only — standalone/satellite hide this panel entirely.
    if (!remoteAccess || remoteAccess.hub_mode === false) return '';
    const ts = remoteAccess.tailscale || {};
    const state = ts.state || (ts.available ? 'up' : 'absent');
    const httpsUrl = ts.https_url || '';
    const serveApplied = Boolean(ts.serve_applied && httpsUrl);
    let body = '';

    if (state === 'absent') {
        body = '<p class="remote-copy">Install Tailscale on this machine, then come back here to publish a phone-ready HTTPS link on your tailnet.</p>'
            + `<a class="btn" id="remote-install" href="${esc(ts.install_url || 'https://tailscale.com/download')}" target="_blank" rel="noopener">Install Tailscale</a>`;
    } else if (state === 'logged-out') {
        body = '<p class="remote-copy">Tailscale is installed. Sign in on this machine, then return here to enable HTTPS for your phone.</p>'
            + `<code class="remote-cmd">${esc(ts.up_command || 'tailscale up')}</code>`
            + '<p class="remote-hint">Run that in a terminal, complete the browser login, then reopen Settings.</p>';
    } else {
        const command = ts.serve_command || 'sudo tailscale serve --bg --https=8443 http://127.0.0.1:8000';
        body = '<p class="remote-copy">Publish a tailnet-only HTTPS address so the phone can install the app and keep a service worker.</p>'
            + `<code class="remote-cmd" title="${esc(command)}">${esc(command)}</code>`;
        if (serveApplied) {
            body += '<div class="remote-ready">'
                + '<div class="remote-row">'
                + `<span class="remote-url" title="${esc(httpsUrl)}">${esc(httpsUrl)}</span>`
                + '<button class="mini-btn" id="copy-remote" type="button">Copy</button>'
                + '</div>'
                + remoteQrMarkup(httpsUrl)
                + '<p class="remote-hint">Scan on a phone that’s on the same Tailscale network.</p>'
                + '</div>';
        } else {
            body += '<button class="btn" id="remote-apply-serve" type="button">Apply HTTPS</button>'
                + '<p class="remote-hint">Runs only when you click — nothing is changed automatically.</p>';
        }
        if (ts.dry_run) {
            body += '<p class="remote-hint remote-dryrun">Dry-run mode: Apply will not change Tailscale Serve on this machine.</p>';
        }
    }

    return `<section class="dr-sec" data-remote-state="${esc(state)}" data-remote-hub="1"><h3>Remote access</h3>${body}</section>`;
}

function renderSharedHome() {
    return '<section class="dr-sec"><h3>Sharing</h3>'
        + '<div class="drawer-action-row">'
        + '<span>Private links and website galleries live together in Shared.</span>'
        + '<button class="btn" id="drawer-open-shared" type="button">Open Shared view</button>'
        + '</div></section>';
}

function detailsSection(title, description, body) {
    return `<details class="dr-sec dr-details" data-settings-section="${esc(title)}"${openSettingSections.has(title) ? ' open' : ''}>`
        + `<summary><span>${esc(title)}</span></summary>`
        + `<div class="dr-details-body">${description ? `<p class="setting-section-description">${esc(description)}</p>` : ''}${body}</div></details>`;
}

function settingInput(field, label, { hint = '' } = {}) {
    const def = SETTING_DEFS[field] || {};
    const value = settingValue(field);
    const unit = def.unit ? `<em>${esc(def.unit)}</em>` : '';
    const attrs = [
        `id="drawer-setting-${field}"`,
        `data-setting-field="${field}"`,
        def.type === 'number' ? 'type="number"' : 'type="text"',
        def.min != null ? `min="${def.min}"` : '',
        def.max != null ? `max="${def.max}"` : '',
        def.step != null ? `step="${def.step}"` : '',
        def.type === 'text' ? 'spellcheck="false" autocomplete="off"' : '',
    ].filter(Boolean).join(' ');
    return `<label class="setting-row" for="drawer-setting-${field}">`
        + `<span><b>${esc(label)}</b>${unit}</span>`
        + `<input class="drawer-input" ${attrs} aria-describedby="drawer-setting-${field}-validation" value="${esc(value)}">`
        + `${hint ? `<small>${esc(hint)}</small>` : ''}`
        + `<small id="drawer-setting-${field}-validation" class="setting-validation" aria-live="polite" hidden></small></label>`;
}

function settingSelect(field, label, options) {
    const value = String(settingValue(field));
    return `<label class="setting-row" for="drawer-setting-${field}">`
        + `<span><b>${esc(label)}</b></span>`
        + `<select id="drawer-setting-${field}" data-setting-field="${field}" aria-describedby="drawer-setting-${field}-validation">`
        + options.map((option) => `<option value="${esc(option.value)}"${String(option.value) === value ? ' selected' : ''}>${esc(option.label)}</option>`).join('')
        + `</select><small id="drawer-setting-${field}-validation" class="setting-validation" aria-live="polite" hidden></small></label>`;
}

function settingToggle(field, label) {
    return '<label class="setting-toggle">'
        + `<span>${esc(label)}</span>`
        + `<input type="checkbox" data-setting-field="${field}" ${settingValue(field) ? 'checked' : ''}>`
        + '<i></i></label>';
}

function renderAiSettings() {
    const rawPresets = settingsPageData && settingsPageData.embedding_model_presets || [];
    const presets = rawPresets.map((preset) => ({
        value: preset.key,
        label: preset.label || preset.key,
    }));
    const selectedPreset = rawPresets.find((preset) => preset.key === settingValue('embed_model_preset'));
    const presetSelect = settingSelect('embed_model_preset', 'Model preset', presets.length ? presets : [
        { value: settingValue('embed_model_preset'), label: settingValue('embed_model_preset') || 'Current preset' },
    ]);
    return detailsSection('AI model', 'Choose how Azimuth understands visual and text search.',
        `<div class="setting-status" data-setting-status="ai">${esc(modelLine())}</div>`
        + '<div class="setting-status warn">Changing model preset rebuilds the search index and can take a while.</div>'
        + presetSelect
        + (selectedPreset?.description ? `<div class="setting-hint">${esc(selectedPreset.description)}</div>` : '')
        + settingInput('search_similarity_threshold', 'Search threshold', { hint: 'Higher is stricter for visual/text search matches.' })
        + settingToggle('refine_semantic_pairing', 'Use semantic pairing in Refine')
        + '<div class="setting-actions">'
        + '<button class="btn primary" id="drawer-install-model" type="button">Save & install</button>'
        + '</div>');
}

function renderImageCacheSettings() {
    return detailsSection('Image cache', 'Balance instant browsing against the memory and disk this computer can spare.',
        `<div class="setting-status" data-setting-status="cache">${esc(cacheUsageLine())}</div>`
        + '<div class="settings-two">'
        + settingInput('memory_cache_gb', 'RAM budget')
        + settingInput('ssd_cache_gb', 'SSD budget')
        + '</div>'
        + settingSelect('cache_profile', 'Cache profile', [
            { value: 'original_heavy', label: 'Best quality' },
            { value: 'balanced', label: 'Balanced' },
            { value: 'browse_fast', label: 'Fastest browsing' },
        ])
        + '<div class="setting-actions">'
        + '<button class="mini-btn" id="drawer-cache-defaults" type="button">Apply defaults</button>'
        + '</div>'
        + '<details class="setting-subdetails"><summary>Advanced</summary>'
        + settingInput('ssd_cache_dir', 'Cache location')
        + '</details>');
}

function renderImportSettings() {
    return detailsSection('Imports', 'Set where new photos land and which folders Azimuth watches.',
        settingInput('import_root', 'Import inbox')
        + '<div id="watched-folders-settings"></div>'
    );
}

function renderThumbnailSettings() {
    return detailsSection('Thumbnail output', 'Tune generated previews. Existing previews stay put unless you choose to replace them.',
        '<div class="settings-two">'
        + settingInput('thumb_size_sm', 'Small long side')
        + settingInput('thumb_size_md', 'Medium long side')
        + settingInput('thumb_size_lg', 'Large long side')
        + settingInput('thumb_quality', 'JPEG quality')
        + '</div>'
        + '<div class="thumb-policy" role="radiogroup" aria-label="Existing previews policy">'
        + `<label><input type="radio" name="drawer_thumbnail_cache_policy" value="keep" ${thumbnailCachePolicy === 'keep' ? 'checked' : ''}> Keep existing previews</label>`
        + `<label><input type="radio" name="drawer_thumbnail_cache_policy" value="replace" ${thumbnailCachePolicy === 'replace' ? 'checked' : ''}> Replace existing previews in the background</label>`
        + '</div>'
        + '<div class="setting-actions">'
        + settingToggle('show_loupe_cache_status', 'Show loupe cache status')
        + '</div>');
}

function renderPeopleSettings() {
    return detailsSection('People recognition', 'Keep face grouping useful without changing your original photos.',
        `<div class="setting-status" data-setting-status="people">${esc(peopleLine())}</div>`
        + settingToggle('people_scan_enabled', 'Scan for people automatically')
        + settingToggle('people_auto_install', 'Install people model automatically')
        + settingInput('face_model_id', 'Face model')
        + settingInput('face_model_dir', 'Model directory')
        + '<div class="settings-two">'
        + settingInput('face_detection_size', 'Detection size')
        + settingInput('face_similarity_threshold', 'Cluster threshold', { hint: 'Higher is stricter when grouping faces.' })
        + settingInput('face_merge_suggestion_threshold', 'Merge threshold', { hint: 'Higher is stricter before suggesting merges.' })
        + '</div>');
}

function renderCaptionSettings() {
    const rawPresets = settingsPageData && settingsPageData.caption_model_presets || [];
    const presets = rawPresets.map((preset) => ({ value: preset.key, label: preset.label || preset.key }));
    const selectedPreset = rawPresets.find((preset) => preset.key === settingValue('caption_model_preset'));
    return detailsSection('Captions', 'Generate searchable photo descriptions in the background.',
        `<div class="setting-status" data-setting-status="captions">${esc(captionLine())}</div>`
        + settingToggle('caption_scan_enabled', 'Caption cached photos automatically')
        + settingSelect('caption_model_preset', 'Caption model', presets.length ? presets : [
            { value: settingValue('caption_model_preset'), label: settingValue('caption_model_preset') || 'Current model' },
        ])
        + (selectedPreset?.description ? `<div class="setting-hint">${esc(selectedPreset.description)}</div>` : '')
        + settingInput('caption_batch_size', 'Batch size'));
}

function renderMetadataSettings() {
    return detailsSection('Metadata', 'Keep camera, lens, and file details ready for search and filtering.',
        `<div class="setting-status" data-setting-status="metadata">${esc(metadataLine())}</div>`
        + '<div class="setting-hint">Metadata indexing keeps searchable file details current in the background.</div>');
}

function publishingStatusNote() {
    const folder = String(settingValue('publish_dir') || '').trim();
    if (!folder) {
        return '<div class="setting-status warn" data-setting-status="publishing">Publishing is off until a Gallery folder is set. Empty folder disables Publish.</div>';
    }
    return `<div class="setting-status" data-setting-status="publishing">Writing galleries to <code>${esc(folder)}</code></div>`;
}

function publishingStatusLine() {
    const folder = String(settingValue('publish_dir') || '').trim();
    return folder
        ? `Writing galleries to ${folder}`
        : 'Publishing is off until a Gallery folder is set. Empty folder disables Publish.';
}

function updateDrawerContext() {
    const context = document.getElementById('drawer-context');
    if (!context) return;
    if (!publishReturn) {
        context.hidden = true;
        context.textContent = '';
        return;
    }
    context.hidden = false;
    context.textContent = `Publishing setup · return to ${publishReturn.name || 'Publish'}`;
}

function publishReturnBar() {
    if (!publishReturn) return '';
    const name = publishReturn.name || 'collection';
    return '<div class="publish-return" role="status">'
        + `<span>Set the folder, save, then return to publish <b>${esc(name)}</b>.</span>`
        + '<button class="btn primary" id="drawer-return-publish" type="button">Return to Publish</button>'
        + '</div>';
}

function renderPublishingSettings() {
    return detailsSection('Publishing', 'Choose where gallery files live and how links present your work.',
        publishReturnBar()
        + publishingStatusNote()
        + settingInput('publish_dir', 'Gallery folder', {
            hint: 'Required. Public gallery files are written here. Leave empty to disable publishing.',
        })
        + settingInput('publish_site_base_url', 'Site base URL', {
            hint: 'Display-only for in-app links (e.g. https://photos.example.com). Does not serve files.',
        })
        + settingInput('share_brand_name', 'Gallery brand name', {
            hint: 'Shown on private share links and published galleries as the photographer or studio name.',
        }));
}

function renderSettingsSections() {
    if (!settingsPageData) {
        return '<section class="dr-sec"><h3>Settings</h3><div class="muted">Loading settings…</div></section>';
    }
    return renderPublishingSettings()
        + renderAiSettings()
        + renderImageCacheSettings()
        + renderThumbnailSettings()
        + renderImportSettings()
        + renderPeopleSettings()
        + renderCaptionSettings()
        + renderMetadataSettings();
}

function checkbox(key, label) {
    return `<div class="pref-row"><label for="pref-${key}">${esc(label)}</label><input id="pref-${key}" type="checkbox" data-pref="${key}" ${viewState.prefs[key] ? 'checked' : ''}></div>`;
}

function renderPrefs() {
    return '<section class="dr-sec"><h3>Preferences</h3>'
        + '<div class="pref-row"><label for="drawer-thumb-size">Thumbnail size</label><input class="ctl-range" id="drawer-thumb-size" type="range" min="120" max="320" step="10" value="' + viewState.thumbSize + '"></div>'
        + '<div class="pref-row"><label for="pref-density">Density</label><select id="pref-density" data-pref-sel="density">'
        + `<option value="comfortable"${viewState.prefs.density === 'comfortable' ? ' selected' : ''}>Comfortable</option>`
        + `<option value="cozy"${viewState.prefs.density === 'cozy' ? ' selected' : ''}>Cozy</option>`
        + `<option value="compact"${viewState.prefs.density === 'compact' ? ' selected' : ''}>Compact</option></select></div>`
        + checkbox('badgeCheck', 'Cell badge · check')
        + checkbox('badgeFlag', 'Cell badge · flag')
        + checkbox('badgeElo', 'Cell badge · rating')
        + checkbox('badgeIndex', 'Cell badge · index')
        + checkbox('collapseStacks', 'Collapse stacks')
        + checkbox('reduceMotion', 'Reduce motion')
        + '</section>';
}

function renderAbout() {
    const version = versionData?.version || 'Unknown';
    return '<section class="dr-sec"><h3>About</h3>'
        + '<div class="setting-status"><b>Azimuth Photo</b><span> · Version ' + esc(version) + '</span></div>'
        + '</section>';
}

function focusPublishingSection() {
    const drawer = document.getElementById('drawer');
    const section = drawer?.querySelector('.dr-details[data-settings-section="Publishing"]');
    if (!section) return;
    section.open = true;
    openSettingSections.add('Publishing');
    section.classList.add('focus-target');
    const field = section.querySelector('#drawer-setting-publish_dir');
    requestAnimationFrame(() => {
        section.scrollIntoView({ block: 'start', behavior: 'smooth' });
        field?.focus();
        window.setTimeout(() => section.classList.remove('focus-target'), 2400);
    });
}

function renderDrawer() {
    const body = document.getElementById('drawer-body');
    if (!body) return;
    body.innerHTML = renderArchiveOverview() + renderWork() + renderPeekStorage() + renderPeekHealth() + renderPeekSources()
        + '<section class="dr-sec"><button class="btn primary" id="drawer-open-system" type="button">System settings →</button></section>';
    bindDrawerActions(body);
    body.querySelector('#drawer-open-system')?.addEventListener('click', () => {
        closeSystemDrawer();
        openSystemSettings();
    });
}

function renderCurrentSystemSurface() {
    if (systemSurfaceRender) systemSurfaceRender();
    if (open || !systemSurfaceRender) renderDrawer();
}

export function renderSystemSections() {
    return {
        library: renderArchiveOverview() + renderSources() + renderLibraryHealth(catalog) + renderAbout(),
        processing: renderAiSettings() + renderPeopleSettings() + renderCaptionSettings() + renderMetadataSettings() + renderWork(),
        performance: renderImageCacheSettings() + renderThumbnailSettings() + renderStorage(),
        import: renderImportSettings(),
        publishing: renderPublishingSettings(),
        connectivity: renderDevices() + renderConnectServer() + renderFreeUp() + renderRemote(),
        preferences: renderPrefs(),
    };
}

export function mountSystemSurface(render) {
    systemSurfaceRender = render;
}

export function unmountSystemSurface() {
    systemSurfaceRender = null;
}

export function bindSystemSurface(body) {
    bindDrawerActions(body);
}

export async function refreshSystemSurface() {
    await refreshDrawer({ initial: false });
}

function patchNodeText(root, selector, value) {
    const node = root.querySelector(selector);
    const text = String(value);
    if (node && node.textContent !== text) node.textContent = text;
}

function patchDrawerStatus(workerGenerations = null) {
    const body = systemSurfaceRender
        ? document.getElementById('system-lens-content')
        : document.getElementById('drawer-body');
    if (!body) return;

    patchArchiveOverview(body);
    for (const source of (catalog && catalog.sources) || []) {
        const card = body.querySelector(`.src-card[data-source-id="${Number(source.id)}"]`);
        if (!card) continue;
        const online = Number(source.online) === 1 || source.online === true;
        card.querySelector('.sc-dot')?.classList.toggle('on', online);
        patchNodeText(card, '.sc-sub', sourceStatusLine(source));
        const scanning = scanSourceId === Number(source.id);
        const progressEl = card.querySelector('.scan-progress');
        if (progressEl) progressEl.hidden = !scanning;
    }

    for (const item of workItems()) {
        if (workerGenerations && (
            workerActionsInFlight.has(item.key)
            || workerGenerations.get(item.key) !== (workerActionGenerations.get(item.key) || 0)
        )) continue;
        const row = body.querySelector(`[data-worker-row="${item.key}"]`);
        if (!row) continue;
        patchNodeText(row, '.wr-top .v', item.detail);
        const progressBar = row.querySelector('.wr-track i');
        if (progressBar) progressBar.style.width = `${pct(item.value)}%`;
        patchNodeText(row, '[data-worker-action]', item.actionLabel || (item.paused ? 'Resume' : 'Pause'));
    }

    const settingLines = {
        ai: modelLine(),
        cache: cacheUsageLine(),
        people: peopleLine(),
        captions: captionLine(),
        metadata: metadataLine(),
    };
    for (const [key, value] of Object.entries(settingLines)) {
        patchNodeText(body, `[data-setting-status="${key}"]`, value);
    }

    const tiers = (cacheStatus && cacheStatus.disk && cacheStatus.disk.tiers) || {};
    for (const size of ['sm', 'md', 'lg']) {
        const chip = body.querySelector(`[data-tier-size="${size}"]`);
        const tier = tiers[size] || {};
        if (!chip) continue;
        patchNodeText(chip, '[data-tier-count]', `${fmt(tier.count)} files`);
        patchNodeText(chip, '[data-tier-bytes]', bytes(tier.bytes));
    }
    const clearCache = body.querySelector('#clear-cache-btn');
    if (clearCache) {
        clearCache.dataset.cacheFiles = String(Object.values(tiers).reduce(
            (sum, tier) => sum + Number(tier?.count || 0), 0,
        ));
    }

    const devices = new Map(((devicesPayload && devicesPayload.devices) || []).map((device) => [String(device.id), device]));
    for (const row of body.querySelectorAll('.device-row[data-device-id]')) {
        const device = devices.get(row.dataset.deviceId);
        if (!device) continue;
        patchNodeText(row, '[data-device-status]', `${device.platform || 'unknown'} · last seen ${formatSeen(device.last_seen)}`);
    }
    if (pairStatus?.hub_url) patchNodeText(body, '[data-setting-status="connection"] code', pairStatus.hub_url);
    patchNodeText(body, '[data-freeup-summary]', freeupSummary());
    patchNodeText(body, '[data-freeup-job-detail]', freeupJobDetail());
    const freeupProgress = body.querySelector('[data-freeup-progress]');
    if (freeupProgress) freeupProgress.style.width = `${progress(freeupJob?.files_done, freeupJob?.files_total)}%`;
}

function patchSettingSurface(field) {
    patchDrawerStatus();
    if (field === 'publish_dir') {
        const body = systemSurfaceRender
            ? document.getElementById('system-lens-content')
            : document.getElementById('drawer-body');
        if (body) patchNodeText(body, '[data-setting-status="publishing"]', publishingStatusLine());
    }
}

async function refreshDrawer({ initial = false } = {}) {
    const workerGenerations = new Map(workerActionGenerations);
    const [nextCatalog, ai, cache, people, captions, metadata, remote, settingsData, version, pair, sync, devices, overview] = await Promise.all([
        getCatalog().catch(() => null),
        getAiStatus().catch(() => null),
        getCacheStatus().catch(() => null),
        getPeopleStatus().catch(() => null),
        getCaptionStatus().catch(() => null),
        getMetadataStatus().catch(() => null),
        getRemoteAccess().catch(() => null),
        getSettings().catch(() => null),
        versionData ? Promise.resolve(versionData) : getVersion().catch(() => null),
        getPairStatus().catch(() => null),
        getSyncStatus().catch(() => null),
        listDevices().catch(() => null),
        getStorageOverview().catch(() => null),
        refreshLibraryHealth(),
    ]);
    if (settingsData) applySettingsData(settingsData);
    catalog = nextCatalog || catalog;
    aiStatus = ai || aiStatus;
    cacheStatus = cache || cacheStatus;
    peopleStatus = people || peopleStatus;
    captionStatus = captions || captionStatus;
    metadataStatus = metadata || metadataStatus || (settingsData && settingsData.metadata_status);
    remoteAccess = remote || remoteAccess;
    versionData = version || versionData;
    pairStatus = pair || pairStatus;
    syncStatus = sync || syncStatus;
    devicesPayload = devices || devicesPayload;
    if (pairStatus?.mode === 'satellite' && pairStatus.has_hub && freeableStatus == null && !freeupActive()) {
        const available = await getFreeable(freeupOlderDays).catch(() => null);
        freeableStatus = available || { files: 0, bytes: 0, unavailable: true };
    }
    storageOverview = overview || storageOverview;
    renderActivity();
    const body = systemSurfaceRender
        ? document.getElementById('system-lens-content')
        : document.getElementById('drawer-body');
    if (initial || !body?.children.length) renderCurrentSystemSurface();
    else patchDrawerStatus(workerGenerations);
}

async function pollScanUntilDone(sourceId) {
    const generation = ++scanTimerGeneration;
    let scanning = true;
    scanSourceId = Number(sourceId) || null;
    clearInterval(scanTimer);
    const tick = async () => {
        const status = await getScanStatus().catch(() => null);
        if (generation !== scanTimerGeneration) return;
        if (!status || !status.scanning) {
            scanning = false;
            clearInterval(scanTimer);
            scanTimer = null;
            scanSourceId = null;
            catalog = await getCatalog().catch(() => catalog);
            renderCurrentSystemSurface();
            emit('scan', { scanning: false, done: true });
            showToast('Source scan finished');
            return;
        }
        emit('scan', status);
        const progressEl = document.querySelector(`.src-card[data-source-id="${scanSourceId}"] .scan-progress`);
        if (progressEl) progressEl.textContent = `Scanning · ${fmt(status.total_found || status.total_inserted || 0)} photos found · thumbnails appear as they’re ready`;
    };
    await tick();
    if (generation !== scanTimerGeneration || !scanning) return;
    scanTimer = setInterval(tick, 1000);
    renderCurrentSystemSurface();
}

async function handleSourceAction(card, action) {
    const sourceId = Number(card.dataset.sourceId);
    if (!sourceId) return;
    if (action === 'rescan') {
        const result = await rescanCatalogSource(sourceId);
        if (result && result.ok) {
            showToast('Rescan started');
            pollScanUntilDone(sourceId);
        } else showToast('Couldn’t start rescan');
    } else if (action === 'remove') {
        card.querySelector('.remove-choice').hidden = false;
    }
}

async function handleRemove(card, mode) {
    const sourceId = Number(card.dataset.sourceId);
    const result = await removeCatalogSource(sourceId, mode);
    if (result && result.ok) {
        catalog = result.catalog || await getCatalog().catch(() => catalog);
        renderCurrentSystemSurface();
        showToast(mode === 'keep' ? 'Source removed; photos stay in the library' : 'Source and library records removed. This can’t be undone.');
    } else showToast('Couldn’t remove source');
}

function sourceAddErrorMessage(result) {
    const bodyError = String(result?.data?.error || result?.data?.detail || '').trim();
    if (result?.status === 400) {
        return bodyError
            ? `That folder cannot be found or read on the computer running Azimuth Photo: ${bodyError}.`
            : 'That folder cannot be found or read on the computer running Azimuth Photo.';
    }
    if (result?.status === 409) {
        return bodyError
            ? `Another scan is already running: ${bodyError}.`
            : 'Another scan is already running. Wait for it to finish, then add this folder.';
    }
    if (result?.status === 0) {
        return 'Could not reach Azimuth Photo. Check the connection and try again.';
    }
    return bodyError
        ? `Could not add that folder (${result?.status || 'unknown status'}): ${bodyError}.`
        : `Could not add that folder (${result?.status || 'unknown status'}).`;
}

async function submitSourceAdd({ path, form }, { onSuccess } = {}) {
    await withBusyAction('source-add', form?.querySelector('button[type="submit"]'), async () => {
        const result = await addCatalogSource(path, true);
        if (result && result.ok) {
            const data = result.data || {};
            catalog = data.catalog || catalog;
            clearSourcePickerSelection();
            await onSuccess?.(data);
            showToast('Source added · scanning for photos');
            pollScanUntilDone(data.source && data.source.id);
        } else {
            const message = sourceAddErrorMessage(result);
            setSourceAddError(message);
            showToast(message);
        }
    });
}

function closeSourceAddFlow() {
    if (!sourceAddFlow) return;
    clearSourcePickerSelection();
    sourceAddFlow.remove();
    sourceAddFlow = null;
    if (sourceAddFlowReturn && document.contains(sourceAddFlowReturn)) {
        sourceAddFlowReturn.focus({ preventScroll: true });
    }
    sourceAddFlowReturn = null;
}

export function openSourceAddFlow({ onSuccess } = {}) {
    if (!document.body) return false;
    if (sourceAddFlow) {
        sourceAddFlow.querySelector('[data-source-picker-toggle]')?.focus();
        return true;
    }
    sourceAddFlowReturn = document.activeElement;
    sourceAddFlow = document.createElement('div');
    sourceAddFlow.id = 'source-add-flow';
    sourceAddFlow.className = 'modal-scrim';
    sourceAddFlow.innerHTML = '<section class="modal-card source-add-flow-card" role="dialog" aria-modal="true" aria-label="Add photo folder">'
        + '<header class="source-add-flow-head"><div><p>Archive</p><h2>Add photo folder</h2></div><button class="mini-btn" type="button" data-source-add-close>Close</button></header>'
        + `<div class="source-add-flow-body">${renderSourceAddUi()}</div></section>`;
    document.body.append(sourceAddFlow);
    bindSourcePicker(sourceAddFlow, {
        onSubmit: async (payload) => submitSourceAdd(payload, {
            onSuccess: async (data) => {
                closeSourceAddFlow();
                await onSuccess?.(data);
            },
        }),
    });
    clearSourcePickerSelection();
    sourceAddFlow.querySelector('[data-source-add-close]')?.addEventListener('click', closeSourceAddFlow);
    sourceAddFlow.addEventListener('click', (event) => {
        if (event.target === sourceAddFlow) closeSourceAddFlow();
    });
    sourceAddFlow.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeSourceAddFlow();
        }
    });
    sourceAddFlow.querySelector('[data-source-picker-toggle]')?.focus();
    return true;
}

function settingValidationMessage(input) {
    if (input.validity.valid) return '';
    const label = input.closest('.setting-row')?.querySelector('b')?.textContent || 'This value';
    if (input.validity.rangeUnderflow || input.validity.rangeOverflow) {
        return `${label} must be between ${input.min} and ${input.max}.`;
    }
    if (input.validity.stepMismatch) return `${label} must use increments of ${input.step || 'the listed value'}.`;
    if (input.validity.badInput) return `${label} needs a number.`;
    return input.validationMessage || `${label} is not valid.`;
}

function bindSettingInputs(body) {
    for (const details of body.querySelectorAll('.dr-details')) {
        details.addEventListener('toggle', () => {
            const title = details.querySelector('summary span')?.textContent || '';
            if (!title) return;
            if (details.open) openSettingSections.add(title);
            else openSettingSections.delete(title);
        });
    }
    for (const input of body.querySelectorAll('[data-setting-field]')) {
        const field = input.dataset.settingField;
        const save = () => {
            const pending = settingTimers.get(input);
            if (pending) clearTimeout(pending.timer);
            settingTimers.delete(input);
            if (!input.validity.valid) {
                const validation = input.closest('.setting-row')?.querySelector('.setting-validation');
                input.setAttribute('aria-invalid', 'true');
                if (validation) {
                    validation.hidden = false;
                    validation.textContent = settingValidationMessage(input);
                }
                return;
            }
            input.removeAttribute('aria-invalid');
            input.closest('.setting-row')?.querySelector('.setting-validation')?.setAttribute('hidden', '');
            const value = input.type === 'checkbox' ? input.checked : input.value;
            if (field === 'embed_model_preset') {
                pendingModelPreset = value;
                return;
            }
            const patch = field === 'caption_model_preset' ? captionPresetConfig(value) : null;
            return applySetting(field, value, { patch, control: input });
        };
        if (input.type === 'text' || input.type === 'number') {
            input.addEventListener('input', () => {
                const pending = settingTimers.get(input);
                if (pending) clearTimeout(pending.timer);
                settingTimers.set(input, { timer: setTimeout(save, 600), save });
            });
            input.addEventListener('blur', save);
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    save();
                }
            });
        } else input.addEventListener('change', save);
    }
    for (const input of body.querySelectorAll('input[name="drawer_thumbnail_cache_policy"]')) {
        input.addEventListener('change', () => {
            thumbnailCachePolicy = input.value || 'keep';
            applySetting('thumbnail_cache_policy', thumbnailCachePolicy);
        });
    }
}

async function withBusyAction(key, button, action) {
    if (busyActions.has(key) || button?.disabled) return;
    busyActions.add(key);
    if (button) button.disabled = true;
    try {
        await action();
    } finally {
        busyActions.delete(key);
        if (button && document.contains(button)) button.disabled = false;
    }
}

async function refreshFreeable() {
    freeableStatus = null;
    renderDrawer();
    const available = await getFreeable(freeupOlderDays).catch(() => null);
    freeableStatus = available || { files: 0, bytes: 0, unavailable: true };
    renderDrawer();
}

function stopFreeUpPolling() {
    clearTimeout(freeupTimer);
    freeupTimer = null;
}

function pollFreeUpJob(jobId) {
    stopFreeUpPolling();
    const tick = async () => {
        const status = await getFreeUpJob(jobId).catch(() => null);
        if (!status) {
            freeupTimer = window.setTimeout(tick, 1200);
            return;
        }
        freeupJob = status;
        patchDrawerStatus();
        if (freeupActive()) {
            freeupTimer = window.setTimeout(tick, 800);
            return;
        }
        stopFreeUpPolling();
        const available = await getFreeable(freeupOlderDays).catch(() => null);
        freeableStatus = available || { files: 0, bytes: 0, unavailable: true };
        renderDrawer();
        if (status.phase === 'completed') showToast(`${bytes(status.bytes_freed)} freed from this computer`);
        else if (status.phase === 'cancelled') showToast(`Free up space stopped · ${bytes(status.bytes_freed)} freed`);
        else showToast('Free up space stopped — remaining originals are untouched');
    };
    tick();
}

async function handleFreeUpAction() {
    if (freeupActive()) {
        const status = await cancelFreeUpJob(freeupJob.job_id);
        if (status) {
            freeupJob = status;
            patchDrawerStatus();
        }
        return;
    }
    const job = await startFreeUpSpace(freeupOlderDays);
    if (!job?.job_id) {
        showToast('Couldn’t start Free up space');
        return;
    }
    freeupJob = job;
    renderDrawer();
    pollFreeUpJob(job.job_id);
}

async function applyCacheDefaults() {
    const defaults = (settingsPageData && settingsPageData.defaults) || {};
    const next = {
        memory_cache_gb: recommendedMemoryGb(settingsPageData.settings || savedSettings),
        ssd_cache_gb: defaults.ssd_cache_gb ?? 100,
        cache_profile: defaults.cache_profile || 'original_heavy',
    };
    const previous = Object.fromEntries(Object.keys(next).map((field) => [field, savedSettings[field]]));
    const result = await saveSettings(next);
    if (!result?.ok) {
        showToast('Couldn’t apply cache defaults');
        return;
    }
    applySettingsData(result);
    renderCurrentSystemSurface();
    showToast('Cache defaults applied', {
        undo: async () => {
            const undone = await saveSettings(previous);
            if (!undone?.ok) throw new Error('undo failed');
            applySettingsData(undone);
            renderCurrentSystemSurface();
        },
    });
}

async function returnToPublish() {
    const target = publishReturn;
    publishReturn = null;
    updateDrawerContext();
    closeSystemDrawer();
    if (!target?.collectionId) {
        showToast('Open Publish from the collection when ready');
        return;
    }
    const { openPublishOverlay } = await import('./panel.js');
    openPublishOverlay(target.collectionId, target.name || 'Collection');
}

function aiInstallActive(status = aiStatus || {}) {
    const index = status.embedding_index || {};
    const installStatus = String(index.install_status || status.install_status || '').toLowerCase();
    return Boolean(index.installing || status.installing || ['starting', 'downloading', 'installing'].includes(installStatus));
}

function pollModelInstall() {
    const generation = ++installTimerGeneration;
    clearInterval(installTimer);
    const tick = async () => {
        const status = await getAiStatus();
        if (generation !== installTimerGeneration) return;
        if (status) aiStatus = status;
        renderActivity();
        if (open) patchDrawerStatus();
        if (!aiInstallActive(status)) {
            clearInterval(installTimer);
            installTimer = null;
        }
    };
    tick();
    installTimer = setInterval(tick, 1500);
}

export function suspendSystemTimers() {
    scanTimerGeneration += 1;
    clearInterval(scanTimer);
    scanTimer = null;
    scanSourceId = null;
    installTimerGeneration += 1;
    clearInterval(installTimer);
    installTimer = null;
    const pendingSaves = [...settingTimers.values()];
    for (const pending of pendingSaves) clearTimeout(pending.timer);
    return Promise.all(pendingSaves.map((pending) => pending.save()));
}

async function saveAndInstallModel() {
    const button = document.getElementById('drawer-install-model');
    if (button) button.disabled = true;
    const saveData = await saveSettings(collectModelSettings());
    if (!saveData || !saveData.ok) {
        showToast('Couldn’t save model settings');
        if (button) button.disabled = false;
        return;
    }
    pendingModelPreset = null;
    applySettingsData(saveData);
    const installData = await installAiModel('active');
    if (installData && installData.ok) {
        aiStatus = installData.ai_status || aiStatus;
        showToast(installData.already_installed ? 'Model already installed' : 'Model install started');
        pollModelInstall();
    } else {
        showToast('Couldn’t start model install');
    }
    renderActivity();
    renderCurrentSystemSurface();
}

function renderLibraryHealthSection() {
    const body = systemSurfaceRender
        ? document.getElementById('system-lens-content')
        : document.getElementById('drawer-body');
    const current = body?.querySelector('.library-health');
    if (!body || !current) return;
    const template = document.createElement('template');
    template.innerHTML = renderLibraryHealth(catalog);
    const next = template.content.firstElementChild;
    if (!next) return;
    current.replaceWith(next);
    bindLibraryHealthActions(body);
}

function bindLibraryHealthActions(body) {
    bindLibraryHealth(body, {
        rerender: renderLibraryHealthSection,
        refreshCatalog: async () => {
            catalog = await getCatalog().catch(() => catalog);
            patchDrawerStatus();
            renderLibraryHealthSection();
            showToast('Sources checked');
        },
    });
}

function bindDrawerActions(body = document.getElementById('drawer-body')) {
    if (!body) return;
    bindLibraryHealthActions(body);
    bindSettingInputs(body);
    body.querySelector('#dismiss-server-update')?.addEventListener('click', () => {
        sessionStorage.setItem('azimuth-server-update-dismissed', '1');
        renderCurrentSystemSurface();
    });
    body.querySelector('#link-device-btn')?.addEventListener('click', (event) => withBusyAction('link-device', event.currentTarget, async () => {
        const result = await createDeviceLink();
        if (result && result.code) {
            linkSession = result;
            renderCurrentSystemSurface();
            showToast('Pairing code ready');
        } else {
            showToast('Couldn’t create a pairing code');
        }
    }));
    for (const btn of body.querySelectorAll('[data-revoke-device]')) {
        btn.addEventListener('click', () => withBusyAction(`revoke-${btn.dataset.revokeDevice}`, btn, async () => {
            const result = await revokeDevice(Number(btn.dataset.revokeDevice));
            if (result && result.ok) {
                devicesPayload = await listDevices().catch(() => devicesPayload);
                renderCurrentSystemSurface();
                showToast('Device revoked');
            } else {
                showToast('Couldn’t revoke device');
            }
        }));
    }
    body.querySelector('#discover-hubs-btn')?.addEventListener('click', (event) => withBusyAction('discover-hubs', event.currentTarget, async () => {
        discoverPayload = await discoverHubs().catch(() => null);
        renderCurrentSystemSurface();
        const count = (discoverPayload && discoverPayload.hubs && discoverPayload.hubs.length) || 0;
        showToast(count ? `Found ${count} hub${count === 1 ? '' : 's'}` : 'No hubs found');
    }));
    for (const btn of body.querySelectorAll('[data-hub-url]')) {
        btn.addEventListener('click', () => {
            const input = document.getElementById('connect-hub-url');
            if (input) input.value = btn.dataset.hubUrl || '';
        });
    }
    body.querySelector('#connect-hub-btn')?.addEventListener('click', (event) => withBusyAction('connect-hub', event.currentTarget, async () => {
        const hubUrl = document.getElementById('connect-hub-url')?.value?.trim() || '';
        const code = document.getElementById('connect-pair-code')?.value?.trim() || '';
        if (!hubUrl || !code) {
            showToast('Enter a hub URL and pair code');
            return;
        }
        const result = await connectToHub({ hubUrl, code });
        if (result && result.ok) {
            pairStatus = await getPairStatus().catch(() => pairStatus);
            renderCurrentSystemSurface();
            showToast('Connected to hub');
        } else {
            showToast((result && (result.error || result.detail)) || 'Couldn’t connect');
        }
    }));
    body.querySelector('#drawer-cache-defaults')?.addEventListener('click', applyCacheDefaults);
    body.querySelector('#drawer-install-model')?.addEventListener('click', (event) => withBusyAction('model-install', event.currentTarget, saveAndInstallModel));
    body.querySelector('#drawer-return-publish')?.addEventListener('click', returnToPublish);
    bindSourcePicker(body, {
        onSubmit: (payload) => submitSourceAdd(payload, { onSuccess: renderCurrentSystemSurface }),
    });
    bindArchiveOpen(body);
    for (const btn of body.querySelectorAll('[data-act]')) {
        btn.addEventListener('click', () => {
            if (btn.getAttribute('aria-disabled') === 'true') return;
            withBusyAction(`source-${btn.dataset.act}-${btn.closest('.src-card')?.dataset.sourceId || ''}`, btn, () => handleSourceAction(btn.closest('.src-card'), btn.dataset.act));
        });
    }
    for (const card of body.querySelectorAll('.src-card[data-source-path]')) {
        card.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            openSourceRevealMenu(card.dataset.sourcePath || '', card, card.dataset.sourceCount, {
                sourceId: card.dataset.sourceId,
                revealAvailable: !String(card.dataset.sourcePath || '').toLowerCase().startsWith('hub:'),
            });
        });
    }
    for (const btn of body.querySelectorAll('[data-mode]')) {
        btn.addEventListener('click', () => withBusyAction(`source-remove-${btn.closest('.src-card')?.dataset.sourceId || ''}`, btn, () => handleRemove(btn.closest('.src-card'), btn.dataset.mode)));
    }
    for (const btn of body.querySelectorAll('[data-remove-cancel]')) {
        btn.addEventListener('click', () => {
            const choice = btn.closest('.remove-choice');
            if (choice) choice.hidden = true;
        });
    }
    for (const btn of body.querySelectorAll('[data-worker-action]')) {
        btn.addEventListener('click', () => withBusyAction(`worker-${btn.dataset.workerAction}`, btn, async () => {
            const key = btn.dataset.workerAction;
            workerActionGenerations.set(key, (workerActionGenerations.get(key) || 0) + 1);
            workerActionsInFlight.add(key);
            let result = null;
            try {
                if (key === 'ai') result = aiStatus && aiStatus.embedding_manual_pause ? await resumeAiEmbeddings() : await pauseAiEmbeddings();
                if (key === 'cache') {
                    const pregen = (cacheStatus && cacheStatus.pregen) || {};
                    result = pregen.manual_pause || pregen.state === 'paused' ? await startCachePregen() : await stopCachePregen();
                }
                if (key === 'people') {
                    const worker = (peopleStatus && peopleStatus.worker) || {};
                    result = worker.manual_pause ? await resumePeopleScan() : await pausePeopleScan();
                }
                if (key === 'captions') result = captionStatus && captionStatus.active ? await pauseCaptionScan() : await resumeCaptionScan();
                if (key === 'metadata') result = metadataStatus && metadataStatus.manual_pause ? await startMetadataScan() : await stopMetadataScan();
                if (!result) showToast('Couldn’t update background work');
            } catch {
                showToast('Couldn’t update background work');
            } finally {
                workerActionGenerations.set(key, (workerActionGenerations.get(key) || 0) + 1);
                workerActionsInFlight.delete(key);
            }
            await refreshDrawer();
        }));
    }
    body.querySelector('#clear-cache-btn')?.addEventListener('click', (event) => withBusyAction('clear-cache', event.currentTarget, async () => {
        const count = Number(event.currentTarget.dataset.cacheFiles || 0);
        const confirmed = await confirmTypedCount({
            title: 'Clear image cache?',
            message: 'This deletes generated previews from disk and cannot be undone. Type the file count to continue.',
            count,
            confirmLabel: 'Clear cache',
        });
        if (!confirmed) return;
        const result = await clearCache();
        if (result && result.ok) {
            cacheStatus = result.cache_stats || await getCacheStatus().catch(() => cacheStatus);
            renderCurrentSystemSurface();
            showToast('Cache cleared. Undo is unavailable.');
        } else showToast('Couldn’t clear cache');
    }));
    body.querySelector('#freeup-age')?.addEventListener('change', (event) => {
        freeupOlderDays = Number(event.currentTarget.value || 0);
        refreshFreeable();
    });
    body.querySelector('#freeup-btn')?.addEventListener('click', (event) => withBusyAction(
        'freeup', event.currentTarget, handleFreeUpAction,
    ));
    body.querySelector('#copy-remote')?.addEventListener('click', async (event) => {
        if (event.currentTarget.getAttribute('aria-disabled') === 'true') return;
        const url = (remoteAccess && remoteAccess.tailscale && remoteAccess.tailscale.https_url)
            || (remoteAccess && remoteAccess.tailscale && remoteAccess.tailscale.url)
            || (remoteAccess && remoteAccess.current_url)
            || '';
        try {
            await navigator.clipboard.writeText(url);
            showToast('Link copied');
        } catch {
            showToast('Couldn’t copy');
        }
    });
    body.querySelector('#remote-apply-serve')?.addEventListener('click', (event) => withBusyAction('remote-serve', event.currentTarget, async () => {
        const result = await applyRemoteAccessServe();
        if (!result || !result.ok) {
            showToast((result && result.error) || 'Couldn’t apply Tailscale Serve');
            return;
        }
        remoteAccess = {
            ...(remoteAccess || {}),
            hub_mode: true,
            tailscale: {
                ...((remoteAccess && remoteAccess.tailscale) || {}),
                ...(result.tailscale || {}),
                https_url: result.https_url || (result.tailscale && result.tailscale.https_url) || '',
                serve_applied: true,
                state: 'up',
            },
        };
        renderCurrentSystemSurface();
        showToast(result.dry_run ? 'HTTPS ready (dry-run)' : 'HTTPS ready on your tailnet');
    }));
    body.querySelector('#drawer-open-shared')?.addEventListener('click', () => {
        closeSystemDrawer();
        setActiveLens('shared');
    });
    body.querySelector('#drawer-thumb-size')?.addEventListener('change', (event) => {
        const previous = viewState.thumbSize;
        setThumbSize(event.target.value);
        showToast('Setting saved', { undo: () => setThumbSize(previous) });
    });
    body.querySelector('[data-pref-sel="density"]')?.addEventListener('change', (event) => applyPreference({ density: event.target.value }));
    for (const input of body.querySelectorAll('[data-pref]')) {
        input.addEventListener('change', () => applyPreference({ [input.dataset.pref]: input.checked }));
    }
}

function startDrawerPolling() {
    clearInterval(drawerTimer);
    refreshDrawer({ initial: true });
    drawerTimer = setInterval(refreshDrawer, 5000);
}

function stopDrawerPolling() {
    clearInterval(drawerTimer);
    drawerTimer = null;
    clearInterval(installTimer);
    installTimer = null;
    stopLibraryHealthPolling();
    if (!freeupActive()) stopFreeUpPolling();
}

function resolvePublishReturnTarget() {
    const overlay = document.getElementById('deliver-overlay');
    return {
        collectionId: Number(overlay?.dataset.collectionId) || 0,
        name: overlay?.dataset.collectionName || 'Collection',
    };
}

export function openPublishingSettings({ returnTo = null } = {}) {
    publishReturn = returnTo;
    publishingFocusPending = true;
    openSettingSections.add('Publishing');
    localStorage.setItem('pa_d_system_section', 'publishing');
    sessionStorage.setItem('pa_d_system_focus_publish', '1');
    document.dispatchEvent(new CustomEvent('system:section', { detail: 'publishing' }));
    if (open) closeSystemDrawer();
    setActiveLens('system');
}

export function openSystemSettings(section = 'library') {
    localStorage.setItem('pa_d_system_section', section);
    document.dispatchEvent(new CustomEvent('system:section', { detail: section }));
    if (open) closeSystemDrawer();
    setActiveLens('system');
}

export function openSystemDrawer() {
    if (open) {
        if (publishingFocusPending || publishReturn) renderCurrentSystemSurface();
        return;
    }
    const drawer = document.getElementById('drawer');
    const scrim = document.getElementById('drawer-scrim');
    open = true;
    scrim.hidden = false;
    drawer.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
        scrim.classList.add('on');
        drawer.classList.add('on');
    });
    trapFocus(drawer, document.getElementById('drawer-close'));
    startDrawerPolling();
}

export function closeSystemDrawer() {
    if (!open) return;
    const drawer = document.getElementById('drawer');
    const scrim = document.getElementById('drawer-scrim');
    open = false;
    publishingFocusPending = false;
    updateDrawerContext();
    scrim.classList.remove('on');
    drawer.classList.remove('on');
    drawer.setAttribute('aria-hidden', 'true');
    afterMotion('slow', () => {
        if (!open) scrim.hidden = true;
    });
    releaseFocus(drawer);
    stopDrawerPolling();
}

export function systemDrawerOpen() {
    return open;
}

export function initDrawer() {
    document.getElementById('system-btn').addEventListener('click', () => openSystemSettings());
    document.getElementById('activity-widget').addEventListener('click', openSystemDrawer);
    document.getElementById('drawer-close').addEventListener('click', closeSystemDrawer);
    document.getElementById('drawer-scrim').addEventListener('click', closeSystemDrawer);
    document.getElementById('drawer').addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeSystemDrawer();
        }
    });
    document.addEventListener('click', (event) => {
        const button = event.target.closest('#publish-open-settings, [data-deliver-open-settings]');
        if (!button) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const returnTo = resolvePublishReturnTarget();
        if (button.matches('[data-deliver-open-settings]')) {
            document.querySelector('#deliver-overlay #deliver-close')?.click();
        } else document.getElementById('publish-close')?.click();
        openPublishingSettings({ returnTo });
    }, true);
    on('thumbsize', () => {
        const input = document.getElementById('drawer-thumb-size');
        if (input) input.value = String(viewState.thumbSize);
    });
    on('prefs', () => {
        if (open) renderCurrentSystemSurface();
    });
    refreshActivity();
    activityTimer = setInterval(refreshActivity, 10000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refreshActivity();
    });
}
