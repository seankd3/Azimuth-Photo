# Core Backend

Owns horizontal app plumbing shared across features:

- `app_factory.py`: FastAPI shell, static mounting, middleware, template factory.
- `background.py`: startup/shutdown task tracking.
- `requests.py`: JSON body parsing and request coercion helpers.
- `responses.py`: response-copy helpers and shared visibility-count fields.
- `query_constraints.py`: whole-library text, deep-search, and People filter composition through app-injected DB-backed providers.
- `cache_events.py`: small invalidation fanout helper for future route extraction.
- `static_assets.py`: git/static version and template context helpers.
- `wiring.py`: runtime provider and feature route dependency wiring used by the
  app shell.

Feature behavior should not accumulate here. If logic belongs to one workflow, put it under `web/features/<feature>/` and let core provide only the reusable contract.
