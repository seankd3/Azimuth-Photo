// Resolve the HTTPS origin used for PWA install / service-worker registration
// prompts when the user is on plain HTTP. Absolute API paths keep working on
// HTTP; only install + SW need a secure origin (see docs/FIELD_HTTPS.md).

const DEFAULT_HTTPS_PORT = '8443';

function httpsPortHint() {
    const fromDom = document.documentElement.dataset.httpsPort;
    if (fromDom != null && String(fromDom).trim() !== '') {
        return String(fromDom).trim();
    }
    return DEFAULT_HTTPS_PORT;
}

export function buildHttpsOrigin(hostname, httpsPort = httpsPortHint()) {
    const host = String(hostname || '').trim();
    if (!host) return '';
    const port = String(httpsPort == null ? DEFAULT_HTTPS_PORT : httpsPort).trim();
    if (!port || port === '443') {
        return `https://${host}`;
    }
    return `https://${host}:${port}`;
}

export function fallbackSecureAppUrl(pathname = location.pathname, search = location.search) {
    return `${buildHttpsOrigin(location.hostname)}${pathname}${search || ''}`;
}

/** Prefer server-reported HTTPS URL; fall back to hostname:8443 heuristic. */
export async function resolveSecureAppUrl(pathname = location.pathname, search = location.search) {
    const path = `${pathname}${search || ''}`;
    try {
        const response = await fetch('/api/remote-access', { cache: 'no-store' });
        if (response.ok) {
            const data = await response.json();
            const httpsBase = String(data?.tailscale?.https_url || '').replace(/\/+$/, '');
            if (httpsBase) return `${httpsBase}${path}`;
            const dns = String(data?.tailscale?.dns_name || '').trim();
            if (dns) return `${buildHttpsOrigin(dns)}${path}`;
        }
    } catch {
        // Offline or probe without Tailscale — use local heuristic.
    }
    return fallbackSecureAppUrl(pathname, search);
}
