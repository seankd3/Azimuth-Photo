// The icon set: a dozen 16px marks in one stroke weight, drawn here rather
// than borrowed from Unicode, so an album, a person, a stack and a camera
// read the same in the sidebar, the chips, the drop and the badges.

const PATHS = {
  stack: 'M3 5h8v8H3z M6 2h8v8',
  album: 'M2.5 3.5h11v9h-11z',
  smart: 'M8 2l1.4 4.6L14 8l-4.6 1.4L8 14l-1.4-4.6L2 8l4.6-1.4z',
  person: 'M8 8.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M2.5 14a5.5 5.5 0 0 1 11 0',
  folder: 'M2 4h4l1.5 1.5H14v7.5H2z',
  camera: 'M2 5h3l1-1.5h4L11 5h3v8H2z M8 11a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  calendar: 'M2.5 4h11v9.5h-11z M2.5 7h11 M5 2.5v3 M11 2.5v3',
  clock: 'M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z M8 5v3l2 1.5',
  recent: 'M3 8a5 5 0 1 0 1.5-3.5 M3 3v2.5h2.5 M8 5.5V8l2 1',
  landscape: 'M1.5 4.5h13v7h-13z',
  portrait: 'M4.5 1.5h7v13h-7z',
  drive: 'M2 9.5h12v3.5H2z M2 9.5L4 3h8l2 6.5 M11 11.5h1',
  label: 'M2 3h7l5 5-5.5 5.5L2 8z M5 6h.01',
  star: 'M8 2l1.8 3.9 4.2.5-3.1 2.9.8 4.2L8 11.4 4.3 13.5l.8-4.2L2 6.4l4.2-.5z',
};

export function icon(name, className = 'icon') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add(className, `icon-${name}`);
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', PATHS[name] || PATHS.album);
  svg.append(path);
  return svg;
}
