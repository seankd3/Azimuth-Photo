export const meta = {
  name: 'latency',
  description: 'Trace each click-to-paint path and find what makes it wait; measure, then fix what is real',
  whenToUse: 'Enforcing the speed covenant mechanically: nothing a person asked for waits on a lock, a decode, a scan, or an empty buffer.',
  phases: [
    { title: 'Survey', detail: 'one agent per interactive path' },
    { title: 'Refute', detail: 'measure the stall rather than assert it' },
    { title: 'Fix', detail: 'one worktree per real stall, numbers before and after' },
  ],
}
const HOUSE = `
You are working in Azimuth Photo: a Windows-first desktop photo app, one Python 3.12
process (web/desktop.py opens a pywebview window on one bundled document and hands it
the verbs; web/boot.py is the product boundary, Library / OwnedLibrary; web/model/ the
five tables), a vanilla-JS UI in web/static/v2/ bundled by esbuild, SQLite. AGENTS.md is
the contract; read it first. docs/FINDINGS.md is the ledger of what past rounds found and
how they closed; docs/ui-architecture.md is the UI canon (the bar, the speed covenant,
the interaction canon, the vocabulary); docs/REWRITE_LEDGER.md says what is Proven.

You are in a Linux cloud container: 4 CPUs, no GPU, no display. The window cannot open.
What you have instead, all from the repository root unless said otherwise:
  ./scripts/azimuth-check            lint, gates, 280+ tests, node specs, ledger, the build; ~60 s
  ./scripts/azimuth-check --quick    lint and gates only
  ~/Azimuth Test/Photos              the development library: 31 public-domain photographs
                                     and one duplicate in Edits / Raws/Digital / Snapshots
                                     (built by scripts/make_test_library.py; PROVENANCE.md inside)
  a Library on it, from web/:        import boot, work; p = boot.Library(catalog, tiles);
                                     d = p.attach(root); p.refresh(d["uuid"]); p.browse();
                                     work.step(p.conn, kinds) runs the worker one step.
                                     web/test_core.py has every idiom. Always an isolated home
                                     under /tmp; never a real catalog.
  node scripts/harness_proof.mjs out.png --probe a.js [--probe b.js]
                                     the UI headless: build/harness.html (the whole UI on a fake
                                     bridge with 124 photographs, in scripts/harness.py's STUB) in
                                     headless Chromium; each probe is JavaScript evaluated in the
                                     page, printed as a PROBE line; a screenshot at the end. Shape
                                     and behaviour, never pixels. scripts/journeys/*.js are probes.
  scripts/bench.py <catalog.db>      times the product boundary's verbs on a copy of a catalog.
  the bridge seam                    web/static/v2/net/index.js (every call the UI makes) ->
                                     web/desktop.py (the verbs) -> web/boot.py (the Library).

Rules that cost this project real time to learn:
1. "Nothing calls it" is evidence about clients, not intent. Before calling anything dead,
   also check scripts/, .github/workflows/, .claude/, docs/, MASTER_PLAN.md's open rows, and
   the bridge table in web/static/v2/net/index.js.
2. Edit by hand. Never regex-sweep the codebase.
3. Behaviour-preserving means proven: a test that fails before and passes after, or a
   measurement before and after, pasted. Not asserted.
4. No test over 10 s (pytest.ini enforces it). Every file added under web/ or scripts/ needs a
   row in docs/REWRITE_LEDGER.md or scripts/rewrite_status.py --check fails the build.
5. Stage explicit paths, never git add -A. Commit messages are plain prose: what changed, why,
   what was measured.
6. Vocabulary is fixed: Rank, Best, Picked / Rejected, Albums, Labels, People, Folders,
   Drives, Trash, photographs. One aria word per element: a button says aria-pressed, an
   option aria-selected, the keyboard's cursor aria-current.
7. Quality over features. This round changes nothing a person would call a new feature.
`

const WORKTREE = `
You are in a fresh git worktree on its own branch. It lacks web/.venv and node_modules (both
ignored). Before any check run, in the worktree root:
  export AZIMUTH_VENV=/home/user/Azimuth-Photo/web/.venv
  ln -s /home/user/Azimuth-Photo/node_modules node_modules
Then ./scripts/azimuth-check must be green before you commit; paste its last lines. For a UI
change, also run node scripts/harness_proof.mjs with a probe that shows the behaviour you
changed, and paste the PROBE line. Commit on the branch you are on. Report the commit hash
(git rev-parse HEAD) and the exact check output. Do not push. If the change turns out wrong,
or needs a product decision, stop with applied=false and say why: that is a good outcome.
Elegance, not bloat: smallness is a consequence of the change having the shape of the problem;
delete what the fix makes unnecessary in the same commit, add no machinery, and prefer the
change that leaves fewer lines and fewer concepts. Say in the commit what was removed.
`

const FINDING = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'file', 'line', 'why', 'fix', 'risk'],
        properties: {
          title: { type: 'string', description: 'one line, the claim itself' },
          file: { type: 'string' },
          line: { type: 'integer' },
          evidence: { type: 'string', description: 'what you observed: a PROBE line, a measurement, other file:line sites' },
          why: { type: 'string', description: 'what goes wrong for a photographer using the app' },
          fix: { type: 'string', description: 'the concrete change, in one sentence' },
          risk: { type: 'string', enum: ['low', 'medium', 'high'] },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['refuted', 'reason'],
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
  },
}

const OUTCOME = {
  type: 'object',
  required: ['applied', 'summary'],
  properties: {
    applied: { type: 'boolean' },
    summary: { type: 'string', description: 'what changed, in plain prose; or why not' },
    commit: { type: 'string', description: 'git rev-parse HEAD after the commit' },
    branch: { type: 'string' },
    checks: { type: 'string', description: 'the exact tail of ./scripts/azimuth-check and any PROBE lines' },
    ledger_row: { type: 'string', description: 'one FINDINGS.md table row in the file\'s voice: | id | finding | fix | status |, id left blank' },
  },
}

// One round stays small enough to read and to land by one hand; the loop is
// what gets coverage. Safest first: an unattended edit should be cheap to be wrong.
const RANK = { low: 0, medium: 1, high: 2 }

async function round(scouts, perRound, fixExtra) {
  phase('Survey')
  const surveyed = await parallel(
    scouts.map((scout) => () =>
      agent(
        `${HOUSE}\n\nYou are the "${scout.key}" scout.\n\n${scout.ask}\n\n` +
          `Read the code and, where the harness or a Library can show it, observe rather than infer. ` +
          `Return at most 4 findings, the strongest first. A finding with no concrete fix is not a ` +
          `finding; a finding already closed in docs/FINDINGS.md is not a finding. Do not edit any file.`,
        // A scout only proposes; every proposal is refuted before anything changes, so
        // the survey runs on the cheaper model and the refuters and fixers on the strong one.
        { label: `survey:${scout.key}`, phase: 'Survey', schema: FINDING, model: 'sonnet' },
      ),
    ),
  )
  const found = surveyed.filter(Boolean).flatMap((r, i) => (r.findings || []).map((f) => ({ ...f, scout: scouts[i].key })))
  const seen = new Set()
  const unique = found.filter((f) => { const k = `${f.file}:${f.line}`; if (seen.has(k)) return false; seen.add(k); return true })
  const ordered = unique.sort((a, b) => (RANK[a.risk] ?? 3) - (RANK[b.risk] ?? 3))
  const queue = ordered.slice(0, perRound)
  const deferred = ordered.slice(perRound)
  log(`${found.length} findings, ${unique.length} distinct, taking ${queue.length}`)
  if (deferred.length) log(`deferred: ${deferred.map((f) => f.title).join(' | ')}`)
  if (!queue.length) return { found: 0, landed: [], refuted: [], held: [], deferred: [] }

  const results = await pipeline(
    queue,
    (f) =>
      agent(
        `${HOUSE}\n\nTry to REFUTE this claim. Default to refuted=true when uncertain.\n\n` +
          `Claim: ${f.title}\nAt: ${f.file}:${f.line}\nEvidence given: ${f.evidence || 'none'}\n` +
          `Why it matters: ${f.why}\nProposed fix: ${f.fix}\n\n` +
          `Refute it if: the behaviour is already as the canon asks; the fix would change something a ` +
          `person relies on; the claim rests on a misreading; docs/FINDINGS.md already closed or rejected ` +
          `it with a reason; or the cost outweighs what a photographer would notice. Read the code; run ` +
          `the harness or a Library if that settles it. Do not edit any file.`,
        { label: `refute:${f.file.split('/').pop()}:${f.line}`, phase: 'Refute', schema: VERDICT, model: 'opus', effort: 'high' },
      ).then((v) => ({ finding: f, verdict: v })),
    (checked) => {
      if (!checked || !checked.verdict || checked.verdict.refuted) return checked
      const f = checked.finding
      return agent(
        `${HOUSE}\n${WORKTREE}\n\nMake this change. It survived an adversarial review.\n\n` +
          `${f.title}\nAt: ${f.file}:${f.line}\nFix: ${f.fix}\nWhy: ${f.why}\n\n${fixExtra}\n\n` +
          `Add a refuter when one can exist (a test that fails before and passes after), keep the change ` +
          `to what the finding needs, and write the FINDINGS.md row for it in your answer rather than ` +
          `editing that file.`,
        // Scouts run on Sonnet: breadth. Refuters and fixers run on Opus at high effort:
        // judgment, and the change has to stay elegant. The strong model reads the whole
        // round's diff once before it merges (the owner, 09-12: "can we use opus sub agents").
        { label: `fix:${f.file.split('/').pop()}`, phase: 'Fix', schema: OUTCOME, isolation: 'worktree', model: 'opus', effort: 'high' },
      ).then((out) => ({ ...checked, outcome: out }))
    },
  )
  const done = results.filter(Boolean)
  return {
    found: found.length,
    distinct: unique.length,
    landed: done.filter((r) => r.outcome && r.outcome.applied).map((r) => ({ title: r.finding.title, file: r.finding.file, commit: r.outcome.commit, branch: r.outcome.branch, summary: r.outcome.summary, checks: r.outcome.checks, ledger_row: r.outcome.ledger_row })),
    refuted: done.filter((r) => r.verdict && r.verdict.refuted).map((r) => ({ title: r.finding.title, reason: r.verdict.reason })),
    held: done.filter((r) => r.outcome && !r.outcome.applied).map((r) => ({ title: r.finding.title, why: r.outcome.summary })),
    deferred: deferred.map((f) => ({ title: f.title, file: f.file, line: f.line, fix: f.fix, risk: f.risk })),
  }
}

const SPEED = `The speed covenant (docs/ui-architecture.md): a keystroke in the cull loop paints in the same
frame, under 50 ms, never past 100; next photograph in the loupe paints something under 100 ms;
a scope switch keeps the last answer until the next lands; a search answers under 1 s; the grid at
150k is virtualized; one bridge call per act; work proportional to what is shown, never to the
library. You are tracing INTERACTIVE paths: a person has acted and is waiting. Background work is
out of scope. Follow a path from the event handler in web/static/v2/ through net/index.js to the
verb in web/desktop.py and the Library in web/boot.py to the first paint. Look for: awaiting a lock,
a decode or a scan before the first paint; a buffer that empties faster than it refills; a fixed
delay longer than its purpose; work proportional to library size; a cache checked after the
expensive call; a sequential loop of bridge calls that could be one; an await inside a loop over
visible items; a query without the index it needs (EXPLAIN QUERY PLAN says SCAN). Measure: a
Library on the fixture for correctness, and for scale a catalog with 150,000 rows -- copy the
fixture's catalog and insert synthetic image rows following web/model/schema.sql, then
scripts/bench.py on it. Report both cold and warm numbers; compare back to back on this machine.`

const PATHS = [
  { key: 'grid-paint-scroll', ask: `${SPEED}\n\nPath: opening the app to the first grid; scrolling it; the page cache (kit/page-cache.js) and virtual grid; changing scope from the sidebar; the days and folders answers.` },
  { key: 'cull-loop', ask: `${SPEED}\n\nPath: the cull loop: P, X, R, U, arrows, a turn, a stack; what each keystroke awaits before the tile shows its answer; undo.` },
  { key: 'loupe-and-tiles', ask: `${SPEED}\n\nPath: opening the loupe, arrowing, zoom; the tile store and the worker's on-screen-first order (work.py look/step), what the window waits on for a rendition.` },
  { key: 'rank-and-search', ask: `${SPEED}\n\nPath: a Rank round (rank.py, the draws), search as you type (search.py, the facets), saving a search.` },
  { key: 'sidebar-and-facets', ask: `${SPEED}\n\nPath: the folder tree, facets, people and albums answers (boot.folders, facets_of, the refacet on the sweep lane), and the pulse: what a sweep or a cull makes the window re-read.` },
  { key: 'import-stage', ask: `${SPEED}\n\nPath: staging a card (intake.scan on the intake lane) and bringing photographs in (intake.bring): what the stage waits on before the first row shows, thumbs, the count.` },
]

const result = await round(PATHS, 6,
  `A performance change ships with its number: paste the measurement before and after, from the same
run of scripts/bench.py or the same timing script, on the same catalog. If the number did not move,
applied=false.`)
return result
