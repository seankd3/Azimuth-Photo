/* ============================================================================
   AZIMUTH · FIELD LOG — interactions & teaching demos
   All data here is real: commit counts per month, measured benchmarks, and the
   film-halation algorithm is a faithful port of web/features/develop/film.py.
   ============================================================================ */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- real data ------------------------------------------------------- */
  const COMMITS_BY_MONTH = [
    ["2023-08", 8], ["2023-09", 1], ["2023-10", 0], ["2024-08", 3],
    ["2026-02", 2], ["2026-04", 222], ["2026-05", 161], ["2026-06", 32], ["2026-07", 1133],
  ];

  /* ---- scroll progress + top bearing ----------------------------------- */
  const railFill = $(".scrollrail > i");
  const bearingEl = $("#bearing");
  function onScroll() {
    const h = document.documentElement;
    const max = h.scrollHeight - h.clientHeight;
    const p = max > 0 ? h.scrollTop / max : 0;
    if (railFill) railFill.style.width = (p * 100).toFixed(2) + "%";
    if (bearingEl) bearingEl.textContent = String(Math.round(p * 359)).padStart(3, "0") + "°";
  }
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ---- reveal on scroll ------------------------------------------------- */
  const io = new IntersectionObserver((es) => {
    es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
  $$(".rv").forEach((el) => io.observe(el));

  /* ---- rail active section --------------------------------------------- */
  const railLinks = $$(".rail a[href^='#']");
  const linkFor = {};
  railLinks.forEach((a) => (linkFor[a.getAttribute("href").slice(1)] = a));
  const secIO = new IntersectionObserver((es) => {
    es.forEach((e) => {
      const a = linkFor[e.target.id];
      if (!a) return;
      if (e.isIntersecting) { railLinks.forEach((l) => l.classList.remove("active")); a.classList.add("active"); }
    });
  }, { rootMargin: "-45% 0px -50% 0px" });
  $$("section[id]").forEach((s) => secIO.observe(s));

  /* ---- commit-density sparkline ---------------------------------------- */
  function drawSpark(canvas) {
    if (!canvas) return;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const W = canvas.clientWidth, H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    const g = canvas.getContext("2d"); g.scale(dpr, dpr);
    const vals = COMMITS_BY_MONTH.map((d) => d[1]);
    const max = Math.max.apply(null, vals);
    const n = vals.length, bw = W / n;
    vals.forEach((v, i) => {
      const bh = Math.max(2, (Math.sqrt(v) / Math.sqrt(max)) * (H - 6));
      const x = i * bw, y = H - bh;
      const grad = g.createLinearGradient(0, y, 0, H);
      grad.addColorStop(0, i === n - 1 ? "#4c9bff" : "#f2b03d");
      grad.addColorStop(1, i === n - 1 ? "#2f6fd0" : "#b57f1f");
      g.fillStyle = grad;
      g.globalAlpha = v === 0 ? 0.18 : 1;
      g.fillRect(x + 1, y, Math.max(2, bw - 2), bh);
    });
  }
  $$(".spark canvas").forEach(drawSpark);
  addEventListener("resize", () => $$(".spark canvas").forEach(drawSpark));

  /* ---- ELO pairing demo ------------------------------------------------- */
  const PHOTOS = (window.AZ_DEMO_PHOTOS || []);
  function initElo(root) {
    if (!root || !PHOTOS.length) return;
    const state = PHOTOS.map((src, i) => ({ src, elo: 1200, comp: 0, id: i }));
    let comparisons = 0, propagated = 0;
    const cardEls = $$(".elo .card", root);
    let pair = [0, 1];

    function expected(a, b) { return 1 / (1 + Math.pow(10, (b - a) / 400)); }
    function pickPair() {
      // bias toward the current top (the 2023 pairing idea)
      const sorted = state.slice().sort((a, b) => b.elo - a.elo);
      const top = sorted.slice(0, Math.max(3, Math.ceil(sorted.length / 2)));
      const a = top[Math.floor(Math.random() * top.length)];
      let b; do { b = state[Math.floor(Math.random() * state.length)]; } while (b.id === a.id);
      pair = [a.id, b.id];
    }
    function render(flash) {
      cardEls.forEach((el, i) => {
        const s = state[pair[i]];
        el.querySelector("img").src = s.src;
        el.querySelector(".rt").textContent = Math.round(s.elo);
        const d = el.querySelector(".delta");
        d.className = "delta" + (flash ? (flash[i] > 0 ? " win" : " lose") : "");
        d.textContent = flash ? (flash[i] > 0 ? "+" : "") + Math.round(flash[i]) : "";
      });
      $("#elo-comp", root).textContent = comparisons;
      $("#elo-prop", root).textContent = propagated;
      const best = state.slice().sort((a, b) => b.elo - a.elo)[0];
      $("#elo-top", root).textContent = Math.round(best.elo);
    }
    function pick(winIdx) {
      const w = state[pair[winIdx]], l = state[pair[1 - winIdx]];
      const K = Math.abs(w.elo - l.elo) < 100 ? 32 : 16;  // adaptive K, 2023 logic
      const ew = expected(w.elo, l.elo);
      const dw = K * (1 - ew), dl = -K * (1 - ew);
      w.elo += dw; l.elo += dl; w.comp++; l.comp++;
      comparisons++;
      // "propagation": nudge a couple of neighbours (embedding idea, faked as ± id-adjacency)
      const nudged = new Set();
      [[w, +1], [l, -1]].forEach(([node, sign]) => {
        state.forEach((o) => {
          if (o.id === node.id || nudged.has(o.id)) return;
          const sim = 1 - Math.abs(o.id - node.id) / PHOTOS.length; // stand-in for cosine
          if (sim > 0.75) { const t = (sim - 0.75) / 0.25; o.elo += sign * K * 0.3 * t * t * t; nudged.add(o.id); }
        });
      });
      propagated += nudged.size;
      const flash = winIdx === 0 ? [dw, dl] : [dl, dw];
      render(flash);
      setTimeout(() => { pickPair(); render(null); }, 620);
    }
    cardEls.forEach((el, i) => {
      el.tabIndex = 0;
      el.addEventListener("click", () => pick(i));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(i); } });
    });
    const reset = $("#elo-reset", root);
    if (reset) reset.addEventListener("click", () => { state.forEach((s) => { s.elo = 1200; s.comp = 0; }); comparisons = 0; propagated = 0; pickPair(); render(null); });
    pickPair(); render(null);
  }
  $$(".elo-demo").forEach(initElo);

  /* ---- HALATION canvas — faithful to film.py --------------------------- */
  // Bright scene light over threshold is blurred and bled back into the RED
  // layer (and a fraction into green): the CineStill 800T glow "emerges".
  function initHalation(root) {
    const cv = $("canvas", root); if (!cv) return;
    const amtEl = $("#hal-amt", root), radEl = $("#hal-rad", root);
    const amtOut = $("#hal-amt-o", root), radOut = $("#hal-rad-o", root);
    let showOn = true;
    const W = 720, H = 320;
    // internal resolution == pixel grid; putImageData ignores ctx transforms,
    // so we render at native W×H and let CSS scale the element up.
    cv.width = W; cv.height = H; cv.style.aspectRatio = W + "/" + H;
    const g = cv.getContext("2d");

    // build a synthetic night frame: dark ground + bright practical lights
    const base = new Float32Array(W * H * 3);
    function seedScene() {
      for (let i = 0; i < W * H * 3; i++) base[i] = 0.02 + Math.random() * 0.015;
      // a horizon band
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 3; const horizon = Math.exp(-Math.pow((y - H * 0.62) / 26, 2)) * 0.06;
        base[i] += horizon * 0.3; base[i + 1] += horizon * 0.35; base[i + 2] += horizon * 0.55;
      }
      // bright lights (floodlights / a sign)
      const lights = [[150, 120, 16, 1.9], [300, 150, 9, 2.4], [470, 110, 22, 1.6], [560, 175, 7, 2.6], [230, 200, 6, 2.1], [400, 205, 5, 1.8]];
      lights.forEach(([cx, cy, r, inten]) => {
        for (let y = cy - r * 3; y <= cy + r * 3; y++) for (let x = cx - r * 3; x <= cx + r * 3; x++) {
          if (x < 0 || y < 0 || x >= W || y >= H) continue;
          const d2 = (x - cx) ** 2 + (y - cy) ** 2; const f = Math.exp(-d2 / (2 * r * r)) * inten;
          const i = (y * W + x) * 3;
          base[i] += f * 1.0; base[i + 1] += f * 0.96; base[i + 2] += f * 0.85;
        }
      });
    }
    seedScene();

    // separable gaussian blur of a single-channel field
    function blur(field, sigma) {
      const rad = Math.max(1, Math.round(sigma * 2)); const k = [];
      let sum = 0; for (let i = -rad; i <= rad; i++) { const v = Math.exp(-(i * i) / (2 * sigma * sigma)); k.push(v); sum += v; }
      for (let i = 0; i < k.length; i++) k[i] /= sum;
      const tmp = new Float32Array(W * H), out = new Float32Array(W * H);
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        let a = 0; for (let t = -rad; t <= rad; t++) { const xx = Math.min(W - 1, Math.max(0, x + t)); a += field[y * W + xx] * k[t + rad]; }
        tmp[y * W + x] = a;
      }
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        let a = 0; for (let t = -rad; t <= rad; t++) { const yy = Math.min(H - 1, Math.max(0, y + t)); a += tmp[yy * W + x] * k[t + rad]; }
        out[y * W + x] = a;
      }
      return out;
    }

    function draw() {
      const amount = showOn ? parseFloat(amtEl.value) : 0;
      const sigma = parseFloat(radEl.value);
      amtOut.textContent = amount.toFixed(2); radOut.textContent = sigma.toFixed(0) + "px";
      // luma over threshold (film.py: excess = max(luma - threshold, 0))
      const thr = 0.85; const excess = new Float32Array(W * H);
      for (let p = 0; p < W * H; p++) {
        const i = p * 3; const luma = 0.2126 * base[i] + 0.7152 * base[i + 1] + 0.0722 * base[i + 2];
        excess[p] = Math.max(luma - thr, 0);
      }
      const glow = amount > 0 ? blur(excess, sigma) : null;
      const img = g.createImageData(W, H); const d = img.data;
      for (let p = 0; p < W * H; p++) {
        const i = p * 3; let r = base[i], gr = base[i + 1], b = base[i + 2];
        if (glow) { r += amount * glow[p]; gr += amount * 0.28 * glow[p]; }  // red + a little green
        // simple filmic-ish tone so highlights don't just clip white
        const tm = (v) => v / (v + 0.9);
        d[p * 4] = Math.min(255, tm(r) * 255 * 1.15);
        d[p * 4 + 1] = Math.min(255, tm(gr) * 255 * 1.05);
        d[p * 4 + 2] = Math.min(255, tm(b) * 255);
        d[p * 4 + 3] = 255;
      }
      g.putImageData(img, 0, 0);
    }
    [amtEl, radEl].forEach((el) => el && el.addEventListener("input", draw));
    $$(".toggle button", root).forEach((b) => b.addEventListener("click", () => {
      showOn = b.dataset.on === "1";
      $$(".toggle button", root).forEach((x) => x.classList.toggle("on", x === b));
      draw();
    }));
    draw();
  }
  $$(".hala-demo").forEach(initHalation);

  /* ---- benchmark race (animate on reveal) ------------------------------ */
  function initRace(root) {
    const raceIO = new IntersectionObserver((es) => {
      es.forEach((e) => {
        if (!e.isIntersecting) return;
        $$(".bar.before", root).forEach((b) => (b.style.width = b.dataset.w + "%"));
        setTimeout(() => $$(".bar.after", root).forEach((a) => (a.style.width = a.dataset.w + "%")), 300);
        raceIO.disconnect();
      });
    }, { threshold: 0.25 });
    raceIO.observe(root);
  }
  $$(".race").forEach(initRace);

  /* ---- twin parity demo ------------------------------------------------ */
  // Same "exposure + tone" op, computed two ways (a stand-in "GL" fast path and
  // a "numpy" reference), landing on identical pixels. Slider drives exposure.
  function initTwin(root) {
    const glc = $("#twin-gl", root), npc = $("#twin-np", root), ev = $("#twin-ev", root);
    const evo = $("#twin-ev-o", root), diffo = $("#twin-diff", root);
    if (!glc) return;
    const W = 320, H = 180;
    [glc, npc].forEach((c) => { c.width = W; c.height = H; c.style.aspectRatio = W + "/" + H; });
    // a smooth test gradient scene
    const scene = new Float32Array(W * H * 3);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 3; const gx = x / W, gy = y / H;
      scene[i] = 0.15 + 0.7 * gx; scene[i + 1] = 0.2 + 0.6 * gy; scene[i + 2] = 0.25 + 0.5 * (1 - gx) * gy;
    }
    const TONE_GAMMA = 2.2; // shared PARITY_TABLE constant
    // "GL" path: float math (mimics shader)
    const opGL = (v, ev) => Math.pow(Math.min(1, v * Math.pow(2, ev)), 1 / TONE_GAMMA);
    // "numpy" path: same math, quantise identically
    const opNP = (v, ev) => Math.pow(Math.min(1, v * Math.pow(2, ev)), 1 / TONE_GAMMA);
    function render() {
      const e = parseFloat(ev.value); evo.textContent = (e >= 0 ? "+" : "") + e.toFixed(2) + " EV";
      const drawTo = (cv, op) => {
        const g = cv.getContext("2d"); const img = g.createImageData(W, H); const d = img.data;
        for (let p = 0; p < W * H; p++) { const i = p * 3;
          d[p * 4] = op(scene[i], e) * 255; d[p * 4 + 1] = op(scene[i + 1], e) * 255; d[p * 4 + 2] = op(scene[i + 2], e) * 255; d[p * 4 + 3] = 255; }
        g.putImageData(img, 0, 0); return d;
      };
      const a = drawTo(glc, opGL), b = drawTo(npc, opNP);
      let maxd = 0; for (let i = 0; i < a.length; i++) maxd = Math.max(maxd, Math.abs(a[i] - b[i]));
      diffo.textContent = maxd.toFixed(2) + " / 255";
    }
    ev.addEventListener("input", render); render();
  }
  $$(".twin-demo").forEach(initTwin);
})();
