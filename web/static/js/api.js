export class FetchJsonError extends Error {
    constructor(message, { url, status = 0, cause = null } = {}) {
        super(message);
        this.name = 'FetchJsonError';
        this.url = url;
        this.status = status;
        this.cause = cause;
    }
}

const NETWORK_MESSAGE = "Can't reach Azimuth Photo. Retrying…";
const SERVER_MESSAGE = "Something went wrong on the server. Your photos are safe — try again.";
const WRITE_MESSAGE = "Couldn't save that change. It'll retry automatically.";

function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function reportApiSuccess(url) {
    emit('azimuth-api-success', { url });
}

export function reportApiFailure({ url, status = 0, body = null, method = 'GET', cause = null } = {}) {
    const network = status === 0;
    const write = !network && !['GET', 'HEAD'].includes(String(method).toUpperCase());
    emit('azimuth-api-failure', {
        url,
        status,
        cause,
        // APIs may offer a precise, safe explanation. The bus uses it for
        // recoverable non-server failures while keeping standard failures calm.
        error: typeof body?.error === 'string' ? body.error : '',
        message: network ? NETWORK_MESSAGE : (write ? WRITE_MESSAGE : SERVER_MESSAGE),
        kind: network ? 'network' : (write ? 'write' : 'server'),
    });
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
        reportApiFailure({ url, cause: error, method: fetchOptions?.method });
        throw new FetchJsonError(`Request failed: ${url}`, { url, cause: error });
    }
    if (!response.ok) {
        const body = await response.clone().json().catch(() => null);
        reportApiFailure({ url, status: response.status, body, method: fetchOptions?.method });
        throw new FetchJsonError(`Request failed with ${response.status}: ${url}`, {
            url,
            status: response.status,
        });
    }
    if (response.status === 204) {
        reportApiSuccess(url);
        return defaultValue;
    }
    const text = await response.text();
    if (!text.trim()) {
        reportApiSuccess(url);
        return defaultValue;
    }
    try {
        const data = JSON.parse(text);
        reportApiSuccess(url);
        return data;
    } catch (error) {
        reportApiFailure({ url, status: response.status, cause: error, method: fetchOptions?.method });
        throw new FetchJsonError(`Response was not JSON: ${url}`, {
            url,
            status: response.status,
            cause: error,
        });
    }
}
