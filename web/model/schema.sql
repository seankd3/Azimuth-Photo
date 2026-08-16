-- The core's tables. Five when it is finished; each arrives with the step that
-- needs it, so this file never describes something that is not yet true.
--
-- Steps 1 and 4: drives, copies. Then decisions and cache, the two halves of
-- "what you decided" and "what we computed".

CREATE TABLE IF NOT EXISTS drives (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT    NOT NULL UNIQUE,
    root       TEXT    NOT NULL,
    label      TEXT    NOT NULL DEFAULT '',
    -- May this drive be the last copy? The archive may; the working disk may
    -- not. It is the only policy bit in the storage model, and reads, backup
    -- and reclaim all derive their behaviour from it.
    is_record  INTEGER NOT NULL DEFAULT 0,
    seen_at    REAL
);

-- A copy is a hint that this drive held this photo. It is never a truth:
-- reading verifies, and deleting verifies harder. Because a stale copy row
-- costs nothing, a sweep may drop what it did not see without any of the
-- ratios, thresholds and override switches that guarding a *verdict* required.
CREATE TABLE IF NOT EXISTS copies (
    photo_id  INTEGER NOT NULL,
    drive_id  INTEGER NOT NULL REFERENCES drives(id) ON DELETE CASCADE,
    -- NULL means "at the photo's own tail", which is the normal case. It is
    -- spelled out only when a copy sits somewhere else.
    tail      TEXT,
    seen_at   REAL NOT NULL,
    PRIMARY KEY (photo_id, drive_id)
);

CREATE INDEX IF NOT EXISTS idx_copies_drive ON copies(drive_id);

-- The only irreplaceable table. Append-only: a decision is never updated and
-- never deleted, so changing your mind is one more row and the row before it
-- is still there. `subject` is any stable identity — a photo's content hash, a
-- folder's tail, a drive's uuid, a person's name — which is what stops a
-- decision about a folder from needing a table of its own.
CREATE TABLE IF NOT EXISTS decisions (
    id      INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    family  TEXT NOT NULL,
    value   TEXT,
    at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_subject
    ON decisions(subject, family, at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_family ON decisions(family, at DESC);

-- Everything the machine worked out, keyed on what it looked at rather than
-- where the file was. `recipe` is a pure function of the inputs and never a
-- timestamp: feed `updated_at` in and reset-then-redo re-renders identical
-- pixels while two machines never share an entry.
--
-- `path` names a rendition on disk; `value` holds a computed fact inline. One
-- table for both because a thumbnail and an embedding differ only in where the
-- answer is big enough to want its own file.
CREATE TABLE IF NOT EXISTS cache (
    hash   TEXT    NOT NULL,
    kind   TEXT    NOT NULL,
    recipe TEXT    NOT NULL DEFAULT '',
    -- ready | failed. A failure is stored so it is not rediscovered every
    -- pass; `note` says why, once.
    state  TEXT    NOT NULL DEFAULT 'ready',
    path   TEXT,
    value  BLOB,
    bytes  INTEGER NOT NULL DEFAULT 0,
    note   TEXT,
    at     REAL    NOT NULL,
    PRIMARY KEY (hash, kind, recipe)
);

-- Eviction reads this: oldest first, within one kind.
CREATE INDEX IF NOT EXISTS idx_cache_kind_age ON cache(kind, at);
