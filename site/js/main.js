import { initGrid } from "./grid.js";
import { initLoupe } from "./loupe.js";
import { initRefine } from "./refine.js";
import { initSearch } from "./search.js";

// Compass tick rings — cheap SVG, GPU-rotated
for (const host of document.querySelectorAll(".compass")) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 1000 1000");
  let d = "";
  for (let i = 0; i < 72; i++) {
    const a = (i * 5 * Math.PI) / 180;
    const major = i % 6 === 0;
    const r1 = major ? 291 : 299, r2 = 308;
    d += `M${500 + r1 * Math.cos(a)} ${500 + r1 * Math.sin(a)}L${500 + r2 * Math.cos(a)} ${500 + r2 * Math.sin(a)}`;
  }
  const ticks = document.createElementNS(NS, "path");
  ticks.setAttribute("d", d);
  ticks.setAttribute("stroke", "rgba(238,241,244,0.16)");
  ticks.setAttribute("stroke-width", "1");
  const ring = document.createElementNS(NS, "circle");
  ring.setAttribute("cx", "500"); ring.setAttribute("cy", "500"); ring.setAttribute("r", "330");
  ring.setAttribute("fill", "none");
  ring.setAttribute("stroke", "rgba(238,241,244,0.05)");
  svg.append(ring, ticks);
  host.appendChild(svg);
}

// Scroll reveals
const io = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    }
  },
  { threshold: 0.1, rootMargin: "0px 0px -30px 0px" }
);
document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

// Widgets
const loupe = initLoupe();
initGrid({ onOpen: loupe.show });
initRefine();
initSearch();
