export function rankingSignalCount(stats = {}) {
    return Number(stats.ranking_signal_count ?? stats.total_comparisons ?? 0);
}


export function visibleTotalLabel(visible, total) {
    const visibleNum = Number(visible ?? 0);
    const totalNum = Number(total ?? visibleNum);
    const visibleText = visibleNum.toLocaleString();
    if (Number.isFinite(totalNum) && totalNum !== visibleNum) {
        return `${visibleText} / ${totalNum.toLocaleString()}`;
    }
    return visibleText;
}


export function poolVisibleCount(stats = {}) {
    return Number(
        stats.filtered_pool_visible
        ?? stats.visible_images
        ?? stats.filtered_pool
        ?? stats['ke' + 'pt']
        ?? 0
    );
}


export function poolTotalCount(stats = {}) {
    const visible = poolVisibleCount(stats);
    return Number(
        stats.filtered_pool_total
        ?? stats.total_images
        ?? stats.total_kept
        ?? stats.filtered_pool
        ?? visible
    );
}


export function bumpRankingSignals(stats = {}, signalDelta, directDelta = 0) {
    const next = Math.max(0, rankingSignalCount(stats) + Number(signalDelta || 0));
    stats.ranking_signal_count = next;
    stats.total_comparisons = next;
    if (stats.direct_comparison_rows !== undefined) {
        stats.direct_comparison_rows = Math.max(
            0,
            Number(stats.direct_comparison_rows || 0) + Number(directDelta || 0),
        );
    }
    return stats;
}


export function mergeCoverageStats(targetStats = {}, stats = {}) {
    for (const key of ['total_images', 'rated_images', 'total_comparisons', 'ranking_signal_count']) {
        if (stats[key] !== undefined) targetStats[key] = stats[key];
    }
    return targetStats;
}


export function renderCompareProgress({
    documentImpl = document,
    stats = {},
    displayedComparisons = -1,
    updateCoverageBarImpl = null,
    rollUpCounterImpl = rollUpCounter,
} = {}) {
    const total = rankingSignalCount(stats);
    const compEl = documentImpl.getElementById('compare-stat-comparisons');
    const poolEl = documentImpl.getElementById('compare-stat-pool');
    if (poolEl) poolEl.textContent = visibleTotalLabel(poolVisibleCount(stats), poolTotalCount(stats));
    if (updateCoverageBarImpl) updateCoverageBarImpl();
    if (!compEl) return displayedComparisons;

    if (displayedComparisons < 0) {
        compEl.textContent = total.toLocaleString();
        return total;
    }
    if (total === displayedComparisons) return displayedComparisons;
    rollUpCounterImpl(compEl, displayedComparisons, total);
    return total;
}


export function coveragePercent(stats = {}) {
    const totalImages = Number(stats.total_images || 0);
    const ratedImages = Number(stats.rated_images || 0);
    const pct = totalImages > 0 ? Math.round((ratedImages / totalImages) * 100) : 0;
    return Math.max(0, Math.min(100, pct));
}


export function renderCoverageBar(stats = {}) {
    const fill = document.getElementById('coverage-fill');
    const label = document.getElementById('coverage-label');
    if (!fill || !label) return false;
    const clamped = coveragePercent(stats);
    fill.style.width = `${clamped}%`;
    label.textContent = `${clamped}% ranked`;
    return true;
}


export function propagationBadgeText(count) {
    return ` +${count} similar`;
}


let propagationBadgeTimer = null;


export function showPropagationBadge(count, { durationMs = 3000 } = {}) {
    const badge = document.getElementById('propagation-badge');
    if (!badge) return false;
    badge.textContent = propagationBadgeText(count);
    badge.classList.add('visible');
    clearTimeout(propagationBadgeTimer);
    propagationBadgeTimer = setTimeout(() => badge.classList.remove('visible'), durationMs);
    return true;
}


let rollupAnimation = null;


export function rollUpCounter(el, from, to) {
    if (!el) return false;
    if (rollupAnimation) cancelAnimationFrame(rollupAnimation);
    const diff = to - from;
    const duration = Math.min(800, Math.max(300, Math.abs(diff) * 10));
    const start = performance.now();

    function tick(now) {
        const t = Math.min((now - start) / duration, 1);
        const eased = 1 - (1 - t) * (1 - t); // ease-out quad
        const current = Math.round(from + diff * eased);
        el.textContent = current.toLocaleString();
        if (t < 1) {
            rollupAnimation = requestAnimationFrame(tick);
        } else {
            rollupAnimation = null;
        }
    }
    rollupAnimation = requestAnimationFrame(tick);
    return true;
}
