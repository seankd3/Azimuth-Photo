import { formatBytes } from './ui.js';


export function parseMetadataDate(value) {
    if (!value) return null;
    if (typeof value === 'number') {
        const millis = value > 100000000000 ? value : value * 1000;
        const parsed = new Date(millis);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }
    const normalized = String(value).trim().replace(' ', 'T');
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}


export function formatShortDate(value) {
    const date = parseMetadataDate(value);
    if (!date) return value || '';
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}


export function formatDateTime(value) {
    const date = parseMetadataDate(value);
    if (!date) return value || '';
    return date.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
    });
}


export function cameraLabel(img) {
    return [img.camera_make, img.camera_model].filter(Boolean).join(' ').trim();
}


export function resolutionLabel(img) {
    const width = Number(img.width || 0);
    const height = Number(img.height || 0);
    if (!width || !height) return '';
    const megapixels = (width * height) / 1000000;
    return `${width} x ${height} (${megapixels.toFixed(megapixels >= 10 ? 0 : 1)} MP)`;
}


export function imageAspectRatio(img) {
    const ar = Number(img?.aspect_ratio || 0);
    if (ar > 0) return ar;
    const width = Number(img?.width || 0);
    const height = Number(img?.height || 0);
    if (width > 0 && height > 0) return width / height;
    return 1.5;
}


export function imageMetadataTitle(img) {
    const parts = [img.filename];
    const camera = cameraLabel(img);
    if (camera) parts.push(camera);
    if (img.lens) parts.push(img.lens);
    if (img.date_taken) parts.push(`Taken ${formatDateTime(img.date_taken)}`);
    const resolution = resolutionLabel(img);
    if (resolution) parts.push(resolution);
    if (img.file_size) parts.push(formatBytes(img.file_size));
    if (img.file_ext) parts.push(img.file_ext.toUpperCase());
    return parts.filter(Boolean).join('\n');
}
