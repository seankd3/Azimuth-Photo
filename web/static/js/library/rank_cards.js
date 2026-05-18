import { escapeHtml } from '../ui.js';
import { imageMetadataTitle } from '../media_metadata.js';
import {
    flagBadge,
    flagClass,
    formatDateGroup,
    getConfidenceClass,
    getTierClass,
    libraryCardInfoLine,
} from './display.js';


export function appendLibraryRankCards({
    documentImpl = document,
    fragment = null,
    images = [],
    rankingsOffset = 0,
    baseIndex = 0,
    rankingsSort = 'elo',
    thumbHeight = 220,
    lastDateGroup = null,
    isBatchMode = () => false,
    isSelected = () => false,
    onCardClick = () => {},
} = {}) {
    const target = fragment || documentImpl.createDocumentFragment();
    const showRank = (rankingsSort === 'elo' || rankingsSort === 'elo_asc');
    const isDateSort = (rankingsSort === 'date_taken' || rankingsSort === 'date_taken_asc');
    let nextDateGroup = lastDateGroup;

    for (let i = 0; i < images.length; i++) {
        const img = images[i];
        const rank = rankingsOffset + i + 1;
        const ar = img.aspect_ratio || 1.5;
        const tier = getTierClass(img.elo, img.comparisons);
        const conf = img.comparisons > 0 ? getConfidenceClass(img.comparisons) : '';

        if (isDateSort) {
            const group = img.date_group || '';
            if (group !== nextDateGroup) {
                nextDateGroup = group;
                const header = documentImpl.createElement('div');
                header.className = 'date-group-header';
                header.dataset.dateGroup = group;
                header.textContent = group ? formatDateGroup(group) : 'No Date';
                target.appendChild(header);
            }
        }

        const card = documentImpl.createElement('div');
        card.className = 'rank-card skeleton-cell' + (tier ? ' ' + tier : '') + (flagClass(img.flag) ? ' ' + flagClass(img.flag) : '');
        if (isBatchMode()) card.classList.add('selectable');
        if (isSelected(img.id)) card.classList.add('selected');
        card.dataset.imageId = img.id;
        card.dataset.ar = ar;
        card.style.height = thumbHeight + 'px';
        card.style.flexGrow = ar;
        card.style.flexBasis = (thumbHeight * ar) + 'px';
        card.title = imageMetadataTitle(img);
        card.onclick = (event) => onCardClick(event, img, card, baseIndex + i);

        const confDot = conf ? `<div class="rank-confidence ${conf}"></div>` : '';
        const infoLine = libraryCardInfoLine(img, rank, showRank, rankingsSort);
        const eagerThumb = rankingsOffset === 0 && i < 12;
        const loadingAttrs = eagerThumb ? 'loading="eager" fetchpriority="high"' : 'loading="lazy"';

        card.innerHTML = `
                    <img src="${escapeHtml(img.thumb_url)}" alt="${escapeHtml(img.filename)}" ${loadingAttrs} onload="this.classList.add('loaded'); this.parentElement.classList.remove('skeleton-cell')">
                    <div class="select-check">✓</div>
                    ${confDot}
                    ${flagBadge(img.flag)}
                    <div class="rank-card-info">${infoLine}</div>
                `;
        target.appendChild(card);
    }

    return {
        fragment: target,
        lastDateGroup: nextDateGroup,
    };
}
