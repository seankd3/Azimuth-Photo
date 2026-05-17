import { visiblePersonLabel } from './people/labels.js';

export const METADATA_FILTER_KEYS = ['taken', 'fileType', 'camera', 'lens'];


export function activeMetadataFilterCount(state = {}) {
    return METADATA_FILTER_KEYS.filter((key) => Boolean(state[key])).length;
}


export function hasActiveFilters(state = {}) {
    return Object.values(state || {}).some(Boolean);
}


export function updateMetadataFilterButton({ filters = {} } = {}) {
    const btn = document.getElementById('metadata-filter-btn');
    if (!btn) return;
    const count = activeMetadataFilterCount(filters);
    btn.textContent = count ? `Metadata (${count})` : 'Metadata';
    btn.classList.toggle('active', count > 0);
}


export function toggleMetadataFilters({ loadFilterOptions } = {}) {
    const panel = document.getElementById('metadata-filter-panel');
    const btn = document.getElementById('metadata-filter-btn');
    if (!panel) return;
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) loadFilterOptions?.();
    if (btn) btn.setAttribute('aria-expanded', panel.classList.contains('hidden') ? 'false' : 'true');
}


export function loadFolderList({ filters = {}, fetchImpl = fetch } = {}) {
    return fetchImpl('/api/folders?max_depth=0').then(r => r.json()).then(data => {
        const sel = document.getElementById('filter-folder');
        if (!sel || !data.folders) return;
        const topFolders = data.folders;
        for (const f of topFolders) {
            const opt = document.createElement('option');
            opt.value = f.path;
            const indent = f.depth > 0 ? '  ' : '';
            opt.textContent = `${indent}${f.path} (${f.count})`;
            sel.appendChild(opt);
        }
        if (filters.folder) sel.value = filters.folder;
    }).catch(() => {});
}


let filterOptionsLoaded = false;
let filterOptionsPromise = null;


function populateSelect(selectId, allLabel, items, valueKey, labelFn, currentValue) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.innerHTML = `<option value="">${allLabel}</option>`;
    for (const item of items || []) {
        const value = item[valueKey] || '';
        if (!value) continue;
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = labelFn(item);
        opt.title = value;
        sel.appendChild(opt);
    }
    sel.value = currentValue || '';
}


export function loadFilterOptions({
    filters = {},
    fetchImpl = fetch,
    updateMetadataFilterButton,
} = {}) {
    if (filterOptionsLoaded) return Promise.resolve();
    if (filterOptionsPromise) return filterOptionsPromise;
    filterOptionsPromise = fetchImpl('/api/filter-options').then(r => r.json()).then(data => {
        const takenSel = document.getElementById('filter-taken');
        if (takenSel) {
            const current = filters.taken || '';
            takenSel.innerHTML = '<option value="">All Dates</option>';
            for (const item of data.years || []) {
                const opt = document.createElement('option');
                opt.value = item.year;
                opt.textContent = `${item.year} (${item.count})`;
                takenSel.appendChild(opt);
            }
            if (Number(data.undated || 0) > 0) {
                const opt = document.createElement('option');
                opt.value = 'undated';
                opt.textContent = `Undated (${data.undated})`;
                takenSel.appendChild(opt);
            }
            takenSel.value = current;
        }

        populateSelect(
            'filter-type',
            'All Types',
            data.file_types || [],
            'ext',
            item => `${String(item.ext || '').replace('.', '').toUpperCase()} (${item.count})`,
            filters.fileType,
        );
        populateSelect(
            'filter-camera',
            'All Cameras',
            data.cameras || [],
            'camera',
            item => `${item.camera} (${item.count})`,
            filters.camera,
        );
        populateSelect(
            'filter-lens',
            'All Lenses',
            data.lenses || [],
            'lens',
            item => `${item.lens} (${item.count})`,
            filters.lens,
        );
        populateSelect(
            'filter-people',
            'All People',
            data.people || [],
            'id',
            item => `${visiblePersonLabel(item, 'Face')} (${item.count})`,
            filters.people,
        );
        updateMetadataFilterButton?.();
        filterOptionsLoaded = true;
    }).catch(() => {}).finally(() => {
        filterOptionsPromise = null;
    });
    return filterOptionsPromise;
}


export function scheduleFilterOptionsLoad({
    filters = {},
    activeMetadataFilterCount: activeMetadataFilterCountImpl = activeMetadataFilterCount,
    loadFilterOptions: loadFilterOptionsImpl,
    requestIdleCallbackImpl = globalThis.window?.requestIdleCallback,
    setTimeoutImpl = setTimeout,
} = {}) {
    if (activeMetadataFilterCountImpl(filters) > 0 || filters.people) {
        loadFilterOptionsImpl?.();
        return;
    }
    const load = () => loadFilterOptionsImpl?.();
    if (typeof requestIdleCallbackImpl === 'function') {
        requestIdleCallbackImpl(load, { timeout: 8000 });
    } else {
        setTimeoutImpl(load, 5000);
    }
}


export function initStarHover() {
    document.querySelectorAll('.filter-star').forEach(star => {
        star.addEventListener('mouseenter', () => {
            const level = parseInt(star.dataset.star);
            document.querySelectorAll('.filter-star').forEach(s => {
                s.classList.toggle('hovered', parseInt(s.dataset.star) <= level);
                if (!s.classList.contains('lit')) {
                    s.textContent = parseInt(s.dataset.star) <= level ? '★' : '☆';
                }
            });
        });
        star.addEventListener('mouseleave', () => {
            document.querySelectorAll('.filter-star').forEach(s => {
                s.classList.remove('hovered');
                if (!s.classList.contains('lit')) s.textContent = '☆';
            });
        });
    });
}
