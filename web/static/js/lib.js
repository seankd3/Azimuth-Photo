export const MONTH_NAMES = Object.freeze({
    short: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
    long: [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ],
});

export function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]));
}

export const escapeHtml = esc;

export function slugifyName(value, fallback = 'collection') {
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96) || fallback;
}

export function formatCount(value) {
    return Number(value || 0).toLocaleString('en-US');
}

// The shape of a photograph, and the only place that question is answered.
//
// **Measured beats remembered.** `aspect_ratio` is a stored derivation of two
// columns in the same row, written once with COALESCE and never updated, so
// wherever it disagrees with the dimensions it is the older value -- 12,127
// photographs in this catalog, every one a cell the wrong shape for its tile.
// Dimensions are re-read whenever the file changes; this is not. It stays only
// as the answer for the 37,260 photographs whose dimensions have not been read
// yet, which is the one thing it is still good for.
export function photoShape(image) {
    const width = Number(image?.width);
    const height = Number(image?.height);
    if (width > 0 && height > 0) return width / height;
    const remembered = Number(image?.aspect_ratio);
    return remembered > 0 ? remembered : 1.5;
}

// The clamp is a layout choice and belongs to the caller; the shape does not.
export function photoAspect(image, min = .45, max = 3.8) {
    return Math.max(min, Math.min(max, photoShape(image)));
}

export function bytes(value) {
    let amount = Math.max(0, Number(value) || 0);
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
        amount /= 1024;
        unit += 1;
    }
    return `${amount >= 10 || unit === 0 ? Math.round(amount) : amount.toFixed(1)} ${units[unit]}`;
}

// The same eight extensions as photo/kind.py's RAW_FORMATS. Two desktop modules
// had kept byte-identical copies of this set; a ninth format would have had to
// be remembered in three places.
export const RAW_EXTENSIONS = Object.freeze(
    new Set(['arw', 'cr2', 'cr3', 'dng', 'nef', 'orf', 'raf', 'rw2']),
);
