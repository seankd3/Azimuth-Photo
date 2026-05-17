let leafletLoaded = false;
let leafletLoadPromise = null;


function loadExternalScript(src) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.onload = resolve;
        script.onerror = () => reject(new Error(`Failed to load ${src}`));
        document.head.appendChild(script);
    });
}


export async function loadLeafletLibraries() {
    if (leafletLoaded) return;
    if (leafletLoadPromise) return leafletLoadPromise;
    leafletLoadPromise = (async () => {
        // Load Leaflet CSS
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
        document.head.appendChild(link);

        // Load MarkerCluster CSS
        const mcLink = document.createElement('link');
        mcLink.rel = 'stylesheet';
        mcLink.href = 'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css';
        document.head.appendChild(mcLink);
        const mcDefaultLink = document.createElement('link');
        mcDefaultLink.rel = 'stylesheet';
        mcDefaultLink.href = 'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css';
        document.head.appendChild(mcDefaultLink);

        // Load Leaflet JS
        await loadExternalScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js');

        // Load MarkerCluster JS
        await loadExternalScript('https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js');

        if (!window.L || typeof window.L.markerClusterGroup !== 'function') {
            throw new Error('Map library did not initialize');
        }

        leafletLoaded = true;
    })();
    try {
        await leafletLoadPromise;
    } catch (e) {
        leafletLoadPromise = null;
        throw e;
    }
}


export function showMapError(container, message) {
    if (!container) return;
    let errorEl = container.querySelector('.map-error');
    if (!errorEl) {
        errorEl = document.createElement('div');
        errorEl.className = 'map-error';
        container.appendChild(errorEl);
    }
    errorEl.textContent = message;
}


export function clearMapError(container) {
    container?.querySelector('.map-error')?.remove();
}


export function mapInfoText(data, {
    visibleTotalLabel = (visible, total) => {
        const visibleNum = Number(visible ?? 0);
        const totalNum = Number(total ?? visibleNum);
        const visibleText = visibleNum.toLocaleString();
        if (Number.isFinite(totalNum) && totalNum !== visibleNum) {
            return `${visibleText} / ${totalNum.toLocaleString()}`;
        }
        return visibleText;
    },
} = {}) {
    const gpsVisible = Number(data.gps_count ?? data.markers?.length ?? 0);
    const gpsTotal = Number(data.gps_total_count ?? gpsVisible);
    const catalogTotal = Number(data.total_count ?? gpsTotal);
    return `${visibleTotalLabel(gpsVisible, gpsTotal)} located photos shown · ${catalogTotal.toLocaleString()} photos in pool`;
}


export function renderMapInfo(container, data, options = {}) {
    if (!container) return null;
    let infoEl = document.getElementById('map-info');
    if (!infoEl) {
        infoEl = document.createElement('div');
        infoEl.id = 'map-info';
        infoEl.className = 'map-info';
        container.appendChild(infoEl);
    }
    infoEl.textContent = mapInfoText(data, options);
    return infoEl;
}


export function buildMapPopup(markerData, {
    openImageById = null,
} = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'map-popup';

    const img = document.createElement('img');
    img.src = markerData.thumb_url || '';
    img.alt = markerData.filename || '';
    img.className = 'map-popup-thumb';
    img.addEventListener('click', () => {
        if (openImageById) openImageById(Number(markerData.id));
    });
    wrap.appendChild(img);

    const name = document.createElement('div');
    name.className = 'map-popup-name';
    name.textContent = markerData.filename || '';
    wrap.appendChild(name);
    return wrap;
}
