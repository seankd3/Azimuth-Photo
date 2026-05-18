import {
    formatBytes,
    hideConfirmModal as hideConfirmModalUi,
    hideShortcuts as hideShortcutsUi,
    initShortcutOverlay as initShortcutOverlayUi,
    showConfirmModal as showConfirmModalUi,
    showShortcuts as showShortcutsUi,
    showToast as showToastUi,
} from '../ui.js';


export function createLegacyUiActionBridge({
    beforeShowToast,
    formatBytesImpl = formatBytes,
    hideConfirmModalImpl = hideConfirmModalUi,
    hideShortcutsImpl = hideShortcutsUi,
    initShortcutOverlayImpl = initShortcutOverlayUi,
    showConfirmModalImpl = showConfirmModalUi,
    showShortcutsImpl = showShortcutsUi,
    showToastImpl = showToastUi,
} = {}) {
    function showToast(message) {
        return showToastImpl(message, { beforeShow: beforeShowToast });
    }

    function showConfirmModal(title, text, onConfirm) {
        return showConfirmModalImpl(title, text, onConfirm);
    }

    function hideConfirmModal() {
        return hideConfirmModalImpl();
    }

    function showShortcuts() {
        return showShortcutsImpl();
    }

    function hideShortcuts() {
        return hideShortcutsImpl();
    }

    function initShortcutOverlay() {
        return initShortcutOverlayImpl();
    }

    return {
        formatBytes: formatBytesImpl,
        hideConfirmModal,
        hideShortcuts,
        initShortcutOverlay,
        showConfirmModal,
        showShortcuts,
        showToast,
    };
}
