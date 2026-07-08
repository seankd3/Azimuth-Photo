import {
    byId, on, selection, setRightCollapsed, viewState,
} from './state.js';
import { getImageExif } from './api.js';

const exifCache = new Map();
let currentImageId = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function bytes(value) {
    const n = Number(value) || 0;
    if (!n) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = n;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function camera(img) {
    return [img.camera_make, img.camera_model].filter(Boolean).join(' ') || '—';
}

function fileType(img) {
    const ext = String(img.file_ext || '').replace(/^\./, '').toUpperCase();
    return ext || '—';
}

function selectedImages() {
    return [...selection].map((id) => byId.get(Number(id))).filter(Boolean);
}

function currentImage() {
    if (selection.size === 1) return byId.get(Number([...selection][0])) || null;
    if (selection.size > 1) return null;
    return viewState.images[viewState.focusIndex] || null;
}

function highlightedIds() {
    if (selection.size) return new Set([...selection].map(Number));
    const img = currentImage();
    return new Set(img ? [Number(img.id)] : []);
}

function renderHistogram() {
    const host = document.getElementById('info-histogram');
    const images = viewState.images.filter((img) => Number.isFinite(Number(img.elo)));
    if (!images.length) {
        host.innerHTML = '<div class="panel-empty">Load a scope to see its Elo shape.</div>';
        return;
    }
    const bins = Array.from({ length: 24 }, () => 0);
    const selectedBins = new Set();
    const ids = highlightedIds();
    const values = images.map((img) => Number(img.elo));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(1, max - min);
    for (const img of images) {
        const bin = Math.min(23, Math.max(0, Math.floor(((Number(img.elo) - min) / span) * 24)));
        bins[bin] += 1;
        if (ids.has(Number(img.id))) selectedBins.add(bin);
    }
    const top = Math.max(...bins, 1);
    const barW = 5;
    const gap = 3;
    const width = bins.length * barW + (bins.length - 1) * gap;
    const rects = bins.map((count, index) => {
        const h = Math.max(2, Math.round((count / top) * 64));
        const x = index * (barW + gap);
        const y = 68 - h;
        return `<rect class="${selectedBins.has(index) ? 'sel-bin' : ''}" x="${x}" y="${y}" width="${barW}" height="${h}" rx="2"></rect>`;
    }).join('');
    host.innerHTML = `<svg viewBox="0 0 ${width} 68" preserveAspectRatio="none" aria-label="Elo histogram">${rects}</svg>`
        + `<div class="histo-cap"><span>${Math.round(min)}</span><span>Elo across loaded scope</span><span>${Math.round(max)}</span></div>`;
}

function confidence(comparisons) {
    const n = Number(comparisons) || 0;
    if (n >= 10) return ['Confident', 'confident'];
    if (n >= 4) return ['Settling', 'settling'];
    return ['Provisional', 'provisional'];
}

function topPercent(img) {
    const ranked = viewState.images
        .filter((item) => Number.isFinite(Number(item.elo)))
        .slice()
        .sort((a, b) => Number(b.elo) - Number(a.elo));
    const index = ranked.findIndex((item) => Number(item.id) === Number(img.id));
    if (index < 0 || !ranked.length) return '—';
    return `top ${Math.max(1, Math.ceil(((index + 1) / ranked.length) * 100))}% of scope`;
}

function renderRanking(img) {
    const host = document.getElementById('ranking-panel');
    if (!img) {
        host.innerHTML = '<div class="panel-empty">Focus one photo to see its rank.</div>';
        return;
    }
    const [label, cls] = confidence(img.comparisons);
    host.innerHTML = '<div class="rk-hero">'
        + `<span class="rk-elo">${Math.round(Number(img.elo) || 0)}</span>`
        + `<span class="rk-pct">${esc(topPercent(img))}</span></div>`
        + '<div class="rk-rows">'
        + `<div class="rk-row"><span class="k">Comparisons</span><span class="v">${fmt(img.comparisons)}</span></div>`
        + `<div class="rk-row"><span class="k">Propagated updates</span><span class="v">${fmt(img.propagated_updates)}</span></div>`
        + '</div>'
        + `<span class="rk-tier ${cls}"><i></i>${label}</span>`;
}

function metaRow(label, value) {
    return `<div class="meta-row"><span class="k">${esc(label)}</span><span class="v" title="${esc(value || '')}">${esc(value || '—')}</span></div>`;
}

function renderExifRows(host, exif) {
    const entries = Object.entries(exif || {})
        .filter(([, value]) => value != null && value !== '')
        .slice(0, 80);
    host.innerHTML = entries.length ? entries.map(([key, value]) => metaRow(key, value)).join('') : '<div class="panel-empty">No EXIF returned.</div>';
}

async function loadExif(img, details) {
    const rows = details.querySelector('.exif-rows');
    if (!rows || details.dataset.loaded === '1') return;
    rows.innerHTML = '<div class="panel-empty">Reading EXIF…</div>';
    let data = exifCache.get(Number(img.id));
    if (!data) {
        data = await getImageExif(img.id);
        exifCache.set(Number(img.id), data || { exif: {} });
    }
    if (Number(img.id) !== currentImageId) return;
    details.dataset.loaded = '1';
    renderExifRows(rows, (data && data.exif) || {});
}

function renderMetadata(img) {
    const host = document.getElementById('metadata-panel');
    currentImageId = img ? Number(img.id) : null;
    if (!img) {
        host.innerHTML = '<div class="panel-empty">Focus one photo to inspect file details.</div>';
        return;
    }
    const size = img.width && img.height ? `${fmt(img.width)} × ${fmt(img.height)}` : '—';
    host.innerHTML = '<div class="meta-rows">'
        + metaRow('File', img.filename)
        + metaRow('Taken', img.date_taken)
        + metaRow('Camera', camera(img))
        + metaRow('Lens', img.lens)
        + metaRow('Size', `${size}${img.file_size ? ` · ${bytes(img.file_size)}` : ''}`)
        + metaRow('Type', fileType(img))
        + '</div>'
        + '<details class="more-exif"><summary>More</summary><div class="exif-rows"></div></details>';
    const details = host.querySelector('details');
    details.addEventListener('toggle', () => {
        if (details.open) loadExif(img, details);
    });
}

function renderSelection() {
    const host = document.getElementById('selection-panel');
    const images = selectedImages();
    if (selection.size <= 1) {
        host.innerHTML = '<div class="panel-empty">Select multiple photos for batch actions.</div>';
        return;
    }
    const totalBytes = images.reduce((sum, img) => sum + (Number(img.file_size) || 0), 0);
    host.innerHTML = `<div class="sel-summary">${fmt(selection.size)} selected · ${bytes(totalBytes)}</div>`
        + '<div class="sel-actions">'
        + '<button class="btn" data-proxy="sel-pick">Pick</button>'
        + '<button class="btn" data-proxy="sel-reject">Reject</button>'
        + '<button class="btn" data-proxy="sel-clear-flags">Unflag</button>'
        + '<button class="btn" data-proxy="sel-collection">Collection</button>'
        + '<button class="btn" data-proxy="sel-export">Export</button>'
        + '</div>';
    for (const btn of host.querySelectorAll('[data-proxy]')) {
        btn.addEventListener('click', () => document.getElementById(btn.dataset.proxy)?.click());
    }
}

function render() {
    const img = currentImage();
    renderHistogram();
    renderRanking(img);
    renderMetadata(img);
    renderSelection();
}

export function toggleRightPanel() {
    setRightCollapsed(!viewState.rightCollapsed);
}

export function initRightPanel() {
    const shell = document.getElementById('shell');
    shell.classList.toggle('right-collapsed', viewState.rightCollapsed);
    document.getElementById('collapse-right').addEventListener('click', toggleRightPanel);
    document.getElementById('btn-right-panel').addEventListener('click', toggleRightPanel);
    on('rightpanel', (collapsed) => shell.classList.toggle('right-collapsed', collapsed));
    on('images', render);
    on('selection', render);
    on('flags', render);
    on('focus', render);
    render();
}
