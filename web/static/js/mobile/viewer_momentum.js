// Small, frame-driven inertial pan helper for the mobile viewer. Keeping this
// separate from gesture recognition makes the gesture grammar easier to tune.


export function createMomentum({ read, write, constrain }) {
    const samples = [];
    let frame = null;

    function stop() {
        if (frame != null) cancelAnimationFrame(frame);
        frame = null;
        samples.length = 0;
    }

    function record(x, y, at = performance.now()) {
        samples.push({ x, y, at });
        while (samples.length > 5 || (samples.length > 1 && at - samples[0].at > 110)) samples.shift();
    }

    function release() {
        if (samples.length < 2) return;
        const first = samples[0];
        const last = samples[samples.length - 1];
        const elapsed = Math.max(1, last.at - first.at);
        let vx = (last.x - first.x) / elapsed;
        let vy = (last.y - first.y) / elapsed;
        samples.length = 0;
        if (Math.hypot(vx, vy) < 0.18) return;

        let previous = performance.now();
        const glide = (now) => {
            const dt = Math.min(32, now - previous);
            previous = now;
            const point = read();
            const next = constrain(point.x + vx * dt, point.y + vy * dt);
            write(next.x, next.y);
            const hitX = Math.abs(next.x - (point.x + vx * dt)) > 0.5;
            const hitY = Math.abs(next.y - (point.y + vy * dt)) > 0.5;
            vx *= Math.pow(0.93, dt / 16.7);
            vy *= Math.pow(0.93, dt / 16.7);
            if ((Math.abs(vx) < 0.015 && Math.abs(vy) < 0.015) || (hitX && hitY)) {
                frame = null;
                return;
            }
            frame = requestAnimationFrame(glide);
        };
        frame = requestAnimationFrame(glide);
    }

    return { record, release, stop };
}
