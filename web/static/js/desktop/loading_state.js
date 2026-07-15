const ASPECTS = [1.5, .75, 1.2, 1.8];

export function gridLoadingHtml({ count = 18, className = 'grid-chunk' } = {}) {
    return `<div class="${className}" aria-label="Loading photos" aria-busy="true">`
        + Array.from({ length: count }, (_, index) => (
            `<div class="cell skel-cell" style="--ar:${ASPECTS[index % ASPECTS.length]}"></div>`
        )).join('')
        + '</div>';
}
