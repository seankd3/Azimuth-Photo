# Azimuth Photo documentation

The documentation has four owners. Read them in this order:

1. [`MASTER_PLAN.md`](../MASTER_PLAN.md) owns Sean's exact product decisions.
2. [`CORE.md`](CORE.md) owns the V2 model and its safety invariants.
3. [`ARCHITECTURE.md`](ARCHITECTURE.md) owns the code shape around the core.
4. [`AGENTS.md`](../AGENTS.md) owns how work is performed and verified.

[`REWRITE_LEDGER.md`](REWRITE_LEDGER.md) is the exhaustive V2 completion gate.
It does not own design; it records whether each area and exact code file
has satisfied the authorities above. Anything absent from its proven register
is legacy by default.

When documents disagree, the owner for that kind of fact wins. A document may
preserve useful V1 behavior without preserving V1 architecture.

## Current V2 guidance

- [Topology](TOPOLOGY.md) — laptop-first runtime and storage roles.
- [Product vision](product-vision.md) — intended finished product.
- [Product roadmap](product-roadmap.md) — rewrite order and release outcomes.
- [UI architecture](ui-architecture.md) — interaction doctrine that survives
  the rewrite.
- [Development](development.md) — local setup and verification; commands must
  be checked against the current branch before use.
- [Install](INSTALL.md), [distribution](DISTRIBUTION_SPEC.md), and
  [releasing](RELEASING.md) — the current one-process Windows artifact.
- [Performance budgets](PERF_BUDGETS.md) — user-visible latency constraints.
- [Recovery](recovery.md) — catalog recovery behavior.
- [Stacks](STACKS_V2.md) — duplicate-safety distinctions and cleanup rules.
- [Gate study](GATES.md) — evidence behind the architecture's mechanical
  constraints; the architecture owns the rules themselves.
- [Rewrite ledger](REWRITE_LEDGER.md) — exact rebuilt, proven, legacy, and
  removed inventory.

## Behavior references awaiting V2 adoption

These documents contain expensive product learning. They do not authorize V1
tables, routes, workers, server roles, or synchronization machinery. A V2
surface adopts the behavior it still needs through the core, then updates the
document in the same commit.

- [Feature guide](features.md)
- [Import](IMPORT_SPEC.md) and [card import](CARD_IMPORT_SPEC.md)
- [Invisible import](INVISIBLE_IMPORT.md) — local-first, silent ingest intent.
- [Develop](DEVELOP_SPEC.md)
- [Lightroom bridge](LR_BRIDGE_SPEC.md)
- [Background work](background-work-behavior.md)
- [Data and privacy](data-and-privacy.md)
- [Publishing](publishing.md)
- [Camera noise profiles](noise-profiles.md)

## Historical V1 architecture

These describe the retired networked product. They are evidence about failure
modes and user expectations, not V2 instructions:

- [Authentication](AUTH_SPEC.md)
- [Hub/satellite sync](FIELD_SPEC_V2.md)
- [Client updates](CLIENT_AUTOUPDATE_SPEC.md)
- [Tailscale HTTPS](FIELD_HTTPS.md)
- [`archive/`](archive/) — quality programs, release waves, and superseded UX
  specifications.

The chronological [`SIMPLIFY_LOG.md`](SIMPLIFY_LOG.md) is an incident ledger.
Use it to recover why a rule exists, never as current architecture. Git history
and Claude's project memories are also evidence, not authority.

[`CARVE.md`](CARVE.md) is a point-in-time measurement of one rewrite commit.
Re-run its instruments before using any ranked item.

## Documentation rule

Do not add another overview, handoff, status report, or competing source of
truth. Update the owner above or the rewrite ledger. Preserve a historical
document when it contains learning the code cannot reveal, but label it
historical and remove it from the current path.
