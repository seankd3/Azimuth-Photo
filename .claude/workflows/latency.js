export const meta = {
  name: 'latency',
  description: 'Trace each click-to-paint path and find what makes it wait',
  whenToUse: 'Enforcing the speed doctrine mechanically: nothing a person asked for should wait on the network, a lock, or an empty buffer.',
  phases: [
    { title: 'Trace', detail: 'one agent per interactive path' },
    { title: 'Prove', detail: 'measure the stall rather than assert it' },
  ],
}

const HOUSE = `
Azimuth Photo, branch "simplify". The standing doctrine: this is meant to be the
fastest photo app ever made, the laptop never waits on the hub, and any lag in
ranking makes it feel unusable.

You are tracing INTERACTIVE paths — the ones where a person has already acted and
is waiting for a result. Background work is out of scope; it is allowed to be slow.

The two defects that motivated this workflow:
  - Refine's replacement buffer was three flat constants (24/12/8) while one pick
    consumes a whole wave, so a 4x3 grid held two picks of headroom and its refill
    trigger sat below a single pick. Rapid ranking drained it and then every pick
    waited on a network round trip.
  - Develop held a per-image lock across a hub fetch measured at 7-69 seconds, so
    every other request for that photo queued behind it.

Look for, on a path a person is waiting on:
  - awaiting the network, a lock, or a subprocess before the first paint
  - a buffer or prefetch queue that can empty faster than it refills
  - a fixed delay (setTimeout, sleep, animation hold) longer than its purpose
  - work proportional to library size where it could be proportional to what is shown
  - a cache checked after the expensive call rather than before
  - a sequential loop of requests that could be one request or run concurrently
  - an await inside a loop over visible items

Measure, do not assert. Boot the app:
  cd web && AZIMUTH_HOME="$TEMP/lat" AZIMUTH_MODE=standalone ./.venv/Scripts/python.exe -m uvicorn app:app --port 8032
and time the routes with curl -w "%{time_total}". Call each route TWICE and report
both: the first call includes cache warming and is not the interactive number.
This machine runs other software, so compare timings back to back, never across runs.
Do not edit source.
`

const STALL = {
  type: 'object',
  required: ['stalls'],
  properties: {
    stalls: {
      type: 'array',
      items: {
        type: 'object',
        required: ['path', 'file', 'line', 'what_waits', 'when_it_bites', 'fix'],
        properties: {
          path: { type: 'string', description: 'the user action, e.g. "click a card in Refine"' },
          file: { type: 'string' },
          line: { type: 'integer' },
          what_waits: { type: 'string' },
          when_it_bites: { type: 'string', description: 'the condition — cold cache, rapid input, large library, hub absent' },
          measured: { type: 'string', description: 'numbers if you took any' },
          fix: { type: 'string' },
          severity: { type: 'string', enum: ['always', 'common', 'edge'] },
        },
      },
    },
  },
}

const PROOF = {
  type: 'object',
  required: ['reproduced', 'evidence'],
  properties: {
    reproduced: { type: 'boolean' },
    evidence: { type: 'string', description: 'commands run and timings, both calls' },
    worth_fixing: { type: 'boolean' },
  },
}

const PATHS = [
  { key: 'grid', ask: 'Opening the app and painting the first grid; scrolling it; changing scope from the sidebar. Includes thumb serving and the rankings query.' },
  { key: 'refine', ask: 'Opening Refine, clicking a card, holding down rapid picks, shuffling, undo. Follow the replacement buffer and the pick save queue.' },
  { key: 'loupe', ask: 'Opening Loupe from the grid, arrowing between photos, zooming. Follow tier upgrades and preloading.' },
  { key: 'develop', ask: 'Opening Develop on a RAW, moving a slider, moving between photos. Follow base cache, locks, and any hub read-through.' },
  { key: 'search', ask: 'Typing in the omnibox, committing a search, opening a person or a map marker.' },
]

phase('Trace')
const traced = await parallel(
  PATHS.map((p) => () =>
    agent(
      `${HOUSE}\n\nPath: ${p.key}.\n${p.ask}\n\n` +
        `Follow it from the event handler to the first paint. Report at most 4 stalls, worst first. ` +
        `A path that never waits is a good answer — say so.`,
      { label: `trace:${p.key}`, phase: 'Trace', schema: STALL },
    ),
  ),
)

const stalls = traced.filter(Boolean).flatMap((r) => r.stalls || [])
const serious = stalls.filter((s) => s.severity !== 'edge')
log(`${stalls.length} stalls reported, ${serious.length} above edge cases`)
if (!serious.length) return { stalls: [], note: 'no interactive path was found waiting' }

phase('Prove')
const proven = await parallel(
  serious.slice(0, 8).map((s) => () =>
    agent(
      `${HOUSE}\n\nReproduce this stall and measure it. Do not take it on faith.\n\n` +
        `Path: ${s.path}\nStalls on: ${s.what_waits}\nBites when: ${s.when_it_bites}\n` +
        `At: ${s.file}:${s.line}\n\n` +
        `Show the commands and both timings. If it does not reproduce, or the wait is off the ` +
        `interactive path, or it is smaller than it looked, say reproduced=false and give the numbers ` +
        `that say so. A stall of a few milliseconds is not worth fixing; say worth_fixing=false.`,
      { label: `prove:${s.path.slice(0, 24)}`, phase: 'Prove', schema: PROOF },
    ).then((v) => ({ ...s, verdict: v })),
  ),
)

const real = proven.filter(Boolean).filter((s) => s.verdict?.reproduced && s.verdict?.worth_fixing)
return {
  traced: stalls.length,
  confirmed: real.map((s) => ({ path: s.path, waits_on: s.what_waits, bites: s.when_it_bites, evidence: s.verdict.evidence, fix: s.fix })),
  dismissed: proven.filter(Boolean).length - real.length,
}
