let scanPoller = null;
let addSourceInFlight = false;


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export async function loadCatalogSources({
    fetchImpl = fetch,
    renderCatalogSources,
    setSettingsStatus,
} = {}) {
    try {
        const res = await fetchImpl('/api/catalog');
        const data = await responseDataOrError(res, 'Could not load catalog sources');
        renderCatalogSources?.(data);
        return data;
    } catch (err) {
        setSettingsStatus?.(`Could not load catalog sources: ${err.message}`, 'error');
        return null;
    }
}


export async function pollScanUntilDone({
    fetchImpl = fetch,
    setIntervalImpl = setInterval,
    clearIntervalImpl = clearInterval,
    loadCatalogSources: loadCatalogSourcesImpl,
    setScanBusy,
    setSettingsStatus,
} = {}) {
    if (scanPoller) clearIntervalImpl(scanPoller);
    let statusFailures = 0;
    setScanBusy?.(true);
    scanPoller = setIntervalImpl(async () => {
        try {
            const res = await fetchImpl('/api/scan/status');
            const data = await responseDataOrError(res, 'Scan status unavailable');
            statusFailures = 0;
            const countEl = document.getElementById('scan-progress-count');
            if (countEl) countEl.textContent = Number(data.total_found || 0).toLocaleString();
            if (!data.scanning) {
                clearIntervalImpl(scanPoller);
                scanPoller = null;
                setScanBusy?.(false);
                await loadCatalogSourcesImpl?.();
                if (data.error) {
                    setSettingsStatus?.(`Scan stopped: ${data.error}`, 'error');
                } else {
                    setSettingsStatus?.(`Scan complete. ${Number(data.total_found || 0).toLocaleString()} photos found.`, 'success');
                }
            }
        } catch (err) {
            statusFailures += 1;
            if (statusFailures === 1) {
                setSettingsStatus?.(`Scan status unavailable: ${err.message}. Retrying...`, 'error');
            }
            if (statusFailures >= 5) {
                clearIntervalImpl(scanPoller);
                scanPoller = null;
                setScanBusy?.(false);
                setSettingsStatus?.(`Scan status unavailable: ${err.message}. Refresh Catalog to check the latest state.`, 'error');
            }
        }
    }, 1000);
}


export async function addCatalogSource({
    fetchImpl = fetch,
    fallbackStats = {},
    pollScanUntilDone: pollScanUntilDoneImpl,
    renderCatalogSources,
    setScanBusy,
    setSettingsStatus,
    showToast,
} = {}) {
    const folderInput = document.getElementById('scan-folder');
    const folder = folderInput?.value?.trim();
    if (!folder || addSourceInFlight) return;

    addSourceInFlight = true;
    const addButton = document.getElementById('scan-btn');
    if (addButton) addButton.disabled = true;
    setSettingsStatus?.('Adding folder and starting scan...', 'muted');
    try {
        const res = await fetchImpl('/api/catalog/sources', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: folder, scan: true }),
        });
        const data = await responseDataOrError(res, 'Could not add folder');
        renderCatalogSources?.(data.catalog || { sources: [data.source], stats: fallbackStats || {} });
        pollScanUntilDoneImpl?.();
    } catch (err) {
        setScanBusy?.(false);
        setSettingsStatus?.(`Add folder failed: ${err.message}`, 'error');
        showToast?.('Add folder failed');
    } finally {
        addSourceInFlight = false;
        if (addButton) addButton.disabled = false;
    }
}


export async function rescanCatalogSource(sourceId, {
    fetchImpl = fetch,
    pollScanUntilDone: pollScanUntilDoneImpl,
    setSettingsStatus,
    showToast,
} = {}) {
    setSettingsStatus?.('Starting folder scan...', 'muted');
    try {
        const res = await fetchImpl(`/api/catalog/sources/${sourceId}/rescan`, { method: 'POST' });
        await responseDataOrError(res, 'Could not start scan');
        pollScanUntilDoneImpl?.();
    } catch (err) {
        setSettingsStatus?.(`Scan failed to start: ${err.message}`, 'error');
        showToast?.('Scan failed to start');
    }
}


export async function removeCatalogSource(sourceId, mode, {
    closeRemoveSourceDialog,
    fetchImpl = fetch,
    renderCatalogSources,
    setSettingsStatus,
    showToast,
} = {}) {
    closeRemoveSourceDialog?.();
    setSettingsStatus?.(mode === 'delete' ? 'Deleting folder catalog data...' : 'Removing folder from active catalog...', 'muted');
    try {
        const res = await fetchImpl(`/api/catalog/sources/${sourceId}/remove`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        const data = await responseDataOrError(res, 'Remove failed');
        renderCatalogSources?.(data.catalog);
        setSettingsStatus?.(
            mode === 'delete'
                ? `Catalog data deleted for ${Number(data.images_deleted || 0).toLocaleString()} photos.`
                : 'Folder removed from active views. Catalog data was kept.',
            'success',
        );
    } catch (err) {
        setSettingsStatus?.(`Remove failed: ${err.message}`, 'error');
        showToast?.('Remove failed');
    }
}
