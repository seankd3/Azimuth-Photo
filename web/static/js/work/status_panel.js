import {
    formatEta,
    formatRatePerMinute,
} from '../settings/display.js';

function clampPct(value) {
    const pct = Number(value || 0);
    if (!Number.isFinite(pct)) return 0;
    return Math.max(0, Math.min(100, pct));
}

function workStateLabel(state, fallback = 'Working') {
    const value = String(state || fallback).replace(/_/g, ' ').trim() || fallback;
    return value.replace(/\b\w/g, (char) => char.toUpperCase());
}

function processState({
    done = 0,
    total = 0,
    remaining = Math.max(0, Number(total || 0) - Number(done || 0)),
    etaSeconds = null,
    rate = 0,
    paused = false,
    active = false,
    installing = false,
    installed = true,
    state = '',
    message = '',
} = {}) {
    const doneNum = Number(done || 0);
    const totalNum = Number(total || 0);
    const remainingNum = Math.max(0, Number(remaining || 0));
    const complete = totalNum > 0 && remainingNum <= 0;
    if (!installed) return { tone: 'missing', label: 'Missing', detail: message || 'Install needed', eta: '-' };
    if (installing) return { tone: 'active', label: 'Installing', detail: message || 'Downloading model', eta: '-' };
    if (active) {
        return {
            tone: 'active',
            label: workStateLabel(state),
            detail: Number(rate || 0) > 0 ? formatRatePerMinute(rate) : (message || 'Measuring rate'),
            eta: etaSeconds ? formatEta(etaSeconds) : 'Measuring',
        };
    }
    if (complete) return { tone: 'done', label: 'Complete', detail: `${doneNum.toLocaleString()} ready`, eta: 'Done' };
    if (paused) return { tone: 'paused', label: 'Paused', detail: `${remainingNum.toLocaleString()} remaining`, eta: 'Paused' };
    return { tone: 'idle', label: 'Waiting', detail: `${remainingNum.toLocaleString()} remaining`, eta: '-' };
}

function renderRow(row) {
    const pct = clampPct(row.pct);
    const done = Number(row.done || 0).toLocaleString();
    const total = Number(row.total || 0).toLocaleString();
    return `
        <div class="background-work-row ${row.tone || 'idle'} ${row.kind || ''}">
            <div class="background-work-row-head">
                <span class="background-work-label">${row.label}</span>
                <span class="background-work-pill">${row.state}</span>
            </div>
            <div class="background-work-progress">
                <div class="background-work-progress-fill" style="width:${pct}%"></div>
            </div>
            <div class="background-work-meta">
                <span class="background-work-progress-text">${done} / ${total} - ${pct.toFixed(1)}%</span>
                <span class="background-work-detail">${row.detail}</span>
                <strong class="background-work-eta">${row.eta}</strong>
            </div>
        </div>
    `;
}

export function backgroundProcessRows(aiStatus = {}, cacheStatus = {}) {
    const fast = aiStatus.embedding_indexes?.fast || aiStatus;
    const deep = aiStatus.embedding_indexes?.deep || {};
    const pregen = cacheStatus.pregen || {};
    const diskTiers = cacheStatus.disk?.tiers || {};
    const preview = pregen.preview || {};
    const originals = pregen.originals || {};
    const fullTier = diskTiers.full || {};

    const previewTotal = Number(preview.total || 0);
    const previewDone = Number(preview.count || 0);
    const originalTotal = Number(originals.total ?? fullTier.progress_total ?? 0);
    const originalDone = Number(originals.count ?? fullTier.progress_count ?? fullTier.count ?? 0);
    const cacheActive = pregen.state === 'running';
    const cachePaused = Boolean(pregen.manual_pause);
    const fastWorkerState = String(fast.worker_state || aiStatus.worker_state || '');
    const fastMessage = fast.worker_message || aiStatus.worker_message;
    const deepWorkerState = String(deep.worker_state || '');

    const fastState = processState({
        done: fast.embedded,
        total: fast.total_images,
        remaining: fast.remaining,
        etaSeconds: aiStatus.eta_seconds,
        rate: aiStatus.recent_images_per_min || aiStatus.overall_images_per_min,
        paused: Boolean(fast.manual_pause ?? aiStatus.embedding_manual_pause) && fastWorkerState !== 'loading_model',
        active: ['embedding', 'loading_model'].includes(fastWorkerState),
        installing: Boolean(fast.installing || aiStatus.installing),
        installed: Boolean(fast.installed ?? aiStatus.model_installed ?? true),
        state: fastWorkerState,
        message: fastWorkerState === 'loading_model' ? (fastMessage || 'Loading 2B search model') : fastMessage,
    });
    const deepState = processState({
        done: deep.embedded,
        total: deep.total_images,
        remaining: deep.remaining,
        etaSeconds: null,
        rate: 0,
        paused: deepWorkerState === 'paused',
        active: ['embedding', 'loading_model', 'scheduled'].includes(deepWorkerState),
        installing: Boolean(deep.installing),
        installed: Boolean(deep.installed ?? true),
        state: deepWorkerState,
        message: deep.worker_message || `${Number(deep.embedded_queries || 0).toLocaleString()} cached queries`,
    });
    const previewState = processState({
        done: previewDone,
        total: previewTotal,
        remaining: preview.image_remaining ?? preview.remaining,
        etaSeconds: pregen.eta_seconds,
        rate: pregen.recent_images_per_min || pregen.overall_images_per_min,
        paused: cachePaused,
        active: cacheActive && (!pregen.active_phase || pregen.active_phase === 'previews'),
        state: pregen.active_phase || pregen.state,
        message: pregen.message,
    });
    const originalState = processState({
        done: originalDone,
        total: originalTotal,
        remaining: originals.remaining,
        etaSeconds: pregen.original_eta_seconds,
        rate: pregen.recent_images_per_min || pregen.overall_images_per_min,
        paused: cachePaused,
        active: cacheActive && pregen.active_phase === 'full',
        state: pregen.active_phase || pregen.state,
        message: pregen.message,
    });

    return [
        {
            kind: 'fast',
            label: 'Daily Search',
            done: Number(fast.embedded || 0),
            total: Number(fast.total_images || 0),
            pct: fast.progress_pct,
            state: fastState.label,
            detail: fastState.detail,
            eta: fastState.eta,
            tone: fastState.tone,
        },
        {
            kind: 'deep',
            label: 'Deep Search',
            done: Number(deep.embedded || 0),
            total: Number(deep.total_images || 0),
            pct: deep.progress_pct,
            state: deepState.label,
            detail: deepState.detail,
            eta: deepState.eta,
            tone: deepState.tone,
        },
        {
            kind: 'preview',
            label: 'Preview Cache',
            done: previewDone,
            total: previewTotal,
            pct: preview.progress_pct || (previewTotal > 0 ? (previewDone / previewTotal) * 100 : 0),
            state: previewState.label,
            detail: previewState.detail,
            eta: previewState.eta,
            tone: previewState.tone,
        },
        {
            kind: 'originals',
            label: 'Original SSD Cache',
            done: originalDone,
            total: originalTotal,
            pct: originalTotal > 0 ? (originalDone / originalTotal) * 100 : Number(originals.progress_pct || fullTier.progress_pct || 0),
            state: originalState.label,
            detail: originalState.detail,
            eta: originalState.eta,
            tone: originalState.tone,
        },
    ];
}

export function activeBackgroundWorkRow(aiStatus = {}, cacheStatus = {}) {
    const rows = backgroundProcessRows(aiStatus, cacheStatus || {});
    return rows.find((row) => row.tone === 'active') || null;
}

export function renderBackgroundWorkPanel(aiStatus = {}, {
    cacheStatus = null,
    documentImpl = globalThis.document,
} = {}) {
    const container = documentImpl?.getElementById?.('background-work-panel-rows');
    if (!container) return false;
    const rows = backgroundProcessRows(aiStatus, cacheStatus || {});
    container.innerHTML = rows.map(renderRow).join('');
    return true;
}

export function renderBackgroundWorkSummary(aiStatus = {}, {
    cacheStatus = null,
    documentImpl = globalThis.document,
} = {}) {
    const total = Number(aiStatus.total_images ?? aiStatus.total_kept ?? 0);
    const embedded = Number(aiStatus.embedded || 0);
    const activeRow = activeBackgroundWorkRow(aiStatus, cacheStatus || {});
    const pct = activeRow ? clampPct(activeRow.pct) : 0;
    const countEl = documentImpl?.getElementById?.('ai-embed-count');
    const totalEl = documentImpl?.getElementById?.('ai-embed-total');
    const stateEl = documentImpl?.getElementById?.('ai-model-state');
    const workEl = documentImpl?.getElementById?.('bar-work');
    const labelEl = workEl?.querySelector?.('.bar-ai-label');
    if (workEl) {
        workEl.style.setProperty('--work-progress', `${pct}%`);
        workEl.style.display = activeRow ? '' : 'none';
        workEl.title = activeRow
            ? `${activeRow.label}: ${activeRow.state}`
            : 'No active background work';
    }
    if (labelEl) labelEl.textContent = activeRow ? activeRow.label : 'Work';
    if (countEl) countEl.textContent = activeRow ? Number(activeRow.done || 0).toLocaleString() : '';
    if (totalEl) totalEl.textContent = activeRow ? Number(activeRow.total || 0).toLocaleString() : '';
    if (stateEl) {
        if (activeRow) {
            stateEl.textContent = activeRow.state;
            stateEl.className = 'bar-ai-state embedding';
        } else {
            stateEl.textContent = '';
            stateEl.className = 'bar-ai-state';
        }
    }
    return { totalImages: total, activeRow };
}

export async function fetchBackgroundWorkCacheStatus({
    fetchImpl = (url) => globalThis.fetch(url),
} = {}) {
    const response = await fetchImpl('/api/cache/status');
    return response.json();
}

export function toggleBackgroundWorkPanel({ documentImpl = globalThis.document } = {}) {
    const panel = documentImpl?.getElementById?.('background-work-panel');
    if (!panel) return false;
    panel.classList.toggle('hidden');
    return true;
}


export function bindBackgroundWorkPanel({
    documentImpl = globalThis.document,
} = {}) {
    const button = documentImpl?.getElementById?.('bar-work');
    if (!button || button.dataset.backgroundWorkBound === '1') return false;
    button.dataset.backgroundWorkBound = '1';
    button.addEventListener('click', (event) => {
        if (event.target?.closest?.('#background-work-panel')) return;
        if (event.target?.closest?.('a,button')) return;
        toggleBackgroundWorkPanel({ documentImpl });
    });
    return true;
}
