import { escapeHtml } from '../ui.js';

export function aiStatusPollDelay(data, {
    activePollMs = 5000,
    idlePollMs = 30000,
} = {}) {
    const workerState = String(data?.worker_state || '');
    if (workerState === 'error' || data?.worker_error || !data?.model_installed) {
        return idlePollMs;
    }
    const active = Boolean(
        data?.installing
        || workerState === 'loading_model'
        || workerState === 'embedding'
    );
    return active ? activePollMs : idlePollMs;
}


export function renderAIBottomBarStatus(data = {}, { documentImpl = globalThis.document } = {}) {
    const countEl = documentImpl?.getElementById?.('ai-embed-count');
    const totalEl = documentImpl?.getElementById?.('ai-embed-total');
    const stateEl = documentImpl?.getElementById?.('ai-model-state');
    const totalImages = Number(data.total_images ?? data.total_kept ?? 0);
    if (countEl) countEl.textContent = data.embedded.toLocaleString();
    if (totalEl) totalEl.textContent = totalImages.toLocaleString();
    if (stateEl) {
        if (data.installing) {
            stateEl.textContent = 'Installing';
            stateEl.className = 'bar-ai-state embedding';
        } else if (!data.model_installed) {
            stateEl.textContent = 'Install';
            stateEl.className = 'bar-ai-state';
        } else if (!data.worker_ready && data.worker_state === 'loading_model') {
            stateEl.textContent = 'Loading';
            stateEl.className = 'bar-ai-state embedding';
        } else if (data.embedded < totalImages) {
            stateEl.textContent = 'Embedding';
            stateEl.className = 'bar-ai-state embedding';
        } else if (data.embedded > 0) {
            stateEl.textContent = 'Ready';
            stateEl.className = 'bar-ai-state trained';
        } else {
            stateEl.textContent = '';
            stateEl.className = 'bar-ai-state';
        }
    }
    return { totalImages };
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
            modelText.textContent = `Model not installed. Open Settings to install ${data.model_id}.`;
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
    const { totalImages } = renderAIBottomBarStatus(data, options);
    renderAIPanelStatus(data, { ...options, totalImages });
}


export function toggleAIPanel({ documentImpl = globalThis.document } = {}) {
    const panel = documentImpl?.getElementById?.('ai-panel');
    if (!panel) return false;
    panel.classList.toggle('hidden');
    return true;
}


export function deepSearchQueryListHtml(queries) {
    const items = Array.isArray(queries) ? queries.slice(0, 60) : [];
    if (!items.length) {
        return '<span class="settings-help-text">No deep-search terms queued yet.</span>';
    }
    return items.map((item) => {
        const cached = Boolean(item.cached);
        const cls = cached ? 'cached' : 'pending';
        const state = cached ? 'ready' : 'pending';
        return `
                <span class="deep-search-query ${cls}">
                    <span>${escapeHtml(item.query || '')}</span>
                    <span class="deep-search-query-state">${state}</span>
                </span>
            `;
    }).join('');
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
            messageText: `${install.model_id || 'Another model'} is installing. The 2B install can start after it finishes.`,
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


export function deepSearchWorkerText(deep = {}) {
    const state = deep.state ? String(deep.state).replace(/_/g, ' ') : 'waiting';
    return deep.message ? `${state}: ${deep.message}` : state;
}


export function deepSearchImageStatusText(deepIndex = {}, deep = {}) {
    const embedded = Number(deepIndex.embedded ?? deep.embedded_images ?? 0).toLocaleString();
    const total = Number(deepIndex.total_images ?? deep.total_images ?? 0).toLocaleString();
    const pending = Number(deepIndex.remaining ?? deep.pending_images ?? 0).toLocaleString();
    return `${embedded} / ${total} indexed, ${pending} pending`;
}


export function deepSearchQueryStatusText(deepIndex = {}, deep = {}) {
    const embedded = Number(deepIndex.embedded_queries ?? deep.embedded_queries ?? 0).toLocaleString();
    const pending = Number(deepIndex.pending_queries ?? deep.pending_queries ?? 0).toLocaleString();
    return `${embedded} ready, ${pending} pending`;
}
