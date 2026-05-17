const version = new URL(import.meta.url).searchParams.get('v') || '';
const suffix = version ? `?v=${encodeURIComponent(version)}` : '';

function createPhotoArchivePlaceholder() {
    const target = {};
    return new Proxy(target, {
        get(current, prop) {
            if (prop in current) return current[prop];
            if (prop === 'then') return undefined;
            if (typeof prop !== 'string') return undefined;
            return (...args) => window.PhotoArchiveReady.then((api) => {
                const fn = api?.[prop];
                if (typeof fn !== 'function') {
                    throw new Error(`PhotoArchive.${prop} is not available`);
                }
                return fn(...args);
            });
        },
    });
}

if (!window.PhotoArchive) {
    window.PhotoArchive = createPhotoArchivePlaceholder();
}

window.PhotoArchiveReady = import(`./js/bootstrap.js${suffix}`).then((module) => module.default);

export default await window.PhotoArchiveReady;
