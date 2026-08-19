---
name: azimuth-orient
description: Orient in the Azimuth Photo repository before touching it — find the live branch, today's gate debt, and whether the area you are about to change is V2 or legacy. Use at the start of any Azimuth task, when returning after time away, before trusting any path or command a document names, and whenever a claim about this codebase needs to be checked against the tree rather than remembered.
---

# Orient in Azimuth

**This tree moves faster than any description of it.** Ninety commits landed on
`v2-carve` in a day; `docs/` is roughly 390 KB, more than any agent will read;
and on 2026-08-17 the authoritative `AGENTS.md` was still assigning preview
generation, the route map and the PWA to three folders that had been deleted.
A document describing this codebase is stale before it is read.

So this skill holds almost no facts. It runs the commands that produce them.
**Prefer a measurement to a memory, including the memories in this file.**

## 1. Find the live branch

```bash
git status --short --branch && git worktree list
```

`main` is not necessarily where the work is. In August 2026 the live branch was
`v2-carve`, checked out in a worktree at `C:\Users\smast\azimuth-sandbox` —
outside the Projects folder entirely — while `main` sat ninety commits behind.

Read what the working tree already carries before you edit. Modified files you
did not touch are another session's work in flight: **stage explicit paths,
never `git add -A`, and never `git checkout <file>` to undo a test edit** — it
discards your own uncommitted work in that file too.

## 2. Measure today's debt

```bash
python scripts/gates/check.py          # every gate, one number each
python scripts/gates/check.py --list   # and what each number is counting
```

Each number rises only by hand and falls by measurement. **Any gate reading
`ROSE` is the current debt** — read those lines first, because they tell you
what is broken right now in a way no document can.

A gate that was already red before you arrived is not yours to fix silently, and
not yours to claim either. To separate your effect from the tree's, measure a
clean baseline instead of guessing:

```bash
git archive --format=tar HEAD | tar -xf - -C <scratch>   # HEAD without in-flight edits
```

Two gates deserve specific respect:

- `paths` — an instruction may not name a path that is not there. If it rises
  after your change, a document you edited is now lying about the tree.
- `names` / `imports` — a name that is called is a name that exists. Python
  resolves a name when the line runs, so a call into a deleted module compiles,
  imports, and passes every test that does not take that branch.

## 3. Ask whether the area is V2 yet

```bash
grep -n '<your area>' docs/REWRITE_LEDGER.md
```

`REWRITE_LEDGER.md` is the only file-level answer to "is this rebuilt?", and
**everything is Legacy by default**. Its states are Legacy → Designing →
Rebuilt → Proven, plus Removed. There is no "adopted" or "mostly migrated": new
code calling old machinery is still Legacy.

This decides your whole approach. In a Legacy area you are not adapting or
wrapping — the standing order is *gut it and rebuild from first principles,
with little regard for the ways of old*, deleting the machinery in the same
commit. In a Proven area you are protecting something that was expensive to
earn.

## 4. Read four documents, in authority order

`docs/README.md` names the chain, and it is short. When two documents disagree,
the owner of that kind of fact wins — do not build a compatibility layer
between two competing truths.

1. `MASTER_PLAN.md` — the owner's exact asks, verbatim and dated. Append-only:
   record a new ask in section 1 **in the same session it is said**, one row per
   ask, never paraphrased into a task title, never deleted to mark it done.
   Contradictions live in section 4 and block work on the area they affect.
2. `docs/CORE.md` — the V2 model and its safety invariants.
3. `docs/ARCHITECTURE.md` — the shape of the code around that core.
4. `AGENTS.md` — how work is performed, verified, and handed off. Its safety
   boundaries are not advisory.

Then read **the one spec that owns the surface you are changing**, and not the
rest of `docs/`.

## 5. Before claiming anything, boot it

A green suite says nothing about deferred call sites. Booting the app and
hitting four endpoints once found **six live NameErrors that 758 passing tests
had not** — including a daily backup that had silently stopped scheduling,
because a background worker's exception is logged, not raised.

```bash
python scripts/make_test_library.py     # a few hundred photos, fast local disk
./scripts/azimuth-check --quick         # then --area <area>, see --list-areas
```

Never develop against the real archive, and never run a writing test against a
real catalog. No test may take longer than 30 seconds.

**Verify from empty.** The first-run path is the one nobody takes, so it is
where silent breakage lives: `model/schema.sql` was read by the test suite and
by nothing in the boot path, so every flag, tile and rotation would have failed
on a fresh install. *Build a fresh catalog to check a schema; never read the
file.*

## The standing bar

Elegance is the acceptance criterion, not size — but a change that only adds is
a change that has not found the shape. Before writing, grep for the concept you
are about to write, and say in one sentence what it does. After writing, say
what it makes unnecessary and delete that in the same commit.

Two habits pay more than the rest here:

- **If two features have the same shape, they are one feature with two names.**
  Collections, keywords, saved views, stacks and trash were five features and
  eight empty tables; they are now one primitive, *a set is a decision*.
- **A four-figure file is held up by two or three small couplings.** Cut those
  and it falls over on its own — `features/library/service.py`, 1,661 lines,
  was held by one eight-line function.

Report what you measured, not what you assumed, and never claim a passing suite
or a UI reproduction that did not happen.
