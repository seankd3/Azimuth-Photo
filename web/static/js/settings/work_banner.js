import {
    formatEta,
    formatRatePerMinute,
} from './display.js';


export function workBannerHtml(aiStatus, cacheStatus) {
    const ai = aiStatus || {};
    const pregen = cacheStatus?.pregen || {};
    const disk = cacheStatus?.disk || {};

    // Embedding progress
    const embedTotal = Number(ai.total_images ?? ai.total_kept ?? 0);
    const embedDone = Number(ai.embedded || 0);
    const embedRemaining = Number(ai.remaining || 0);
    const embedPct = Number(ai.progress_pct || 0);
    const embedPaused = Boolean(ai.embedding_manual_pause);
    const embedComplete = embedRemaining <= 0 && embedTotal > 0;

    // Preview cache progress stays separate from original SSD warming.
    const diskTiers = disk.tiers || {};
    const previewTierNames = ['sm', 'md', 'lg'];
    const previewSummary = pregen.preview || {};
    let previewDone = Number(previewSummary.count || 0);
    let previewTotal = Number(previewSummary.total || 0);
    if (previewTotal <= 0) {
        for (const name of previewTierNames) {
            const t = diskTiers[name] || {};
            previewDone += Number(t.progress_count ?? t.count ?? 0);
            previewTotal += Number(t.progress_total || 0);
        }
    }
    const previewPct = previewTotal > 0 ? Math.min(100, previewDone / previewTotal * 100) : 0;
    const thumbPaused = Boolean(pregen.manual_pause);
    const previewComplete = previewPct >= 95;

    const fullTier = diskTiers.full || {};
    const originals = pregen.originals || {};
    const originalDone = Number(originals.count ?? fullTier.progress_count ?? fullTier.count ?? 0);
    const originalTotal = Number(originals.total ?? fullTier.progress_total ?? 0);
    const originalBudget = Number(originals.budget_bytes ?? fullTier.budget_bytes ?? 0);
    const originalPct = originalTotal > 0
        ? Math.min(100, originalDone / originalTotal * 100)
        : Math.min(100, Number(originals.utilization_pct ?? fullTier.utilization_pct ?? 0));
    const originalEnabled = originalBudget > 0 || originalTotal > 0 || originalDone > 0;
    const originalComplete = !originalEnabled || originalPct >= 95 || Number(originals.remaining || 0) <= 0;

    // ETAs
    let embedEta = '';
    if (embedComplete) embedEta = 'Done';
    else if (ai.eta_seconds) embedEta = formatEta(ai.eta_seconds);
    else if (ai.worker_state === 'embedding') embedEta = 'Measuring\u2026';

    let previewEta = '';
    if (previewComplete) previewEta = 'Done';
    else if (pregen.eta_seconds) previewEta = formatEta(pregen.eta_seconds);
    else if (pregen.state === 'running') previewEta = 'Measuring\u2026';

    let originalEta = '';
    if (originalComplete) originalEta = 'Done';
    else if (pregen.original_eta_seconds) originalEta = formatEta(pregen.original_eta_seconds);
    else if (pregen.state === 'running' && pregen.active_phase === 'full') originalEta = 'Measuring\u2026';

    // Speeds
    const embedSpeed = Number(ai.recent_images_per_min || ai.overall_images_per_min || 0);
    const thumbSpeed = Number(pregen.recent_images_per_min || pregen.overall_images_per_min || 0);

    // Build HTML
    const allDone = embedComplete && previewComplete && originalComplete;
    const pauseAllLabel = (embedPaused && thumbPaused) ? 'Resume All' : 'Pause All';
    const pauseAllAction = (embedPaused && thumbPaused) ? 'resumeAllWork' : 'pauseAllWork';

    return `
            <div class="work-banner-head">
                <span class="work-banner-title">Background Work</span>
                ${!allDone ? `<div class="work-banner-actions">
                    <button class="bar-btn" type="button" onclick="PhotoArchive.${pauseAllAction}()">${pauseAllLabel}</button>
                </div>` : ''}
            </div>
            <div class="work-banner-rows">
                <div class="work-row embed">
                    <div class="work-row-head">
                        <span class="work-row-label">AI Embeddings</span>
                        <span class="work-row-stat">${embedDone.toLocaleString()} / ${embedTotal.toLocaleString()}</span>
                    </div>
                    <div class="work-row-bar"><div class="work-row-fill" style="width:${embedPct}%"></div></div>
                    <div class="work-row-detail">
                        <span>${embedSpeed > 0 ? formatRatePerMinute(embedSpeed) : (embedComplete ? 'Complete' : embedPaused ? 'Paused' : 'Waiting')}</span>
                        <span class="work-row-eta">${embedEta}</span>
                    </div>
                </div>
                <div class="work-row thumb">
                    <div class="work-row-head">
                        <span class="work-row-label">Preview Cache</span>
                        <span class="work-row-stat">${previewDone.toLocaleString()} / ${previewTotal.toLocaleString()}</span>
                    </div>
                    <div class="work-row-bar"><div class="work-row-fill" style="width:${previewPct}%"></div></div>
                    <div class="work-row-detail">
                        <span>${thumbSpeed > 0 ? formatRatePerMinute(thumbSpeed) : (previewComplete ? 'Complete' : thumbPaused ? 'Paused' : 'Waiting')}</span>
                        <span class="work-row-eta">${previewEta}</span>
                    </div>
                </div>
                <div class="work-row thumb">
                    <div class="work-row-head">
                        <span class="work-row-label">Original SSD Cache</span>
                        <span class="work-row-stat">${originalDone.toLocaleString()} / ${originalTotal.toLocaleString()}</span>
                    </div>
                    <div class="work-row-bar"><div class="work-row-fill" style="width:${originalPct}%"></div></div>
                    <div class="work-row-detail">
                        <span>${originalComplete ? 'Complete' : thumbPaused ? 'Paused' : pregen.active_phase === 'full' ? 'Warming' : 'Waiting'}</span>
                        <span class="work-row-eta">${originalEta}</span>
                    </div>
                </div>
            </div>
        `;
}
