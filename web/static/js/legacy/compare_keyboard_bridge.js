import { createCompareKeyboardHandler } from '../compare/keyboard.js';
import {
    deselectMosaicCell as deselectMosaicCellCore,
    findMosaicCellInDirection as findMosaicCellInDirectionCore,
    selectMosaicCell as selectMosaicCellCore,
} from '../compare/navigation.js';


export function createLegacyCompareKeyboardBridge({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
    setTimeoutImpl = globalThis.setTimeout,
    getCompareMode,
    getSelectedMosaicIndex,
    setSelectedMosaicIndex,
    getMosaicImages,
    mosaicClick,
    undoComparison,
    submitComparison,
    createCompareKeyboardHandlerImpl = createCompareKeyboardHandler,
    selectMosaicCellImpl = selectMosaicCellCore,
    deselectMosaicCellImpl = deselectMosaicCellCore,
    findMosaicCellInDirectionImpl = findMosaicCellInDirectionCore,
} = {}) {
    function selectMosaicCell(index, cells) {
        const selected = selectMosaicCellImpl(index, cells);
        if (selected !== null) setSelectedMosaicIndex(selected);
        return selected;
    }

    function deselectMosaicCell(cells) {
        const selected = deselectMosaicCellImpl(cells);
        setSelectedMosaicIndex(selected);
        return selected;
    }

    function findMosaicCellInDirection(cells, currentIdx, direction) {
        return findMosaicCellInDirectionImpl(cells, currentIdx, direction);
    }

    const handleCompareKey = createCompareKeyboardHandlerImpl({
        documentImpl,
        windowImpl,
        setTimeoutImpl,
        getCompareMode,
        getSelectedMosaicIndex,
        getMosaicImages,
        selectMosaicCell,
        deselectMosaicCell,
        findMosaicCellInDirection,
        mosaicClick,
        undoComparison,
        submitComparison,
    });

    return {
        deselectMosaicCell,
        findMosaicCellInDirection,
        handleCompareKey,
        selectMosaicCell,
    };
}
