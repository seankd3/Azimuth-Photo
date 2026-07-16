export const MONTH_NAMES = Object.freeze({
    short: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
    long: [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ],
});

export function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]));
}

export function formatCount(value) {
    return Number(value || 0).toLocaleString('en-US');
}

export function photoAspect(image) {
    const ratio = Number(image?.aspect_ratio) || (Number(image?.width) && Number(image?.height) ? Number(image.width) / Number(image.height) : 1.5);
    return Math.max(.45, Math.min(3.8, ratio));
}
