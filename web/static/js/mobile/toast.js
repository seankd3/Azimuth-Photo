// Undo-first toast (interaction canon: no confirmations,
// every action is a toast with Undo).

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
    msg.textContent = message;
    currentUndo = undo;
    undoBtn.hidden = !undo;
    root.style.setProperty('--toast-duration', `${duration}ms`);
    root.classList.remove('on');
    void root.offsetWidth;
    root.classList.add('on');
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hideToast, duration);
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
