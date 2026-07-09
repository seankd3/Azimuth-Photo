// Undo-first toast (interaction canon: no confirmations,
// every action is a toast with Undo).

import { tick } from './haptics.js';
import { isOffline } from './state.js';

let hideTimer = null;
let currentUndo = null;

function els() {
    return {
        root: document.getElementById('m-toast'),
        msg: document.getElementById('m-toast-msg'),
        undo: document.getElementById('m-toast-undo'),
    };
}

export function showToast(message, { undo = null, duration = 6000 } = {}) {
    const { root, msg, undo: undoBtn } = els();
    if (!root) return;
    const toastDuration = undo && isOffline() ? Math.max(duration, 20000) : duration;
    msg.textContent = message;
    currentUndo = undo;
    undoBtn.hidden = !undo;
    if (undo) tick(10);
    root.style.setProperty('--toast-duration', `${toastDuration}ms`);
    root.classList.remove('on');
    void root.offsetWidth;
    root.classList.add('on');
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hideToast, toastDuration);
}

export function hideToast() {
    const { root } = els();
    if (root) root.classList.remove('on');
    currentUndo = null;
    clearTimeout(hideTimer);
}

export function initToast() {
    const { undo } = els();
    undo.addEventListener('click', () => {
        const fn = currentUndo;
        hideToast();
        if (fn) fn();
    });
}
