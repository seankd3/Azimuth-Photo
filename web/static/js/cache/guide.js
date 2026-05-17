import { formatBytes } from '../ui.js';
import {
    cacheProfileLabel,
    formatEta,
} from '../settings/display.js';


export function renderCacheTierGuide(cs, settings = {}) {
    const el = document.getElementById('cache-tier-table');
    const titleEl = document.getElementById('cache-health-title');
    const subtitleEl = document.getElementById('cache-health-subtitle');
    const adviceEl = document.getElementById('cache-storage-advice');
    const rec = cs?.recommendations;
    if (!el || !rec?.tiers) return;

    const disk = cs.disk || {};
    const diskTiers = disk.tiers || {};
    const profile = rec.budget?.profile || 'original_heavy';
    const profileLabel = cacheProfileLabel(profile);
    const warmed = (name) => Number(diskTiers[name]?.progress_pct || 0);
    const smWarm = warmed('sm');
    const mdWarm = warmed('md');
    const originalsRemaining = Number(cs.pregen?.originals?.remaining || 0);
    const estimateIsEarly = (name) => {
        const plan = rec.tiers[name] || {};
        const tier = diskTiers[name] || {};
        const count = Number(plan.sample_count ?? tier.count ?? 0);
        const total = Number(tier.progress_total || 0);
        if (count <= 0) return true;
        if (count < 5000) return true;
        return total > 0 && count / total < 0.05;
    };
    const headline = mdWarm >= 95
        ? (originalsRemaining > 0 ? 'Fast browsing cache is warm; originals are warming' : 'Fast browsing cache is fully warmed')
        : smWarm >= 95
            ? 'Grid browsing is warmed; loupe previews are still building'
            : 'photoArchive is building the fast cache';

    if (titleEl) {
        const building = mdWarm < 95 || originalsRemaining > 0;
        titleEl.innerHTML = headline + (building ? ' <span class="cache-building-dot"></span>' : '');
    }
    if (subtitleEl) {
        const freeBytes = Number(cs.system_resources?.disk?.free_bytes || 0);
        const freeText = freeBytes > 0 ? ` · ${formatBytes(freeBytes)} free on disk` : '';
        subtitleEl.textContent =
            `${profileLabel} priority · ${formatBytes(disk.used_bytes)} used of ${formatBytes(disk.limit_bytes)} on SSD${freeText}`;
    }

    const card = (name, title) => {
        const plan = rec.tiers[name] || {};
        const actual = diskTiers[name] || {};
        const pct = Math.max(0, Math.min(100, Number(actual.progress_pct || 0)));
        const capacityPct = Math.max(0, Math.min(100, Number(plan.coverage_pct || 0)));
        const progressCount = Number((actual.progress_count ?? actual.count) || 0);
        const staleCount = Number(actual.stale_count || 0);
        const cached = progressCount.toLocaleString();
        const total = Number(actual.progress_total || (name === 'full' ? rec.total_images : rec.eligible_images) || 0).toLocaleString();
        const estimated = Number(plan.estimated_cached || 0).toLocaleString();
        const capacityText = capacityPct >= 95
            ? estimateIsEarly(name) && name !== 'full' ? 'Likely space for all' : 'Space for all'
            : `Space for ~${estimated}`;
        let state = 'Not started';
        let cls = '';
        if (pct >= 95) {
            state = actual.replacement_mode ? 'Refreshed' : 'Generated';
            cls = 'ready';
        } else if (pct > 0) {
            state = actual.replacement_mode ? `${pct.toFixed(0)}% refreshed` : `${pct.toFixed(0)}% generated`;
            cls = 'selective';
        } else if (actual.replacement_mode && staleCount > 0) {
            state = 'Refreshing';
            cls = 'selective';
        }
        const remaining = name === 'full'
            ? Number(cs.pregen?.originals?.remaining || 0)
            : Number(cs.pregen?.phases?.[name]?.remaining || 0);
        const etaSeconds = name === 'full' ? cs.pregen?.original_eta_seconds : cs.pregen?.eta_seconds;
        const etaText = remaining > 0 && etaSeconds
            ? `<div class="cache-card-meta"><span>${remaining.toLocaleString()} remaining</span><span>ETA ${formatEta(etaSeconds)}</span></div>`
            : '';
        const availabilityText = actual.replacement_mode && staleCount > 0
            ? `${cached} refreshed · ${staleCount.toLocaleString()} older usable`
            : name === 'full'
                ? `${cached} of ${total} cached`
                : `${cached} of ${total} generated`;
        return `
                <div class="cache-friendly-card tier-${name} ${cls}">
                    <div class="cache-card-meta"><strong>${title}</strong><span>${state}</span></div>
                    <div class="cache-progress"><div class="cache-progress-fill" style="width:${pct}%"></div></div>
                    <div class="cache-card-meta"><span>${availabilityText}</span><span>${capacityText}</span></div>
                    ${etaText}
                </div>
            `;
    };
    el.innerHTML = [
        card('sm', 'Grid scrolling'),
        card('md', 'Loupe previews'),
        card('lg', 'High-res previews'),
        card('full', 'Original SSD cache'),
    ].join('');

    if (adviceEl) {
        const needs = (name) => Number(rec.tiers[name]?.full_archive_bytes || 0);
        const used = (name) => Number(diskTiers[name]?.used_bytes || 0);
        const instantBytes = needs('sm') + needs('md');
        const allHighResBytes = instantBytes + needs('lg');
        const originalsBytes = needs('full');
        const ssdBudget = Number(disk.limit_bytes || 0);
        const memoryBudget = Number(cs.memory?.limit_bytes || 0);
        const resources = cs.system_resources || {};
        const systemDisk = resources.disk || {};
        const systemMemory = resources.memory || {};
        const machineFreeBytes = Number(systemDisk.free_bytes || 0);
        const machineFreePct = Number(systemDisk.free_pct || 0);
        const ramAvailableBytes = Number(systemMemory.available_bytes || 0);
        const ramAvailablePct = Number(systemMemory.available_pct || 0);

        // Budget bar segments (proportional to actual usage within the budget)
        const seg = (name) => ssdBudget > 0 ? Math.max(0, Math.min(100, used(name) / ssdBudget * 100)) : 0;
        const usedTotal = used('sm') + used('md') + used('lg') + used('full');

        // RAM assessment (short)
        const ramNote = memoryBudget >= 3 * 1024 * 1024 * 1024
            ? `${formatBytes(memoryBudget)} RAM — generous; recent previews stay hot.`
            : memoryBudget >= 1 * 1024 * 1024 * 1024
                ? `${formatBytes(memoryBudget)} RAM — solid for browsing.`
                : `${formatBytes(memoryBudget)} RAM — conservative; previews cycle out faster.`;
        const machineNote = machineFreeBytes > 0
            ? `${formatBytes(machineFreeBytes)} SSD free on the cache volume (${machineFreePct.toFixed(1)}%). ${formatBytes(ramAvailableBytes)} RAM currently available (${ramAvailablePct.toFixed(1)}%).`
            : `${formatBytes(ramAvailableBytes)} RAM currently available (${ramAvailablePct.toFixed(1)}%).`;

        adviceEl.innerHTML = `
                <div class="cache-budget-bar">
                    <div class="cache-budget-seg seg-sm" style="width:${seg('sm')}%"></div>
                    <div class="cache-budget-seg seg-md" style="width:${seg('md')}%"></div>
                    <div class="cache-budget-seg seg-lg" style="width:${seg('lg')}%"></div>
                    <div class="cache-budget-seg seg-full" style="width:${seg('full')}%"></div>
                </div>
                <div class="cache-budget-legend">
                    <span class="leg-sm">Grid ${formatBytes(used('sm'))}</span>
                    <span class="leg-md">Loupe ${formatBytes(used('md'))}</span>
                    <span class="leg-lg">High-res ${formatBytes(used('lg'))}</span>
                    <span class="leg-full">Originals ${formatBytes(used('full'))}</span>
                    <span class="leg-free">${formatBytes(Math.max(0, ssdBudget - usedTotal))} free</span>
                </div>
                <div class="cache-metrics">
                    <div class="cache-metric">
                        <div class="cache-metric-label">Everyday target</div>
                        <div class="cache-metric-value">${formatBytes(instantBytes)}</div>
                    </div>
                    <div class="cache-metric">
                        <div class="cache-metric-label">All previews</div>
                        <div class="cache-metric-value">${formatBytes(allHighResBytes)}</div>
                    </div>
                    <div class="cache-metric">
                        <div class="cache-metric-label">All originals</div>
                        <div class="cache-metric-value">${formatBytes(originalsBytes)}</div>
                    </div>
                </div>
                <div class="cache-ram-note">${ramNote}</div>
                <div class="cache-ram-note">${machineNote}</div>
            `;
    }
}
