export const meta = {
  name: 'elegance',
  description: 'Find duplicated rules and needless machinery, refute the weak findings, fix what survives',
  whenToUse: 'A refactoring pass over Azimuth for simplification and code quality. Run it repeatedly; it is designed to be a loop.',
  phases: [
    { title: 'Survey', detail: 'parallel scouts, one concern each' },
    { title: 'Refute', detail: 'try to kill each finding before believing it' },
    { title: 'Fix', detail: 'one worktree per surviving finding' },
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
        { label: `refute:${f.file.split('/').pop()}:${f.line}`, phase: 'Refute', schema: VERDICT, model: 'sonnet', effort: 'high' },
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
        // The strong model reads the whole round's diff once before it merges; per-finding
        // work runs on Sonnet at high effort, which is where the budget goes furthest.
        { label: `fix:${f.file.split('/').pop()}`, phase: 'Fix', schema: OUTCOME, isolation: 'worktree', model: 'sonnet', effort: 'high' },
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

// Each scout owns ONE question. They overlap deliberately: a rule written twice
// looks like duplication to one scout and a missing name to another.
const SCOUTS = [
  { key: 'repeated-rule', ask: `Find a decision this codebase makes in more than one place: the same SQL predicate, the same extension set, the same threshold, the same status vocabulary, the same guard, the same date parsing. Rank by how badly the copies could disagree, not by how many there are. Precedent from this repo: "status IN ('kept','maybe')" appeared ~170 times; six extension sets turned out to be three different questions. AGENTS.md's rule: one rule for whether a photo is in the library, in one place, used everywhere.` },
  { key: 'needless-machinery', ask: `Find abstraction that costs more than it earns: a wrapper that only forwards, a parameter nobody varies, a registry with one entry, a cache nobody reads, a knob with one value in the whole tree, a helper with one caller that would read better inline. Judge architectural weight by coupling: how many files must change together to add or repair one behaviour.` },
  { key: 'silent-failure', ask: `Find code that swallows a mistake instead of reporting it: .get(key, <plausible default>) on user input, a bare except that returns a neutral value, a fallback that makes a broken state look healthy, a guard that answers "no problem" when it was never wired up, a catch(() => {}) in the UI around a verb whose failure a person would want to know about.` },
  { key: 'naming-and-lies', ask: `Find names and words that lie or hide: a predicate whose name is the opposite of what it returns for some input, a docstring or comment describing behaviour the code no longer has, a literal repeated often enough to deserve a name, a doc row or MASTER_PLAN status the tree contradicts, a module whose contents left long ago.` },
  { key: 'js-shape', ask: `In web/static/v2/: find state kept in two places that can drift (a DOM attribute and a JS variable both saying the same thing), a render that rebuilds what did not change, an event handler bound more than once, a listener never removed on a surface that closes, a CSS rule that fights another for the same element. Precedent: a kit helper named count shadowed by a local string drew nothing; the linter refuses shadowing now.` },
  { key: 'python-shape', ask: `In web/*.py and web/model/: find a surface owning a table or holding state between calls (AGENTS.md forbids it: a feature is a query plus the decisions it writes), a loop in a surface that belongs to the worker, an import that crosses the layer rule, a function that reads the catalog twice for one answer, SQL built by string concatenation where a parameter would do.` },
]

const result = await round(SCOUTS, 6,
  `Behaviour-preserving means proven: for SQL, diff the emitted string or its query plan before and
after; for a projection or a verb, the existing tests plus one you add. Say in the commit what was
collapsed and into what sentence.`)
return result
