import { appendLibraryRankCards } from '../library/rank_cards.js';

export function createLegacyLibraryRankCardsBridge({
    appendLibraryRankCardsImpl = appendLibraryRankCards,
} = {}) {
    return {
        appendLibraryRankCards: (...args) => appendLibraryRankCardsImpl(...args),
    };
}
