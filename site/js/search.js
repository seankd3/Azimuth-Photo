import { PHOTOS } from "./data.js";
import { SEARCHES } from "./search-data.js";

// Search: rankings captured from the live archive's /api/search fusion —
// pick a query, the frames reorder to what the real engines returned.
export function initSearch() {
  const chipsEl = document.getElementById("search-chips");
  const grid = document.getElementById("search-grid");
  const meta = document.getElementById("search-meta");
  const src = document.getElementById("search-src");
  const byId = new Map(PHOTOS.map((p) => [p.id, p]));

  function show(q) {
    const s = SEARCHES.find((s) => s.q === q);
    for (const el of chipsEl.children) el.classList.toggle("on", el.textContent === `“${q}”`);
    grid.innerHTML = "";
    const hits = s.ids.map((id, i) => ({ p: byId.get(id), rank: i + 1 })).filter((h) => h.p);
    hits.forEach(({ p, rank }, i) => {
      const cell = document.createElement("div");
      cell.className = "search-cell";
      cell.style.animationDelay = `${i * 45}ms`;
      cell.innerHTML =
        `<img src="assets/photos/sm/${p.id}.jpg" alt="${p.fn}" decoding="async">` +
        `<span class="cell-rank mono">${String(rank).padStart(2, "0")}</span>`;
      grid.appendChild(cell);
    });
    meta.textContent = `“${q.toUpperCase()}” — ${hits.length} HITS IN SCOPE · ${s.ms}ms LIVE`;
    src.textContent = `ENGINES: ${s.sources.join(" + ").toUpperCase()}`;
  }

  for (const s of SEARCHES) {
    const b = document.createElement("button");
    b.className = "chip mono";
    b.textContent = `“${s.q}”`;
    b.onclick = () => show(s.q);
    chipsEl.appendChild(b);
  }
  show(SEARCHES[0].q);
}
