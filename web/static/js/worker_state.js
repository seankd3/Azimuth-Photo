export const INACTIVE_WORKER_STATES = new Set([
    'idle', 'ready', 'paused', 'complete', 'caught_up',
    'error', 'disabled', 'unavailable', 'stale',
]);

export const normalizeWorkerState = (state) => String(state || '').toLowerCase();
