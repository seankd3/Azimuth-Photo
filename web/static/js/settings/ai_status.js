import {
    deepSearchImageStatusText,
    deepSearchQueryListHtml,
    deepSearchQueryStatusText,
    deepSearchWorkerText,
    modelInstallDisplay,
} from '../ai/status.js';
import { embeddingIndexDisplay } from './display.js';


export function renderModelStatus(modelStatus) {
    const statusEl = document.getElementById('model-install-status');
    const messageEl = document.getElementById('model-install-message');
    const buttonEl = document.getElementById('install-model-btn');
    if (!statusEl || !messageEl || !buttonEl || !modelStatus) return;

    const display = modelInstallDisplay(modelStatus);
    statusEl.textContent = display.statusText;
    messageEl.textContent = display.messageText;
    buttonEl.disabled = display.buttonDisabled;
    buttonEl.textContent = display.buttonText;
}


export function renderEmbeddingIndex(role, index) {
    const modelEl = document.getElementById(`${role}-index-model`);
    const badgeEl = document.getElementById(`${role}-index-badge`);
    const meterEl = document.getElementById(`${role}-index-meter`);
    const progressEl = document.getElementById(`${role}-index-progress`);
    const statusEl = document.getElementById(`${role}-index-status`);
    if (!modelEl && !badgeEl && !meterEl && !progressEl && !statusEl) return;

    const display = embeddingIndexDisplay(role, index || {});
    if (modelEl) {
        modelEl.textContent = display.modelText;
    }
    if (badgeEl) {
        badgeEl.textContent = display.badge.text;
        badgeEl.className = `embedding-index-badge ${display.badge.cls}`;
    }
    if (meterEl) meterEl.style.width = `${display.pct}%`;
    if (progressEl) {
        progressEl.textContent = display.progressText;
    }
    if (statusEl) {
        statusEl.textContent = display.statusText;
    }
}


export function renderDeepSearchQueryList(queries) {
    const el = document.getElementById('deep-search-query-list');
    if (!el) return;
    el.innerHTML = deepSearchQueryListHtml(queries);
}


export function renderAISettingsStatus(aiStatus) {
    if (!aiStatus) return;
    const indexes = aiStatus.embedding_indexes || {};
    renderEmbeddingIndex('fast', indexes.fast || {
        model_id: aiStatus.model_id,
        dimension: aiStatus.model_dimension,
        installed: aiStatus.model_installed,
        embedded: aiStatus.embedded,
        total_images: aiStatus.total_images,
        remaining: aiStatus.remaining,
        progress_pct: aiStatus.progress_pct,
        worker_state: aiStatus.worker_state,
        worker_message: aiStatus.worker_message,
    });
    renderEmbeddingIndex('deep', indexes.deep || {});
    const deep = aiStatus.deep_search || {};
    const deepIndex = indexes.deep || {};
    renderDeepSearchQueryList(deepIndex.queries || []);
    const workerEl = document.getElementById('deep-search-worker-status');
    const imageEl = document.getElementById('deep-search-image-status');
    const queryEl = document.getElementById('deep-search-query-status');
    if (workerEl) {
        workerEl.textContent = deepSearchWorkerText(deep);
    }
    if (imageEl) {
        imageEl.textContent = deepSearchImageStatusText(deepIndex, deep);
    }
    if (queryEl) {
        queryEl.textContent = deepSearchQueryStatusText(deepIndex, deep);
    }
}


export function renderEmbeddingModelPresets(presets) {
    const select = document.getElementById('embed_model_preset');
    if (!select || select.dataset.populated === '1') return;
    select.innerHTML = '';
    for (const preset of presets) {
        const option = document.createElement('option');
        option.value = preset.key;
        option.textContent = preset.label;
        option.dataset.modelId = preset.model_id;
        option.dataset.revision = preset.revision;
        option.dataset.dimension = preset.dimension;
        option.dataset.modelDir = preset.model_dir;
        select.appendChild(option);
    }
    const custom = document.createElement('option');
    custom.value = 'custom';
    custom.textContent = 'Custom model';
    select.appendChild(custom);
    select.dataset.populated = '1';
}


export function applySelectedEmbeddingPreset() {
    const select = document.getElementById('embed_model_preset');
    const option = select?.selectedOptions?.[0];
    if (!select || !option || select.value === 'custom') return;
    const fields = {
        embed_model_id: option.dataset.modelId,
        embed_model_revision: option.dataset.revision,
        embed_model_dir: option.dataset.modelDir,
        embed_model_dim: option.dataset.dimension,
    };
    for (const [id, value] of Object.entries(fields)) {
        const input = document.getElementById(id);
        if (input && value !== undefined) input.value = value;
    }
}
