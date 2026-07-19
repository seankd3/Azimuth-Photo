import assert from 'node:assert/strict';

const api = await import(`./static/js/api.js?test=${Date.now()}`);

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

{
    let attempts = 0;
    globalThis.fetch = async () => {
        attempts += 1;
        if (attempts < 3) {
            const error = new TypeError('Failed to fetch');
            error.name = 'TypeError';
            throw error;
        }
        return {
            ok: true,
            status: 200,
            async text() { return JSON.stringify({ ok: true, attempts }); },
        };
    };
    const originalRandom = Math.random;
    Math.random = () => 0;
    try {
        const started = Date.now();
        const data = await api.fetchJson('/api/probe');
        const elapsed = Date.now() - started;
        assert.equal(data.ok, true);
        assert.equal(attempts, 3);
        // New backoff: 0ms jitter (random=0) + 3s second delay.
        assert.ok(elapsed >= 2500, `expected ~0s+3s backoff, got ${elapsed}ms`);
    } finally {
        Math.random = originalRandom;
    }
}

{
    let attempts = 0;
    globalThis.fetch = async () => {
        attempts += 1;
        return { ok: false, status: 500, async text() { return ''; } };
    };
    await assert.rejects(
        () => api.fetchJson('/api/probe'),
        (error) => error instanceof api.FetchJsonError && error.status === 500,
    );
    assert.equal(attempts, 1, 'non-transient HTTP errors must not retry');
}

{
    let attempts = 0;
    globalThis.fetch = async () => {
        attempts += 1;
        const error = new TypeError('Failed to fetch');
        throw error;
    };
    await assert.rejects(
        () => api.fetchJson('/api/probe', { fetchOptions: { method: 'POST', body: '{}' } }),
        (error) => error instanceof api.FetchJsonError,
    );
    assert.equal(attempts, 1, 'POST must not retry');
}

{
    let attempts = 0;
    globalThis.fetch = async () => {
        attempts += 1;
        if (attempts === 1) {
            return { ok: false, status: 503, async text() { return ''; } };
        }
        return {
            ok: true,
            status: 200,
            async text() { return JSON.stringify({ recovered: true }); },
        };
    };
    const originalRandom = Math.random;
    Math.random = () => 0;
    try {
        const data = await api.fetchJson('/api/probe');
        assert.equal(data.recovered, true);
        assert.equal(attempts, 2);
    } finally {
        Math.random = originalRandom;
    }
}

{
    let attempts = 0;
    const controller = new AbortController();
    controller.abort();
    globalThis.fetch = async () => {
        attempts += 1;
        const error = new Error('The operation was aborted');
        error.name = 'AbortError';
        throw error;
    };
    await assert.rejects(
        () => api.fetchJson('/api/probe', { fetchOptions: { signal: controller.signal } }),
        (error) => error instanceof api.FetchJsonError,
    );
    assert.equal(attempts, 1, 'caller-aborted signal must not retry');
}

// Keep the suite snappy if a future change regresses delay math.
await delay(0);
console.log('fetchJson retry: GET recovers after transient failures; POST/abort do not retry');
