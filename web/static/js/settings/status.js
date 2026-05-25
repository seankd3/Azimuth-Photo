export function setSettingsStatus(message, tone = '') {
    const el = document.getElementById('settings-status');
    if (!el) return;
    el.textContent = message;
    el.className = 'settings-status' + (tone ? ' ' + tone : '');
}


export function backgroundWorkStatusText(cacheStats, aiStatus) {
    const cacheState = cacheStats?.pregen?.state || 'paused';
    const aiState = aiStatus?.worker_state || 'paused';
    const running = cacheState === 'running' || aiState === 'embedding' || aiState === 'loading_model';
    return {
        statusText: running ? 'Manual work is running' : 'Manual work is paused',
        heavyText: running ? 'running' : 'paused',
    };
}
