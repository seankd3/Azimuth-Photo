---
name: azimuth-orient
description: Orient in the Azimuth Photo repository before touching it — find the live branch, today's gate debt, and whether the area you are about to change is V2 or legacy. Use at the start of any Azimuth task, when returning after time away, and whenever a claim about this codebase needs checking against the tree rather than remembered.
---

# Orient in Azimuth

This tree moves faster than any description of it — ninety commits in a day, and
`docs/` is bigger than anyone will read. So run these four things. **Prefer a
measurement to a memory, including the memories in this file.**

**1. Find the live branch.**

```bash
git status --short --branch && git worktree list
```

`main` is often not where the work is, and the live branch is often a worktree
outside the Projects folder. Files you did not modify are another session's work
in flight: stage explicit paths, never `git add -A`, and never
`git checkout <file>` to undo an edit — it discards your own work in that file
too.

**2. Measure today's debt.**

```bash
python scripts/gates/check.py
```

Any gate reading `ROSE` is what is broken right now. One that was red before you
arrived is not yours to claim or to fix silently — check a clean baseline with
`git archive HEAD` before believing you caused it.

**3. Ask whether your area is V2 yet.**

```bash
grep -n '<your area>' docs/REWRITE_LEDGER.md
```

Everything is **Legacy** until that ledger says otherwise. In a Legacy area the
standing order is to gut it and rebuild from first principles, deleting the old
machinery in the same commit. In a **Proven** area you are protecting something
expensive. Nothing in between counts: new code calling old machinery is Legacy.

**4. Boot it before claiming anything.**

```bash
python scripts/make_test_library.py
./scripts/azimuth-check --quick
```

A green suite says nothing about deferred call sites — booting the app once
found six live NameErrors that 758 passing tests had not. Never develop against
the real archive. Build a fresh catalog to check a schema; never read the file.

Then read `docs/README.md` for the four owner documents, and the one spec that
owns the surface you are changing. Not the rest of `docs/`.
