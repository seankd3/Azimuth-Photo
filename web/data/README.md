# Data Layer

`web/db.py` remains the compatibility facade during modularization. New domain SQL should move into `web/data/repositories/` with old function names re-exported from `db.py` until every call site has migrated.

Keep schema and connection lifecycle separate:

- `connection.py`: SQLite path, WAL setup, and connection helpers.
- `schema.py`: migrations and schema checks.
- `repositories/`: domain SQL grouped by feature.

