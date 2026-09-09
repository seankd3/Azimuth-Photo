// Days, said once: how a day is titled, and where each day starts in a
// list sorted by date. The grid's chapters, the timeline rail and the
// import stage all read these, so they cannot disagree about the words or
// the arithmetic.

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "Tue · Sep 8, 2026" for a YYYY-MM-DD day; the day itself when it will
// not parse; "Undated" for none.
export function title(day, { weekday = true } = {}) {
  if (!day) return 'Undated';
  const when = new Date(`${day}T12:00:00`);
  if (Number.isNaN(when.getTime())) return day;
  const date = `${MONTHS[when.getMonth()]} ${when.getDate()}, ${when.getFullYear()}`;
  return weekday ? `${WEEKDAYS[when.getDay()]} · ${date}` : date;
}

// Where each day starts in a grid sorted newest or oldest: a running sum
// of the counts. Empty when the counts do not add up to the total -- a
// sweep in flight, a stack opened in place -- so nothing draws a chapter
// at a wrong index.
export function chapters(days, sort, total) {
  const ordered = sort === 'oldest' ? [...days].reverse() : days;
  const out = [];
  let at = 0;
  for (const entry of ordered) {
    out.push({ index: at, day: entry.day, count: entry.count });
    at += entry.count;
  }
  return at === total ? out : [];
}
