import { formatBytes } from '../ui.js';
import {
    formatDateTime,
    resolutionLabel,
} from '../media_metadata.js';


export function loupeMetadataParts(e = {}) {
    const parts = [];
    const camera = [e.camera_make, e.camera_model].filter(Boolean).join(' ');
    if (camera) parts.push(camera);
    if (e.date_taken || e.date) parts.push('Taken ' + formatDateTime(e.date_taken || e.date));
    const settings = [];
    if (e.focal_length) settings.push(e.focal_length);
    if (e.focal_length_35mm) settings.push(`${e.focal_length_35mm} equiv`);
    if (e.aperture) settings.push(e.aperture);
    if (e.shutter_speed) settings.push(e.shutter_speed + 's');
    if (e.iso) settings.push('ISO ' + e.iso);
    if (settings.length) parts.push(settings.join('  '));
    if (e.lens) parts.push(e.lens);
    const exposure = [e.exposure_program, e.exposure_bias, e.metering_mode, e.white_balance, e.flash].filter(Boolean);
    if (exposure.length) parts.push(exposure.join('  '));
    const fileBits = [];
    if (e.dimensions) fileBits.push(e.dimensions);
    else {
        const resolution = resolutionLabel(e);
        if (resolution) fileBits.push(resolution);
    }
    if (e.filesize) fileBits.push(e.filesize);
    else if (e.file_size) fileBits.push(formatBytes(e.file_size));
    if (e.file_ext) fileBits.push(String(e.file_ext).replace('.', '').toUpperCase());
    if (fileBits.length) parts.push(fileBits.join('  '));
    if (e.file_modified) parts.push('Modified ' + formatDateTime(e.file_modified));
    else if (e.file_modified_at) parts.push('Modified ' + formatDateTime(e.file_modified_at));
    if (e.filepath) parts.push(e.filepath);
    return parts;
}


export function renderLoupeMetadata(metadata, exifEl = document.getElementById('loupe-overlay-exif')) {
    if (!exifEl) return '';
    const text = loupeMetadataParts(metadata).join('\n');
    exifEl.textContent = text;
    return text;
}


export function loupeStatsText(img = {}, { eloToStarsImpl = () => 0 } = {}) {
    const stars = eloToStarsImpl(img.elo, img.comparisons);
    const starStr = stars > 0 ? '★'.repeat(stars) + '☆'.repeat(5 - stars) + '  ' : '';
    return `${starStr}${img.elo} Elo · ${img.comparisons} ranking signals`;
}


export function renderLoupeStats(img, statsEl = document.getElementById('loupe-overlay-stats'), options = {}) {
    if (!statsEl) return '';
    const text = loupeStatsText(img, options);
    statsEl.textContent = text;
    return text;
}


export function renderLoupeMetadataOverlay({
    documentImpl = document,
    image = {},
    eloToStarsImpl = () => 0,
} = {}) {
    const filenameEl = documentImpl.getElementById('loupe-overlay-filename');
    const exifEl = documentImpl.getElementById('loupe-overlay-exif');
    const statsEl = documentImpl.getElementById('loupe-overlay-stats');
    const tierEl = documentImpl.getElementById('loupe-overlay-tier');

    if (filenameEl) filenameEl.textContent = image.filename;
    const metadataText = renderLoupeMetadata(image, exifEl);
    if (tierEl) {
        tierEl.classList.remove('loupe-tier-loading');
        tierEl.textContent = '';
    }
    const statsText = renderLoupeStats(image, statsEl, { eloToStarsImpl });
    return { exifEl, metadataText, statsText };
}
