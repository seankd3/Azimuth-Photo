-- The whole durable core. An empty file plus this schema is a valid catalog;
-- nothing in data/, features/, or a migration has to exist first.

CREATE TABLE IF NOT EXISTS images (
    id               INTEGER PRIMARY KEY,
    filename         TEXT    NOT NULL DEFAULT '',
    tail             TEXT,
    file_size        INTEGER,
    file_modified_ns INTEGER,
    content_hash     TEXT,
    date_taken       TEXT,
    camera_make      TEXT,
    camera_model     TEXT,
    lens             TEXT,
    file_ext         TEXT,
    width            INTEGER,
    height           INTEGER,
    status           TEXT    NOT NULL DEFAULT 'kept',
    stars            INTEGER NOT NULL DEFAULT 0,
    elo              REAL    NOT NULL DEFAULT 1200.0,
    version_of       INTEGER REFERENCES images(id) ON DELETE SET NULL,
    -- Transitional only. A V1 virtual copy has no file of its own; it leaves
    -- when Develop is rebuilt as decisions plus cached pixels.
    vc_of            INTEGER REFERENCES images(id) ON DELETE CASCADE,
    created_at       REAL    NOT NULL DEFAULT (unixepoch())
);

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
    at      REAL NOT NULL,
    -- Who made the decision. Your own answer outranks imported metadata no
    -- matter which was observed last; this column makes that one rule rather
    -- than a WHERE clause copied into every importer.
    by      TEXT NOT NULL DEFAULT 'you'
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

-- The photo table's indexes. There are six, and the number is the point: the
-- old schema carried 86 on this one table and the grid still fell to a full
-- scan with a temp B-tree, because none of them matched the query anyone
-- actually ran. Measured on 157,064 rows: 180 ms before, 0.4 ms after.
--
-- Each one exists because a named query in library.py reads it, and each is
-- partial on the same predicate that query uses, spelled identically. SQLite
-- only applies a partial index when the query's WHERE implies the index's own,
-- so rewording either side silently costs a table scan.
-- One index serves newest *and* oldest: SQLite scans an index backwards for
-- free, so a second one in the other direction buys nothing. Measured: both
-- directions read this same index at 0.3 ms.
CREATE INDEX IF NOT EXISTS idx_photos_date
    ON images(date_taken ASC, id ASC) WHERE status != 'trashed';
CREATE INDEX IF NOT EXISTS idx_photos_best
    ON images(elo DESC, id DESC) WHERE status != 'trashed';
CREATE INDEX IF NOT EXISTS idx_photos_stars
    ON images(stars DESC, date_taken DESC) WHERE status != 'trashed';
-- Folder browsing is a prefix of a tail, and a sweep looks photos up by one.
CREATE INDEX IF NOT EXISTS idx_photos_tail ON images(tail);
-- Identity: what `identify()` asks, and what every cache row is keyed on.
CREATE INDEX IF NOT EXISTS idx_photos_hash ON images(content_hash);
-- The one debt that is not a cache kind, newest first, cursor-free.
CREATE INDEX IF NOT EXISTS idx_photos_unidentified
    ON images(date_taken DESC, id DESC) WHERE content_hash IS NULL;
