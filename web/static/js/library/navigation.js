import { findElementInAdjacentVisualRow } from '../ui.js';


export function selectLibraryCard(index, cards = document.querySelectorAll('.rank-card'), { scrollRoot = null } = {}) {
    if (index < 0 || index >= cards.length) return null;
    cards.forEach(c => c.classList.remove('kb-selected'));
    cards[index].classList.add('kb-selected');
    scrollCardFullyVisible(cards[index], scrollRoot);
    return index;
}


export function scrollCardFullyVisible(el, scrollRoot = null) {
    const rect = el.getBoundingClientRect();
    if (scrollRoot) {
        const rootRect = scrollRoot.getBoundingClientRect();
        if (rect.bottom > rootRect.bottom) {
            scrollRoot.scrollBy({ top: rect.bottom - rootRect.bottom + 8, behavior: 'smooth' });
        } else if (rect.top < rootRect.top) {
            scrollRoot.scrollBy({ top: rect.top - rootRect.top - 8, behavior: 'smooth' });
        }
        return;
    }
    if (rect.bottom > window.innerHeight) {
        window.scrollBy({ top: rect.bottom - window.innerHeight + 8, behavior: 'smooth' });
    } else if (rect.top < 0) {
        window.scrollBy({ top: rect.top - 8, behavior: 'smooth' });
    }
}


export function deselectLibraryCard(cards = document.querySelectorAll('.rank-card')) {
    cards.forEach(c => c.classList.remove('kb-selected'));
    return -1;
}


export function findCardInDirection(cards, currentIdx, direction) {
    return findElementInAdjacentVisualRow(cards, currentIdx, direction);
}
