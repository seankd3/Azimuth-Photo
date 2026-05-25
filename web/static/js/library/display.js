import {
    escapeHtml,
    formatBytes,
} from '../ui.js';
import {
    cameraLabel,
    formatShortDate,
    resolutionLabel,
} from '../media_metadata.js';


export function flagClass(flag) {
    if (flag === 'picked') return 'flag-picked';
    if (flag === 'rejected') return 'flag-rejected';
    return '';
}


export function flagBadge(flag) {
    if (flag === 'picked') return '<div class="rank-flag flag-picked">P</div>';
    if (flag === 'rejected') return '<div class="rank-flag flag-rejected">X</div>';
    return '';
}


export function similarityLabel(img) {
    const similarity = Number(img?.similarity);
    return Number.isFinite(similarity) ? `${(similarity * 100).toFixed(0)}% match` : 'metadata match';
}


export function eloToStars(elo, comparisons) {
    if (comparisons === 0 && Math.abs(Number(elo || 1200) - 1200) < 0.01) return 0;
    if (elo >= 1500) return 5;
    if (elo >= 1350) return 4;
    if (elo >= 1250) return 3;
    if (elo >= 1150) return 2;
    return 1;
}


export function getConfidenceClass(comparisons) {
    if (comparisons >= 10) return 'high';
    if (comparisons >= 3) return 'medium';
    return 'low';
}


export function getTierClass(elo, comparisons) {
    if (comparisons < 5 && Math.abs(Number(elo || 1200) - 1200) < 0.01) return '';
    if (elo >= 1500) return 'tier-gold';
    if (elo >= 1350) return 'tier-silver';
    if (elo >= 1250) return 'tier-bronze';
    return '';
}


export function formatDateGroup(dateStr) {
    try {
        const [year, month] = dateStr.split('-');
        const date = new Date(parseInt(year), parseInt(month) - 1);
        return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long' });
    } catch {
        return dateStr;
    }
}


export function libraryCardInfoLine(img, rank, showRank, sort) {
    if (showRank) {
        return `<span class="rank-number">#${rank}</span><span class="rank-elo">${escapeHtml(img.elo)}</span>`;
    }
    if (sort === 'taste') {
        const rawScore = Number(img.taste_score);
        const label = Number.isFinite(rawScore)
            ? `${Math.round(Math.max(0, Math.min(1, (rawScore + 1) / 2)) * 100)}% taste`
            : 'Taste pending';
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(label)}</span>`;
    }
    if (sort === 'date_taken' || sort === 'date_taken_asc') {
        const label = formatShortDate(img.date_taken) || 'No date';
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(label)}</span>`;
    }
    if (sort === 'file_size' || sort === 'file_size_asc') {
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(formatBytes(img.file_size))}</span>`;
    }
    if (sort === 'date_modified' || sort === 'date_modified_asc') {
        const label = formatShortDate(img.file_modified_at) || 'No modified date';
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(label)}</span>`;
    }
    if (sort === 'resolution' || sort === 'resolution_asc') {
        const label = resolutionLabel(img) || 'No size';
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(label)}</span>`;
    }
    if (sort === 'camera' || sort === 'camera_desc') {
        const label = cameraLabel(img) || 'No camera';
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(label)}</span>`;
    }
    if (sort === 'newest' || sort === 'oldest') {
        const label = formatShortDate(img.created_at) || 'Added';
        return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(label)}</span>`;
    }
    return `<span class="rank-elo">${escapeHtml(img.elo)}</span><span class="rank-comparisons">${escapeHtml(img.comparisons)} signal${Number(img.comparisons || 0) === 1 ? '' : 's'}</span>`;
}


export function dateScrubberLabelsHtml(dateGroups) {
    let currentYear = '';
    let html = '';
    for (const group of dateGroups || []) {
        if (!group.date) {
            html += '<div class="scrubber-label" data-date-group="">No Date</div>';
            continue;
        }
        const year = group.date.substring(0, 4);
        const month = group.date.substring(5, 7);
        const monthName = new Date(parseInt(year), parseInt(month) - 1).toLocaleDateString(undefined, { month: 'short' });
        if (year !== currentYear) {
            currentYear = year;
            html += `<div class="scrubber-year" data-date-group="${escapeHtml(group.date)}">${escapeHtml(year)}</div>`;
        }
        html += `<div class="scrubber-label" data-date-group="${escapeHtml(group.date)}" title="${escapeHtml(group.label)} (${escapeHtml(group.count)})">${escapeHtml(monthName)}</div>`;
    }
    return html;
}


export function similarLibraryCardHtml(img) {
    const simLabel = similarityLabel(img);
    return `
                <img src="${escapeHtml(img.thumb_url)}" alt="${escapeHtml(img.filename)}" loading="lazy" onload="this.classList.add('loaded'); this.parentElement.classList.remove('skeleton-cell')">
                ${flagBadge(img.flag)}
                <div class="rank-card-info">
                    <span class="rank-elo">${escapeHtml(img.elo)} Elo</span>
                    <span class="rank-similarity">${escapeHtml(simLabel)}</span>
                </div>
            `;
}
