export const LOUPE_TIER_LABELS = ['Thumbnail (sm)', 'Medium (md)', 'Large (lg)', 'Original'];
export const LOUPE_TIER_NAMES = ['sm', 'md', 'lg', 'full'];
export const LOUPE_TIER_TIMEOUTS = { md: 1800, lg: 2600, full: 5000 };
export const LOUPE_TIER_RANKS = { sm: 0, md: 1, lg: 2, full: 3 };
export const LOUPE_BLANK_SRC = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';

export function loupeTierUrl(tier, imageId, cachedOnly = false) {
    if (tier === 'full') {
        return `/api/full/${imageId}${cachedOnly ? '?cached=1' : ''}`;
    }
    return `/api/thumb/${tier}/${imageId}${cachedOnly ? '?cached=1' : ''}`;
}
