export const meta = {
  name: 'realdata',
  description: 'Exercise the deployed hub against 153k real photos and report what only real data shows',
  whenToUse: 'After deploying to omarchy. The four-photo fixture cannot produce OOM, cold-start cost, or skewed distributions.',
  phases: [
    { title: 'Probe', detail: 'one agent per concern, against the live hub' },
    { title: 'Judge', detail: 'separate real defects from known behaviour' },
  ],
}

const HOUSE = `
Azimuth Photo's hub runs on omarchy, reachable over Tailscale. Reach it with ssh:

  ssh omarchy 'HUB=$(tailscale ip -4|head -1); curl -s -m 60 "http://$HUB:8000/api/stats"'

It serves a real library: about 153,891 catalog rows, 147,334 active, 2.6M
comparisons, on a 16 GB box with the catalog on a spinning archive disk.

READ-ONLY. You may GET any route and read logs and the database. You must not POST,
PUT or DELETE, must not restart the service, must not run migrations, and must not
write to the catalog. These are the owner's actual photographs. If a check would
need a write, describe the check instead of running it.

Things only real data has shown, so far:
  - the OOM killer took the service during startup when 20 requests arrived while
    it was still doing boot work: 8.7 GB peak, 6 GB swap
  - the first ranking query after a restart cost 4-29 s while caches populated,
    against 1.5 ms warm
  - 1,306 files named .CR2 are actually JPEGs
  - ~58k preview rows pointed at deleted files after a past space reclaim

Useful, all read-only:
  sudo journalctl -u azimuth-photo.service --since "1 hour ago" --no-pager
  sqlite3 /home/sean/Projects/photo-archive/web/azimuth.db "<SELECT ...>"
  free -g ; df -h ; ss -tlnp
Always call a route twice and report both timings; the first includes cache warming.
`

const FINDING = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'observed', 'why_it_matters'],
        properties: {
          title: { type: 'string' },
          observed: { type: 'string', description: 'the command run and its actual output' },
          why_it_matters: { type: 'string' },
          scale_dependent: { type: 'boolean', description: 'true if a small library would never show this' },
          severity: { type: 'string', enum: ['broken', 'slow', 'untidy'] },
        },
      },
    },
  },
}

const JUDGED = {
  type: 'object',
  required: ['is_defect', 'reason'],
  properties: {
    is_defect: { type: 'boolean' },
    reason: { type: 'string' },
    already_known: { type: 'boolean', description: 'true if docs/SIMPLIFY_LOG.md or MASTER_PLAN.md already records it' },
    fix: { type: 'string' },
  },
}

const PROBES = [
  { key: 'routes', ask: 'Call every route the desktop UI uses, twice each, and report status and both timings. Flag any non-200, and any warm call slower than 250 ms.' },
  { key: 'distribution', ask: 'Query the catalog for shapes that skew behaviour: status counts, photos with no embedding, no thumbnail, no date, null aspect_ratio, duplicate content hashes, sources marked offline. Report what a small fixture would never contain.' },
  { key: 'memory', ask: 'Watch the service under a burst of concurrent GETs. Report RSS before and after, swap use, and anything in journalctl about OOM or restarts. Do not push it hard enough to kill it — this is the owner\'s live server.' },
  { key: 'coldstart', ask: 'Measure what the first request after a quiet period costs versus a warm one, across the main routes. Do NOT restart the service; use routes that are unlikely to be warm already, and say plainly which numbers are cold.' },
  { key: 'integrity', ask: 'Look for rows the app would render wrong: preview cache rows pointing at missing files, images whose filepath no longer exists, hub_image_id collisions, statuses outside kept/maybe/trashed.' },
]

phase('Probe')
const probed = await parallel(
  PROBES.map((p) => () =>
    agent(
      `${HOUSE}\n\nProbe: ${p.key}.\n${p.ask}\n\n` +
        `Report at most 4 findings. Paste real command output as evidence — a finding without it ` +
        `will be thrown out. "Healthy" is a good answer.`,
      { label: `probe:${p.key}`, phase: 'Probe', schema: FINDING },
    ),
  ),
)

const found = probed.filter(Boolean).flatMap((r) => r.findings || [])
log(`${found.length} observations from the live archive`)
if (!found.length) return { findings: [], note: 'the hub looked healthy on every probe' }

phase('Judge')
const judged = await parallel(
  found.slice(0, 8).map((f) => () =>
    agent(
      `${HOUSE}\n\nIs this a defect, or is it how the system is meant to work?\n\n` +
        `${f.title}\nObserved: ${f.observed}\nClaimed to matter because: ${f.why_it_matters}\n\n` +
        `Read docs/SIMPLIFY_LOG.md and MASTER_PLAN.md before answering — several of these are known ` +
        `and recorded, and re-reporting them wastes the owner's attention. A cold cache is not a ` +
        `defect. Background work being slow is not a defect. Say is_defect=false freely.`,
      { label: `judge:${f.title.slice(0, 28)}`, phase: 'Judge', schema: JUDGED },
    ).then((v) => ({ ...f, verdict: v })),
  ),
)

const defects = judged.filter(Boolean).filter((f) => f.verdict?.is_defect && !f.verdict?.already_known)
return {
  observed: found.length,
  defects: defects.map((f) => ({ title: f.title, observed: f.observed, why: f.why_it_matters, fix: f.verdict.fix, scale_dependent: f.scale_dependent })),
  known: judged.filter(Boolean).filter((f) => f.verdict?.already_known).length,
  dismissed: judged.filter(Boolean).filter((f) => !f.verdict?.is_defect).length,
}
