// F means fullscreen. Everywhere, in every lens, with nothing else bound to it.
//
// Fullscreen is a property of the window, not of a lens, so this deliberately
// does not ask what is on screen. Whatever surface you are in fills the display:
// the mosaic in Refine, the photo in Loupe, the canvas in Develop.

export function fullscreenActive() {
    return Boolean(document.fullscreenElement);
}

export function toggleFullscreen() {
    if (fullscreenActive()) {
        document.exitFullscreen?.();
        return false;
    }
    // Firefox and Safari answer with a rejected promise rather than throwing,
    // and a browser that refuses fullscreen is not an error worth surfacing —
    // the keypress simply does nothing.
    document.documentElement.requestFullscreen?.().catch(() => {});
    return true;
}
