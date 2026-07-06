export function showToast(message, { beforeShow = null } = {}) {
    if (beforeShow) beforeShow();
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = 'toast-item';
    toast.textContent = message;
    container.prepend(toast);
    while (container.children.length > 3) {
        container.lastElementChild?.remove();
    }
    requestAnimationFrame(() => toast.classList.add('visible'));
    setTimeout(() => {
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}


export function showConfirmModal(title, text, onConfirm) {
    const modal = document.getElementById('confirm-modal');
    document.getElementById('confirm-modal-title').textContent = title;
    document.getElementById('confirm-modal-text').textContent = text;
    const btn = document.getElementById('confirm-modal-confirm');
    btn.onclick = () => {
        hideConfirmModal();
        onConfirm();
    };
    modal.classList.remove('hidden');
}


export function hideConfirmModal() {
    document.getElementById('confirm-modal').classList.add('hidden');
}


export function showShortcuts() {
    document.getElementById('shortcut-overlay')?.classList.remove('hidden');
}


export function hideShortcuts() {
    document.getElementById('shortcut-overlay')?.classList.add('hidden');
}


export function handleShortcutOverlayKey(event) {
    const overlay = document.getElementById('shortcut-overlay');
    const overlayVisible = overlay && !overlay.classList.contains('hidden');
    if (overlayVisible && event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        hideShortcuts();
        return;
    }
    const confirmModal = document.getElementById('confirm-modal');
    const confirmVisible = confirmModal && !confirmModal.classList.contains('hidden');
    if (confirmVisible && event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        hideConfirmModal();
        return;
    }
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') return;
    if (event.key === '?' || (event.key === '/' && event.shiftKey)) {
        event.preventDefault();
        showShortcuts();
    }
}


let shortcutOverlayInitialized = false;
let bottomBarResizeObserver = null;
let bottomBarResizeListenerAdded = false;
let visibilityListenerAdded = false;


export function initShortcutOverlay() {
    if (shortcutOverlayInitialized) return;
    document.addEventListener('keydown', handleShortcutOverlayKey);
    document.addEventListener('click', (event) => {
        const action = event.target?.closest?.('[data-action]')?.dataset?.action;
        if (action === 'hide-shortcuts') {
            event.preventDefault();
            hideShortcuts();
        } else if (action === 'hide-confirm-modal') {
            event.preventDefault();
            hideConfirmModal();
        }
        const helpTip = event.target?.closest?.('.help-tip');
        document.querySelectorAll('.tooltip-open').forEach((el) => {
            if (el !== helpTip) el.classList.remove('tooltip-open');
        });
        helpTip?.classList.toggle('tooltip-open');
    });
    shortcutOverlayInitialized = true;
}


export function updateBottomBarHeightVar({
    documentImpl = document,
    onMeasured = null,
} = {}) {
    const bar = documentImpl.querySelector('.bottom-bar');
    const height = bar?.offsetHeight || 0;
    documentImpl.documentElement.style.setProperty('--current-bottom-bar-height', `${height}px`);
    onMeasured?.();
}


export function initBottomBarMeasurement({
    documentImpl = document,
    windowImpl = window,
    onMeasured = null,
} = {}) {
    const measure = () => updateBottomBarHeightVar({ documentImpl, onMeasured });
    measure();
    if (bottomBarResizeObserver) bottomBarResizeObserver.disconnect();

    const bar = documentImpl.querySelector('.bottom-bar');
    if (bar && 'ResizeObserver' in windowImpl) {
        bottomBarResizeObserver = new windowImpl.ResizeObserver(measure);
        bottomBarResizeObserver.observe(bar);
    }
    if (!bottomBarResizeListenerAdded) {
        windowImpl.addEventListener('resize', measure);
        bottomBarResizeListenerAdded = true;
    }
}


export function initVisibilityRefresh({
    documentImpl = document,
    onVisible = null,
} = {}) {
    if (visibilityListenerAdded) return;
    documentImpl.addEventListener('visibilitychange', () => {
        if (documentImpl.hidden) return;
        onVisible?.();
    });
    visibilityListenerAdded = true;
}


export function findElementInAdjacentVisualRow(elements, currentIdx, direction) {
    const current = elements[currentIdx];
    if (!current) return currentIdx;

    const boxes = Array.from(elements, (el, index) => {
        const rect = el.getBoundingClientRect();
        return {
            index,
            centerX: rect.left + rect.width / 2,
            centerY: rect.top + rect.height / 2,
            height: rect.height,
        };
    });
    const currentBox = boxes[currentIdx];
    const sameRowTolerance = Math.max(2, Math.min(12, currentBox.height * 0.08));
    const targetRowTolerance = Math.max(4, Math.min(32, currentBox.height * 0.25));
    let targetRowDelta = Infinity;

    for (const box of boxes) {
        if (box.index === currentIdx) continue;
        const rowDelta = (box.centerY - currentBox.centerY) * direction;
        if (rowDelta <= sameRowTolerance) continue;
        targetRowDelta = Math.min(targetRowDelta, rowDelta);
    }

    if (!Number.isFinite(targetRowDelta)) return currentIdx;

    let best = currentIdx;
    let bestDist = Infinity;
    for (const box of boxes) {
        if (box.index === currentIdx) continue;
        const rowDelta = (box.centerY - currentBox.centerY) * direction;
        if (Math.abs(rowDelta - targetRowDelta) > targetRowTolerance) continue;
        const dist = Math.abs(box.centerX - currentBox.centerX);
        if (dist < bestDist) {
            bestDist = dist;
            best = box.index;
        }
    }
    return best;
}


export function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[ch]));
}


export function jsString(value) {
    return JSON.stringify(String(value ?? '')).replace(/</g, '\\u003c').replace(/>/g, '\\u003e');
}


export function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = value;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    const digits = size >= 100 || unit === 0 ? 0 : size >= 10 ? 1 : 2;
    return `${size.toFixed(digits)} ${units[unit]}`;
}
