export class FetchJsonError extends Error {
    constructor(message, { url, status = 0, cause = null } = {}) {
        super(message);
        this.name = 'FetchJsonError';
        this.url = url;
        this.status = status;
        this.cause = cause;
    }
}

function optionsWithTimeout(fetchOptions, timeoutMs) {
    if (!timeoutMs || fetchOptions?.signal || typeof AbortSignal === 'undefined' || !AbortSignal.timeout) {
        return fetchOptions;
    }
    return { ...(fetchOptions || {}), signal: AbortSignal.timeout(timeoutMs) };
}

export async function fetchJson(url, {
    defaultValue = null,
    fetchOptions = undefined,
    timeoutMs = 15000,
} = {}) {
    let response = null;
    try {
        response = await fetch(url, optionsWithTimeout(fetchOptions, timeoutMs));
    } catch (error) {
        throw new FetchJsonError(`Request failed: ${url}`, { url, cause: error });
    }
    if (!response.ok) {
        throw new FetchJsonError(`Request failed with ${response.status}: ${url}`, {
            url,
            status: response.status,
        });
    }
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
}
