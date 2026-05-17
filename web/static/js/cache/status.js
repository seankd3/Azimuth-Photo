import { formatBytes } from '../ui.js';
import {
    formatEta,
    formatRatePerMinute,
} from '../settings/display.js';


export function formatCacheTier(label, tier) {
    if (!tier) return `${label}: —`;
    const budget = Number(tier.budget_bytes || 0);
    const progressTotal = Number(tier.progress_total || 0);
    const progressCount = Number((tier.progress_count ?? tier.count) || 0);
    const staleCount = Number(tier.stale_count || 0);
    const progress = progressTotal > 0
        ? `${progressCount.toLocaleString()} / ${progressTotal.toLocaleString()} (${Number(tier.progress_pct || 0).toFixed(1)}%)`
        : `${Number(tier.count || 0).toLocaleString()} cached`;
    const budgetText = budget > 0
        ? `${formatBytes(tier.bytes)} / ${formatBytes(budget)}`
        : `${formatBytes(tier.bytes)} / off`;
    const fallbackText = staleCount > 0 ? ` · ${staleCount.toLocaleString()} older usable` : '';
    return `${label}: ${budgetText} · ${progress}${fallbackText}`;
}


export function cacheMemoryUsageText(memory = {}) {
    return `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.limit_bytes)}`;
}


export function cacheMemoryTiersText(memory = {}) {
    const memoryTiers = memory.tiers || {};
    return ['sm', 'md', 'lg'].map((size) => {
        const tier = memoryTiers[size] || {};
        return `${size} ${Number(tier.count || 0)} · ${formatBytes(tier.bytes)}`;
    }).join('   ');
}


export function resourceFreeText(resource = {}) {
    const free = Number(resource.free_bytes ?? resource.available_bytes ?? 0);
    const total = Number(resource.total_bytes || 0);
    const pct = Number(resource.free_pct ?? resource.available_pct ?? 0);
    return total > 0
        ? `${formatBytes(free)} / ${formatBytes(total)} (${pct.toFixed(1)}% free)`
        : 'Unknown';
}


export function cacheDiskUsageText(disk = {}) {
    const pct = Number(disk.utilization_pct || 0);
    return `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.limit_bytes)} (${pct.toFixed(1)}%)`;
}


export function pregenSummaryText(pregen = {}) {
    const state = pregen.replacement_mode ? 'refreshing previews' : (pregen.state || 'idle');
    const phase = pregen.active_phase ? ` · ${pregen.active_phase}` : '';
    const message = pregen.message ? ` · ${pregen.message}` : '';
    const rate = Number(pregen.recent_images_per_min || pregen.overall_images_per_min || 0);
    const eta = pregen.eta_seconds ? ` · ETA ${formatEta(pregen.eta_seconds)}` : '';
    const speed = rate > 0 ? ` · ${formatRatePerMinute(rate)}` : '';
    return `${state}${phase}${message}${speed}${eta}`;
}


export function pregenDiagnosticsText(pregen = {}) {
    const reads = Number(pregen.recent_source_reads_per_min || 0);
    const writes = Number(pregen.recent_thumbnails_written_per_min || 0);
    const mbps = Number(pregen.recent_read_mbps || 0);
    const readMs = Number(pregen.avg_source_read_seconds || 0) * 1000;
    const encodeMs = Number(pregen.avg_decode_encode_seconds || 0) * 1000;
    const failures = Number(pregen.source_read_failures || 0);
    return `${reads.toFixed(1)} reads/min · ${writes.toFixed(1)} thumbs/min · ` +
        `${mbps.toFixed(1)} MB/s · read ${readMs.toFixed(0)}ms · encode ${encodeMs.toFixed(0)}ms · ` +
        `${failures.toLocaleString()} failures`;
}
