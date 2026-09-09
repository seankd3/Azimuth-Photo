// The timeline: a rail at the grid's right edge on date-sorted views. Years
// and months sit where their photographs are -- by index, so the rail is the
// library's own proportions, not a calendar's -- a marker rides with what is
// on screen, and a press or a drag jumps there with the day under the pointer
// named. It is drawn from the same day list the chapters are, so the two can
// never disagree about where a month starts.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// Below this many photographs the grid is shorter than the rail would be.
const WORTH_IT = 40;

function element(tag, className, text = '') {
  const node = document.createElement(tag);
  node.className = className;
  if (text) node.textContent = text;
  return node;
}

function chaptersOf(state) {
  // The day list arrives newest first; the oldest sort reads it backwards.
  // Either way a running sum of the counts is each day's first index.
  const days = state.sort === 'oldest' ? [...state.days].reverse() : state.days;
  const chapters = [];
  let at = 0;
  for (const entry of days) {
    chapters.push({ index: at, day: entry.day });
    at += entry.count;
  }
  return at === state.total ? chapters : [];
}

function named(day) {
  if (!day) return 'Undated';
  const when = new Date(`${day}T12:00:00`);
  return Number.isNaN(when.getTime()) ? day
    : `${MONTHS[when.getMonth()]} ${when.getDate()}, ${when.getFullYear()}`;
}

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
        month = thisMonth;
        if (top - lastYearY >= 14) {
          const mark = element('div', 'timeline-year', year);
          mark.style.top = `${top}px`;
          track.append(mark);
          lastYearY = top;
        }
        continue;
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
  }

  function follow() {
    if (rail.hidden || !total) return;
    const index = indexAt(workspace.scrollTop);
    if (index === null) return;
    marker.style.top = `${y(index)}px`;
  }

  function render(state) {
    const on = state.view === 'library'
      && !state.query && !(state.like || []).length
      && (state.sort === 'newest' || state.sort === 'oldest')
      && (state.days || []).length > 1
      && state.total >= WORTH_IT;
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
      chapters = chaptersOf(state);
      track.style.height = `${height}px`;
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
    if (!pressed) return;
    const cell = place(index);
    if (cell) scrollTo(cell.top);
  });
  const release = () => {
    pressed = false;
    label.hidden = true;
  };
  track.addEventListener('pointerup', release);
  track.addEventListener('pointercancel', release);
  track.addEventListener('pointerleave', () => { if (!pressed) label.hidden = true; });

  return { render, follow };
}
