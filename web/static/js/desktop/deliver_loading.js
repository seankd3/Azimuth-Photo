const idleState = () => ({
    status: 'idle',
    value: null,
    error: null,
    request: 0,
});

export function createDeliverLoads(loaders, onChange = () => {}) {
    const states = Object.fromEntries(Object.keys(loaders).map((key) => [key, idleState()]));
    let disposed = false;

    const snapshot = (key) => ({ ...states[key] });
    const current = (key, request) => !disposed && states[key]?.request === request;
    const notify = (key) => onChange(key, snapshot(key));

    async function load(key) {
        if (!loaders[key]) throw new Error(`Unknown Deliver load: ${key}`);
        const request = states[key].request + 1;
        states[key] = {
            ...states[key],
            status: 'loading',
            error: null,
            request,
        };
        notify(key);
        try {
            const value = await loaders[key]();
            if (!current(key, request)) return snapshot(key);
            states[key] = { status: 'ready', value, error: null, request };
        } catch (error) {
            if (!current(key, request)) return snapshot(key);
            states[key] = {
                status: 'error',
                value: states[key].value,
                error,
                request,
            };
        }
        notify(key);
        return snapshot(key);
    }

    return {
        load,
        loadAll() {
            return Promise.all(Object.keys(loaders).map(load));
        },
        state(key) {
            return snapshot(key);
        },
        settled() {
            return Object.values(states).every(({ status }) => status === 'ready' || status === 'error');
        },
        dispose() {
            disposed = true;
            for (const state of Object.values(states)) state.request += 1;
        },
    };
}
