# photoArchive Agent Guide

Parent instructions in `/home/sean/Projects/AGENTS.md` still apply.

## Modularization Rule

- Features are vertical: page/API workflow ownership belongs in `web/features/<feature>/`.
- Shared engines are horizontal: reusable app shell, request helpers, response copying, query constraints, and invalidation fanout belong in `web/core/`.
- Keep `web/app.py` as the compatibility surface while extracting. `uvicorn app:app` must keep working from `web/`.
- Preserve public URLs, JSON response shapes, settings keys, and `window.PhotoArchive.*` behavior.
- Original source photos/videos are source-of-truth media. Do not mutate or delete them.
- Frontend remains no-build browser-native JavaScript. Do not introduce Vite, TypeScript, or bundling as part of modularization.

## Extraction Discipline

- Add or update a parity test before moving a route/helper that affects public behavior.
- Prefer facades during migration. Remove old import paths only in a final cleanup after all call sites move.
- Keep UX-neutral refactors separate from product changes, styling changes, and performance rewrites.
- When splitting routes, expose a `router` from `web/features/<feature>/routes.py` and register it from `web/core/app_factory.py` or the current compatibility module.
- When splitting SQL, move domain queries into `web/data/repositories/` and keep old names available through `web/db.py` until the migration is complete.

