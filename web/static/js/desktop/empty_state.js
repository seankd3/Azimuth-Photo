import { icon } from '../icons.js';
import { escapeHtml as esc } from '../lib.js';

export function emptyStateHtml({ title, detail, actions = [], iconName = 'image' } = {}) {
    const buttons = actions.map(({ label, action = '', primary = false }) => (
        `<button class="btn${primary ? ' primary' : ''}" data-empty-action="${esc(action)}">${esc(label)}</button>`
    )).join('');
    return `<section class="empty-state" aria-live="polite"><span class="empty-state-icon">${icon(iconName)}</span>`
        + `<h3>${esc(title || 'Nothing here yet')}</h3><p>${esc(detail || 'Adjust this view to continue.')}</p>`
        + (buttons ? `<div class="empty-state-actions">${buttons}</div>` : '')
        + '</section>';
}
