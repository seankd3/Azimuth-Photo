const version = new URL(import.meta.url).searchParams.get('v') || '';
const suffix = version ? `?v=${encodeURIComponent(version)}` : '';
const legacyModule = await import(`./legacy/app.js${suffix}`);

const PhotoArchive = legacyModule.default;
const compatibilityTarget = (
    window.PhotoArchive && typeof window.PhotoArchive === 'object'
) ? window.PhotoArchive : {};
Object.assign(compatibilityTarget, PhotoArchive);
window.PhotoArchive = compatibilityTarget;

export default compatibilityTarget;
