"use strict";

/** Subtle read-only star display — stars are the stored projection of Elo. */

import { icon } from '../icons.js';

export function starsMarkup({ stars = 0, whisper = '' } = {}) {
    const value = Number(stars) || 0;
    if (value <= 0) return '';
    const title = whisper || 'Ranked';
    const glyphs = [1, 2, 3, 4, 5].map((level) => (
        `<span class="elo-star${level <= value ? ' on' : ''}" aria-hidden="true">${icon('star')}</span>`
    )).join('');
    return `<span class="elo-stars-display" title="${escapeAttr(title)}" data-elo-stars="${value}">${glyphs}</span>`;
}

function escapeAttr(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
}
