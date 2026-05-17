export async function saveSettings({
    collectSettingsForm,
    populateSettingsForm,
    rememberThumbnailOutput,
    renderSettingsMeta,
    setSettingsStatus,
    showToast,
    updateThumbnailChangeNotice,
}) {
    setSettingsStatus('Saving settings…', 'muted');
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collectSettingsForm()),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Save failed');
        }
        populateSettingsForm(data.settings || {});
        rememberThumbnailOutput(data.settings || {});
        updateThumbnailChangeNotice();
        renderSettingsMeta(data);
        setSettingsStatus('Saved. New requests are using the updated runtime settings.', 'success');
        return data;
    } catch (err) {
        setSettingsStatus(`Save failed: ${err.message}`, 'error');
        showToast('Save failed');
        return null;
    }
}


export function resetSettings({
    populateSettingsForm,
    rememberThumbnailOutput,
    renderSettingsMeta,
    setSettingsStatus,
    showConfirmModal,
    showToast,
    updateThumbnailChangeNotice,
}) {
    showConfirmModal('Reset to defaults?', 'All settings will be restored to their default values.', async () => {
        setSettingsStatus('Resetting to defaults…', 'muted');
        try {
            const res = await fetch('/api/settings/reset', { method: 'POST' });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                throw new Error(data.error || 'Reset failed');
            }
            populateSettingsForm(data.settings || {});
            rememberThumbnailOutput(data.settings || {});
            updateThumbnailChangeNotice();
            renderSettingsMeta(data);
            setSettingsStatus('Defaults restored. Runtime settings were updated.', 'success');
        } catch (err) {
            setSettingsStatus(`Reset failed: ${err.message}`, 'error');
            showToast('Reset failed');
        }
    });
}


export function clearThumbnailCache({
    formatBytes,
    renderSettingsMeta,
    setSettingsStatus,
    showConfirmModal,
    showToast,
}) {
    showConfirmModal('Clear thumbnail cache?', 'This will delete all cached thumbnails. Regeneration may take hours for large archives.', async () => {
        setSettingsStatus('Clearing in-memory and disk thumbnail cache…', 'muted');
        try {
            const res = await fetch('/api/cache/clear', { method: 'POST' });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                throw new Error(data.error || 'Cache clear failed');
            }
            renderSettingsMeta(data);
            setSettingsStatus(
                `Cleared ${data.memory_entries_cleared || 0} RAM entries (${formatBytes(data.memory_bytes_cleared || 0)}) and ${data.disk_files_removed || 0} disk files.`,
                'success'
            );
        } catch (err) {
            setSettingsStatus(`Cache clear failed: ${err.message}`, 'error');
            showToast('Cache clear failed');
        }
    });
}


export async function startCachePregeneration({
    renderCacheSettingsStatus,
    setSettingsStatus,
    showToast,
}) {
    setSettingsStatus('Starting cache pre-generation…', 'muted');
    try {
        const res = await fetch('/api/cache/pregen/start', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Could not start pre-generation');
        }
        renderCacheSettingsStatus(data.cache);
        setSettingsStatus('Pre-generation is running.', 'success');
        return data;
    } catch (err) {
        setSettingsStatus(`Could not start pre-generation: ${err.message}`, 'error');
        showToast('Could not start pre-generation');
        return null;
    }
}


export async function stopCachePregeneration({
    renderCacheSettingsStatus,
    setSettingsStatus,
    showToast,
}) {
    setSettingsStatus('Pausing cache pre-generation…', 'muted');
    try {
        const res = await fetch('/api/cache/pregen/stop', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Could not pause pre-generation');
        }
        renderCacheSettingsStatus(data.cache);
        setSettingsStatus('Pre-generation paused.', 'success');
        return data;
    } catch (err) {
        setSettingsStatus(`Could not pause pre-generation: ${err.message}`, 'error');
        showToast('Could not pause pre-generation');
        return null;
    }
}


export async function pauseEmbeddings({
    renderAISettingsStatus,
    setSettingsStatus,
    showToast,
}) {
    setSettingsStatus('Pausing background embeddings...', 'muted');
    try {
        const res = await fetch('/api/ai/embeddings/pause', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Could not pause embeddings');
        }
        renderAISettingsStatus(data.ai_status);
        setSettingsStatus('Embeddings paused. Current in-flight batch may finish first.', 'success');
        return data;
    } catch (err) {
        setSettingsStatus(`Could not pause embeddings: ${err.message}`, 'error');
        showToast('Could not pause embeddings');
        return null;
    }
}


export async function resumeEmbeddings({
    renderAISettingsStatus,
    setSettingsStatus,
    showToast,
}) {
    setSettingsStatus('Resuming background embeddings...', 'muted');
    try {
        const res = await fetch('/api/ai/embeddings/resume', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Could not resume embeddings');
        }
        renderAISettingsStatus(data.ai_status);
        setSettingsStatus('Embeddings resumed.', 'success');
        return data;
    } catch (err) {
        setSettingsStatus(`Could not resume embeddings: ${err.message}`, 'error');
        showToast('Could not resume embeddings');
        return null;
    }
}


export async function installAIModel(role = 'fast', {
    collectSettingsForm,
    populateSettingsForm,
    rememberThumbnailOutput,
    renderAISettingsStatus,
    renderModelStatus,
    renderSettingsMeta,
    setSettingsStatus,
    showToast,
    updateThumbnailChangeNotice,
}) {
    const installRole = role === 'deep' ? 'deep' : 'fast';
    const label = installRole === 'deep' ? '8B deep model' : '2B daily model';
    setSettingsStatus(`Saving settings and starting ${label} install…`, 'muted');
    try {
        const saveRes = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collectSettingsForm()),
        });
        const saveData = await saveRes.json();
        if (!saveRes.ok || !saveData.ok) {
            throw new Error(saveData.error || 'Could not save settings');
        }
        populateSettingsForm(saveData.settings || {});
        rememberThumbnailOutput(saveData.settings || {});
        updateThumbnailChangeNotice();
        renderSettingsMeta(saveData);

        const installRes = await fetch(`/api/ai/model/install?role=${encodeURIComponent(installRole)}`, { method: 'POST' });
        const installData = await installRes.json();
        if (!installRes.ok || !installData.ok) {
            throw new Error(installData.error || 'Install could not be started');
        }
        if (installRole === 'fast') renderModelStatus(installData.model_status);
        renderAISettingsStatus(installData.ai_status);
        setSettingsStatus(`${label} install started. The worker will pick it up automatically when the download finishes.`, 'success');
        return installData;
    } catch (err) {
        setSettingsStatus(`Model install failed to start: ${err.message}`, 'error');
        showToast('Model install failed to start');
        return null;
    }
}
