import { PHOTOS } from "./data.js";

// Refine: the real Elo update (K=24), session-local. Click the best frame —
// the winner takes rating from every frame it beat, a fresh contender enters.
const K = 24;
const TILES = 6;

export function initRefine() {
  const mosaic = document.getElementById("refine-mosaic");
  const stats = document.getElementById("refine-stats");
  const list = document.getElementById("refine-list");

  // Session copy of the catalog — starts from the real ratings.
  const session = PHOTOS.map((p) => ({ ...p, sElo: p.elo, sCmp: 0 }));
  const byId = new Map(session.map((p) => [p.id, p]));
  let picks = 0;

  // Rotating queue: least-compared first, like the Explore strategy.
  let queue = [...session].sort((a, b) => a.sCmp - b.sCmp || Math.random() - 0.5);
  let shown = queue.splice(0, TILES);

  function expected(ra, rb) { return 1 / (1 + Math.pow(10, (rb - ra) / 400)); }

  function pick(winnerId) {
    const w = byId.get(winnerId);
    for (const t of shown) {
      if (t.id === winnerId) continue;
      const ew = expected(w.sElo, t.sElo);
      const delta = K * (1 - ew);
      w.sElo += delta; t.sElo -= delta;
      w.sCmp++; t.sCmp++;
      t.lastDelta = -delta;
    }
    w.lastDelta = shown.reduce((s, t) => (t.id === winnerId ? s : s + K * (1 - expected(w.sElo, t.sElo))), 0);
    picks++;

    // Winner leaves the mosaic, a fresh contender enters; the beaten stay.
    const slot = shown.findIndex((t) => t.id === winnerId);
    if (!queue.length) queue = [...session].filter((p) => !shown.includes(p)).sort(() => Math.random() - 0.5);
    shown[slot] = queue.shift();
    render(winnerId, slot);
  }

  function render(winnerId, newSlot) {
    mosaic.innerHTML = "";
    shown.forEach((p, i) => {
      const cell = document.createElement("button");
      cell.className = "refine-cell" + (i === newSlot && winnerId ? " entering" : "");
      cell.style.setProperty("--ar", p.ar);
      const d = p.lastDelta;
      cell.innerHTML =
        `<img src="assets/photos/sm/${p.id}.jpg" alt="${p.fn}" decoding="async">` +
        `<span class="cell-elo mono">${Math.round(p.sElo)}` +
        (d && Math.abs(d) >= 1 ? `<i class="${d > 0 ? "up" : "dn"}">${d > 0 ? "+" : ""}${Math.round(d)}</i>` : "") +
        `</span>`;
      cell.onclick = () => pick(p.id);
      mosaic.appendChild(cell);
    });
    stats.textContent = `${picks} PICKS THIS SESSION · ${picks * (TILES - 1)} COMPARISONS RESOLVED`;
    renderBoard();
  }

  function renderBoard() {
    const top = [...session].sort((a, b) => b.sElo - a.sElo).slice(0, 8);
    list.innerHTML = "";
    top.forEach((p, i) => {
      const li = document.createElement("li");
      const moved = Math.round(p.sElo - p.elo);
      li.innerHTML =
        `<span class="rb-rank mono">${String(i + 1).padStart(2, "0")}</span>` +
        `<img src="assets/photos/sm/${p.id}.jpg" alt="">` +
        `<span class="rb-name mono">${p.fn}</span>` +
        `<span class="rb-elo mono">${Math.round(p.sElo)}` +
        (moved ? ` <i class="${moved > 0 ? "up" : "dn"}">${moved > 0 ? "▲" : "▼"}${Math.abs(moved)}</i>` : "") +
        `</span>`;
      list.appendChild(li);
    });
  }

  render();
}
