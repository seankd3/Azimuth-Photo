export function createCompareKeyboardHandler({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
    setTimeoutImpl = globalThis.setTimeout,
    getCompareMode,
    getSelectedMosaicIndex,
    getMosaicImages,
    selectMosaicCell,
    deselectMosaicCell,
    findMosaicCellInDirection,
    mosaicClick,
    undoComparison,
    submitComparison,
    libraryPath = '/library',
} = {}) {
    return function handleCompareKey(e) {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const tagName = e.target?.tagName;
        if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT' || e.target?.isContentEditable) return;

        if (e.key === 'Tab') {
            e.preventDefault();
            windowImpl.location.href = libraryPath;
            return;
        }

        if (getCompareMode() === 'mosaic') {
            const cells = documentImpl.querySelectorAll('.mosaic-cell');
            if (!cells.length) return;

            const selectedMosaicIndex = getSelectedMosaicIndex();

            // ArrowUp with no cell selected is the advertised undo shortcut;
            // it must win over arrow-key cell selection.
            if (e.key === 'ArrowUp' && selectedMosaicIndex < 0) {
                e.preventDefault();
                undoComparison();
                return;
            }

            if (e.key === 'ArrowRight' || e.key === 'ArrowLeft' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (selectedMosaicIndex < 0) {
                    selectMosaicCell(0, cells);
                    return;
                }
                if (e.key === 'ArrowRight') {
                    selectMosaicCell(Math.min(selectedMosaicIndex + 1, cells.length - 1), cells);
                } else if (e.key === 'ArrowLeft') {
                    selectMosaicCell(Math.max(selectedMosaicIndex - 1, 0), cells);
                } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    const target = findMosaicCellInDirection(cells, selectedMosaicIndex, e.key === 'ArrowDown' ? 1 : -1);
                    selectMosaicCell(target, cells);
                }
            } else if (e.key === 'Enter' && selectedMosaicIndex >= 0 && selectedMosaicIndex < getMosaicImages().length) {
                e.preventDefault();
                const keepIdx = selectedMosaicIndex;
                mosaicClick(getMosaicImages()[selectedMosaicIndex].id);
                setTimeoutImpl(() => {
                    const nextCells = documentImpl.querySelectorAll('.mosaic-cell');
                    if (keepIdx < nextCells.length) selectMosaicCell(keepIdx, nextCells);
                }, 200);
            } else if (e.key === 'Escape' && selectedMosaicIndex >= 0) {
                e.preventDefault();
                deselectMosaicCell(cells);
            }
            return;
        }

        if (e.key === 'ArrowLeft') {
            submitComparison('left');
        } else if (e.key === 'ArrowRight') {
            submitComparison('right');
        } else if (e.key === 'ArrowUp') {
            undoComparison();
        }
    };
}
