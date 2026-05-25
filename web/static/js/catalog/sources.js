import { formatDateTime } from '../media_metadata.js';
import { escapeHtml } from '../ui.js';
import { sourceState } from './status.js';


export function catalogSourcesHtml(sources = []) {
    if (!sources.length) {
        return `
                <div class="catalog-empty">
                    <strong>No folders added yet.</strong>
                    <span>Choose a photo folder above, then use Add + Scan to populate Library.</span>
                </div>
            `;
    }
    return sources.map((source) => {
        const state = sourceState(source);
        const count = Number(source.image_count || 0).toLocaleString();
        const active = Number(source.active_image_count || 0).toLocaleString();
        const lastScan = source.last_scan_at ? formatDateTime(source.last_scan_at) : 'Never scanned';
        const canScan = source.online ? '' : 'disabled';
        const restoreLabel = source.included ? 'Rescan' : 'Restore + Scan';
        return `
                <div class="catalog-source ${state.cls}">
                    <div class="catalog-source-main">
                        <div class="catalog-source-title">
                            <strong>${escapeHtml(source.display_name || source.path)}</strong>
                            <span class="catalog-source-state ${state.cls} has-tooltip" data-tooltip="${escapeHtml(state.help || '')}">${state.label}</span>
                        </div>
                        <code>${escapeHtml(source.path)}</code>
                        <div class="catalog-source-meta">${active} active · ${count} catalog · ${escapeHtml(lastScan)}</div>
                        ${source.included && !source.online ? '<div class="catalog-source-note">Cached Library, Compare, search, and People views remain available while the drive is offline.</div>' : ''}
                    </div>
                    <div class="catalog-source-actions">
                        <button class="bar-btn has-tooltip" type="button" ${canScan} data-action="rescan-catalog-source" data-source-id="${escapeHtml(source.id)}" data-tooltip="Look for new, changed, or missing photos in this folder.">${restoreLabel}</button>
                        <button class="bar-btn danger has-tooltip" type="button" data-action="open-remove-source-dialog" data-source-id="${escapeHtml(source.id)}" data-tooltip="Remove this folder from active views or delete its catalog/cache rows. Originals stay on disk.">Remove</button>
                    </div>
                </div>
            `;
    }).join('');
}


export function renderCatalogSources(catalog) {
    const stats = catalog?.stats || {};
    const activeEl = document.getElementById('scan-total-images');
    const catalogEl = document.getElementById('catalog-total-images');
    if (activeEl) activeEl.textContent = Number(stats.active_images ?? stats.total_images ?? 0).toLocaleString();
    if (catalogEl) catalogEl.textContent = Number(stats.total_catalog_images ?? stats.total_images ?? 0).toLocaleString();

    const sources = catalog?.sources || [];
    const list = document.getElementById('catalog-source-list');
    if (list) list.innerHTML = catalogSourcesHtml(sources);
    return sources;
}


export function openRemoveSourceDialog(source) {
    if (!source) return;
    document.getElementById('catalog-remove-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'catalog-remove-modal';
    modal.className = 'modal-backdrop';
    modal.innerHTML = `
            <div class="catalog-remove-dialog">
                <h2>Remove Folder</h2>
                <p>${escapeHtml(source.path)}</p>
                <div class="catalog-remove-actions">
                    <button class="bar-btn" type="button" data-action="remove-catalog-source" data-source-id="${escapeHtml(source.id)}" data-policy="keep">Remove Folder, Keep Catalog Data</button>
                    <button class="bar-btn danger" type="button" data-action="remove-catalog-source" data-source-id="${escapeHtml(source.id)}" data-policy="delete">Remove Folder and Delete Catalog Data</button>
                    <button class="bar-btn" type="button" data-action="close-remove-source-dialog">Cancel</button>
                </div>
            </div>
        `;
    document.body.appendChild(modal);
}


export function closeRemoveSourceDialog() {
    document.getElementById('catalog-remove-modal')?.remove();
}
