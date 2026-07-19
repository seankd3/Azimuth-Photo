"use strict";

/** Subtle elo_stars / lr_rating star display with projection whisper. */

import { icon } from '../icons.js';

export function starsMarkup({ eloStars = 0, lrRating = 0, whisper = '' } = {}) {
    const yours = Number(lrRating) > 0;
    const value = yours ? Number(lrRating) : Number(eloStars) || 0;
    if (value <= 0) return '';
    const title = yours
        ? 'Your rating'
        : (whisper || 'Ranked');
    const glyphs = [1, 2, 3, 4, 5].map((level) => (
        `<span class="elo-star${level <= value ? ' on' : ''}" aria-hidden="true">${icon('star')}</span>`
    )).join('');
    const tick = yours ? '<i class="elo-stars-yours" title="yours" aria-label="yours">yours</i>' : '';
    return `<span class="elo-stars-display" title="${escapeAttr(title)}" data-elo-stars="${value}" data-yours="${yours ? '1' : '0'}">${glyphs}${tick}</span>`;
}

function escapeAttr(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
}
