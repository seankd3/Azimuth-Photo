import { getCollection, getMapMarkers, thumbUrl } from './api.js';
import { emit, on, scope, scopeParams, setImages, setRankingsMeta } from './state.js';

let mounted = false;
let initialized = false;
let generation = 0;
let markers = [];

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function latOf(marker) {
    return Number(marker.lat ?? marker.latitude);
}

function lngOf(marker) {
    return Number(marker.lng ?? marker.lon ?? marker.longitude);
}

function project(marker) {
    const lat = Math.max(-85, Math.min(85, latOf(marker)));
    const lng = ((lngOf(marker) + 180) % 360 + 360) % 360 - 180;
    return {
        x: ((lng + 180) / 360) * 1000,
        y: ((85 - lat) / 170) * 500,
    };
}

function cluster(rawMarkers) {
    const buckets = new Map();
    for (const marker of rawMarkers) {
        const lat = latOf(marker);
        const lng = lngOf(marker);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
        const point = project(marker);
        const key = `${Math.round(point.x / 34)}:${Math.round(point.y / 34)}`;
        const bucket = buckets.get(key) || { markers: [], x: 0, y: 0 };
        bucket.markers.push(marker);
        bucket.x += point.x;
        bucket.y += point.y;
        buckets.set(key, bucket);
    }
    return [...buckets.values()].map((bucket) => ({
        markers: bucket.markers,
        x: bucket.x / bucket.markers.length,
        y: bucket.y / bucket.markers.length,
    }));
}

function graticule() {
    const lines = [];
    for (let lon = -120; lon <= 120; lon += 60) {
        const x = ((lon + 180) / 360) * 1000;
        lines.push(`<line x1="${x}" y1="0" x2="${x}" y2="500" class="grat ${lon === 0 ? 'major' : ''}"/>`);
    }
    for (let lat = -60; lat <= 60; lat += 30) {
        const y = ((85 - lat) / 170) * 500;
        lines.push(`<line x1="0" y1="${y}" x2="1000" y2="${y}" class="grat ${lat === 0 ? 'major' : ''}"/>`);
    }
    return lines.join('');
}

function renderMap(data = {}) {
    const stage = document.getElementById('map-stage');
    const clusters = cluster(markers);
    if (!markers.length) {
        stage.innerHTML = '<div id="map-empty"><div class="me-glyph">◈</div><h3>No GPS data in this scope</h3><p>Photos with coordinates will appear here as map points.</p></div>';
        return;
    }
    stage.innerHTML = '<svg id="map-svg" viewBox="0 0 1000 500" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Photo map">'
        + `<rect x="0" y="0" width="1000" height="500" class="map-ocean"/>${graticule()}`
        + '</svg><div id="map-pins">'
        + clusters.map((item, index) => {
            const count = item.markers.length;
            return `<button class="map-pin" data-cluster="${index}" style="left:${(item.x / 10).toFixed(2)}%;top:${(item.y / 5).toFixed(2)}%" aria-label="${fmt(count)} located photos">`
                + `<span>${count > 1 ? fmt(count) : ''}</span></button>`;
        }).join('')
        + '</div>'
        + `<div id="map-info">${fmt(data.gps_count ?? markers.length)} located photos shown · ${fmt(data.total_count ?? data.gps_total_count ?? markers.length)} photos in pool</div>`
        + '<div id="map-popover"></div>';
}

async function loadCollectionMarkers() {
    let offset = 0;
    let total = 0;
    let all = [];
    do {
        const data = await getCollection(scope.collectionId, { limit: 1000, offset });
        const collection = data && data.collection;
        if (!collection) break;
        total = Number(collection.image_count) || 0;
        const incoming = collection.images || [];
        all = all.concat(incoming);
        offset += incoming.length;
        if (!incoming.length) break;
    } while (offset < total);
    return {
        markers: all.filter((img) => Number.isFinite(Number(img.latitude)) && Number.isFinite(Number(img.longitude))).map((img) => ({
            ...img,
            lat: img.latitude,
            lng: img.longitude,
            thumb_url: img.thumb_url || thumbUrl('sm', img.id),
        })),
        total_count: total,
        gps_count: all.filter((img) => Number.isFinite(Number(img.latitude)) && Number.isFinite(Number(img.longitude))).length,
    };
}

async function load() {
    if (!mounted) return;
    const seq = ++generation;
    const stage = document.getElementById('map-stage');
    stage.innerHTML = '<div class="map-loading skel"></div>';
    const data = scope.collectionId
        ? await loadCollectionMarkers()
        : await getMapMarkers(scopeParams());
    if (seq !== generation) return;
    markers = (data && data.markers) || [];
    setImages(markers);
    setRankingsMeta({ visibleImages: Number((data && (data.total_count || data.gps_total_count || data.gps_count)) || markers.length), sortQuality: null });
    renderMap(data || {});
}

function openCluster(index, pin) {
    const item = cluster(markers)[index];
    if (!item) return;
    const first = item.markers[0];
    if (item.markers.length === 1) {
        emit('loupe:open', { id: Number(first.id), index: markers.findIndex((marker) => Number(marker.id) === Number(first.id)) });
        return;
    }
    const pop = document.getElementById('map-popover');
    const rect = pin.getBoundingClientRect();
    pop.innerHTML = `<div class="mp-head">${fmt(item.markers.length)} photos here</div>`
        + item.markers.slice(0, 9).map((marker) => (
            `<button data-id="${marker.id}"><img src="${esc(marker.thumb_url || thumbUrl('sm', marker.id))}" alt=""><span>${esc(marker.filename || `Image ${marker.id}`)}</span></button>`
        )).join('');
    pop.style.left = `${Math.min(window.innerWidth - 220, rect.left)}px`;
    pop.style.top = `${Math.min(window.innerHeight - 260, rect.bottom + 8)}px`;
    pop.classList.add('on');
}

export function initMap() {
    if (initialized) return;
    initialized = true;
    document.getElementById('map-stage').addEventListener('click', (event) => {
        const popItem = event.target.closest('#map-popover button[data-id]');
        if (popItem) {
            emit('loupe:open', { id: Number(popItem.dataset.id), index: markers.findIndex((marker) => Number(marker.id) === Number(popItem.dataset.id)) });
            document.getElementById('map-popover').classList.remove('on');
            return;
        }
        const pin = event.target.closest('.map-pin[data-cluster]');
        if (pin) openCluster(Number(pin.dataset.cluster), pin);
        else document.getElementById('map-popover')?.classList.remove('on');
    });
    on('scope', load);
}

export function mountMap() {
    mounted = true;
    document.getElementById('view-map').classList.add('active');
    load();
}

export function unmountMap() {
    mounted = false;
    generation += 1;
    markers = [];
    document.getElementById('view-map').classList.remove('active');
    document.getElementById('map-popover')?.classList.remove('on');
}
