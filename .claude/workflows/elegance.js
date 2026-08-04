export const meta = {
  name: 'elegance',
  description: 'Find duplicated rules and needless machinery, refute the weak findings, fix what survives',
  whenToUse: 'A refactoring pass over Azimuth for simplification and code quality. Run it repeatedly; it is designed to be a loop.',
  phases: [
    { title: 'Survey', detail: 'parallel scouts, one concern each' },
    { title: 'Refute', detail: 'try to kill each finding before believing it' },
    { title: 'Fix', detail: 'one worktree per surviving finding' },
    { title: 'Report', detail: 'rank what landed and what is left' },
  ],
}

// Each scout owns ONE question. They overlap deliberately: a rule written twice
// looks like duplication to one scout and a missing name to another, and two
// descriptions of the same defect are worth more than one.
const SCOUTS = [
  {
    key: 'repeated-rule',
    ask: `Find a decision this codebase makes in more than one place: the same SQL predicate,
the same extension set, the same threshold, the same status vocabulary, the same guard.
Rank by how badly the copies could disagree, not by how many there are.
Precedent from this repo: "status IN ('kept','maybe')" appeared ~170 times; six extension
sets turned out to be three different questions; five predicates answered "what node am I".`,
  },
  {
    key: 'needless-machinery',
    ask: `Find abstraction that costs more than it earns: a wrapper that only forwards, a
parameter nobody varies, a registry with one entry, a configure() that sets what the module
could import, a cache nobody reads, a knob with one value in the whole tree.
Precedent: core/wiring.py was 650 lines whose arguments were 90 lambdas of "lambda: db.x()".`,
  },
  {
    key: 'silent-failure',
    ask: `Find code that swallows a mistake instead of reporting it: .get(key, <plausible default>)
on user input, a bare except that returns a neutral value, a fallback that makes a broken
config look healthy, a guard that answers "no problem" when it was never wired up.
Precedent: RANKING_SORTS.get(sort, "elo DESC") renders Elo order under a control reading "Lens".`,
  },
  {
    key: 'shape-mismatch',
    ask: `Find where two sides of one seam disagree: a route declaring a scalar for something the
client sends repeatedly, a cache key built two ways, a field the writer sets and the reader
never looks at, a default that differs between two callers of the same function.
Precedent: /api/mosaic/next declared folder: str while the sidebar sends one folder= per
selected node, so Refine silently scoped to the last of three folders.`,
  },
  {
    key: 'naming',
    ask: `Find names that lie or hide: a predicate whose name is the opposite of what it returns
for some input, a helper whose docstring describes behaviour it no longer has, a literal
repeated often enough to deserve a name, a module whose contents left long ago.
Precedent: is_satellite_mode() returned True for standalone; it was exactly not is_hub_mode().`,
  },
]

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
          evidence: { type: 'string', description: 'other file:line sites that share the defect' },
          why: { type: 'string', description: 'what goes wrong for a person using the app, or for the next agent' },
          fix: { type: 'string', description: 'the concrete change; name the one sentence it collapses to' },
          risk: { type: 'string', enum: ['low', 'medium', 'high'] },
          lines_removed: { type: 'integer', description: 'estimated net line change, negative means shrinkage' },
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
    hidden_caller: { type: 'string', description: 'where a "dead" thing is actually used, if anywhere' },
  },
}

const OUTCOME = {
  type: 'object',
  required: ['applied', 'summary'],
  properties: {
    applied: { type: 'boolean' },
    summary: { type: 'string' },
    branch: { type: 'string' },
    net_lines: { type: 'integer' },
    checks: { type: 'string', description: 'exact commands run and their results' },
  },
}

// Rules this repo paid for. Prepended to every agent so none of them has to be
// rediscovered the expensive way.
const HOUSE = `
You are working in Azimuth Photo (FastAPI + vanilla JS + SQLite), on branch "simplify".
Read AGENTS.md and docs/SIMPLIFY_LOG.md before you touch anything.

Rules that cost this project real time to learn:

1. "Nothing calls it" is evidence about clients, not about intent. A hand-run CLI, a CI job,
   a systemd timer, a generator and a release gate all look identical to dead code under grep.
   Before calling anything dead, also check: scripts/build_server.py, deploy/*.service and
   *.timer, desktop/src-tauri/, clients/, android/, .github/workflows/, scripts/azimuth-check,
   and MASTER_PLAN.md's open rows.
2. Automated sweeps are for FINDING. Edit by hand. Regex substitution across this codebase has
   corrupted it repeatedly: matching a function name inside a longer name, matching a parameter
   name, prefixing the wrong quote inside a triple-quoted string. If you sweep, parse the result
   with ast and assert the emitted text is what you meant.
3. Behaviour-preserving means proven, not assumed. If you change SQL, diff the emitted string
   against the original character for character — data/schema.py carries the same predicates in
   partial indexes and SQLite only uses those when the query's WHERE implies theirs.
4. Every test and check command finishes in under 10 seconds. pytest.ini enforces it.
5. Timing comparisons must be back to back on the same machine. This box runs Steam.

Verification available to you, from web/:
  ./.venv/Scripts/python.exe -m ruff check .
  ./.venv/Scripts/python.exe -m harness --check     # goldens: routes, decode, renditions
  ./.venv/Scripts/python.exe -m pytest -q -p no:warnings <files>
and from the repo root:
  ./scripts/smoke                                    # boots the app, probes 17 routes, exit code is the result
`

phase('Survey')
const surveyed = await parallel(
  SCOUTS.map((scout) => () =>
    agent(
      `${HOUSE}\n\nYou are the "${scout.key}" scout.\n\n${scout.ask}\n\n` +
        `Read widely before concluding. Return at most 4 findings, the strongest first. ` +
        `A finding with no concrete fix is not a finding. Do not edit any file.`,
      { label: `survey:${scout.key}`, phase: 'Survey', schema: FINDING },
    ),
  ),
)

const found = surveyed
  .filter(Boolean)
  .flatMap((r, i) => (r.findings || []).map((f) => ({ ...f, scout: SCOUTS[i].key })))

// Dedup across scouts: same file and line is the same defect described twice.
const seen = new Set()
const unique = found.filter((f) => {
  const key = `${f.file}:${f.line}`
  if (seen.has(key)) return false
  seen.add(key)
  return true
})
// One round stays small enough to read. The loop is what gets coverage, not the
// width of any single pass — and a round nobody reads gets merged unread.
const PER_ROUND = 5
// Safest first, then biggest shrinkage. A loop that applies its own changes
// should spend its budget where being wrong is cheap; a high-risk finding is
// worth surfacing to a person, not worth an unattended edit.
const RANK = { low: 0, medium: 1, high: 2 }
const ordered = unique.sort(
  (a, b) => (RANK[a.risk] ?? 3) - (RANK[b.risk] ?? 3) || (a.lines_removed ?? 0) - (b.lines_removed ?? 0),
)
const queue = ordered.slice(0, PER_ROUND)
const deferred = ordered.slice(PER_ROUND)
log(`${found.length} findings, ${unique.length} distinct, taking ${queue.length}`)
if (deferred.length) {
  log(`deferred to the next round: ${deferred.map((f) => f.title).join(' | ')}`)
}

if (!queue.length) return { landed: [], note: 'nothing found worth changing' }

// Refute then fix, per finding, without a barrier — a cheap finding should not
// wait behind an expensive one.
const results = await pipeline(
  queue,
  (f) =>
    agent(
      `${HOUSE}\n\nTry to REFUTE this claim. Default to refuted=true when uncertain.\n\n` +
        `Claim: ${f.title}\nAt: ${f.file}:${f.line}\nReasoning given: ${f.why}\n` +
        `Proposed fix: ${f.fix}\n\n` +
        `Refute it if: the copies are not actually the same decision; the "duplicate" sites have ` +
        `different semantics; something does call it (check every tree listed in rule 1); the fix ` +
        `would change behaviour; or the abstraction earns its cost. Read the code. Do not edit.`,
      { label: `refute:${f.file.split('/').pop()}:${f.line}`, phase: 'Refute', schema: VERDICT },
    ).then((v) => ({ finding: f, verdict: v })),
  (checked) => {
    if (!checked || !checked.verdict || checked.verdict.refuted) return checked
    const f = checked.finding
    return agent(
      `${HOUSE}\n\nMake this change. It survived an adversarial review.\n\n` +
        `${f.title}\nAt: ${f.file}:${f.line}\nFix: ${f.fix}\nWhy: ${f.why}\n\n` +
        `Work on a branch named elegance/<short-slug> off the current HEAD. Commit only when ` +
        `ruff, the harness, and the tests covering the files you touched are green — run them and ` +
        `paste the real output in your summary. If the change turns out to be wrong or to need a ` +
        `product decision, stop and say so with applied=false; that is a good outcome, not a failure. ` +
        `Write the commit message so it explains what was collapsed and why, in plain prose.`,
      {
        label: `fix:${f.file.split('/').pop()}`,
        phase: 'Fix',
        schema: OUTCOME,
        isolation: 'worktree',
      },
    ).then((out) => ({ ...checked, outcome: out }))
  },
)

const done = results.filter(Boolean)
const landed = done.filter((r) => r.outcome && r.outcome.applied)
const refuted = done.filter((r) => r.verdict && r.verdict.refuted)
const held = done.filter((r) => r.outcome && !r.outcome.applied)

phase('Report')
const report = await agent(
  `${HOUSE}\n\nSummarise this refactoring round for the repo owner, who reads three lines before deciding.\n\n` +
    `Landed:\n${JSON.stringify(landed.map((r) => ({ title: r.finding.title, branch: r.outcome.branch, net: r.outcome.net_lines, checks: r.outcome.checks })), null, 1)}\n\n` +
    `Refuted:\n${JSON.stringify(refuted.map((r) => ({ title: r.finding.title, reason: r.verdict.reason })), null, 1)}\n\n` +
    `Held for a decision:\n${JSON.stringify(held.map((r) => ({ title: r.finding.title, why: r.outcome.summary })), null, 1)}\n\n` +
    `Say what landed and what it is worth, what the refutations reveal about where the survey was ` +
    `weak, and the single highest-value thing to do next. Append a dated section to ` +
    `docs/SIMPLIFY_LOG.md recording this round, matching the file's existing voice. ` +
    `Do not overstate: a refuted finding is the workflow working.`,
  { label: 'report', phase: 'Report' },
)

return {
  found: found.length,
  distinct: unique.length,
  landed: landed.map((r) => ({ title: r.finding.title, branch: r.outcome.branch, net_lines: r.outcome.net_lines })),
  refuted: refuted.length,
  held: held.length,
  report,
}
