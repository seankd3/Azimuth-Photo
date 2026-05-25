import { escapeHtml } from '../ui.js';
import { visiblePersonLabel } from './labels.js';


export function personCardHtml(person, { draftName } = {}) {
    const id = Number(person.id || 0);
    const savedName = String(person.name || '').trim();
    const title = visiblePersonLabel(person, '');
    const effectiveDraftName = Object.prototype.hasOwnProperty.call(arguments[1] || {}, 'draftName')
        ? String(draftName || '')
        : savedName;
    const titleHtml = title
        ? `<div class="people-card-title">${escapeHtml(title)}</div>`
        : '';
    const faceThumb = person.face_thumb_url || person.thumb_url || '';
    const fallbackThumb = person.image_thumb_url || person.thumb_url || '';
    const fallbackAttrs = fallbackThumb && fallbackThumb !== faceThumb
        ? ` data-fallback-src="${escapeHtml(fallbackThumb)}"`
        : '';
    const thumb = faceThumb
        ? `<img src="${escapeHtml(faceThumb)}" alt="${escapeHtml(title || 'Face')}" loading="lazy"${fallbackAttrs}>`
        : '<div class="people-thumb-placeholder"></div>';
    return `
            <article class="people-card${title ? '' : ' people-card-unnamed'}" data-person-id="${id}">
                <button type="button" class="people-thumb has-tooltip" data-people-action="filter-library" data-person-id="${id}" title="Filter Library" data-tooltip="Open Library filtered to this person.">
                    ${thumb}
                </button>
                <div class="people-card-main">
                    ${titleHtml}
                    <div class="people-card-meta">${Number(person.photo_count || 0).toLocaleString()} photos</div>
                    <div class="people-label-row">
                        <input id="person-label-${id}" type="text" value="${escapeHtml(effectiveDraftName)}" data-person-id="${id}" data-server-name="${escapeHtml(savedName)}" placeholder="Name">
                        <button type="button" class="bar-btn has-tooltip" data-people-action="label" data-person-id="${id}" data-tooltip="Save this name for the face group.">Label</button>
                    </div>
                </div>
                <div class="people-card-actions">
                    <button type="button" class="bar-btn has-tooltip" data-people-action="filter-library" data-person-id="${id}" data-tooltip="See photos containing this person.">Library</button>
                    <button type="button" class="bar-btn subtle has-tooltip" data-people-action="ignore" data-person-id="${id}" data-tooltip="Hide this face group from People review.">Ignore</button>
                </div>
            </article>
        `;
}


export function peopleGridHtml(people, emptyText, labelDrafts = new Map()) {
    const list = Array.isArray(people) ? people : [];
    if (!list.length) return `<div class="people-empty">${escapeHtml(emptyText)}</div>`;
    return list.map((person) => {
        const id = Number(person.id || 0);
        const options = labelDrafts?.has?.(id) ? { draftName: labelDrafts.get(id) } : {};
        return personCardHtml(person, options);
    }).join('');
}


export function reviewFaceChipHtml(person, fallbackPersonId) {
    const id = Number(person?.id || fallbackPersonId || 0);
    const title = visiblePersonLabel(person, '');
    const faceThumb = person?.face_thumb_url || person?.thumb_url || '';
    const fallbackThumb = person?.image_thumb_url || person?.thumb_url || '';
    const fallbackAttrs = fallbackThumb && fallbackThumb !== faceThumb
        ? ` data-fallback-src="${escapeHtml(fallbackThumb)}"`
        : '';
    const thumb = faceThumb
        ? `<img src="${escapeHtml(faceThumb)}" alt="${escapeHtml(title || 'Suggested merge face')}" loading="lazy"${fallbackAttrs}>`
        : '<div class="people-thumb-placeholder"></div>';
    const label = title ? `<div class="people-review-face-label">${escapeHtml(title)}</div>` : '';
    const meta = person
        ? `<div class="people-review-face-meta">${Number(person.photo_count || 0).toLocaleString()} photos</div>`
        : '';
    const click = id > 0 ? ` data-people-action="filter-library" data-person-id="${id}"` : '';
    return `
            <div class="people-review-face">
                <button type="button" class="people-review-face-thumb has-tooltip"${click} title="Filter Library" data-tooltip="Open Library filtered to this suggested person.">
                    ${thumb}
                </button>
                ${label}
                ${meta}
            </div>
        `;
}


export function mergeSuggestionsHtml(suggestions) {
    const list = Array.isArray(suggestions) ? suggestions : [];
    if (!list.length) return '<div class="people-empty">No merge suggestions.</div>';
    return list.map((item) => {
        const sourceId = Number(item.source_person_id || 0);
        const targetId = Number(item.target_person_id || 0);
        const confidence = Math.round(Number(item.confidence || 0) * 100);
        return `
                <article class="people-review-row">
                    <div class="people-review-pair">
                        ${reviewFaceChipHtml(item.source, sourceId)}
                        <div class="people-review-match">${confidence}%</div>
                        ${reviewFaceChipHtml(item.target, targetId)}
                    </div>
                    <div class="people-card-actions">
                        <button type="button" class="bar-btn has-tooltip" data-people-action="merge" data-source-person-id="${sourceId}" data-target-person-id="${targetId}" data-tooltip="Combine these two groups into one person.">Merge</button>
                        <button type="button" class="bar-btn subtle has-tooltip" data-people-action="reject-merge" data-suggestion-id="${Number(item.id)}" data-tooltip="Dismiss this suggestion without merging.">Reject</button>
                    </div>
                </article>
            `;
    }).join('');
}
