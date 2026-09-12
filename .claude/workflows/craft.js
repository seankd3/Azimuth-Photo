export const meta = {
  name: 'craft',
  description: 'Find the small details that make an app feel loved, one family of detail per scout; refute what would be noise; fix what survives',
  whenToUse: 'The craft round: feedback for every act, settling and motion, rhythm and type, the voice, hints that retire, the moments worth a quiet flourish, restraint. Run it after journeys.',
  phases: [
    { title: 'Survey', detail: 'one scout per family of detail, across every surface' },
    { title: 'Refute', detail: 'kill what would be noise; life is not decoration' },
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

const CRAFT = `The owner's ask (09-12): "all the subtle UI/UX details that breathe love and life into
the app". The bar is Factorio and Notion. Factorio's love is feedback: every act answers at once,
visibly, in the place it happened, and the game never lets a person wonder whether it heard them.
Notion's love is restraint: calm type, a spacing rhythm, nothing moves that does not need to, the
copy speaks like a person. Life comes from both, never from decoration. Judge against
docs/ui-architecture.md (the speed covenant, the frequency rule for motion, the vocabulary) and
against these, which are the family of detail you scout for. A finding is one place where the
app is mute, jumpy, uneven or cold, with the file and line that decides it and the smallest change
that gives it life; and for every finding say what it would cost a person who does this act a
hundred times a day, because a flourish on a hundred-a-day act is noise. Not a feature. Not a
redesign. The harness shows you the surface: write a probe, run node scripts/harness_proof.mjs,
read the PROBE line and the capture.`

const FAMILIES = [
  { key: 'answered-at-once', ask: `${CRAFT}\n\nFamily: every act answered. For each key and click in the grid, loupe, Rank, sidebar and search: what changes on screen within 100 ms, where, and is it in the place the act happened (the cell, the card, the count) rather than a toast elsewhere; and every act from a text field (search, a chip, naming a person, a label, a rename) answered in words as well, because the owner asked for it (09-12: "I really like toasts, esp in the people area and search, and other text fields ... but everywhere"), in the wording the thing will wear, so a word with two readings is disambiguated (his example: "Landscape photos vs landscape orientation"). List every act the app answers with nothing, or late, or somewhere else, and every text-field act with no sentence.` },
  { key: 'settling', ask: `${CRAFT}\n\nFamily: settling. When a set changes (a pick leaves, tiles arrive, a sort flips, Best reorders, a stack folds), does the change settle or jump: what moves, over how long, with what easing, and whether a person can follow what went where. Hold each to the frequency rule; jumps on tens-a-day acts and motion on hundreds-a-day acts are both findings.` },
  { key: 'rhythm-and-type', ask: `${CRAFT}\n\nFamily: rhythm and type (index.css and every shell CSS). One spacing scale, honoured; one type scale; numbers tabular and aligned; optical alignment of icons to text; consistent radii, borders and shadows; the same control the same size everywhere. Measure in the harness (getComputedStyle) rather than reading the CSS alone; list every stray value.` },
  { key: 'the-voice', ask: `${CRAFT}\n\nFamily: the voice. Every string a person reads: toasts, empty states, tooltips, the count line, the inspector, dialogs, errors (kit/words.js, kit/why.js and inline). Does it speak like a photographer would to a friend, in a sentence, with the right plural and the vocabulary; does it say what happened and what to do; is it the same voice everywhere. List the cold, the jargon, the label-where-a-sentence-belongs.` },
  { key: 'hints-that-retire', ask: `${CRAFT}\n\nFamily: teaching that gets out of the way. First-time hints (the loupe's key hints, the ? map, tooltips, empty-state teaching): do they appear when a person is new, teach the key, and retire once the person has used it; do they come back when needed. Anything that teaches forever, or never, is a finding (kit/remembered.js is the memory).` },
  { key: 'moments', ask: `${CRAFT}\n\nFamily: the moments that deserve a quiet flourish, and have none. The first sweep finishing (what it found: photographs, years, cameras); Best settling after a Rank sitting; a stack folding; Empty trash; a day's cull complete; a drive coming back. Rare acts, once-a-sitting, where a beat of acknowledgement is life and its absence is a shrug. Each is one finding with the words and the motion it would have.` },
  { key: 'pointer-and-cursor', ask: `${CRAFT}\n\nFamily: the pointer and the cursor. Cursor shapes (grab on draggable, pointer on clickable, default on inert, not-allowed where a drop refuses), the keyboard cursor's ring and its motion, focus visible only from the keyboard, hover that adds rather than reveals, drag ghosts and drop targets that say yes or no before the drop. Measure in the harness.` },
  { key: 'restraint', ask: `${CRAFT}\n\nFamily: restraint. The opposite scout: find what is already too much. Motion on hundreds-a-day acts, toasts on hundreds-a-day grid acts whose result is on the tile (never the toasts after a text-field act: the owner wants those), confirmations undo already covers, decoration that carries no information, colour where grey would do, two ways to say one thing. Every removal that would make the app calmer is a finding.` },
]

const result = await round(FAMILIES, 8,
  `A UI change is proven in the harness: write or adapt a probe under a temporary path (not in
scripts/journeys unless it is a real task no probe covers), run node scripts/harness_proof.mjs,
and paste the PROBE line and the capture path. The built document is what the window opens, so a
change under web/static/v2/ is complete on its own; do not commit build/. Motion is transform and
opacity only, honours reduced-motion, and follows the frequency rule; a rule you find missing from
docs/ui-architecture.md, write into it in the same commit, one sentence.`)
return result
