import { PHOTOS } from "./data.js";

// Justified-rows layout over real aspect ratios, with composable filters —
// the same grammar as the app: every facet ANDs, counts answer instantly.
const FILTERS = [
  { key: "all", label: "ALL", test: () => true },
  { key: "2022", label: "2022", group: "year", test: (p) => p.date.startsWith("2022") },
  { key: "2025", label: "2025+", group: "year", test: (p) => p.date >= "2025" },
  { key: "land", label: "LANDSCAPE", group: "orient", test: (p) => p.ar >= 1 },
  { key: "port", label: "PORTRAIT", group: "orient", test: (p) => p.ar < 1 },
  { key: "top", label: "ELO 1400+", group: "elo", test: (p) => p.elo >= 1400 },
];

export function initGrid({ onOpen }) {
  const grid = document.getElementById("lib-grid");
  const bar = document.getElementById("grid-filters");
  const count = document.getElementById("grid-count");
  const active = new Map(); // group -> filter key

  for (const f of FILTERS) {
    const b = document.createElement("button");
    b.className = "chip mono" + (f.key === "all" ? " on" : "");
    b.textContent = f.label;
    b.onclick = () => {
      if (f.key === "all") active.clear();
      else if (active.get(f.group) === f.key) active.delete(f.group);
      else active.set(f.group, f.key);
      for (const el of bar.children) el.classList.remove("on");
      if (active.size === 0) bar.children[0].classList.add("on");
      else for (const [, k] of active) [...bar.children].find((el) => el.dataset.k === k)?.classList.add("on");
      render();
    };
    b.dataset.k = f.key;
    bar.appendChild(b);
  }

  function visible() {
    const tests = [...active.entries()].map(([g, k]) => FILTERS.find((f) => f.key === k).test);
    return PHOTOS.filter((p) => tests.every((t) => t(p)));
  }

  function render() {
    const t0 = performance.now();
    const photos = visible();
    grid.innerHTML = "";
    const W = grid.clientWidth || 1100;
    const GAP = 4, TARGET = 168;
    let row = [], rowAr = 0;
    const flush = (last) => {
      if (!row.length) return;
      let h = (W - GAP * (row.length - 1)) / rowAr;
      if (last && h > TARGET * 1.25) h = TARGET;
      const div = document.createElement("div");
      div.className = "lib-row";
      div.style.height = Math.round(h) + "px";
      for (const p of row) {
        const cell = document.createElement("button");
        cell.className = "lib-cell";
        cell.style.width = Math.round(h * p.ar) + "px";
        cell.innerHTML =
          `<img src="assets/photos/sm/${p.id}.jpg" alt="${p.fn}" loading="lazy">` +
          `<span class="cell-elo mono">${p.elo}</span>`;
        cell.onclick = () => onOpen(p.id);
        div.appendChild(cell);
      }
      grid.appendChild(div);
      row = []; rowAr = 0;
    };
    for (const p of photos) {
      row.push(p); rowAr += p.ar;
      if (rowAr * TARGET >= W - GAP * (row.length - 1)) flush(false);
    }
    flush(true);
    const ms = Math.max(1, Math.round(performance.now() - t0));
    const elos = photos.map((p) => p.elo);
    count.textContent = photos.length
      ? `${photos.length} FRAMES · ELO ${Math.min(...elos)}–${Math.max(...elos)} · ${ms}ms`
      : "0 FRAMES — FILTERS TOO NARROW";
  }

  render();
  let raf;
  addEventListener("resize", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(render); });
}
