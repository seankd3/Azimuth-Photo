# Deferred Refactor Notes

## Remaining Compatibility Surfaces

- Keep draining `web/static/js/legacy/app.js` into browser-native ES modules while preserving `window.PhotoArchive.*` methods.
- Keep shrinking `web/db.py` only after repository call sites and cache-invalidation fanout have parity tests.
- Keep moving thumbnail worker internals out of `web/thumbnails/__init__.py` while preserving the `import thumbnails` facade.
- Revisit cache placement once helper ownership is stable, especially the line between app-level visibility rules and thumbnail cache internals.
- Audit fetch-wrapper edge cases before centralizing requests: aborted generations, warm-cache reads, stale search state, and non-JSON error responses.
