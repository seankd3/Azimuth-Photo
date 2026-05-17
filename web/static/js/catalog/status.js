export function sourceState(source) {
    if (!source?.included) return { label: 'Removed', cls: 'removed' };
    if (!source?.online) return { label: 'Offline', cls: 'offline' };
    return { label: 'Online', cls: 'online' };
}


export function setScanBusy(busy, label = 'Add + Scan') {
    const btn = document.getElementById('scan-btn');
    if (btn) {
        btn.disabled = busy;
        btn.textContent = busy ? 'Scanning...' : label;
    }
    const progress = document.getElementById('scan-progress');
    if (progress) progress.classList.toggle('hidden', !busy);
}
