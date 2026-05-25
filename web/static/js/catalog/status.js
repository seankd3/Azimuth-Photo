export function sourceState(source) {
    if (!source?.included) {
        return {
            label: 'Removed',
            cls: 'removed',
            help: 'Hidden from Library and Compare until restored.',
        };
    }
    if (!source?.online) {
        return {
            label: 'Offline',
            cls: 'offline',
            help: 'Drive not reachable right now. Cached browsing, search, ranking, and People data remain available.',
        };
    }
    return {
        label: 'Online',
        cls: 'online',
        help: 'Drive is reachable for rescans and any previews that still need source files.',
    };
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
