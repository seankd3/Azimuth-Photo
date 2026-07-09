const QUERY = '(prefers-reduced-motion: reduce)';

let media = null;

function query() {
    if (typeof window === 'undefined' || !window.matchMedia) return null;
    if (!media) media = window.matchMedia(QUERY);
    return media;
}

export function prefersReducedMotion() {
    if (typeof document !== 'undefined' && document.documentElement?.dataset?.motion === 'reduced') {
        return true;
    }
    return Boolean(query()?.matches);
}

export function motionMs(token = 'base') {
    if (prefersReducedMotion()) return 0;
    if (token === 'fast') return 120;
    if (token === 'slow') return 240;
    return 180;
}

export function afterMotion(token, fn) {
    const wait = motionMs(token);
    if (wait <= 0) {
        fn();
        return 0;
    }
    return window.setTimeout(fn, wait);
}

export function syncMotionPreference() {
    const root = document.documentElement;
    if (!root) return;
    if (query()?.matches) root.dataset.motion = 'reduced';
    else if (root.dataset.motion === 'reduced') delete root.dataset.motion;
}

export function initMotion() {
    syncMotionPreference();
    const mq = query();
    if (!mq) return;
    const onChange = () => syncMotionPreference();
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
}
