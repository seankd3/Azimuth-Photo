// Small guarded haptics helper; Android Chrome exposes navigator.vibrate,
// desktop browsers usually do not.

export function tick(ms = 10) {
    if (!navigator.vibrate) return;
    try {
        navigator.vibrate(ms);
    } catch {
        // Best-effort feedback only.
    }
}
