# Azimuth Photo documentation

Start with the document that matches the question. Historical lane ledgers,
agent receipts, superseded release plans, and one-off performance reports live
in Git history and the Field Log rather than in the active documentation set.

## Product

- [Product vision](product-vision.md) — who the product is for and what it
  should feel like.
- [Product roadmap](product-roadmap.md) — current outcomes and next product
  priorities.
- [Feature guide](features.md) — the user-facing surface.
- [UI architecture](ui-architecture.md) — desktop and mobile interaction
  doctrine.

## Build and operate

- [Agent guide](../AGENTS.md) — authoritative workflow, safety boundaries, and
  documentation ownership for coding agents.
- [Topology](TOPOLOGY.md) — canonical checkouts, machine roles, runtime data,
  caches, and originals.
- [Install](INSTALL.md) — supported installation paths.
- [Getting started](getting-started.md) — first library and first run.
- [Development](development.md) — code map, local setup, and verification.
- [Codebase map](CODEBASE_MAP.md) — route and module ownership.
- [Releasing](RELEASING.md) — version and artifact release procedure.
- [Recovery](recovery.md) — catalog backups and restore.
- [Windows QA](WINDOWS_QA.md) — XPS/Windows verification.
- [QA harness](QA_HARNESS.md) — isolated desktop end-to-end verification.
- [Tailscale HTTPS](FIELD_HTTPS.md) — secure phone/PWA access.

## Core behavior

- [Background work](background-work-behavior.md)
- [Data and privacy](data-and-privacy.md)
- [Performance budgets](PERF_BUDGETS.md)
- [Publishing](publishing.md)
- [Camera noise profiles](noise-profiles.md)

## Feature specifications

Feature specifications capture behavior that is expensive to rediscover:

- [Authentication](AUTH_SPEC.md)
- [Import](IMPORT_SPEC.md) and [card import](CARD_IMPORT_SPEC.md)
- [Hub/satellite sync](FIELD_SPEC.md) and [v2 addendum](FIELD_SPEC_V2.md)
- [Distribution](DISTRIBUTION_SPEC.md)
- [Develop](DEVELOP_SPEC.md)
- [Client updates](CLIENT_AUTOUPDATE_SPEC.md)
- [Lightroom bridge](LR_BRIDGE_SPEC.md)
- [Stacks](STACKS_V2.md)

The public development narrative and preserved experiments are maintained under
