import { afterMotion, prefersReducedMotion } from './motion.js';

let nextId = 1;
const toasts = [];

function removeFromHistory(item) {
    const index = toasts.indexOf(item);
    if (index >= 0) toasts.splice(index, 1);
}

function removeElement(item) {
    if (!item.el) return;
    const el = item.el;
    item.el = null;
    item.visible = false;
    el.classList.remove('on');
    if (prefersReducedMotion()) {
        el.remove();
        return;
    }
    el.addEventListener('transitionend', () => el.remove(), { once: true });
    afterMotion('slow', () => el.remove());
}

function dismiss(item, { keepUndo = false } = {}) {
    if (!keepUndo) {
        removeFromHistory(item);
        clearTimeout(item.timer);
    }
    removeElement(item);
}

function replaceVisibleToasts() {
    for (const item of toasts) {
        if (!item.visible) continue;
        dismiss(item);
    }
}

function expire(item) {
    removeFromHistory(item);
    removeElement(item);
}

function runUndo(item) {
    const fn = item?.undo;
    if (!fn) return false;
    dismiss(item);
    try {
        Promise.resolve(fn()).catch(() => showToast('Couldn’t undo'));
    } catch {
        showToast('Couldn’t undo');
    }
    return true;
}

export function showToast(message, { undo = null, duration = 8000 } = {}) {
    const root = document.getElementById('toast');
    if (!root) return;
    const item = {
        id: nextId++,
        undo,
        timer: null,
        visible: true,
        el: document.createElement('div'),
    };
    item.el.className = 'toast-item';
    item.el.innerHTML = '<span class="t-msg"></span><button class="t-undo"><span>Undo</span><kbd>Ctrl</kbd><kbd>Z</kbd></button><span class="t-timer"><i></i></span>';
    item.el.querySelector('.t-msg').textContent = message;
    item.el.querySelector('.t-timer i').style.animationDuration = `${duration}ms`;
    const undoButton = item.el.querySelector('.t-undo');
    undoButton.hidden = !undo;
    undoButton.addEventListener('click', () => {
        runUndo(item);
    });
    replaceVisibleToasts();
    root.appendChild(item.el);
    toasts.push(item);
    requestAnimationFrame(() => item.el?.classList.add('on'));
    item.timer = setTimeout(() => expire(item), duration);
    while (toasts.length > 8) dismiss(toasts[0]);
}

export function undoLatestToast() {
    for (let i = toasts.length - 1; i >= 0; i -= 1) {
        if (runUndo(toasts[i])) return true;
    }
    return false;
}

export function hideToast() {
    const item = [...toasts].reverse().find((toast) => toast.visible);
    if (item) dismiss(item);
}

export function initToast() {
    document.getElementById('toast').innerHTML = '';
}
