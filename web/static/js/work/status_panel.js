import {
    formatEta,
    formatRatePerMinute,
} from '../settings/display.js';
import { peopleWorkerLabel } from '../people/labels.js';

function clampPct(value) {
    const pct = Number(value || 0);
    if (!Number.isFinite(pct)) return 0;
    return Math.max(0, Math.min(100, pct));
}

function workStateLabel(state, fallback = 'Working') {
    const value = String(state || fallback).replace(/_/g, ' ').trim() || fallback;
    return value.replace(/\b\w/g, (char) => char.toUpperCase());
}

function stateClass(tone) {
    if (tone === 'active') return 'embedding';
    if (tone === 'paused') return 'paused';
    if (tone === 'missing') return 'missing';
    if (tone === 'error') return 'error';
    return '';
}

function hasRemainingWork(row) {
    return Number(row?.total || 0) > Number(row?.done || 0);
}

function isFallbackOnlyRow(row) {
    return Boolean(row?.fallbackOnly);
}

function escapeAttr(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const BACKGROUND_WORK_ACTIONS = {
    embedding: {
        pause: { url: '/api/ai/embeddings/pause', pending: 'Stopping…' },
        resume: { url: '/api/ai/embeddings/resume', pending: 'Starting…' },
    },
    preview: {
        pause: { url: '/api/cache/pregen/stop', pending: 'Stopping…' },
        resume: { url: '/api/cache/pregen/start', pending: 'Starting…' },
    },
    faces: {
        pause: { url: '/api/people/scan/pause', pending: 'Stopping…' },
        resume: { url: '/api/people/scan/resume', pending: 'Starting…' },
    },
};

const ACTION_PIN_MS = 8000;

const lastBackgroundWorkState = {
    aiStatus: {},
    cacheStatus: null,
    peopleStatus: null,
    lastGoodAiStatus: null,
    lastGoodCacheStatus: null,
    lastGoodPeopleStatus: null,
    lastPeopleRow: null,
    pins: {},
};

function snapshotAiStatus(aiStatus = {}) {
    return {
        ...aiStatus,
        embedding_index: aiStatus.embedding_index ? { ...aiStatus.embedding_index } : undefined,
    };
}

function snapshotCacheStatus(cacheStatus = {}) {
    return {
        ...cacheStatus,
        pregen: cacheStatus.pregen ? { ...cacheStatus.pregen } : undefined,
        disk: cacheStatus.disk ? { ...cacheStatus.disk } : undefined,
        memory: cacheStatus.memory ? { ...cacheStatus.memory } : undefined,
    };
}

function snapshotPeopleStatus(peopleStatus = {}) {
    return {
        ...peopleStatus,
        worker: peopleStatus.worker ? { ...peopleStatus.worker } : undefined,
        counts: peopleStatus.counts ? { ...peopleStatus.counts } : undefined,
    };
}

function hasUsableAiSnapshot(aiStatus = {}) {
    return Number(aiStatus.total_images || 0) > 0
        || Number(aiStatus.embedded || 0) > 0
        || ['embedding', 'loading_model', 'error', 'paused', 'idle'].includes(String(aiStatus.worker_state || ''));
}

function hasUsableCacheSnapshot(cacheStatus = {}) {
    const pregen = cacheStatus.pregen || {};
    const preview = pregen.preview || {};
    const originals = pregen.originals || {};
    return Number(preview.total || 0) > 0
        || Number(originals.total || 0) > 0
        || pregen.state === 'running';
}

function hasUsablePeopleSnapshot(peopleStatus = {}) {
    const counts = peopleStatus.counts || {};
    return Number(counts.pending_cached_images || 0) > 0
        || Number(counts.detected_faces || 0) > 0
        || Number((counts.scan || {}).scanned || 0) > 0
        || ['installing', 'scanning'].includes(String(peopleStatus.worker?.state || ''));
}

function isStaleAiStatus(aiStatus = {}) {
    if (!aiStatus || typeof aiStatus !== 'object') return true;
    if (aiStatus.status_stale || aiStatus.counts_stale) return true;
    if (aiStatus.worker_state === 'stale') return true;
    if (String(aiStatus.worker_message || '').includes('Status unavailable') && !hasUsableAiSnapshot(aiStatus)) return true;
    return false;
}

function isStaleCacheStatus(cacheStatus = null) {
    if (!cacheStatus || typeof cacheStatus !== 'object') return true;
    if (cacheStatus.status_stale || cacheStatus.counts_stale) return true;
    return false;
}

function isStalePeopleStatus(peopleStatus = {}) {
    if (!peopleStatus || typeof peopleStatus !== 'object') return true;
    return Boolean(peopleStatus.status_stale || peopleStatus.counts_stale);
}

function staleStatusDetail(incoming = {}, fallback = 'Updating status…') {
    const latency = Number(incoming.latency_ms || 0);
    return latency > 0 ? `Status delayed ${latency.toFixed(0)}ms` : fallback;
}

function unknownStaleRow(kind, label, incoming = {}) {
    return {
        kind,
        label,
        done: 0,
        total: 0,
        pct: 0,
        state: 'Updating',
        detail: staleStatusDetail(incoming),
        eta: '-',
        tone: 'idle',
        hideProgress: true,
        control: null,
        fallbackOnly: true,
    };
}

function isTransientDatabaseLock(value) {
    const text = String(value || '').toLowerCase();
    return text.includes('database is locked')
        || text.includes('database table is locked')
        || text.includes('database schema is locked');
}

function mergeStaleAiStatus(incoming = {}, previous = null) {
    if (!isStaleAiStatus(incoming)) {
        lastBackgroundWorkState.lastGoodAiStatus = snapshotAiStatus(incoming);
        return incoming;
    }
    if (!previous || !hasUsableAiSnapshot(previous)) return incoming;
    return {
        ...previous,
        status_stale: true,
        worker_message: staleStatusDetail(incoming, previous.worker_message || 'Status temporarily unavailable'),
    };
}

function mergeStaleCacheStatus(incoming = {}, previous = null) {
    if (!isStaleCacheStatus(incoming)) {
        lastBackgroundWorkState.lastGoodCacheStatus = snapshotCacheStatus(incoming);
        return incoming;
    }
    if (!previous || !hasUsableCacheSnapshot(previous)) return incoming;
    return {
        ...previous,
        status_stale: true,
        counts_stale: true,
        latency_ms: incoming.latency_ms,
    };
}

function mergeStalePeopleStatus(incoming = {}, previous = null) {
    if (!isStalePeopleStatus(incoming)) {
        lastBackgroundWorkState.lastGoodPeopleStatus = snapshotPeopleStatus(incoming);
        return incoming;
    }
    if (!previous || !hasUsablePeopleSnapshot(previous)) return incoming;
    return {
        ...previous,
        status_stale: true,
        counts_stale: true,
        latency_ms: incoming.latency_ms,
        worker: {
            ...(previous.worker || {}),
            message: staleStatusDetail(incoming, previous.worker?.message || 'Status temporarily unavailable'),
        },
    };
}

function pinBackgroundWorkKind(kind) {
    lastBackgroundWorkState.pins[kind] = Date.now();
}

function clearBackgroundWorkPin(kind) {
    delete lastBackgroundWorkState.pins[kind];
}

function isBackgroundWorkKindPinned(kind) {
    const pinnedAt = Number(lastBackgroundWorkState.pins[kind] || 0);
    return pinnedAt > 0 && (Date.now() - pinnedAt) < ACTION_PIN_MS;
}

function workControl(kind, stopped, {
    available = true,
    tooltip = '',
} = {}) {
    if (!BACKGROUND_WORK_ACTIONS[kind]) return null;
    const command = stopped ? 'resume' : 'pause';
    return {
        kind,
        command,
        label: stopped ? 'Start' : 'Stop',
        stopped,
        available,
        tooltip: tooltip || (stopped ? `Start ${kind}.` : `Stop ${kind}.`),
    };
}

export function ingestBackgroundWorkPoll(aiStatus = {}, cacheStatus = null, peopleStatus = null) {
    let nextAi = mergeStaleAiStatus(aiStatus, lastBackgroundWorkState.lastGoodAiStatus);
    let nextCache = mergeStaleCacheStatus(cacheStatus || {}, lastBackgroundWorkState.lastGoodCacheStatus);
    let nextPeople = mergeStalePeopleStatus(peopleStatus || {}, lastBackgroundWorkState.lastGoodPeopleStatus);

    if (isBackgroundWorkKindPinned('embedding') && lastBackgroundWorkState.aiStatus) {
        const pinnedPause = Boolean(lastBackgroundWorkState.aiStatus.embedding_manual_pause);
        const pollPause = Boolean(aiStatus.embedding_manual_pause);
        if (pollPause === pinnedPause) {
            clearBackgroundWorkPin('embedding');
        } else {
            nextAi = {
                ...aiStatus,
                embedding_manual_pause: pinnedPause,
                embedding_index: {
                    ...(aiStatus.embedding_index || {}),
                    manual_pause: pinnedPause,
                },
            };
        }
    }

    if (isBackgroundWorkKindPinned('preview') && lastBackgroundWorkState.cacheStatus?.pregen) {
        const pinnedPause = Boolean(lastBackgroundWorkState.cacheStatus.pregen.manual_pause);
        const pollPause = Boolean(cacheStatus?.pregen?.manual_pause);
        if (pollPause === pinnedPause) {
            clearBackgroundWorkPin('preview');
        } else {
            nextCache = {
                ...(cacheStatus || {}),
                pregen: {
                    ...(cacheStatus?.pregen || {}),
                    manual_pause: pinnedPause,
                },
            };
        }
    }

    if (isBackgroundWorkKindPinned('faces') && lastBackgroundWorkState.peopleStatus) {
        const pinnedActive = Boolean(lastBackgroundWorkState.peopleStatus.active);
        const pollActive = Boolean(peopleStatus?.active);
        if (pollActive === pinnedActive) {
            clearBackgroundWorkPin('faces');
        } else {
            nextPeople = {
                ...(peopleStatus || {}),
                active: pinnedActive,
            };
        }
    }

    return {
        aiStatus: nextAi,
        cacheStatus: nextCache,
        peopleStatus: nextPeople,
    };
}

function renderRowControl(row) {
    const control = row.control;
    if (!control) return '';
    return `
        <span class="background-work-action-group" role="group" aria-label="${escapeAttr(`${row.label} controls`)}">
            <button
                type="button"
                class="background-work-action"
                data-work-kind="${escapeAttr(control.kind)}"
                data-work-command="${escapeAttr(control.command)}"
                aria-label="${escapeAttr(`${control.label} ${row.label}`)}"
                ${control.available ? '' : 'disabled'}
            >${escapeHtml(control.label)}</button>
        </span>
    `;
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
    errored = false,
    state = '',
    message = '',
} = {}) {
    const doneNum = Number(done || 0);
    const totalNum = Number(total || 0);
    const remainingNum = Math.max(0, Number(remaining || 0));
    const complete = totalNum > 0 && remainingNum <= 0;
    if (!installed) return { tone: 'missing', label: 'Needs Setup', detail: message || 'Install needed', eta: '-' };
    if (installing) return { tone: 'missing', label: 'Installing', detail: message || 'Installing model', eta: '-' };
    if (errored) {
        return {
            tone: 'error',
            label: 'Error',
            detail: message || 'Needs attention',
            eta: '-',
        };
    }
    if (active) {
        return {
            tone: 'active',
            label: 'Running',
            detail: Number(rate || 0) > 0 ? formatRatePerMinute(rate) : (message || 'Measuring rate'),
            eta: etaSeconds ? formatEta(etaSeconds) : 'Measuring',
        };
    }
    if (complete) return { tone: 'done', label: 'Done', detail: `${doneNum.toLocaleString()} ready`, eta: 'Done' };
    if (paused) return { tone: 'paused', label: 'Stopped', detail: `${remainingNum.toLocaleString()} remaining`, eta: 'Stopped' };
    if (remainingNum > 0) {
        return {
            tone: 'idle',
            label: 'Running',
            detail: message || `${remainingNum.toLocaleString()} remaining`,
            eta: '-',
        };
    }
    return { tone: 'idle', label: 'Waiting', detail: message || 'Waiting', eta: '-' };
}

function renderRow(row) {
    const pct = clampPct(row.pct);
    const done = Number(row.done || 0).toLocaleString();
    const total = Number(row.total || 0).toLocaleString();
    const progressHtml = row.hideProgress
        ? ''
        : `
            <div class="background-work-progress">
                <div class="background-work-progress-fill" style="width:${pct}%"></div>
            </div>
        `;
    const progressText = row.hideProgress
        ? ''
        : `<span class="background-work-progress-text">${done} / ${total} - ${pct.toFixed(1)}%</span>`;
    const controlHtml = renderRowControl(row);
    const toolsClass = controlHtml ? ' has-actions' : '';
    return `
        <div class="background-work-row ${row.tone || 'idle'} ${row.kind || ''}">
            <div class="background-work-row-head">
                <span class="background-work-label">${escapeHtml(row.label)}</span>
                <span class="background-work-row-tools${toolsClass}">
                    <span class="background-work-pill">${escapeHtml(row.state)}</span>
                    ${controlHtml}
                </span>
            </div>
            ${progressHtml}
            <div class="background-work-meta${row.hideProgress ? ' background-work-meta-compact' : ''}">
                ${progressText}
                <span class="background-work-detail">${escapeHtml(row.detail)}</span>
                <strong class="background-work-eta">${escapeHtml(row.eta)}</strong>
            </div>
        </div>
    `;
}

function peopleQueueRow(peopleStatus = null, previousRow = null) {
    if (!peopleStatus) {
        return {
            kind: 'faces',
            label: 'People',
            done: 0,
            total: 0,
            pct: 0,
            state: 'Waiting',
            detail: 'Waiting for People status',
            eta: '-',
            tone: 'idle',
            control: workControl('faces', true, { available: true }),
        };
    }
    const worker = peopleStatus.worker || {};
    const counts = peopleStatus.counts || {};
    const scan = counts.scan || {};
    const state = String(worker.state || '').trim();
    const workerPending = Number(worker.pending_cached_images || 0) || 0;
    const countsPending = Number(counts.pending_cached_images || 0) || 0;
    const pending = Math.max(0, workerPending, countsPending);
    const scanned = Math.max(0, Number(scan.scanned || 0) || 0);
    const errors = Math.max(0, Number(scan.error || 0) || 0);
    const done = Math.max(0, scanned + errors);
    const total = Math.max(done, done + pending);
    const active = ['installing', 'scanning'].includes(state);
    const stopped = !peopleStatus.active;
    const errored = state === 'error' || Boolean(worker.last_error);
    const complete = total > 0 && pending <= 0 && !active;
    const queued = pending > 0 && !active && !stopped && !errored;
    const stale = Boolean(peopleStatus.counts_stale || peopleStatus.status_stale);
    const delayedDetail = staleStatusDetail(peopleStatus, 'Status delayed');
    const detail = pending > 0
        ? `${pending.toLocaleString()} queued`
        : (worker.message || `${Number(counts.detected_faces || 0).toLocaleString()} faces detected`);
    const statusLabel = active
        ? 'Running'
        : stopped
            ? 'Stopped'
            : errored
                ? 'Error'
                : complete
                    ? 'Done'
                    : queued
                        ? 'Catching Up'
                        : stale && total <= 0
                            ? 'Updating'
                            : 'Waiting';
    const row = {
        kind: 'faces',
        label: 'People',
        done,
        total,
        pct: total > 0 ? (done / total) * 100 : 0,
        state: statusLabel,
        detail: active ? peopleWorkerLabel(peopleStatus) : detail,
        eta: active ? 'Measuring' : complete ? 'Done' : stopped ? 'Stopped' : '-',
        tone: active ? 'active' : errored ? 'error' : complete ? 'done' : stopped ? 'paused' : 'idle',
        control: (pending > 0 || !stopped || active)
            ? workControl('faces', stopped, { available: true })
            : null,
    };
    if (stale && total <= 0 && previousRow && Number(previousRow.total || 0) > 0) {
        return {
            ...previousRow,
            state: active ? 'Running' : (stopped ? 'Stopped' : previousRow.state),
            detail: delayedDetail,
            eta: active ? 'Measuring' : previousRow.eta || '-',
            control: workControl('faces', stopped, { available: true }),
        };
    }
    if (stale && total <= 0) {
        return unknownStaleRow('faces', 'People', peopleStatus);
    }
    if (stale) {
        return {
            ...row,
            detail: delayedDetail,
        };
    }
    return row;
}

function updateBackgroundWorkPanelNote({
    documentImpl = globalThis.document,
} = {}) {
    const note = documentImpl?.getElementById?.('background-work-panel-note');
    if (!note) return '';
    const text = '';
    note.textContent = text;
    note.classList.toggle('hidden', !text);
    return text;
}

export function backgroundProcessRows(aiStatus = {}, cacheStatus = {}, peopleStatus = null, {
    lastPeopleRow = null,
} = {}) {
    const activeIndex = aiStatus.embedding_index || aiStatus;
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
    const cacheStopped = Boolean(pregen.manual_pause);
    const rawActiveWorkerState = String(activeIndex.worker_state || aiStatus.worker_state || '');
    const activeMessage = activeIndex.worker_message || aiStatus.worker_message;
    const activeWorkerError = activeIndex.worker_error || aiStatus.worker_error;
    const activeDatabaseLock = isTransientDatabaseLock(activeMessage) || isTransientDatabaseLock(activeWorkerError);
    const activeWorkerState = activeDatabaseLock && rawActiveWorkerState === 'error'
        ? 'embedding'
        : rawActiveWorkerState;
    const embeddingStopped = Boolean(activeIndex.manual_pause ?? aiStatus.embedding_manual_pause);
    const embeddingTotal = Number(activeIndex.total_images ?? aiStatus.total_images ?? 0);
    const embeddingDone = Number(activeIndex.embedded ?? aiStatus.embedded ?? 0);
    const embeddingRemaining = Math.max(
        0,
        Number(activeIndex.remaining ?? aiStatus.remaining ?? Math.max(embeddingTotal - embeddingDone, 0))
    );
    const embeddingInstalled = Boolean(activeIndex.installed ?? aiStatus.model_installed ?? true);
    const embeddingStatusStale = Boolean(aiStatus.status_stale || aiStatus.counts_stale);
    const previewRemaining = Math.max(
        0,
        Number(preview.image_remaining ?? preview.remaining ?? Math.max(previewTotal - previewDone, 0))
    );
    const originalRemaining = Math.max(
        0,
        Number(originals.remaining ?? Math.max(originalTotal - originalDone, 0))
    );
    const previewCombinedDone = previewDone + originalDone;
    const previewCombinedTotal = previewTotal + originalTotal;
    const previewCombinedRemaining = previewRemaining + originalRemaining;

    const activeState = processState({
        done: activeIndex.embedded,
        total: activeIndex.total_images,
        remaining: activeIndex.remaining,
        etaSeconds: aiStatus.eta_seconds,
        rate: aiStatus.recent_images_per_min || aiStatus.overall_images_per_min,
        paused: embeddingStopped,
        active: ['embedding', 'loading_model'].includes(activeWorkerState),
        installing: Boolean(activeIndex.installing || aiStatus.installing),
        installed: Boolean(activeIndex.installed ?? aiStatus.model_installed ?? true),
        errored: rawActiveWorkerState === 'error' && !activeDatabaseLock,
        state: activeWorkerState,
        message: activeDatabaseLock
            ? 'Waiting for catalog database'
            : activeWorkerState === 'loading_model'
                ? (activeMessage || 'Loading selected AI model')
                : activeMessage,
    });
    const previewState = processState({
        done: previewCombinedDone,
        total: previewCombinedTotal,
        remaining: previewCombinedRemaining,
        etaSeconds: pregen.eta_seconds,
        rate: pregen.recent_images_per_min || pregen.overall_images_per_min,
        paused: cacheStopped,
        active: cacheActive,
        state: pregen.active_phase || pregen.state,
        message: pregen.message,
    });
    const previewDetail = previewState.tone === 'done'
        ? `${previewDone.toLocaleString()} previews ready; ${originalDone.toLocaleString()} full-size cached`
        : previewState.detail;

    const searchRow = embeddingStatusStale && embeddingTotal <= 0
        ? unknownStaleRow('embedding', 'Search', aiStatus)
        : {
            kind: 'embedding',
            label: 'Search',
            done: Number(activeIndex.embedded || 0),
            total: Number(activeIndex.total_images || 0),
            pct: activeIndex.progress_pct,
            state: activeState.label,
            detail: embeddingStatusStale ? staleStatusDetail(aiStatus, activeMessage || 'Updating status…') : activeState.detail,
            eta: activeState.eta,
            tone: activeState.tone,
            control: !embeddingStatusStale && (embeddingRemaining > 0 || !embeddingStopped) && embeddingInstalled
                ? workControl('embedding', embeddingStopped, {
                    available: true,
                    tooltip: embeddingStopped ? 'Start Search.' : 'Stop Search.',
                })
                : null,
            tooltip: 'Search builds the active local embedding index used for semantic search and similarity.',
        };
    const previewsRow = cacheStatus.status_stale && previewCombinedTotal <= 0
        ? unknownStaleRow('preview', 'Previews', cacheStatus)
        : {
            kind: 'preview',
            label: 'Previews',
            done: previewCombinedDone,
            total: previewCombinedTotal,
            pct: previewCombinedTotal > 0 ? (previewCombinedDone / previewCombinedTotal) * 100 : 0,
            state: previewState.label,
            detail: cacheStatus.status_stale ? staleStatusDetail(cacheStatus) : previewDetail,
            eta: previewState.eta,
            tone: previewState.tone,
            control: !cacheStatus.status_stale && (previewCombinedRemaining > 0 || !cacheStopped)
                ? workControl('preview', cacheStopped, {
                    available: true,
                    tooltip: cacheStopped ? 'Start Previews.' : 'Stop Previews.',
                })
                : null,
            tooltip: 'Previews builds browsing previews and copies full-size originals into cache when storage permits.',
        };

    const rows = [searchRow, previewsRow];
    const peopleRow = peopleQueueRow(peopleStatus, lastPeopleRow);
    if (peopleRow) rows.push(peopleRow);
    return rows;
}

function summarizeBackgroundWorkRow(rows) {
    const realRows = rows.filter((row) => !isFallbackOnlyRow(row));
    return realRows.find((row) => row.tone === 'active')
        || realRows.find((row) => row.tone === 'paused' && hasRemainingWork(row))
        || realRows.find((row) => row.tone === 'idle' && hasRemainingWork(row))
        || realRows.find((row) => row.tone === 'error')
        || realRows.find((row) => row.tone === 'missing')
        || realRows.find((row) => row.tone === 'paused')
        || null;
}

export function activeBackgroundWorkRow(aiStatus = {}, cacheStatus = {}, peopleStatus = null, options = {}) {
    const rows = backgroundProcessRows(aiStatus, cacheStatus || {}, peopleStatus, {
        ...options,
        lastPeopleRow: lastBackgroundWorkState.lastPeopleRow,
    });
    return summarizeBackgroundWorkRow(rows);
}

export function renderBackgroundWorkPanel(aiStatus = {}, {
    cacheStatus = null,
    peopleStatus = null,
    documentImpl = globalThis.document,
} = {}) {
    const container = documentImpl?.getElementById?.('background-work-panel-rows');
    if (!container) return false;
    lastBackgroundWorkState.aiStatus = aiStatus || {};
    lastBackgroundWorkState.cacheStatus = cacheStatus || {};
    lastBackgroundWorkState.peopleStatus = peopleStatus || null;
    const rows = backgroundProcessRows(aiStatus, cacheStatus || {}, peopleStatus, {
        lastPeopleRow: lastBackgroundWorkState.lastPeopleRow,
    });
    const peopleRow = rows.find((row) => row.kind === 'faces');
    if (peopleRow && !(peopleStatus?.counts_stale || peopleStatus?.status_stale)) {
        lastBackgroundWorkState.lastPeopleRow = peopleRow;
    }
    container.innerHTML = rows.map(renderRow).join('');
    updateBackgroundWorkPanelNote({ documentImpl });
    return true;
}

export function renderBackgroundWorkSummary(aiStatus = {}, {
    cacheStatus = null,
    peopleStatus = null,
    documentImpl = globalThis.document,
} = {}) {
    const total = Number(aiStatus.total_images ?? aiStatus.total_kept ?? 0);
    const rows = backgroundProcessRows(aiStatus, cacheStatus || {}, peopleStatus, {
        lastPeopleRow: lastBackgroundWorkState.lastPeopleRow,
    });
    const activeRow = summarizeBackgroundWorkRow(rows);
    const hasFallbackRows = rows.some(isFallbackOnlyRow);
    const allFallbackRows = rows.length > 0 && rows.every(isFallbackOnlyRow);
    const pct = activeRow ? clampPct(activeRow.pct) : 0;
    const countEl = documentImpl?.getElementById?.('ai-embed-count');
    const totalEl = documentImpl?.getElementById?.('ai-embed-total');
    const countWrapEl = documentImpl?.getElementById?.('bar-work-count');
    const stateEl = documentImpl?.getElementById?.('ai-model-state');
    const workEl = documentImpl?.getElementById?.('bar-work');
    const labelEl = workEl?.querySelector?.('.bar-ai-label');
    if (workEl) {
        workEl.style.setProperty('--work-progress', `${pct}%`);
        workEl.style.display = '';
        workEl.dataset.workTone = activeRow?.tone || 'idle';
        workEl.title = activeRow
            ? `Background Work - ${activeRow.label}: ${activeRow.state}`
            : allFallbackRows || hasFallbackRows
                ? 'Background Work - updating'
                : 'Background Work - idle';
        workEl.setAttribute?.('aria-label', workEl.title);
    }
    if (labelEl) labelEl.textContent = 'Background Work';
    if (countWrapEl) countWrapEl.style.display = 'none';
    if (countEl) countEl.textContent = activeRow ? Number(activeRow.done || 0).toLocaleString() : '';
    if (totalEl) totalEl.textContent = activeRow ? Number(activeRow.total || 0).toLocaleString() : '';
    if (stateEl) {
        if (activeRow) {
            stateEl.textContent = `Background Work · ${activeRow.label}: ${activeRow.state}`;
            stateEl.className = `bar-ai-state ${stateClass(activeRow.tone)}`.trim();
        } else {
            stateEl.textContent = allFallbackRows || hasFallbackRows
                ? 'Background Work · Updating'
                : 'Background Work · Idle';
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
    syncBackgroundWorkTrigger(documentImpl);
    return true;
}

function closeBackgroundWorkPanel({ documentImpl = globalThis.document } = {}) {
    const panel = documentImpl?.getElementById?.('background-work-panel');
    if (!panel) return false;
    panel.classList.add('hidden');
    syncBackgroundWorkTrigger(documentImpl);
    return true;
}

async function responseDataOrError(response, fallbackMessage) {
    const contentType = response.headers?.get?.('content-type') || '';
    let data = {};
    try {
        data = contentType.includes('application/json')
            ? await response.json()
            : { error: await response.text() };
    } catch {
        data = {};
    }
    if (!response.ok || data.ok === false || data.error) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}

function snapshotBackgroundWorkState() {
    return {
        aiStatus: {
            ...(lastBackgroundWorkState.aiStatus || {}),
            embedding_index: { ...(lastBackgroundWorkState.aiStatus?.embedding_index || {}) },
        },
        cacheStatus: lastBackgroundWorkState.cacheStatus
            ? {
                ...lastBackgroundWorkState.cacheStatus,
                pregen: { ...(lastBackgroundWorkState.cacheStatus.pregen || {}) },
            }
            : null,
        peopleStatus: lastBackgroundWorkState.peopleStatus ? { ...lastBackgroundWorkState.peopleStatus } : null,
        lastPeopleRow: lastBackgroundWorkState.lastPeopleRow,
        pins: { ...(lastBackgroundWorkState.pins || {}) },
    };
}

function restoreBackgroundWorkState(snapshot) {
    if (!snapshot) return;
    lastBackgroundWorkState.aiStatus = snapshot.aiStatus || {};
    lastBackgroundWorkState.cacheStatus = snapshot.cacheStatus || null;
    lastBackgroundWorkState.peopleStatus = snapshot.peopleStatus || null;
    lastBackgroundWorkState.lastPeopleRow = snapshot.lastPeopleRow || null;
    lastBackgroundWorkState.pins = snapshot.pins || {};
}

function pinBackgroundWorkKinds(kind) {
    pinBackgroundWorkKind(kind);
}

function optimisticBackgroundWorkAction(kind, command, { documentImpl = globalThis.document } = {}) {
    const starting = command === 'resume';
    if (kind === 'embedding') {
        lastBackgroundWorkState.aiStatus = {
            ...(lastBackgroundWorkState.aiStatus || {}),
            embedding_manual_pause: !starting,
            embedding_index: {
                ...(lastBackgroundWorkState.aiStatus?.embedding_index || {}),
                manual_pause: !starting,
            },
        };
    } else if (kind === 'preview') {
        lastBackgroundWorkState.cacheStatus = {
            ...(lastBackgroundWorkState.cacheStatus || {}),
            pregen: {
                ...(lastBackgroundWorkState.cacheStatus?.pregen || {}),
                manual_pause: !starting,
            },
        };
    } else if (kind === 'faces') {
        lastBackgroundWorkState.peopleStatus = {
            ...(lastBackgroundWorkState.peopleStatus || {}),
            active: starting,
        };
    }
    pinBackgroundWorkKinds(kind);
    renderBackgroundWorkSummary(lastBackgroundWorkState.aiStatus, {
        cacheStatus: lastBackgroundWorkState.cacheStatus,
        peopleStatus: lastBackgroundWorkState.peopleStatus,
        documentImpl,
    });
    renderBackgroundWorkPanel(lastBackgroundWorkState.aiStatus, {
        cacheStatus: lastBackgroundWorkState.cacheStatus,
        peopleStatus: lastBackgroundWorkState.peopleStatus,
        documentImpl,
    });
}

function applyBackgroundWorkActionResult(kind, data, { documentImpl = globalThis.document } = {}) {
    pinBackgroundWorkKinds(kind);
    if (kind === 'embedding' && data.ai_status) {
        lastBackgroundWorkState.aiStatus = data.ai_status;
    }
    if (kind === 'preview' && (data.cache || data.cache_stats)) {
        lastBackgroundWorkState.cacheStatus = data.cache || data.cache_stats;
    }
    if (kind === 'faces' && (data.status || data.people_status)) {
        lastBackgroundWorkState.peopleStatus = data.status || data.people_status;
    }
    renderBackgroundWorkSummary(lastBackgroundWorkState.aiStatus, {
        cacheStatus: lastBackgroundWorkState.cacheStatus,
        peopleStatus: lastBackgroundWorkState.peopleStatus,
        documentImpl,
    });
    renderBackgroundWorkPanel(lastBackgroundWorkState.aiStatus, {
        cacheStatus: lastBackgroundWorkState.cacheStatus,
        peopleStatus: lastBackgroundWorkState.peopleStatus,
        documentImpl,
    });
}

export async function runBackgroundWorkAction(kind, command, {
    button = null,
    documentImpl = globalThis.document,
    fetchImpl = (url, options) => globalThis.fetch(url, options),
} = {}) {
    const action = BACKGROUND_WORK_ACTIONS[kind]?.[command];
    if (!action) return null;
    const previousText = button?.textContent || '';
    const snapshot = snapshotBackgroundWorkState();
    if (button) {
        button.disabled = true;
        button.textContent = action.pending || previousText;
    }
    optimisticBackgroundWorkAction(kind, command, { documentImpl });
    try {
        const response = await fetchImpl(action.url, { method: 'POST' });
        const data = await responseDataOrError(response, 'Background work action failed');
        applyBackgroundWorkActionResult(kind, data, { documentImpl });
        return data;
    } catch (err) {
        restoreBackgroundWorkState(snapshot);
        renderBackgroundWorkSummary(lastBackgroundWorkState.aiStatus, {
            cacheStatus: lastBackgroundWorkState.cacheStatus,
            peopleStatus: lastBackgroundWorkState.peopleStatus,
            documentImpl,
        });
        renderBackgroundWorkPanel(lastBackgroundWorkState.aiStatus, {
            cacheStatus: lastBackgroundWorkState.cacheStatus,
            peopleStatus: lastBackgroundWorkState.peopleStatus,
            documentImpl,
        });
        if (button) {
            button.disabled = false;
            button.textContent = previousText || 'Start';
            button.title = err?.message || 'Background work action failed';
        }
        console.warn('Background work action failed', err);
        return null;
    }
}

function syncBackgroundWorkTrigger(documentImpl = globalThis.document) {
    const trigger = documentImpl?.getElementById?.('bar-work');
    const panel = documentImpl?.getElementById?.('background-work-panel');
    if (!trigger || !panel) return;
    const open = !panel.classList.contains('hidden');
    trigger.setAttribute('aria-expanded', String(open));
    trigger.title = open ? 'Close Background Work' : 'Open Background Work';
    // The bottom bar's own z-index creates a stacking context that would
    // trap the panel below an open loupe; raise the bar while the panel
    // is open (see .bottom-bar.work-panel-open in style.css).
    panel.closest?.('.bottom-bar')?.classList?.toggle('work-panel-open', open);
}

function handleBackgroundWorkDocumentClick(event, documentImpl = globalThis.document) {
    const toggleTarget = event.target?.closest?.('[data-action="toggle-background-work-panel"]');
    if (toggleTarget) {
        event.preventDefault?.();
        event.stopPropagation?.();
        const panel = documentImpl?.getElementById?.('background-work-panel');
        const opening = panel?.classList?.contains?.('hidden');
        toggleBackgroundWorkPanel({ documentImpl });
        syncBackgroundWorkTrigger(documentImpl);
        if (opening && panel && !panel.classList.contains('hidden')) {
            renderBackgroundWorkPanel(lastBackgroundWorkState.aiStatus, {
                cacheStatus: lastBackgroundWorkState.cacheStatus,
                peopleStatus: lastBackgroundWorkState.peopleStatus,
                documentImpl,
            });
        }
        return true;
    }
    const actionButton = event.target?.closest?.('[data-work-kind][data-work-command]');
    if (actionButton) {
        event.preventDefault?.();
        event.stopPropagation?.();
        runBackgroundWorkAction(actionButton.dataset.workKind, actionButton.dataset.workCommand, {
            button: actionButton,
            documentImpl,
        });
        return true;
    }
    return false;
}

export function bindBackgroundWorkPanel({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
} = {}) {
    const root = documentImpl?.documentElement;
    const trigger = documentImpl?.getElementById?.('bar-work');
    if (!root || !trigger) return false;
    if (root.dataset.paBackgroundWorkBound === '1') {
        syncBackgroundWorkTrigger(documentImpl);
        return true;
    }
    root.dataset.paBackgroundWorkBound = '1';
    syncBackgroundWorkTrigger(documentImpl);

    documentImpl.addEventListener('click', (event) => {
        handleBackgroundWorkDocumentClick(event, documentImpl);
    }, true);

    documentImpl.addEventListener('click', (event) => {
        const closeLoupe = event.target?.closest?.('[data-action="close-loupe"]');
        if (!closeLoupe) return;
        event.preventDefault?.();
        event.stopPropagation?.();
        const close = windowImpl.PhotoArchive?.closeLightbox;
        if (typeof close === 'function') {
            close();
            return;
        }
        windowImpl.PhotoArchiveReady?.then?.((api) => api.closeLightbox?.());
    }, true);

    trigger.addEventListener('keydown', (event) => {
        if (!['Enter', ' '].includes(event.key)) return;
        event.preventDefault?.();
        toggleBackgroundWorkPanel({ documentImpl });
        syncBackgroundWorkTrigger(documentImpl);
    });
    return true;
}

export function installAppShellControls({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
} = {}) {
    return bindBackgroundWorkPanel({ documentImpl, windowImpl });
}
