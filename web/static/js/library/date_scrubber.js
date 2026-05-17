import { dateScrubberLabelsHtml } from './display.js';
import { libraryScrollRoot } from './shell.js';


let dateScrubberScrollRoot = null;
let dateScrubberScrollHandler = null;
let dateScrubberScrollRaf = null;


export function isDateSortValue(sort) {
    return sort === 'date_taken' || sort === 'date_taken_asc';
}


export function findDateGroupHeader(group, documentImpl = document) {
    return Array.from(documentImpl.querySelectorAll('.date-group-header'))
        .find(header => header.dataset.dateGroup === group);
}


export function dateGroupOffset(dateGroups, group) {
    let offset = 0;
    for (const item of dateGroups || []) {
        const itemGroup = item.date || '';
        if (itemGroup === group) return offset;
        offset += Number(item.count || 0);
    }
    return null;
}


export async function jumpToDateGroup(group, {
    isActive = () => false,
    findHeader = findDateGroupHeader,
    setActiveGroup = setActiveDateScrubberGroup,
    scrollToElement = () => {},
    getOffset = () => null,
    resetForOffset = () => 0,
    isCurrentJump = () => true,
    loadRankings = async () => {},
    scrollRoot = libraryScrollRoot,
    documentImpl = document,
} = {}) {
    if (!isActive()) return false;

    const existingHeader = findHeader(group);
    if (existingHeader) {
        setActiveGroup(group);
        scrollToElement(existingHeader);
        return true;
    }

    const offset = getOffset(group);
    if (offset === null) return false;

    const gen = resetForOffset(offset);

    const grid = documentImpl.getElementById('rankings-grid');
    if (grid) grid.innerHTML = '';
    scrollRoot()?.scrollTo({ top: 0, behavior: 'auto' });

    await loadRankings(true);
    if (!isCurrentJump(gen)) return false;

    const loadedHeader = findHeader(group);
    if (loadedHeader) {
        scrollToElement(loadedHeader, 'auto');
        setActiveGroup(group);
        return true;
    }
    return false;
}


export function setActiveDateScrubberGroup(group, documentImpl = document) {
    const year = group ? group.substring(0, 4) : '';
    documentImpl.querySelectorAll('.scrubber-label, .scrubber-year').forEach(el => {
        const elGroup = el.dataset.dateGroup || '';
        const isYear = el.classList.contains('scrubber-year');
        const active = isYear && year
            ? elGroup.substring(0, 4) === year
            : elGroup === group;
        el.classList.toggle('active', active);
    });
}


export function updateActiveDateScrubberGroup(scrollRoot = libraryScrollRoot(), documentImpl = document) {
    const headers = Array.from(documentImpl.querySelectorAll('.date-group-header'));
    if (!scrollRoot || !headers.length) return;

    const currentTop = scrollRoot.scrollTop + 8;
    let activeHeader = headers[0];
    for (const header of headers) {
        if (header.offsetTop <= currentTop) activeHeader = header;
        else break;
    }
    setActiveDateScrubberGroup(activeHeader?.dataset.dateGroup || '', documentImpl);
}


export function teardownDateScrubberScrollTracking({ cancelAnimationFrameImpl = cancelAnimationFrame } = {}) {
    if (dateScrubberScrollRoot && dateScrubberScrollHandler) {
        dateScrubberScrollRoot.removeEventListener('scroll', dateScrubberScrollHandler);
    }
    if (dateScrubberScrollRaf) cancelAnimationFrameImpl(dateScrubberScrollRaf);
    dateScrubberScrollRoot = null;
    dateScrubberScrollHandler = null;
    dateScrubberScrollRaf = null;
}


export function setupDateScrubberScrollTracking({
    scrollRoot = libraryScrollRoot(),
    documentImpl = document,
    requestAnimationFrameImpl = requestAnimationFrame,
    cancelAnimationFrameImpl = cancelAnimationFrame,
} = {}) {
    teardownDateScrubberScrollTracking({ cancelAnimationFrameImpl });
    const headers = documentImpl.querySelectorAll('.date-group-header');
    if (!scrollRoot || !headers.length) return false;

    dateScrubberScrollRoot = scrollRoot;
    dateScrubberScrollHandler = () => {
        if (dateScrubberScrollRaf) return;
        dateScrubberScrollRaf = requestAnimationFrameImpl(() => {
            dateScrubberScrollRaf = null;
            updateActiveDateScrubberGroup(scrollRoot, documentImpl);
        });
    };
    scrollRoot.addEventListener('scroll', dateScrubberScrollHandler, { passive: true });
    updateActiveDateScrubberGroup(scrollRoot, documentImpl);
    return true;
}


export function renderDateScrubber(dateGroups, {
    documentImpl = document,
    onJump = null,
    setupScrollObserver = null,
    syncVisibility = null,
    teardownScrollTracking = null,
} = {}) {
    let scrubber = documentImpl.getElementById('date-scrubber');
    if (!dateGroups.length) {
        if (scrubber) scrubber.remove();
        teardownScrollTracking?.();
        syncVisibility?.();
        return null;
    }
    if (!scrubber) {
        scrubber = documentImpl.createElement('div');
        scrubber.id = 'date-scrubber';
        scrubber.className = 'date-scrubber';
    }
    const host = documentImpl.querySelector('body.library-shell main') || documentImpl.body;
    if (scrubber.parentElement !== host) host.appendChild(scrubber);
    syncVisibility?.();

    scrubber.innerHTML = dateScrubberLabelsHtml(dateGroups);
    scrubber.querySelectorAll('[data-date-group]').forEach(label => {
        label.onclick = () => {
            onJump?.(label.dataset.dateGroup || '');
        };
    });

    setupScrollObserver?.();
    return scrubber;
}
