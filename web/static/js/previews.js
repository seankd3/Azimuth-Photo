export function createPendingPreviewPoll({ active, refresh, delay = 3000 }) {
    let count = 0, timer = 0, wait = delay;

    const isActive = () => active?.() !== false;

    function stop() {
        window.clearTimeout(timer);
        timer = 0;
    }

    function schedule() {
        if (!isActive() || !count || timer) return;
        timer = window.setTimeout(async () => {
            timer = 0;
            if (!isActive() || !count) return;
            await refresh();
            schedule();
        }, wait);
    }

    function update(value) {
        const next = Math.max(0, Number(value) || 0);
        // An unchanged count means nothing is producing previews right now —
        // the hub is unreachable, or the preview engine is paused — so stretch
        // the poll instead of refetching the same answer every 3 seconds
        // forever. Movement in either direction (a preview landed, new pending
        // cells scrolled in) snaps it back to the base cadence.
        wait = next && next === count ? Math.min(wait * 2, 60_000) : delay;
        count = next;
        if (!count) stop();
        else schedule();
    }

    return { get count() { return count; }, schedule, stop, update };
}

export const pendingCount = (data) => Number(data?.pending_thumbnails ?? data?.hidden_pending_thumbnails) || 0;

export const pendingPreviewCount = (images) => (images || []).filter((image) => image?.preview_ready === false).length;
// **The address names the picture.** A browser that cached a tile under a long
// max-age never asks again, so a cache you cannot reach is not fixed by fixing
// the server -- only by changing the URL. Rotation was the first reason found
// for that and it was fixed too narrowly: the general reason is that the bytes
// on disk can change. Lightroom writing metadata rewrites the photograph, and
// the tile with it, at the same address.
//
// So the identity rides in the URL, which is what the ETag was already keyed
// on. Measured on a roll Lightroom had rewritten: the same URL returned
// 265x400 from the network and 400x265 from the browser's own cache, and every
// portrait frame sat sideways in a correctly-shaped cell.
//
// `thumb_url` used to short-circuit this for grid tiles. It is the same path
// without the version, which made it a second source of truth that was always
// the wrong one; it is deliberately not consulted.
export const previewThumbUrl = (image, size = 'sm') => {
    if (!image || image.preview_ready === false) return '';
    const turn = Number(image.rotate || 0) % 360;
    const query = new URLSearchParams();
    if (turn) query.set('r', String(turn));
    if (image.hash) query.set('v', String(image.hash).slice(0, 12));
    const suffix = query.toString();
    return `/api/thumb/${size}/${image.id}` + (suffix ? `?${suffix}` : '');
};
