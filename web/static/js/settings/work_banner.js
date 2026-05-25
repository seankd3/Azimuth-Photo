import { backgroundProcessRows } from '../work/status_panel.js';


function clampPct(value) {
    const pct = Number(value || 0);
    if (!Number.isFinite(pct)) return 0;
    return Math.max(0, Math.min(100, pct));
}


function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}


function workRowHtml(row) {
    const pct = clampPct(row.pct);
    return `
        <div class="work-row ${escapeHtml(row.kind || '')} ${escapeHtml(row.tone || 'idle')}">
            <div class="work-row-head">
                <span class="work-row-label">${escapeHtml(row.label)}</span>
                <span class="work-row-stat">${Number(row.done || 0).toLocaleString()} / ${Number(row.total || 0).toLocaleString()}</span>
            </div>
            <div class="work-row-bar"><div class="work-row-fill" style="width:${pct}%"></div></div>
            <div class="work-row-detail">
                <span>${escapeHtml(row.detail || row.state || '')}</span>
                <span class="work-row-eta">${escapeHtml(row.eta || row.state || '')}</span>
            </div>
        </div>
    `;
}


export function workBannerHtml(aiStatus, cacheStatus, peopleStatus = null) {
    const rows = backgroundProcessRows(aiStatus || {}, cacheStatus || {}, peopleStatus);
    const activeRows = rows.filter((row) => row.tone !== 'done');

    return `
        <div class="work-banner-head">
            <span class="work-banner-title">Background Work</span>
            ${activeRows.length ? `<span class="work-row-eta">${activeRows.length} active or pending</span>` : ''}
        </div>
        <div class="work-banner-rows">
            ${rows.map(workRowHtml).join('')}
        </div>
    `;
}
