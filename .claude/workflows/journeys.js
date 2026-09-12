export const meta = {
  name: 'journeys',
  description: "Walk each of a person's afternoons through the app, find every wait, wonder and dead end, refute the weak findings, fix what survives",
  whenToUse: 'The journeys round: first run, a cull, a Rank sitting, finding one photograph, organising, the second sitting, discoverability, when things go wrong. The bar is Notion and Factorio. Run it repeatedly.',
  phases: [
    { title: 'Survey', detail: 'one scout per journey, walked in the harness' },
    { title: 'Refute', detail: 'try to kill each finding before believing it' },
    { title: 'Fix', detail: 'one worktree per surviving finding, harness-proven' },
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

const BAR = `Walk this journey as a person would, in the harness (write probes, run node
scripts/harness_proof.mjs, read the PROBE lines and the captures) and, where the engine is the
question, with a Library on the development library. Count the steps, the waits, the moments of
wondering what to do, the dead ends, the acts that lose state. The bar is what the owner named
(09-12): "notion/factorio levels of polish and QoL UX". Concretely: an act is one key or one
click; its effect is instant and visible; everything is remembered across a close and an open;
everything is undoable and the undo says what it undid; every control teaches its key; no state
is a dead end without a way on; the person is never asked what the app already knows. A finding
is one moment on the journey that misses that bar, with the file and line that decides it and
the smallest change that meets it. Not a feature. Not a redesign.`

const JOURNEYS = [
  { key: 'first-run', ask: `${BAR}\n\nJourney: the first afternoon. Open the app with an empty home, choose where the catalog lives, attach a folder, watch the sweep, see the first tiles arrive, make a first pick. Where does the person wait, and is the wait told; where do they wonder what to do next.` },
  { key: 'the-cull', ask: `${BAR}\n\nJourney: a day's cull. A hundred photographs from one day: pick and reject through the grid and the loupe by keyboard alone, undo a mistake, compare two, see Best settle. Count keystrokes; every act that takes two where one would do; every place the cursor or the scroll is lost.` },
  { key: 'the-rank-sitting', ask: `${BAR}\n\nJourney: a Rank sitting. Open Rank on a folder, change the size, play twenty rounds by keyboard, undo one, press Done, look at how Best changed. What is said between rounds, how quickly the next set paints, whether the sitting is remembered.` },
  { key: 'finding-one', ask: `${BAR}\n\nJourney: finding one photograph. From everything to one: by a word, by a month on the timeline, by folder, by person, by label, by a saved search. Count the steps from ten thousand to one, and every dead end (an empty result with no way on, a filter that cannot be cleared where it was set).` },
  { key: 'organising', ask: `${BAR}\n\nJourney: organising. Make an album, add to it from the grid by drag and by key, rename it, remove from it, delete it, undo that; label a set; the sidebar counts follow every act. Where the person is asked to confirm what undo already covers.` },
  { key: 'the-second-sitting', ask: `${BAR}\n\nJourney: the second sitting. Close and reopen. Does the app come back where the person was: view, folder, sort, grid size, selection, scroll position, Rank size and mode, the loupe's zoom, the sidebar's folds. List every setting the app has and whether a close forgets it (kit/remembered.js is the mechanism).` },
  { key: 'discoverability', ask: `${BAR}\n\nJourney: learning the app. For every action the UI can do (the bridge table in web/static/v2/net/index.js, the key map, the command mode), how would a person learn it exists: a tooltip carrying its key, the ? map, the > command mode. Diff the set of actions against what ? and the tooltips teach; every action nothing teaches is a finding.` },
  { key: 'when-things-go-wrong', ask: `${BAR}\n\nJourney: when things go wrong. A drive that is away, a file that is gone, a file that will not decode, a sweep interrupted by a close, a home folder that is read-only. What the person is told, in what words, and whether they can carry on with everything else meanwhile.` },
]

const result = await round(JOURNEYS, 8,
  `A UI change is proven in the harness: write or adapt a probe under a temporary path (not in
scripts/journeys unless it is a real task no probe covers), run node scripts/harness_proof.mjs,
and paste the PROBE line and the capture path. The built document is what the window opens, so a
change under web/static/v2/ is complete on its own; do not commit build/. Remembered state goes
through kit/remembered.js, never a new store.`)
return result
