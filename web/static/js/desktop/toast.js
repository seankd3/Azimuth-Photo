let nextId = 1;
const toasts = [];

function dismiss(item) {
    const index = toasts.indexOf(item);
    if (index >= 0) toasts.splice(index, 1);
    clearTimeout(item.timer);
    item.el.classList.remove('on');
    item.el.addEventListener('transitionend', () => item.el.remove(), { once: true });
    setTimeout(() => item.el.remove(), 240);
}

export function showToast(message, { undo = null, duration = 8000 } = {}) {
    const root = document.getElementById('toast');
    if (!root) return;
    const item = {
        id: nextId++,
        undo,
        timer: null,
        el: document.createElement('div'),
    };
    item.el.className = 'toast-item';
    item.el.innerHTML = '<span class="t-msg"></span><button class="t-undo">Undo</button><span class="t-timer"><i></i></span>';
    item.el.querySelector('.t-msg').textContent = message;
    item.el.querySelector('.t-timer i').style.animationDuration = `${duration}ms`;
    const undoButton = item.el.querySelector('.t-undo');
    undoButton.hidden = !undo;
    undoButton.addEventListener('click', () => {
        const fn = item.undo;
        dismiss(item);
        if (fn) fn();
    });
    root.appendChild(item.el);
    toasts.push(item);
    requestAnimationFrame(() => item.el.classList.add('on'));
    item.timer = setTimeout(() => dismiss(item), duration);
    while (toasts.length > 3) dismiss(toasts[0]);
}

export function hideToast() {
    const item = toasts[toasts.length - 1];
    if (item) dismiss(item);
}

export function initToast() {
    document.getElementById('toast').innerHTML = '';
}
