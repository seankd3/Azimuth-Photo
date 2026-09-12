// What the window keeps between launches: a sort, a density, which panels
// are folded. One place to read and write it, so every remembered thing is
// spelled the same way and a browser with no storage costs nothing.
export function recall(key, fallback) {
  try {
    const held = localStorage.getItem(key);
    return held === null ? fallback : JSON.parse(held);
  } catch {
    return fallback;
  }
}

export function remember(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Nothing to do: the next launch starts from the fallback.
  }
}
