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

export function photoAspect(image) {
    const ratio = Number(image?.aspect_ratio) || (Number(image?.width) && Number(image?.height) ? Number(image.width) / Number(image.height) : 1.5);
    return Math.max(.45, Math.min(3.8, ratio));
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
