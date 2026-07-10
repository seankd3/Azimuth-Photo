import {
    byId, on, selection, setRightCollapsed, setScope, viewState,
} from './state.js';
import { getCaptionStatus, getImageCaption, getImageExif, saveImageCaption } from './api.js';
import { showToast } from './toast.js';

const exifCache = new Map();
const exifPromises = new Map();
const captionCache = new Map();
let currentImageId = null;
let focusedImage = null;
let histogramCache = { signature: '', bins: [], min: 0, max: 0, empty: true };
let rankCache = { signature: '', byId: new Map(), total: 0 };
let imageVersion = 0;
let captionStatus = null;
let captionEditId = null;
let captionToken = 0;

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
    return focusedImage || viewState.images[viewState.focusIndex] || null;
}

function highlightedIds() {
    if (selection.size) return new Set([...selection].map(Number));
    const img = currentImage();
    return new Set(img ? [Number(img.id)] : []);
}

function imagesSignature() {
    return String(imageVersion);
}

function histogramData() {
    const signature = imagesSignature();
    if (histogramCache.signature === signature) return histogramCache;
    const images = viewState.images.filter((img) => Number.isFinite(Number(img.elo)));
    if (!images.length) {
        histogramCache = { signature, bins: [], min: 0, max: 0, empty: true };
        return histogramCache;
    }
    const bins = Array.from({ length: 24 }, () => ({ count: 0, ids: [] }));
    const values = images.map((img) => Number(img.elo));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(1, max - min);
    for (const img of images) {
        const bin = Math.min(23, Math.max(0, Math.floor(((Number(img.elo) - min) / span) * 24)));
        bins[bin].count += 1;
        bins[bin].ids.push(Number(img.id));
    }
    histogramCache = { signature, bins, min, max, empty: false };
    return histogramCache;
}

function renderHistogram() {
    const host = document.getElementById('info-histogram');
    const data = histogramData();
    if (data.empty) {
        host.innerHTML = '<div class="panel-empty">Load photos to see the rating spread.</div>';
        return;
    }
    const ids = highlightedIds();
    const top = Math.max(...data.bins.map((bin) => bin.count), 1);
    const barW = 5;
    const gap = 3;
    const width = data.bins.length * barW + (data.bins.length - 1) * gap;
    const rects = data.bins.map((bin, index) => {
        const h = Math.max(2, Math.round((bin.count / top) * 64));
        const x = index * (barW + gap);
        const y = 68 - h;
        const selected = bin.ids.some((id) => ids.has(id));
        return `<rect class="${selected ? 'sel-bin' : ''}" x="${x}" y="${y}" width="${barW}" height="${h}" rx="2"></rect>`;
    }).join('');
    host.innerHTML = `<svg viewBox="0 0 ${width} 68" preserveAspectRatio="none" aria-label="Rating histogram">${rects}</svg>`
        + `<div class="histo-cap"><span>${Math.round(data.min)}</span><span>Rating across this view</span><span>${Math.round(data.max)}</span></div>`;
}

function confidence(comparisons) {
    const n = Number(comparisons) || 0;
    if (n >= 10) return ['Confident', 'confident'];
    if (n >= 4) return ['Settling', 'settling'];
    return ['Provisional', 'provisional'];
}

function topPercent(img) {
    const signature = imagesSignature();
    if (rankCache.signature !== signature) {
        const ranked = viewState.images
            .filter((item) => Number.isFinite(Number(item.elo)))
            .slice()
            .sort((a, b) => Number(b.elo) - Number(a.elo));
        rankCache = {
            signature,
            total: ranked.length,
            byId: new Map(ranked.map((item, index) => [Number(item.id), index])),
        };
    }
    const index = rankCache.byId.get(Number(img.id));
    if (index == null || !rankCache.total) return '—';
    return `top ${Math.max(1, Math.ceil(((index + 1) / rankCache.total) * 100))}% of this view`;
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

function takenLabel(img) {
    const date = img.date_taken || '';
    if (!date) return '';
    return img.date_source && img.date_source !== 'exif' ? `${date} · approx` : date;
}

function exposureLine(img, exif = {}) {
    const metadata = { ...img, ...exif };
    const iso = metadata.iso ? `ISO ${String(metadata.iso).replace(/^ISO\s*/i, '')}` : '';
    return [metadata.focal_length, metadata.aperture, metadata.shutter_speed, iso]
        .filter(Boolean)
        .join(' · ');
}

async function fetchExif(img) {
    const imageId = Number(img.id);
    if (exifCache.has(imageId)) return exifCache.get(imageId);
    if (!exifPromises.has(imageId)) {
        exifPromises.set(imageId, getImageExif(imageId)
            .then((data) => {
                const result = data || { exif: {} };
                exifCache.set(imageId, result);
                return result;
            })
            .finally(() => exifPromises.delete(imageId)));
    }
    return exifPromises.get(imageId);
}

function renderExifRows(host, exif) {
    const entries = Object.entries(exif || {})
        .filter(([, value]) => value != null && value !== '')
        .slice(0, 80);
    host.innerHTML = entries.length ? entries.map(([key, value]) => metaRow(key, value)).join('') : '<div class="panel-empty">No EXIF returned.</div>';
}

function renderExifError(rows, img, details) {
    rows.innerHTML = '<div class="panel-empty">Couldn\'t load EXIF.'
        + '<button class="mini-btn" id="exif-retry" type="button">Try again</button></div>';
    rows.querySelector('#exif-retry')?.addEventListener('click', () => {
        details.dataset.loaded = '0';
        loadExif(img, details);
    });
}

async function loadExif(img, details) {
    const rows = details.querySelector('.exif-rows');
    if (!rows || details.dataset.loaded === '1') return;
    rows.innerHTML = '<div class="panel-empty">Reading EXIF…</div>';
    let data;
    try {
        data = await fetchExif(img);
    } catch {
        if (Number(img.id) !== currentImageId) return;
        renderExifError(rows, img, details);
        return;
    }
    if (Number(img.id) !== currentImageId) return;
    details.dataset.loaded = '1';
    renderExifRows(rows, (data && data.exif) || {});
}

function renderCaptionError(host, img) {
    host.innerHTML = '<div class="panel-empty">Couldn\'t load caption.'
        + '<button class="mini-btn" id="caption-retry" type="button">Try again</button></div>';
    host.querySelector('#caption-retry')?.addEventListener('click', () => renderCaption(img));
}

function renderMetadata(img) {
    const host = document.getElementById('metadata-panel');
    currentImageId = img ? Number(img.id) : null;
    if (!img) {
        host.innerHTML = '<div class="panel-empty">Focus one photo to inspect file details.</div>';
        return;
    }
    const size = img.width && img.height ? `${fmt(img.width)} × ${fmt(img.height)}` : '—';
    const cachedExif = exifCache.get(Number(img.id));
    const exposure = exposureLine(img, (cachedExif && cachedExif.exif) || {});
    host.innerHTML = '<div class="meta-rows">'
        + metaRow('File', img.filename)
        + metaRow('Taken', takenLabel(img))
        + metaRow('Camera', camera(img))
        + metaRow('Lens', img.lens)
        + metaRow('Size', `${size}${img.file_size ? ` · ${bytes(img.file_size)}` : ''}`)
        + metaRow('Type', fileType(img))
        + (exposure ? metaRow('Exposure', exposure) : '')
        + '</div>'
        + '<details class="more-exif"><summary>More</summary><div class="exif-rows"></div></details>';
    const details = host.querySelector('details');
    details.addEventListener('toggle', () => {
        if (details.open) loadExif(img, details);
    });
    if (!cachedExif) {
        fetchExif(img).then(() => {
            if (Number(img.id) === currentImageId) renderMetadata(img);
        }).catch(() => {});
    }
}

function captionProgressHint() {
    const counts = (captionStatus && captionStatus.counts) || {};
    const captioned = Number(counts.captioned || 0);
    const pending = Number(counts.pending_cached_images || 0);
    const active = Boolean(captionStatus && captionStatus.active);
    const worker = (captionStatus && captionStatus.worker) || {};
    if (active && pending > 0) return `Captions running · ${fmt(captioned)} done, ${fmt(pending)} queued`;
    if (active) return `Captions running · ${fmt(captioned)} done`;
    if (worker.last_error) return `Captions paused · ${fmt(captioned)} done`;
    return captioned > 0 ? `Captions idle · ${fmt(captioned)} done` : '';
}

function tagChips(tags) {
    return (tags || []).map((tag) => (
        `<button class="cap-tag" data-caption-tag="${esc(tag)}" title="tag:${esc(tag)}">#${esc(tag)}</button>`
    )).join('');
}

function renderCaptionView(img, caption) {
    const host = document.getElementById('caption-panel');
    if (!img) {
        host.innerHTML = '<div class="panel-empty">Focus one photo to see its caption.</div>';
        return;
    }
    if (!caption || !caption.has_caption) {
        const hint = captionProgressHint();
        host.innerHTML = '<div class="panel-empty">Not yet captioned'
            + (hint ? `<span class="cap-progress">${esc(hint)}</span>` : '')
            + '<button class="mini-btn" id="caption-edit">Write caption</button></div>';
        bindCaptionPanel(host, img, { has_caption: false, caption: '', tags: [] });
        return;
    }
    const edited = caption.user_edited ? '<span class="cap-edited">edited</span>' : '';
    host.innerHTML = '<div class="cap-text"></div>'
        + `<div class="cap-tags">${tagChips(caption.tags)}</div>`
        + `<div class="cap-actions">${edited}<button class="mini-btn" id="caption-edit">Edit</button></div>`;
    host.querySelector('.cap-text').textContent = caption.caption || '';
    bindCaptionPanel(host, img, caption);
}

function renderCaptionEditor(img, caption) {
    const host = document.getElementById('caption-panel');
    const tags = (caption && caption.tags) || [];
    host.innerHTML = '<div class="cap-editor">'
        + '<textarea id="caption-text" rows="5"></textarea>'
        + '<input id="caption-tags" class="drawer-input" autocomplete="off" placeholder="Tags, comma-separated">'
        + '<div class="cap-actions"><button class="mini-btn" id="caption-cancel">Cancel</button><button class="mini-btn" id="caption-save">Save</button></div>'
        + '</div>';
    host.querySelector('#caption-text').value = (caption && caption.caption) || '';
    host.querySelector('#caption-tags').value = tags.join(', ');
    bindCaptionPanel(host, img, caption);
    host.querySelector('#caption-text').focus({ preventScroll: true });
}

function bindCaptionPanel(host, img, caption) {
    host.querySelector('#caption-edit')?.addEventListener('click', () => {
        captionEditId = Number(img.id);
        renderCaptionEditor(img, caption || {});
    });
    host.querySelector('#caption-cancel')?.addEventListener('click', () => {
        captionEditId = null;
        renderCaptionView(img, caption || null);
    });
    host.querySelector('#caption-save')?.addEventListener('click', async () => {
        const text = host.querySelector('#caption-text')?.value || '';
        const tags = (host.querySelector('#caption-tags')?.value || '')
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean);
        host.querySelector('#caption-save').disabled = true;
        const saved = await saveImageCaption(img.id, { caption: text, tags });
        if (!saved || !saved.caption) {
            host.querySelector('#caption-save').disabled = false;
            showToast("Couldn't save caption");
            return;
        }
        captionEditId = null;
        captionCache.set(Number(img.id), saved.caption);
        const card = byId.get(Number(img.id));
        if (card) {
            card.has_caption = true;
            card.caption_tags = saved.caption.tags || [];
        }
        renderCaptionView(img, saved.caption);
    });
    for (const chip of host.querySelectorAll('[data-caption-tag]')) {
        chip.addEventListener('click', () => {
            const tag = chip.dataset.captionTag || '';
            setScope({
            tag,
            collectionId: '',
            collectionName: '',
            collectionSmart: false,
            similarIds: [],
            similarSourceId: '',
            similarLimit: 100,
            similarLabel: '',
            }, { merge: true });
            showToast(`Showing tag · ${tag}`);
        });
    }
}

async function renderCaption(img) {
    const host = document.getElementById('caption-panel');
    if (!host) return;
    if (!img) {
        renderCaptionView(null, null);
        return;
    }
    const imageId = Number(img.id);
    if (captionEditId === imageId) return;
    const token = ++captionToken;
    try {
        captionStatus = await getCaptionStatus();
    } catch {
        captionStatus = null;
    }
    let caption = captionCache.get(imageId);
    if (!caption) {
        host.innerHTML = '<div class="panel-empty">Loading caption…</div>';
        try {
            caption = await getImageCaption(imageId);
        } catch {
            if (token !== captionToken || Number(currentImageId) !== imageId) return;
            renderCaptionError(host, img);
            return;
        }
        if (token !== captionToken || Number(currentImageId) !== imageId) return;
        captionCache.set(imageId, caption || { has_caption: false, tags: [] });
    }
    renderCaptionView(img, caption);
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
        + '<button class="btn" data-proxy="sel-clear-flags">Clear flags</button>'
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
    renderCaption(img);
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
    on('images', () => {
        imageVersion += 1;
        render();
    });
    on('selection', render);
    on('flags', render);
    on('focus', ({ image } = {}) => {
        focusedImage = image || null;
        render();
    });
    render();
}
