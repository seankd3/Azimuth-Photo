export class FetchJsonError extends Error {
    constructor(message, { url, status = 0, cause = null } = {}) {
        super(message);
        this.name = 'FetchJsonError';
        this.url = url;
        this.status = status;
        this.cause = cause;
    }
}

export function fetchOptionsWithTimeout(fetchOptions, timeoutMs) {
    if (!timeoutMs || typeof AbortSignal === 'undefined' || !AbortSignal.timeout) {
        return fetchOptions;
    }
    const timeout = AbortSignal.timeout(timeoutMs);
    if (!fetchOptions?.signal) return { ...(fetchOptions || {}), signal: timeout };
    if (AbortSignal.any) return { ...(fetchOptions || {}), signal: AbortSignal.any([fetchOptions.signal, timeout]) };
    return fetchOptions;
}

function requestMethod(fetchOptions) {
    return String(fetchOptions?.method || 'GET').toUpperCase();
}

function isIdempotentGet(fetchOptions) {
    return requestMethod(fetchOptions) === 'GET';
}

function isTransientFailure(error, status, fetchOptions) {
    // Caller-aborted requests are intentional cancels, not transient blips.
    if (fetchOptions?.signal?.aborted) return false;
    if (status === 502 || status === 503 || status === 504) return true;
    if (status) return false;
    const cause = error?.cause || error;
    const name = cause?.name || error?.name || '';
    return name === 'AbortError'
        || name === 'TimeoutError'
        || name === 'TypeError'
        || /network|failed to fetch|load failed/i.test(String(cause?.message || error?.message || ''));
}

function retryDelayMs(attempt) {
    // attempt 1 → ~0–1s jitter before second try; attempt 2 → 3–6s before third.
    if (attempt <= 1) return Math.floor(Math.random() * 1000);
    return 3000 + Math.floor(Math.random() * 3000);
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchOnce(url, fetchOptions, timeoutMs) {
    let response = null;
    try {
        response = await fetch(url, fetchOptionsWithTimeout(fetchOptions, timeoutMs));
    } catch (error) {
        throw new FetchJsonError(`Request failed: ${url}`, { url, cause: error });
    }
    if (!response.ok) {
        throw new FetchJsonError(`Request failed with ${response.status}: ${url}`, {
            url,
            status: response.status,
        });
    }
    return response;
}

export async function fetchJson(url, {
    defaultValue = null,
    fetchOptions = undefined,
    timeoutMs = 15000,
} = {}) {
    const maxAttempts = isIdempotentGet(fetchOptions) ? 3 : 1;
    let lastError = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
            const response = await fetchOnce(url, fetchOptions, timeoutMs);
            if (response.status === 204) return defaultValue;
            const text = await response.text();
            if (!text.trim()) return defaultValue;
            try {
                return JSON.parse(text);
            } catch (error) {
                throw new FetchJsonError(`Response was not JSON: ${url}`, {
                    url,
                    status: response.status,
                    cause: error,
                });
            }
        } catch (error) {
            lastError = error;
            const status = error?.status || 0;
            const canRetry = attempt < maxAttempts && isTransientFailure(error, status, fetchOptions);
            if (!canRetry) throw error;
            await sleep(retryDelayMs(attempt));
        }
    }

    throw lastError;
}
