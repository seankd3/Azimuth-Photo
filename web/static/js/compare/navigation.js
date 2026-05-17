import { findElementInAdjacentVisualRow } from '../ui.js';


export function selectMosaicCell(index, cells = document.querySelectorAll('.mosaic-cell')) {
    if (index < 0 || index >= cells.length) return null;
    cells.forEach(c => c.classList.remove('kb-selected'));
    cells[index].classList.add('kb-selected');
    return index;
}


export function deselectMosaicCell(cells = document.querySelectorAll('.mosaic-cell')) {
    cells.forEach(c => c.classList.remove('kb-selected'));
    return -1;
}


export function findMosaicCellInDirection(cells, currentIdx, direction) {
    return findElementInAdjacentVisualRow(cells, currentIdx, direction);
}
