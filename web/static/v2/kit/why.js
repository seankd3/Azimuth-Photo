// What a failure says. The bridge raises in its own words -- lower case,
// no full stop, a field name here and there -- and the toast must not
// repeat them. The phrases the product is known to raise are mapped to a
// sentence a photographer can act on; anything else is made a sentence.

const KNOWN = [
  [/desktop window is unavailable/i, 'The window is not ready yet — try again in a moment.'],
  [/a page contains between/i, 'That is more than the library shows at once.'],
  [/no such sort/i, 'That order is not one the library knows.'],
  [/database is locked/i, 'The library is busy writing — try again in a moment.'],
  [/permission denied|access is denied/i, 'That file or folder cannot be written to.'],
  [/no such file|cannot find the (file|path)/i, 'That file is not where the library last saw it.'],
];

export function why(error, doing = '') {
  const raw = String(error?.message || error || '').trim();
  for (const [pattern, said] of KNOWN) if (pattern.test(raw)) return said;
  if (!raw) return doing ? `${doing} did not finish.` : 'That did not finish.';
  const sentence = raw[0].toUpperCase() + raw.slice(1);
  return /[.!?…]$/.test(sentence) ? sentence : `${sentence}.`;
}
