import { appendFilterParams } from '../query_state.js';
import { appendSearchParams } from '../search/query.js';
import {
    buildMapPopup,
    clearMapError,
    loadLeafletLibraries,
    renderMapInfo,
    showMapError,
} from './map.js';


export function createLibraryMapController({
    documentImpl = document,
    windowImpl = window,
    fetchImpl = fetch,
    consoleImpl = console,
    clearWarmups,
    clearBatchSelection,
    currentFilterState,
    currentQueryState,
    libraryScrollRoot,
    openImageById,
    syncDateScrubberVisibility,
} = {}) {
    let mapInstance = null;
    let mapMarkerLayer = null;
    let mapCenter = [20, 0];
    let mapZoom = 2;
    let libraryView = 'grid';
    let libraryScrollBeforeMap = 0;
    let mapRequestGeneration = 0;

    function getLibraryView() {
        return libraryView;
    }

    function setLibraryView(mode) {
        clearWarmups?.();
        libraryView = mode;
        const grid = documentImpl.getElementById('rankings-grid');
        const mapEl = documentImpl.getElementById('map-container');
        const gridBtn = documentImpl.getElementById('view-grid-btn');
        const mapBtn = documentImpl.getElementById('view-map-btn');
        const scrollRoot = libraryScrollRoot?.();

        if (mode === 'map') {
            clearBatchSelection?.();
            libraryScrollBeforeMap = scrollRoot?.scrollTop || 0;
            scrollRoot?.classList.add('map-active');
            scrollRoot?.scrollTo({ top: 0, behavior: 'auto' });
            grid?.classList.add('hidden');
            mapEl?.classList.remove('hidden');
            gridBtn?.classList.remove('active');
            mapBtn?.classList.add('active');
            syncDateScrubberVisibility?.();
            loadMap();
        } else {
            if (mapInstance) {
                mapCenter = [mapInstance.getCenter().lat, mapInstance.getCenter().lng];
                mapZoom = mapInstance.getZoom();
            }
            scrollRoot?.classList.remove('map-active');
            grid?.classList.remove('hidden');
            mapEl?.classList.add('hidden');
            gridBtn?.classList.add('active');
            mapBtn?.classList.remove('active');
            syncDateScrubberVisibility?.();
            windowImpl.requestAnimationFrame?.(() => {
                if (scrollRoot) scrollRoot.scrollTop = libraryScrollBeforeMap;
            });
        }
    }

    async function loadMap() {
        const container = documentImpl.getElementById('map-container');
        const requestGeneration = ++mapRequestGeneration;
        try {
            await loadLeafletLibraries();
        } catch (e) {
            consoleImpl.error('Map library load error:', e);
            showMapError(container, 'Map unavailable. Check your connection and try again.');
            return;
        }
        if (requestGeneration !== mapRequestGeneration) return;
        clearMapError(container);

        const leaflet = windowImpl.L;
        if (!mapInstance) {
            mapInstance = leaflet.map(container).setView(mapCenter, mapZoom);
            leaflet.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap contributors',
                maxZoom: 19,
            }).addTo(mapInstance);
        } else {
            // Resize map to fit container
            windowImpl.setTimeout?.(() => mapInstance.invalidateSize(), 100);
        }

        const mapParams = appendFilterParams(new URLSearchParams(), currentFilterState?.());
        appendSearchParams(mapParams, currentQueryState?.());
        const query = mapParams.toString();
        const url = `/api/map/markers${query ? `?${query}` : ''}`;
        try {
            const data = await fetchImpl(url).then(r => r.json());
            if (requestGeneration !== mapRequestGeneration) return;
            if (mapMarkerLayer) {
                mapInstance.removeLayer(mapMarkerLayer);
            }
            mapMarkerLayer = leaflet.markerClusterGroup();

            for (const m of data.markers) {
                const marker = leaflet.marker([m.lat, m.lng]);
                marker.bindPopup(buildMapPopup(m, { openImageById }), { maxWidth: 200 });
                mapMarkerLayer.addLayer(marker);
            }
            mapInstance.addLayer(mapMarkerLayer);

            renderMapInfo(container, data);

            if (data.markers.length > 0 && mapZoom === 2) {
                mapInstance.fitBounds(mapMarkerLayer.getBounds(), { padding: [30, 30] });
            }
        } catch (e) {
            consoleImpl.error('Map load error:', e);
            showMapError(container, 'Map markers could not be loaded.');
        }
    }

    return {
        getLibraryView,
        loadMap,
        setLibraryView,
    };
}
