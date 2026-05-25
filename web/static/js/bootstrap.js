const version = new URL(import.meta.url).searchParams.get('v') || '';
const suffix = version ? `?v=${encodeURIComponent(version)}` : '';
const [{ installAppShellControls }, legacyModule] = await Promise.all([
    import(`./work/status_panel.js${suffix}`),
    import(`./legacy/app.js${suffix}`),
]);

installAppShellControls();

const PhotoArchive = legacyModule.default;
const compatibilityTarget = (
    window.PhotoArchive && typeof window.PhotoArchive === 'object'
) ? window.PhotoArchive : {};
Object.assign(compatibilityTarget, PhotoArchive);
window.PhotoArchive = compatibilityTarget;

export default compatibilityTarget;
