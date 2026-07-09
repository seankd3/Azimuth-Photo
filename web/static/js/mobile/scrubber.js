// Right-edge fast scrubber. Snaps to months from the whole-archive
// histogram, so a drag can jump anywhere in 47k photos instantly.

import { on } from './state.js';
import { tick } from './haptics.js';
import { jumpToMonth, monthForFraction, monthLabel, scrollInfo, zoomLevel } from './timeline.js';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

export function initScrubber() {
    const scrub = document.getElementById('m-scrub');
    const bubble = document.getElementById('scrub-bubble');
    const handle = scrub.querySelector('.ms-handle');

    let hideTimer = null;
    let dragging = false;
    let pending = null;
    let lastKey = null;
    let jumpTimer = null;

    function positionHandle() {
        const { pane } = scrollInfo();
        if (!pane) return;
        const denom = pane.scrollHeight - pane.clientHeight;
        const frac = denom > 0 ? pane.scrollTop / denom : 0;
        handle.style.top = `${(frac * Math.max(0, scrub.clientHeight - 48)).toFixed(1)}px`;
    }

    function show() {
        const { pane, hasMonths } = scrollInfo();
        if (!pane || zoomLevel() === 2 || !hasMonths) return;
        if (pane.scrollHeight > pane.clientHeight * 1.4) {
            scrub.classList.add('avail');
            positionHandle();
            clearTimeout(hideTimer);
            hideTimer = setTimeout(() => {
                if (!dragging) scrub.classList.remove('avail');
            }, 1500);
        }
    }

    function scrubTo(clientY) {
        const rect = scrub.getBoundingClientRect();
        const frac = clamp((clientY - rect.top) / Math.max(1, rect.height), 0, 0.999);
        const month = monthForFraction(frac);
        if (!month) return;
        if (month.key !== lastKey) {
            lastKey = month.key;
            tick(8);
            // Debounce the actual jump so a fast drag doesn't fetch every
            // month it passes — only where the finger settles.
            clearTimeout(jumpTimer);
            jumpTimer = setTimeout(() => jumpToMonth(month.key), 140);
        }
        bubble.textContent = monthLabel(month.key);
        bubble.style.top = `${clientY}px`;
        bubble.classList.add('on');
        positionHandle();
    }

    scrub.addEventListener('pointerdown', (e) => {
        dragging = true;
        lastKey = null;
        scrub.classList.add('dragging');
        scrub.setPointerCapture(e.pointerId);
        scrubTo(e.clientY);
        e.preventDefault();
    });
    scrub.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        if (pending == null) {
            requestAnimationFrame(() => {
                const y = pending;
                pending = null;
                if (y != null && dragging) scrubTo(y);
            });
        }
        pending = e.clientY;
    });
    const end = () => {
        dragging = false;
        scrub.classList.remove('dragging');
        bubble.classList.remove('on');
        show();
    };
    scrub.addEventListener('pointerup', end);
    scrub.addEventListener('pointercancel', end);

    on('timeline-scroll', show);
    on('histogram', show);
}
