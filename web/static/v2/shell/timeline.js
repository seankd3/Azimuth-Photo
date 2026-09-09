// The timeline: a rail at the grid's right edge on date-sorted views. Years
// and months sit where their photographs are -- by index, so the rail is the
// library's own proportions, not a calendar's -- a marker rides with what is
// on screen, and a press or a drag jumps there with the day under the pointer
// named. It is drawn from the same day list the chapters are, so the two can
// never disagree about where a month starts.

import { MONTHS, chapters as chaptersIn, title } from '../kit/days.js';

// Below this many photographs the grid is shorter than the rail would be.
const WORTH_IT = 40;

function element(tag, className, text = '') {
  const node = document.createElement(tag);
  node.className = className;
  if (text) node.textContent = text;
  return node;
}

const chaptersOf = (state) => chaptersIn(state.days, state.sort, state.total);
const named = (day) => title(day, { weekday: false });

export function createTimeline({ workspace, before, top, place, indexAt, scrollTo }) {
  const rail = element('div', 'timeline');
  rail.hidden = true;
  const track = element('div', 'timeline-track');
  const marker = element('div', 'timeline-marker');
  const label = element('div', 'timeline-label');
  label.hidden = true;
  rail.append(track, marker, label);
  workspace.insertBefore(rail, before);

  let chapters = [];
  let total = 0;
  let height = 0;
  let built = '';
  let idle = null;
  // The rail is a scroll control: it takes the keyboard like one.
  track.tabIndex = 0;
  track.setAttribute('role', 'slider');
  track.setAttribute('aria-label', 'Timeline');

  const y = (index) => (total ? (index / total) * height : 0);
  const at = (offsetY) => Math.max(0, Math.min(total - 1, Math.floor((offsetY / height) * total)));
  const dayAt = (index) => {
    let found = chapters[0];
    for (const chapter of chapters) {
      if (chapter.index > index) break;
      found = chapter;
    }
    return found ? found.day : '';
  };

  function draw() {
    track.replaceChildren();
    let year = '';
    let month = '';
    let lastMonthY = -Infinity;
    let lastYearY = -Infinity;
    const months = [];
    for (const chapter of chapters) {
      if (!chapter.day) continue;
      const top = y(chapter.index);
      const thisYear = chapter.day.slice(0, 4);
      const thisMonth = chapter.day.slice(0, 7);
      if (thisYear !== year) {
        year = thisYear;
        if (top - lastYearY >= 14) {
          const mark = element('div', 'timeline-year', year);
          mark.style.top = `${top}px`;
          track.append(mark);
          lastYearY = top;
        }
      }
      if (thisMonth !== month) {
        month = thisMonth;
        months.push({ top, name: MONTHS[Number(thisMonth.slice(5, 7)) - 1] });
      }
    }
    // Months get a tick when there is room for one and a name when there is
    // room for that; a rail with every label on top of the next says nothing.
    for (const entry of months) {
      if (entry.top - lastMonthY < 8) continue;
      const tick = element('div', 'timeline-month');
      tick.style.top = `${entry.top}px`;
      if (entry.top - lastMonthY >= 26) tick.textContent = entry.name;
      track.append(tick);
      lastMonthY = entry.top;
    }
    // The far end says where time runs to, dimmer, when the last year mark
    // is not already near it.
    const last = chapters.filter((c) => c.day).at(-1);
    if (last && height - lastYearY > 30) {
      const end = element('div', 'timeline-year is-end', last.day.slice(0, 4));
      end.style.top = `${height - 8}px`;
      track.append(end);
    }
  }

  function follow() {
    if (rail.hidden || !total) return;
    const index = indexAt(workspace.scrollTop);
    if (index === null) return;
    marker.style.top = `${y(index)}px`;
    track.setAttribute('aria-valuenow', String(index));
    track.setAttribute('aria-valuetext', named(dayAt(index)));
  }

  // The keyboard walks chapters: arrows a day, Page keys a year, Home and
  // End the ends. The same place() and scrollTo() the pointer uses.
  function jump(index) {
    const cell = place(Math.max(0, Math.min(total - 1, index)));
    if (cell) scrollTo(cell.top);
  }
  track.addEventListener('keydown', (event) => {
    if (!chapters.length) return;
    const current = indexAt(workspace.scrollTop) ?? 0;
    let k = 0;
    for (let i = 0; i < chapters.length; i += 1) if (chapters[i].index <= current) k = i;
    const yearOf = (i) => (chapters[i].day || '').slice(0, 4);
    let next = null;
    if (event.key === 'ArrowDown') next = chapters[Math.min(chapters.length - 1, k + 1)].index;
    else if (event.key === 'ArrowUp') next = chapters[Math.max(0, k - 1)].index;
    else if (event.key === 'PageDown') {
      let j = k;
      while (j < chapters.length - 1 && yearOf(j) === yearOf(k)) j += 1;
      next = chapters[j].index;
    } else if (event.key === 'PageUp') {
      let j = k;
      while (j > 0 && yearOf(j - 1) === yearOf(k)) j -= 1;
      next = chapters[Math.max(0, j === k ? j - 1 : j)].index;
    } else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = total - 1;
    if (next === null) return;
    jump(next);
    event.preventDefault();
  });

  function render(state) {
    const dated = state.view === 'library'
      && !state.query && !(state.like || []).length
      && (state.sort === 'newest' || state.sort === 'oldest')
      && (state.days || []).length > 1
      && state.total >= WORTH_IT;
    // While the day counts and the total disagree (a sweep in flight, a
    // stack opened in place) there is nothing true to draw, so nothing is.
    const found = dated ? chaptersOf(state) : [];
    const on = found.length > 0;
    rail.hidden = !on;
    if (!on) {
      built = '';
      return;
    }
    height = Math.max(0, workspace.clientHeight - top);
    const key = `${state.sort}:${state.total}:${state.days.length}:${height}`;
    if (key !== built) {
      built = key;
      total = state.total;
      chapters = found;
      track.style.height = `${height}px`;
      rail.title = state.sort === 'oldest' ? 'Oldest at the top' : 'Newest at the top';
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', String(Math.max(0, total - 1)));
      draw();
    }
    follow();
  }

  function say(offsetY) {
    const index = at(offsetY);
    label.textContent = named(dayAt(index));
    label.style.top = `${Math.max(10, Math.min(height - 10, offsetY))}px`;
    label.hidden = false;
    return index;
  }

  let pressed = false;
  track.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    pressed = true;
    track.setPointerCapture(event.pointerId);
    const index = say(event.offsetY);
    const cell = place(index);
    if (cell) scrollTo(cell.top);
    event.preventDefault();
  });
  track.addEventListener('pointermove', (event) => {
    const offsetY = Math.max(0, Math.min(height, event.clientY - track.getBoundingClientRect().top));
    const index = say(offsetY);
    clearTimeout(idle);
    if (!pressed) {
      // A hand that comes to rest on the rail is not asking; the date goes.
      idle = setTimeout(() => { label.hidden = true; }, 1500);
      return;
    }
    const cell = place(index);
    if (cell) scrollTo(cell.top);
  });
  const release = () => {
    pressed = false;
    label.hidden = true;
  };
  track.addEventListener('pointerup', release);
  track.addEventListener('pointercancel', release);
  track.addEventListener('pointerleave', () => { clearTimeout(idle); label.hidden = true; });

  return { render, follow };
}
