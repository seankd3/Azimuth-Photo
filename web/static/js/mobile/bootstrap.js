// Mobile app entry: four tabs (Photos / Search / Refine / Library),
// offline awareness, and the timeline pinch-zoom fallback.

import { emit, nav, setNetworkOnline } from './state.js';
import { initToast } from './toast.js';
import { initTimeline, reload, stepZoom } from './timeline.js';
import { initScrubber } from './scrubber.js';
import { initSelection } from './selection.js';
import { initViewer } from './viewer.js';
import { initRefine, showRefine } from './refine.js';
import { initSearch, showSearch } from './search.js';
import { initLibrary, showLibrary } from './library.js';
import { initHistory, onHistoryTab, replaceTab } from './history.js';
import { mountIconSprite } from '../icons.js';
import { initWriteQueue } from './write_queue.js';
import './install.js';

function secureContextBanner() {
    if (window.isSecureContext) return;
    const target = `https://${location.hostname}:8443${location.pathname}`;
    const bar = document.createElement('a');
    bar.href = target;
    bar.id = 'm-secure-banner';
    bar.textContent = 'Open the secure app address to install';
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:890;display:block;padding:10px 14px calc(10px);background:#d4a04f;color:#141517;font:600 13px system-ui;text-align:center;text-decoration:none;padding-top:max(10px, env(safe-area-inset-top));';
    document.body.appendChild(bar);
}

const TABS = ['photos', 'search', 'refine', 'library'];
const scrollMemory = new Map();
let activeTab = '';

secureContextBanner();

function setTab(tab) {
    if (!TABS.includes(tab)) return;
    if (tab === activeTab) {
        if (tab === 'photos') {
            const pane = document.getElementById('tab-photos');
            pane?.scrollTo({ top: 0, behavior: 'smooth' });
            scrollMemory.set('photos', 0);
        }
        return;
    }
    const currentPane = document.getElementById(`tab-${activeTab}`);
    if (currentPane) scrollMemory.set(activeTab, currentPane.scrollTop);
    activeTab = tab;
    document.body.dataset.tab = tab;
    for (const pane of document.querySelectorAll('.m-tab')) {
        pane.classList.toggle('active', pane.id === `tab-${tab}`);
    }
    for (const btn of document.querySelectorAll('#m-tabbar button')) {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    }
    emit('tab', tab);
    if (tab === 'search') showSearch();
    else if (tab === 'refine') showRefine();
    else if (tab === 'library') showLibrary();
    replaceTab(tab);
    requestAnimationFrame(() => {
        const pane = document.getElementById(`tab-${tab}`);
        if (pane) pane.scrollTop = scrollMemory.get(tab) || 0;
    });
}

function installTabbar() {
    document.getElementById('m-tabbar').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-tab]');
        if (btn) setTab(btn.dataset.tab);
    });
    nav.setTab = setTab;
}

function installOfflineBanner() {
    const banner = document.getElementById('m-offline');
    const secure = document.getElementById('m-secure-banner');

    const refreshBannerOffset = () => {
        const offlineH = banner && !banner.hidden ? banner.getBoundingClientRect().height : 0;
        if (secure) {
            secure.style.top = offlineH ? `${offlineH}px` : '0';
            secure.style.paddingTop = offlineH ? '10px' : 'max(10px, env(safe-area-inset-top))';
        }
        const secureH = secure ? secure.getBoundingClientRect().height : 0;
        const offset = offlineH + secureH;
        document.body.classList.toggle('m-bannered', offset > 0);
        document.documentElement.style.setProperty('--top-banner-offset', `${Math.ceil(offset)}px`);
    };

    const syncMutationControls = () => {
        const offline = !navigator.onLine;
        document.body.classList.toggle('offline', offline);
        const selectors = [
            '[data-mutating]',
            '#m-sel-actions [data-action="collection"]',
            '#mv-coll',
            '#mr-stage .mr-card',
        ];
        for (const el of document.querySelectorAll(selectors.join(','))) {
            if ('disabled' in el) el.disabled = offline;
            el.setAttribute('aria-disabled', String(offline));
        }
    };

    const sync = () => {
        const online = navigator.onLine;
        banner.hidden = online;
        setNetworkOnline(online);
        syncMutationControls();
        refreshBannerOffset();
    };
    banner.setAttribute('role', 'button');
    banner.tabIndex = 0;
    banner.addEventListener('click', () => reload());
    banner.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            reload();
        }
    });
    window.addEventListener('online', sync);
    window.addEventListener('offline', sync);
    window.addEventListener('resize', refreshBannerOffset);
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', refreshBannerOffset);
    }
    document.addEventListener('sheet-mutated', syncMutationControls);
    document.addEventListener('selection-actions-mutated', syncMutationControls);
    sync();
}

// Two-finger pinch on the timeline steps the Google-Photos zoom
// levels: 3-col day ↔ 5-col dense ↔ month list.
function installTimelinePinch() {
    const pane = document.getElementById('tab-photos');
    let pinchDist = null;
    let pinchStepped = false;
    pane.addEventListener('touchstart', (e) => {
        if (e.touches.length === 2) {
            pinchDist = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY,
            );
            pinchStepped = false;
        }
    }, { passive: true });
    pane.addEventListener('touchmove', (e) => {
        if (e.touches.length !== 2 || pinchDist == null) return;
        const d = Math.hypot(
            e.touches[0].clientX - e.touches[1].clientX,
            e.touches[0].clientY - e.touches[1].clientY,
        );
        // One discrete level per gesture: pinch in = denser/months, out = bigger.
        if (!pinchStepped && Math.abs(d - pinchDist) > 70) {
            stepZoom(d < pinchDist ? 1 : -1);
            pinchStepped = true;
        }
    }, { passive: true });
    pane.addEventListener('touchend', () => {
        pinchDist = null;
    }, { passive: true });
}

async function boot() {
    await mountIconSprite();
    initHistory('photos');
    onHistoryTab(setTab);
    initToast();
    initWriteQueue();
    installTabbar();
    installOfflineBanner();
    initSelection();
    initViewer();
    initTimeline();
    initScrubber();
    initSearch();
    initRefine();
    initLibrary();
    installTimelinePinch();
    setTab('photos');
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
} else {
    boot();
}
