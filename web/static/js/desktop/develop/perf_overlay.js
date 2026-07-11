const ENABLED = new URLSearchParams(window.location.search).get('debug') === 'perf';
const MAX_SAMPLES = 240;
const samples = [];
let pending = null;
let overlay = null;

function percentile(values, ratio) {
    if (!values.length) return 0;
    const ordered = [...values].sort((a, b) => a - b);
    return ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * ratio) - 1)];
}

function format(value) {
    return `${value.toFixed(1)} ms`;
}

function renderOverlay() {
    if (!overlay) return;
    const p50 = percentile(samples, .5);
    const p95 = percentile(samples, .95);
    const last = samples.at(-1) || 0;
    overlay.innerHTML = `<strong>Develop latency</strong>
        <span><i>p50</i><b>${format(p50)}</b></span>
        <span><i>p95</i><b>${format(p95)}</b></span>
        <span><i>last</i><b>${format(last)}</b></span>
        <small>${samples.length} slider→frame samples · 2048 budget: 16 ms</small>`;
    overlay.dataset.overBudget = p95 > 16 ? 'true' : 'false';
}

function mountOverlay() {
    if (!ENABLED || overlay || !document.body) return;
    overlay = document.createElement('aside');
    overlay.id = 'develop-perf-overlay';
    overlay.setAttribute('aria-label', 'Develop performance');
    overlay.style.cssText = 'position:fixed;z-index:10000;right:14px;bottom:14px;width:218px;padding:11px 12px;border:1px solid rgba(117,151,179,.42);border-radius:7px;color:#e8eef4;background:rgba(12,15,19,.94);box-shadow:0 10px 32px rgba(0,0,0,.38);font:11px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;backdrop-filter:blur(12px);pointer-events:none';
    const style = document.createElement('style');
    style.textContent = '#develop-perf-overlay strong{display:block;margin-bottom:7px;color:#fff;font:650 11px/1.2 system-ui,sans-serif;letter-spacing:.02em}#develop-perf-overlay span{display:flex;justify-content:space-between;margin-top:3px}#develop-perf-overlay i{color:#8d99a6;font-style:normal}#develop-perf-overlay b{font-weight:600;font-variant-numeric:tabular-nums}#develop-perf-overlay small{display:block;margin-top:7px;color:#74808c;font-size:9px}#develop-perf-overlay[data-over-budget=true] span:nth-of-type(2) b{color:#ffb45f}';
    document.head.append(style);
    document.body.append(overlay);
    renderOverlay();
}

export function markSettingsChange(detail = '') {
    if (!ENABLED) return;
    const name = 'develop-settings-change';
    performance.clearMarks(name);
    performance.mark(name, { detail });
    pending = { name, detail };
}

export function markFrameDone() {
    if (!ENABLED || !pending) return;
    const end = 'develop-frame-done';
    performance.clearMarks(end);
    performance.mark(end, { detail: pending.detail });
    performance.clearMeasures('develop-slider-to-frame');
    const measure = performance.measure('develop-slider-to-frame', pending.name, end);
    samples.push(measure.duration);
    if (samples.length > MAX_SAMPLES) samples.splice(0, samples.length - MAX_SAMPLES);
    pending = null;
    renderOverlay();
}

export function perfStats() {
    return {
        count: samples.length,
        last: samples.at(-1) || 0,
        p50: percentile(samples, .5),
        p95: percentile(samples, .95),
    };
}

if (ENABLED) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountOverlay, { once: true });
    else mountOverlay();
    window.__developPerf = { stats: perfStats };
}
