export function setSettingsStatus(message, tone = '') {
    const el = document.getElementById('settings-status');
    if (!el) return;
    el.textContent = message;
    el.className = 'settings-status' + (tone ? ' ' + tone : '');
}


export function backgroundWorkStatusText(cacheStats, aiStatus, modeLabel) {
    const governor = cacheStats?.governor || aiStatus?.governor || {};
    const state = governor.pause ? 'paused' : (governor.mode || 'ready');
    const reason = governor.reason ? `: ${governor.reason}` : '';
    return {
        governorText: `${modeLabel} · ${state}${reason}`,
        heavyText: governor.pause ? 'paused' : 'enabled',
    };
}
