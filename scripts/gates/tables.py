"""A table created once by hand is a table that does not exist.

`web/model/schema.sql` is the schema. Table DDL anywhere else runs on whichever
machine happened to reach that code path, which is how `taxonomy_move_journal`
came to be declared by `features/imports/move_journal.py` and to be absent from
all 86 tables of the live catalog on 2026-08-16.

The gate counts the words, not the statement, so prose about the DDL counts as
DDL — this docstring was itself the 69th hit until it was reworded. Temporary
tables are scratch inside one connection and are not counted; tests build their
own fixtures and are not counted. Measured 2026-08-16: **68**, none of them
prose.
"""

from __future__ import annotations

import re

from common import read, tracked

CREATE = re.compile(r"CREATE\s+TABLE", re.IGNORECASE)
SCHEMA = "web/model/schema.sql"


def run(root) -> list[str]:
    found = []
    for path in tracked(root, "*.py", "*.sql", tests=False):
        if path == SCHEMA:
            continue
        for number, line in enumerate(read(root, path).splitlines(), 1):
            if CREATE.search(line):
                found.append(f"{path}:{number}: {line.strip()[:80]}")
    return found
