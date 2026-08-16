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
// The rotation rides in the URL, not just in the ETag. A turned photograph is
// a different picture, so it deserves a different address -- and a browser
// that cached the unturned one under a long max-age will never revalidate it,
// which is the same shape as the poisoned-service-worker incident: a cache you
// cannot reach is not fixed by fixing the server, only by changing the URL.
export const previewThumbUrl = (image, size = 'sm') => {
    if (!image || image.preview_ready === false) return '';
    if (size === 'sm' && image.thumb_url) return image.thumb_url;
    const turn = Number(image.rotate || 0) % 360;
    return `/api/thumb/${size}/${image.id}` + (turn ? `?r=${turn}` : '');
};
