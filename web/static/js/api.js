export async function fetchJson(url, {
    defaultValue = null,
    fetchOptions = undefined,
} = {}) {
    try {
        const response = await fetch(url, fetchOptions);
        if (!response.ok) return defaultValue;
        return await response.json();
    } catch {
        return defaultValue;
    }
}
