export function focusLoupe({
    documentImpl = document,
} = {}) {
    const wrap = documentImpl.getElementById('loupe-image-wrap');
    if (wrap) wrap.focus({ preventScroll: true });
}


export function loupeFocusableElements(loupe) {
    return Array.from(loupe.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter((el) => !el.disabled && !el.closest('.hidden'));
}


export function trapLoupeFocus(event, {
    documentImpl = document,
    focusLoupeImpl = focusLoupe,
} = {}) {
    const loupe = documentImpl.getElementById('loupe');
    if (!loupe || loupe.classList.contains('hidden')) return false;
    const focusable = loupeFocusableElements(loupe);
    if (!focusable.length) {
        event.preventDefault();
        focusLoupeImpl({ documentImpl });
        return true;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!loupe.contains(documentImpl.activeElement)) {
        event.preventDefault();
        first.focus({ preventScroll: true });
        return true;
    }
    if (event.shiftKey && documentImpl.activeElement === first) {
        event.preventDefault();
        last.focus({ preventScroll: true });
        return true;
    }
    if (!event.shiftKey && documentImpl.activeElement === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
        return true;
    }
    return false;
}
