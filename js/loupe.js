import { PHOTOS } from "./data.js";

// Loupe: full-size image with real metadata, a histogram computed live from
// the pixels via canvas, and the app's actual key grammar (←/→, P/X/U, L).
export function initLoupe() {
  const widget = document.getElementById("loupe-widget");
  const img = document.getElementById("loupe-img");
  const els = Object.fromEntries(
    ["title", "pos", "elo", "elo-sub", "cmp", "flag", "file", "date", "cam", "lens"].map(
      (k) => [k, document.getElementById("loupe-" + k)]
    )
  );
  const hist = document.getElementById("loupe-hist");
  const flags = new Map(); // id -> 'pick' | 'reject'
  let idx = 0, lightsOut = false;

  function drawHistogram() {
    const c = document.createElement("canvas");
    const w = 160, h = Math.max(1, Math.round(160 / (PHOTOS[idx].ar || 1)));
    c.width = w; c.height = h;
    const x = c.getContext("2d", { willReadFrequently: true });
    try {
      x.drawImage(img, 0, 0, w, h);
      const d = x.getImageData(0, 0, w, h).data;
      const bins = new Float64Array(64);
      for (let i = 0; i < d.length; i += 4) {
        const lum = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
        bins[Math.min(63, lum >> 2)]++;
      }
      const max = Math.max(...bins);
      const g = hist.getContext("2d");
      g.clearRect(0, 0, hist.width, hist.height);
      const bw = hist.width / 64;
      for (let i = 0; i < 64; i++) {
        const bh = Math.pow(bins[i] / max, 0.5) * (hist.height - 4);
        g.fillStyle = "rgba(238,241,244,0.75)";
        g.fillRect(i * bw, hist.height - bh, bw - 1, bh);
      }
    } catch { /* canvas blocked (file://) — histogram simply stays empty */ }
  }

  function render() {
    const p = PHOTOS[idx];
    img.src = `assets/photos/lg/${p.id}.jpg`;
    els.title.textContent = `LOUPE — ${p.fn}`;
    els.pos.textContent = `${idx + 1} / ${PHOTOS.length} · ELO ${p.elo}`;
    els.elo.textContent = p.elo;
    const rank = PHOTOS.filter((q) => q.elo > p.elo).length + 1;
    els["elo-sub"].textContent = `#${rank} of ${PHOTOS.length} in scope`;
    els.cmp.textContent = p.cmp;
    els.file.textContent = p.fn;
    els.date.textContent = p.date || "—";
    els.cam.textContent = p.cam || "—";
    els.lens.textContent = p.lens || "—";
    const f = flags.get(p.id);
    els.flag.textContent = f === "pick" ? "● PICK" : f === "reject" ? "✕ REJECT" : "—";
    els.flag.className = "mono " + (f === "pick" ? "flag-pick" : f === "reject" ? "flag-reject" : "");
    img.onload = drawHistogram;
    if (img.complete && img.naturalWidth) drawHistogram();
  }

  function key(k) {
    const p = PHOTOS[idx];
    if (k === "ArrowLeft") { idx = (idx - 1 + PHOTOS.length) % PHOTOS.length; render(); }
    else if (k === "ArrowRight") { idx = (idx + 1) % PHOTOS.length; render(); }
    else if (k === "p") { flags.set(p.id, "pick"); render(); }
    else if (k === "x") { flags.set(p.id, "reject"); render(); }
    else if (k === "u") { flags.delete(p.id); render(); }
    else if (k === "l") { lightsOut = !lightsOut; widget.classList.toggle("lights-out", lightsOut); }
    else return false;
    return true;
  }

  document.getElementById("loupe-prev").onclick = () => key("ArrowLeft");
  document.getElementById("loupe-next").onclick = () => key("ArrowRight");
  for (const b of widget.querySelectorAll(".lp-keys button")) {
    b.onclick = () => key(b.dataset.k);
  }

  // Keys are live while the loupe is on screen — no focus hunting, no hijacked
  // scrolling elsewhere on the page.
  let onScreen = false;
  new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; }, { threshold: 0.35 })
    .observe(widget);
  addEventListener("keydown", (e) => {
    if (!onScreen || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^(input|textarea|select)$/i.test(document.activeElement?.tagName || "")) return;
    if (key(e.key.length === 1 ? e.key.toLowerCase() : e.key)) e.preventDefault();
  });

  render();
  return {
    show(id) {
      const i = PHOTOS.findIndex((p) => p.id === id);
      if (i >= 0) { idx = i; render(); }
      document.getElementById("loupe").scrollIntoView({ behavior: "smooth", block: "start" });
    },
  };
}
