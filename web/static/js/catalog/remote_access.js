function accessLine(data = {}) {
    const tailscale = data.tailscale || {};
    if (!tailscale.available) {
        return {
            label: 'Tailscale unavailable',
            detail: tailscale.error || 'Start Tailscale on Omarchy to use the laptop console.',
            url: '',
        };
    }
    return {
        label: data.access_mode === 'tailscale' ? 'Tailscale console active' : 'Tailscale address ready',
        detail: tailscale.url || 'Tailscale is connected.',
        url: tailscale.url || '',
    };
}


export function createRemoteAccessController({
    documentImpl = document,
    navigatorImpl = navigator,
    setSettingsStatus,
    showToast,
} = {}) {
    let currentUrl = '';

    async function loadRemoteAccess() {
        const card = documentImpl.getElementById('remote-access-card');
        const title = documentImpl.getElementById('remote-access-title');
        const detail = documentImpl.getElementById('remote-access-detail');
        const urlInput = documentImpl.getElementById('remote-access-url');
        if (!card) return null;
        try {
            const response = await fetch('/api/remote-access');
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || 'Remote access unavailable');
            const line = accessLine(data);
            currentUrl = line.url;
            if (title) title.textContent = line.label;
            if (detail) detail.textContent = line.detail;
            if (urlInput) urlInput.value = currentUrl;
            card.dataset.available = currentUrl ? 'true' : 'false';
            return data;
        } catch (error) {
            currentUrl = '';
            if (title) title.textContent = 'Remote access unavailable';
            if (detail) detail.textContent = error.message;
            if (urlInput) urlInput.value = '';
            card.dataset.available = 'false';
            return null;
        }
    }

    async function copyRemoteUrl() {
        if (!currentUrl) {
            setSettingsStatus?.('No Tailscale URL available yet.', 'error');
            return false;
        }
        try {
            await navigatorImpl.clipboard?.writeText(currentUrl);
            setSettingsStatus?.('Tailscale URL copied.', 'success');
            return true;
        } catch {
            showToast?.('Copy failed');
            setSettingsStatus?.(currentUrl, 'muted');
            return false;
        }
    }

    function initRemoteAccessPanel() {
        loadRemoteAccess();
    }

    return {
        copyRemoteUrl,
        initRemoteAccessPanel,
        loadRemoteAccess,
    };
}
