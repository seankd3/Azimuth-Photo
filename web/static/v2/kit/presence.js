// Whether a photograph can be shown, said once. The grid cell, the loupe's
// note and the inspector's Where row all read this, so the words for a
// drive that is away, a file that cannot be read, a picture still being
// made, or a row no drive holds are the same words everywhere.

const SAID = {
  here: 'Here',
  pending: 'Preparing this photograph…',
  unshowable: 'This photograph cannot be shown.',
  away: 'On a drive that is away',
  missing: 'Missing — no drive holds it',
};

// `shown` says whether a picture is in hand: with one, the photograph is
// simply here; without one, the state says why not.
export function presence(photo, shown) {
  const state = !photo.placed && photo.placed !== undefined ? 'missing'
    : shown ? 'here'
      : photo.tile_failed || photo.loupe_failed ? 'unshowable'
        : photo.reachable ? 'pending' : 'away';
  return { state, said: SAID[state] };
}
