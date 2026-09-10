# Azimuth Photo documentation

The documentation has five owners. Read them in this order:

1. [`MASTER_PLAN.md`](../MASTER_PLAN.md) owns Sean's exact product decisions.
2. [`CORE.md`](CORE.md) owns the V2 model and its safety invariants.
3. [`ARCHITECTURE.md`](ARCHITECTURE.md) owns the code shape around the core.
4. [`AGENTS.md`](../AGENTS.md) owns how work is performed and verified.
5. [`FINDINGS.md`](FINDINGS.md) owns what the audits found and what became of it.

[`REWRITE_LEDGER.md`](REWRITE_LEDGER.md) is the exhaustive V2 completion gate.
It does not own design; it records whether each area and exact code file has
satisfied the authorities above.

When documents disagree, the owner for that kind of fact wins.

## Current V2 guidance

- [Development](development.md) — setup, the edit map, checks, proofs, and
  the first-install smoke.
- [Install](INSTALL.md) — the frozen build, first launch, and where data lives.
- [Data and privacy](data-and-privacy.md) — originals are read in place.
- [Releasing](RELEASING.md) and [distribution](DISTRIBUTION_SPEC.md) — what a
  tag produces today.
- [Topology](TOPOLOGY.md) — laptop-first runtime and storage roles.
- [Product vision](product-vision.md) and [roadmap](product-roadmap.md) — the
  finished product, and the order surfaces are rebuilt in.
- [UI architecture](ui-architecture.md) — the interaction doctrine.
- [Organizing](ORGANIZING.md) — albums, labels, people, sessions as decisions.
- [Develop](DEVELOP.md) — the colour pipeline as it is; [the spec](DEVELOP_SPEC.md)
  is the parked Lightroom-shaped workspace.
- [Lightroom bridge](LR_BRIDGE_SPEC.md) — what a sidecar carries each way.
- [Guided-filter masking](guided-filter.md) — the mask-refine primitive and its
  measurements in [`examples/guided-filter/`](examples/guided-filter/).
- [Performance budgets](PERF_BUDGETS.md) — user-visible latency constraints.
- [Taste, sharpness and culling research](taste-and-culling-research.md) —
  the 09-10 studies distilled: the facets, the head, the experiments in order.
- [Gate study](GATES.md) — evidence behind the mechanical constraints.
- [Rewrite ledger](REWRITE_LEDGER.md) — exact rebuilt, proven, and removed
  inventory.
- [`assets/`](assets/) — demo-safe visuals for the public README.

## Historical

These describe the retired networked product. They are evidence about failure
modes and user expectations, not instructions:

- [`archive/`](archive/) — every V1 specification (authentication, the three
  import designs, hub/satellite sync, client updates), the V1 feature guide,
  publishing, background work, noise profiles, the changelog, the simplify
  log, quality programs, release waves, and superseded UX specifications.
  The import designs' custody and UX lessons were adopted on 08-19 by
  `web/model/intake.py`, the sweep, and the import overlay.

## Documentation rule

Do not add another overview, handoff, status report, or competing source of
truth. Update the owner above or the ledgers. A document that has stopped
being true is folded or moved to `archive/` with a dated banner in the same
commit that made it untrue. `scripts/gates/paths.py` fails the build when a
guide names a path that is not there.
