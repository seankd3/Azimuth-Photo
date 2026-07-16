/** Token-gated polling for foreground jobs. */

export function pollJob({ fetchStatus, isDone, onTick, intervalMs = 2500 }) {
    let cancelled = false;
    let timer = 0;
    let token = 0;

    const cancel = () => {
        cancelled = true;
        token += 1;
        window.clearTimeout(timer);
    };
    const tick = async () => {
        const tickToken = token;
        let status;
        try {
            status = await fetchStatus();
        } catch {
            return;
        }
        if (cancelled || tickToken !== token) return;
        onTick(status);
        if (cancelled || tickToken !== token || isDone(status)) return;
        timer = window.setTimeout(tick, intervalMs);
    };
    tick();
    return { cancel };
}
