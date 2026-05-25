import {
    bindBackgroundWorkPanel as bindBackgroundWorkPanelCore,
    ingestBackgroundWorkPoll,
    renderBackgroundWorkPanel,
    renderBackgroundWorkSummary,
    toggleBackgroundWorkPanel as toggleBackgroundWorkPanelCore,
} from '../work/status_panel.js';

export { ingestBackgroundWorkPoll };

export function aiStatusPollDelay(data, {
    activePollMs = 5000,
    idlePollMs = 30000,
    cacheStatus = null,
    peopleStatus = null,
} = {}) {
    const workerState = String(data?.worker_state || '');
    const peopleWorkerState = String(peopleStatus?.worker?.state || '');
    const cacheWorkerState = String(cacheStatus?.pregen?.state || '');
    if (workerState === 'error' || data?.worker_error || !data?.model_installed) {
        return idlePollMs;
    }
    const active = Boolean(
        data?.installing
        || workerState === 'loading_model'
        || workerState === 'embedding'
        || cacheWorkerState === 'running'
        || cacheStatus?.counts_stale
        || cacheStatus?.status_stale
        || peopleWorkerState === 'installing'
        || peopleWorkerState === 'scanning'
        || peopleStatus?.counts_stale
        || peopleStatus?.status_stale
    );
    return active ? activePollMs : idlePollMs;
}


export function renderAIBottomBarStatus(data = {}, {
    cacheStatus = null,
    peopleStatus = null,
    documentImpl = globalThis.document,
} = {}) {
    return renderBackgroundWorkSummary(data, { cacheStatus, peopleStatus, documentImpl });
}


export function renderAIPanelStatus(data = {}, {
    documentImpl = globalThis.document,
    totalImages = Number(data.total_images ?? data.total_kept ?? 0),
} = {}) {
    const embedFill = documentImpl?.getElementById?.('ai-panel-embed-fill');
    const embedText = documentImpl?.getElementById?.('ai-panel-embed-text');
    const modelText = documentImpl?.getElementById?.('ai-panel-model-text');
    const predText = documentImpl?.getElementById?.('ai-panel-predictions-text');
    if (embedFill) {
        const pct = totalImages > 0 ? (data.embedded / totalImages * 100) : 0;
        embedFill.style.width = pct + '%';
    }
    if (embedText) embedText.textContent = `${data.embedded.toLocaleString()} / ${totalImages.toLocaleString()} images`;
    if (modelText) {
        if (data.installing) {
            modelText.textContent = data.install_message || `Installing ${data.model_id}`;
            modelText.className = 'ai-panel-value';
        } else if (!data.model_installed) {
            modelText.textContent = `Model not installed. Open Catalog to install ${data.model_id}.`;
            modelText.className = 'ai-panel-value';
        } else if (data.worker_state === 'loading_model') {
            modelText.textContent = data.worker_message || `Loading ${data.model_id}`;
            modelText.className = 'ai-panel-value';
        } else if (data.embedded > 0) {
            modelText.textContent = `${Number(data.rated_images ?? data.compared ?? 0).toLocaleString()} ranked images · Elo propagation active`;
            modelText.className = 'ai-panel-value trained';
        } else {
            modelText.textContent = 'Not started';
            modelText.className = 'ai-panel-value';
        }
    }
    if (predText) {
        if (Number(data.ranking_signal_count || 0) > 0) {
            predText.textContent = `${Number(data.ranking_signal_count || 0).toLocaleString()} ranking signals`;
        } else {
            predText.textContent = 'None yet';
        }
    }
}


export function renderAIStatusWidgets(data = {}, options = {}) {
    const merged = ingestBackgroundWorkPoll(
        data,
        options.cacheStatus,
        options.peopleStatus,
    );
    const renderOptions = {
        ...options,
        cacheStatus: merged.cacheStatus,
        peopleStatus: merged.peopleStatus,
    };
    const { totalImages } = renderAIBottomBarStatus(merged.aiStatus, renderOptions);
    renderAIPanelStatus(merged.aiStatus, { ...renderOptions, totalImages });
    renderBackgroundWorkPanel(merged.aiStatus, renderOptions);
}


export function toggleAIPanel({ documentImpl = globalThis.document } = {}) {
    const legacyPanel = documentImpl?.getElementById?.('ai-panel');
    if (legacyPanel) {
        legacyPanel.classList.toggle('hidden');
        return true;
    }
    return toggleBackgroundWorkPanel({ documentImpl });
}


export function toggleBackgroundWorkPanel({ documentImpl = globalThis.document } = {}) {
    return toggleBackgroundWorkPanelCore({ documentImpl });
}


export function bindBackgroundWorkPanel({ documentImpl = globalThis.document } = {}) {
    return bindBackgroundWorkPanelCore({ documentImpl });
}


export function modelInstallDisplay(modelStatus = {}) {
    const install = modelStatus.install || {};
    const installApplies = Boolean(install.model_dir && modelStatus.model_dir && install.model_dir === modelStatus.model_dir);
    if (install.running && installApplies) {
        return {
            statusText: 'Downloading',
            messageText: install.message || `Downloading ${modelStatus.model_id}\u2026`,
            buttonDisabled: true,
            buttonText: 'Installing\u2026',
        };
    }
    if (install.running) {
        return {
            statusText: 'Installer busy',
            messageText: `${install.model_id || 'Another model'} is installing. This install can start after it finishes.`,
            buttonDisabled: true,
            buttonText: 'Installer Busy',
        };
    }
    if (modelStatus.installed) {
        return {
            statusText: 'Installed',
            messageText: `${modelStatus.model_id} is available locally at ${modelStatus.model_dir}`,
            buttonDisabled: false,
            buttonText: 'Reinstall Model',
        };
    }
    if (install.status === 'error' && installApplies) {
        return {
            statusText: 'Error',
            messageText: install.message || 'Model install failed.',
            buttonDisabled: false,
            buttonText: 'Retry Install',
        };
    }
    return {
        statusText: 'Not installed',
        messageText: `Install ${modelStatus.model_id} to enable offline embeddings.`,
        buttonDisabled: false,
        buttonText: 'Save + Install Model',
    };
}
