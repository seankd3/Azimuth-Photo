export const meta = {
  name: 'seams',
  description: 'Check every client call against the route it calls: params, shapes, repetition, types',
  whenToUse: 'Hunting the class of bug where two sides of one boundary disagree and neither side is wrong on its own.',
  phases: [
    { title: 'Map', detail: 'inventory both sides of the boundary' },
    { title: 'Compare', detail: 'one agent per surface area' },
    { title: 'Confirm', detail: 'prove the mismatch bites' },
  ],
}

const HOUSE = `
Azimuth Photo, branch "simplify". FastAPI backend in web/, vanilla-JS clients in
web/static/js/desktop and /mobile, Kotlin in android/, Tauri shell in desktop/.

You are hunting seam defects: a place where the caller and the callee each look
correct alone and disagree with each other. The one that motivated this workflow:
the sidebar sent one "folder=" per selected node, /api/mosaic/next declared
"folder: str", FastAPI kept only the LAST value, and Refine silently scoped to a
third of what the user picked. It showed as "Not enough photos to refine" on a
195-photo selection. Nothing in either file looked wrong.

Shapes of this defect worth looking for:
  - a repeated query parameter received as a scalar (or vice versa)
  - a client sending a comma-joined string where the route parses a list
  - a field the client reads that the route never returns, or returns under
    another name, so the UI silently renders a default
  - a type the client assumes (number) that the route can return as null
  - a default that differs between the route signature and the service function
  - a route the client calls with params the route does not declare at all
  - pagination/limit semantics that differ between caller and callee

Verify against the running app rather than by reading alone where you can:
  cd web && AZIMUTH_HOME="$TEMP/seams" AZIMUTH_MODE=standalone ./.venv/Scripts/python.exe -m uvicorn app:app --port 8031
then curl it. Its OpenAPI schema is at /openapi.json and is the authority on what
each route declares. Do not edit any file.
`

const MISMATCH = {
  type: 'object',
  required: ['mismatches'],
  properties: {
    mismatches: {
      type: 'array',
      items: {
        type: 'object',
        required: ['route', 'client_file', 'client_line', 'server_file', 'server_line', 'disagreement', 'user_visible_effect'],
        properties: {
          route: { type: 'string' },
          client_file: { type: 'string' },
          client_line: { type: 'integer' },
          server_file: { type: 'string' },
          server_line: { type: 'integer' },
          disagreement: { type: 'string', description: 'what each side believes' },
          user_visible_effect: { type: 'string', description: 'what a person would see; "none observable" is an honest answer' },
          confidence: { type: 'string', enum: ['certain', 'likely', 'speculative'] },
        },
      },
    },
  },
}

const CONFIRMED = {
  type: 'object',
  required: ['real', 'evidence'],
  properties: {
    real: { type: 'boolean' },
    evidence: { type: 'string', description: 'the request made and the response got, or why it cannot bite' },
    fix: { type: 'string' },
  },
}

const AREAS = [
  { key: 'library', ask: 'The grid and its scope: /api/rankings, /api/counts, /api/date-groups, /api/date-histogram, /api/filter-options, /api/map/markers. Focus on how scope params (folder, date_taken, people, ids, exclude_sources) travel.' },
  { key: 'refine', ask: 'Ranking: /api/mosaic/next, /api/mosaic/pick, /api/compare, /api/compare/undo, /api/propagation/last. Focus on what the client sends per pick and what it reads back.' },
  { key: 'develop', ask: 'Editing and export: the develop settings routes, /api/export, preview and thumb routes. Focus on setting names and numeric types crossing the boundary.' },
  { key: 'collections', ask: 'Collections, shares and publishing routes. Focus on id list shapes, smart-collection queries, and fields the gallery template reads.' },
  { key: 'android', ask: 'The Kotlin client in android/. It hand-writes URLs and parses JSON by field name, so it drifts silently. Compare what it sends and reads against the routes it calls.' },
]

phase('Map')
const found = await parallel(
  AREAS.map((area) => () =>
    agent(
      `${HOUSE}\n\nSurface area: ${area.key}.\n${area.ask}\n\n` +
        `For each route in your area, read the client call site AND the route signature AND ` +
        `the service function behind it. Report only genuine disagreements, at most 4, strongest first. ` +
        `"Both sides agree" is a fine result — say so rather than inventing something.`,
      { label: `map:${area.key}`, phase: 'Map', schema: MISMATCH },
    ),
  ),
)

const all = found.filter(Boolean).flatMap((r) => r.mismatches || [])
const worth = all.filter((m) => m.confidence !== 'speculative')
log(`${all.length} mismatches reported, ${worth.length} above speculative`)
if (!worth.length) return { mismatches: [], note: 'both sides agreed everywhere checked' }

phase('Confirm')
const confirmed = await parallel(
  worth.slice(0, 8).map((m) => () =>
    agent(
      `${HOUSE}\n\nProve or disprove this by MAKING THE REQUEST, not by reading.\n\n` +
        `Route: ${m.route}\nClaim: ${m.disagreement}\nExpected effect: ${m.user_visible_effect}\n` +
        `Client: ${m.client_file}:${m.client_line}\nServer: ${m.server_file}:${m.server_line}\n\n` +
        `Boot the app on a scratch home, send the request the client would send, and show what came ` +
        `back. If the mismatch cannot actually bite — the value is unused, the shapes coincide, the ` +
        `path is dead — say real=false and why. Do not edit source.`,
      { label: `confirm:${m.route}`, phase: 'Confirm', schema: CONFIRMED },
    ).then((v) => ({ ...m, verdict: v })),
  ),
)

const real = confirmed.filter(Boolean).filter((m) => m.verdict?.real)
return {
  checked: all.length,
  confirmed: real.map((m) => ({ route: m.route, disagreement: m.disagreement, effect: m.user_visible_effect, evidence: m.verdict.evidence, fix: m.verdict.fix })),
  dismissed: confirmed.filter(Boolean).filter((m) => !m.verdict?.real).length,
}
