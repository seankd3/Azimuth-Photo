export function createHomeScanController({
    documentImpl = document,
    fetchImpl = fetch,
    setIntervalImpl = setInterval,
    clearIntervalImpl = clearInterval,
    setTimeoutImpl = setTimeout,
    locationImpl = window.location,
    pollMs = 500,
    reloadDelayMs = 1000,
} = {}) {
    let scanPoller = null;

    function setButtonBusy(button, busy) {
        if (!button) return;
        button.disabled = busy;
        button.textContent = busy ? 'Scanning...' : 'Scan Folder';
    }

    function showProgress(progress) {
        progress?.classList?.remove('hidden');
    }

    function showError(statusText, progress, button, message) {
        if (statusText) {
            statusText.textContent = message;
            statusText.classList.add('scan-error');
        }
        showProgress(progress);
        setButtonBusy(button, false);
    }

    async function startScan() {
        const folder = documentImpl.getElementById('folder-input')?.value?.trim();
        if (!folder) return;

        const button = documentImpl.getElementById('scan-btn');
        const progress = documentImpl.getElementById('scan-progress');
        const statusText = documentImpl.getElementById('scan-status-text');
        const fill = documentImpl.getElementById('scan-fill');

        setButtonBusy(button, true);
        statusText?.classList?.remove('scan-error');
        showProgress(progress);

        try {
            const response = await fetchImpl('/api/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder }),
            });
            if (!response.ok) {
                const error = await response.json();
                showError(statusText, progress, button, error.error || 'Scan failed');
                return;
            }

            if (scanPoller) clearIntervalImpl(scanPoller);
            scanPoller = setIntervalImpl(async () => {
                const status = await (await fetchImpl('/api/scan/status')).json();
                statusText?.classList?.remove('scan-error');
                if (statusText) {
                    statusText.textContent = `Found ${status.total_found} images, inserted ${status.total_inserted}...`;
                }
                if (fill) {
                    fill.style.width = status.total_found > 0
                        ? `${(status.total_inserted / status.total_found * 100)}%`
                        : '0%';
                }

                if (status.done) {
                    clearIntervalImpl(scanPoller);
                    scanPoller = null;
                    statusText?.classList?.remove('scan-error');
                    if (statusText) {
                        statusText.textContent = `Done! ${status.total_inserted} images ready.`;
                    }
                    setButtonBusy(button, false);
                    setTimeoutImpl(() => locationImpl.reload(), reloadDelayMs);
                }
            }, pollMs);
        } catch (error) {
            showError(statusText, progress, button, `Scan failed: ${error.message}`);
        }
    }

    return { startScan };
}
