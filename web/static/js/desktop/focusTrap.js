const stack = [];

function focusables(container) {
    return [...container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
        .filter((el) => !el.disabled && !el.closest('[hidden]') && el.offsetParent !== null);
}

export function trapFocus(container, initial = null) {
    if (!container) return;
    const existingIndex = stack.findIndex((entry) => entry.container === container);
    let returnEl = document.activeElement;
    if (existingIndex >= 0) {
        const existing = stack.splice(existingIndex, 1)[0];
        if (existing.returnEl && document.contains(existing.returnEl)) returnEl = existing.returnEl;
    }
    stack.push({ container, returnEl });
    let tries = 15;
    const attempt = () => {
        if (!stack.length || stack[stack.length - 1].container !== container) return;
        const target = initial || focusables(container)[0] || container;
        if (target && target.focus) target.focus({ preventScroll: true });
        if (document.activeElement !== target && --tries > 0) requestAnimationFrame(attempt);
    };
    attempt();
}

export function releaseFocus(container) {
    if (!container) return;
    for (let i = stack.length - 1; i >= 0; i -= 1) {
        if (stack[i].container === container) {
            const { returnEl } = stack.splice(i, 1)[0];
            if (i === stack.length && returnEl && document.contains(returnEl) && returnEl.focus) {
                returnEl.focus({ preventScroll: true });
            }
            return;
        }
    }
}

document.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || !stack.length) return;
    const container = stack[stack.length - 1].container;
    const items = focusables(container);
    if (!items.length) {
        event.preventDefault();
        return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (!container.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
});
