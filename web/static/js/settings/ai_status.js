import { modelInstallDisplay } from '../ai/status.js';
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
    const actionEl = document.getElementById(`${role}-index-actions`);
    const installBtn = document.getElementById(`${role}-index-install-btn`);
    if (!modelEl && !badgeEl && !meterEl && !progressEl && !statusEl && !actionEl && !installBtn) return;

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
    if (installBtn) {
        installBtn.disabled = Boolean(index?.installed || index?.installing);
        installBtn.textContent = index?.installing ? 'Installing' : 'Install Model';
    }
    if (actionEl) {
        actionEl.hidden = Boolean(index?.installed);
    }
}


export function renderAISettingsStatus(aiStatus) {
    if (!aiStatus) return;
    const activeIndex = aiStatus.embedding_index || {
        model_id: aiStatus.model_id,
        dimension: aiStatus.model_dimension,
        installed: aiStatus.model_installed,
        embedded: aiStatus.embedded,
        total_images: aiStatus.total_images,
        remaining: aiStatus.remaining,
        progress_pct: aiStatus.progress_pct,
        worker_state: aiStatus.worker_state,
        worker_message: aiStatus.worker_message,
    };
    renderEmbeddingIndex('active', activeIndex);
    const progressEl = document.getElementById('active-index-progress');
    const statusEl = document.getElementById('active-index-status');
    const display = embeddingIndexDisplay('active', activeIndex);
    if (progressEl) progressEl.textContent = display.progressText;
    if (statusEl) statusEl.textContent = display.statusText;
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
