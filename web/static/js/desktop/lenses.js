import { eventGap, initEvents, mountEvents, setEventGap, unmountEvents } from './events.js';
import { initDateScrubber } from './date_scrubber.js';
import { initGrid, mountGrid, unmountGrid } from './grid.js';
import { initMap, mountMap, unmountMap } from './map.js';
import { initPeople, mountPeople, unmountPeople } from './people.js';
import { on, setActiveLens, viewState } from './state.js';

const LENSES = {
    grid: { mount: mountGrid, unmount: unmountGrid },
    events: { mount: mountEvents, unmount: unmountEvents },
    people: { mount: mountPeople, unmount: unmountPeople },
    map: { mount: mountMap, unmount: unmountMap },
};

let current = null;

function syncChrome(lens) {
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        const active = button.dataset.view === lens;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
    document.getElementById('shell').classList.toggle('right-hidden', lens === 'events' || lens === 'map');
    document.getElementById('event-gap-wrap').hidden = lens !== 'events';
    document.getElementById('sort-select').disabled = lens === 'people' || lens === 'map';
    document.getElementById('thumb-size').disabled = lens === 'people' || lens === 'map';
}

function activate(lens) {
    const next = LENSES[lens] ? lens : 'grid';
    if (current === next) {
        syncChrome(next);
        return;
    }
    if (current) LENSES[current].unmount();
    current = next;
    syncChrome(next);
    LENSES[next].mount();
}

export function switchLens(lens) {
    setActiveLens(lens);
}

export function activeLens() {
    return current || viewState.activeLens || 'grid';
}

export function initLenses() {
    initGrid();
    initEvents();
    initPeople();
    initMap();
    initDateScrubber();
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        button.addEventListener('click', () => switchLens(button.dataset.view));
    }
    const gap = document.getElementById('event-gap');
    gap.value = String(eventGap());
    gap.addEventListener('change', () => setEventGap(gap.value));
    on('lens', activate);
    activate(viewState.activeLens || 'grid');
}
