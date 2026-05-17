import {
    cacheDiskUsageText,
    cacheMemoryTiersText,
    cacheMemoryUsageText,
    formatCacheTier,
    pregenDiagnosticsText,
    pregenSummaryText,
    resourceFreeText,
} from '../cache/status.js';
import {
    autoPrefetchText,
    autoWorkersText,
    browserCachePolicyText,
} from './display.js';


export function renderCacheSettingsStatus(cacheStatus) {
    if (!cacheStatus) return;

    const memory = cacheStatus.memory || {};
    const disk = cacheStatus.disk || {};
    const tiers = disk.tiers || {};
    const pregen = cacheStatus.pregen || {};

    const cacheEl = document.getElementById('cache-stats-inline');
    const ramEl = document.getElementById('cache-ram-usage');
    const systemRamEl = document.getElementById('system-ram-free');
    const ssdEl = document.getElementById('cache-ssd-usage');
    const systemSsdEl = document.getElementById('system-ssd-free');
    const smEl = document.getElementById('cache-tier-sm');
    const mdEl = document.getElementById('cache-tier-md');
    const lgEl = document.getElementById('cache-tier-lg');
    const fullEl = document.getElementById('cache-tier-full');
    const pregenEl = document.getElementById('cache-pregen-summary');
    const pregenDiagnosticsEl = document.getElementById('cache-pregen-diagnostics');
    const thumbPauseBtn = document.getElementById('thumb-pause-btn');
    const thumbResumeBtn = document.getElementById('thumb-resume-btn');

    if (cacheEl) {
        cacheEl.textContent = cacheMemoryUsageText(memory);
    }
    if (ramEl) {
        ramEl.textContent = cacheMemoryTiersText(memory);
    }
    const resources = cacheStatus.system_resources || {};
    const systemMemory = resources.memory || {};
    const systemDisk = resources.disk || {};
    if (systemRamEl) {
        systemRamEl.textContent = resourceFreeText(systemMemory);
    }
    if (ssdEl) {
        ssdEl.textContent = cacheDiskUsageText(disk);
    }
    if (systemSsdEl) {
        systemSsdEl.textContent = resourceFreeText(systemDisk);
    }
    if (smEl) smEl.textContent = formatCacheTier('sm', tiers.sm);
    if (mdEl) mdEl.textContent = formatCacheTier('md', tiers.md);
    if (lgEl) lgEl.textContent = formatCacheTier('lg', tiers.lg);
    if (fullEl) fullEl.textContent = formatCacheTier('full', tiers.full);
    if (pregenEl) {
        pregenEl.textContent = pregenSummaryText(pregen);
    }
    if (pregenDiagnosticsEl) {
        pregenDiagnosticsEl.textContent = pregenDiagnosticsText(pregen);
    }
    const thumbnailsPaused = Boolean(pregen.manual_pause);
    if (thumbPauseBtn) thumbPauseBtn.disabled = thumbnailsPaused;
    if (thumbResumeBtn) thumbResumeBtn.disabled = !thumbnailsPaused && pregen.state === 'running';
}


export function renderAutoTuningStatus(settings) {
    if (!settings) return;

    const workersEl = document.getElementById('cache-auto-workers');
    const prefetchEl = document.getElementById('cache-auto-prefetch');
    const browserEl = document.getElementById('cache-browser-policy');

    if (workersEl) {
        workersEl.textContent = autoWorkersText(settings);
    }

    if (prefetchEl) {
        prefetchEl.textContent = autoPrefetchText(settings);
    }

    if (browserEl) {
        browserEl.textContent = browserCachePolicyText(settings);
    }
}
