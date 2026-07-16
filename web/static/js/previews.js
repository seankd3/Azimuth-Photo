export function createPendingPreviewPoll({ active, pending, refresh, delay = 3000 }) {
    let count = 0, timer = 0;
    const isActive = () => active?.() !== false;
    const hasPending = () => (pending ? pending() : count) > 0;

    function stop() {
        window.clearTimeout(timer);
        timer = 0;
    }

    function schedule() {
        if (!isActive() || !hasPending() || timer) return;
        timer = window.setTimeout(async () => {
            timer = 0;
            if (!isActive() || !hasPending()) return;
            await refresh();
            schedule();
        }, delay);
    }

    function update(value) {
        count = Math.max(0, Number(value) || 0);
        if (!count) stop();
        else schedule();
    }

    return { get count() { return count; }, schedule, stop, update };
}

export const pendingCount = (data) => Number(data?.pending_thumbnails ?? data?.hidden_pending_thumbnails) || 0;

export const pendingPreviewCount = (images) => (images || []).filter((image) => image?.preview_ready === false).length;
